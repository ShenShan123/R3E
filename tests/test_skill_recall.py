"""召回部件(B) 纯逻辑测: 检索相似 / 三态分组 / top-K 截断 / 泛化呈现(无裸WNS) / 空库."""
import json

from microsurgeon_flow import skill_recall as sr
from microsurgeon_flow.failed_skill_builder import build_backend_skill_payload


def _seed(tmp_path, payloads):
    f = tmp_path / "backend_lib" / "skill_index.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(json.dumps(p) for p in payloads) + "\n")
    return f


def _mk(metric, design="gcd", platform="asap7", period=580.0, endpoint="resp_msg[2]",
        seq=None, initial=-50.0, final=None):
    if final is None:
        final = 1.0 if metric == "closed" else -10.0
    return build_backend_skill_payload(
        backend_metric=metric, case_id="c", design=design, platform=platform,
        variant="base", poison_param="clk_period=x", period=period,
        initial_wns=initial, final_wns=final,
        wns_trajectory=[-10.0], action_class_sequence=seq or ["vt_faster", "vt_faster"],
        rounds=2, violating_endpoints=[{"endpoint": endpoint}],
        failure_class=None if metric == "closed" else "other")


def test_empty_lib_returns_empty(tmp_path):
    f = tmp_path / "backend_lib" / "skill_index.jsonl"
    assert sr.recall_skills(f, design="gcd", platform="asap7", period=580) == \
        {"closed": [], "routed_unclosed": [], "failed": []}
    assert sr.format_recall({"closed": [], "routed_unclosed": [], "failed": []}) == ""


def test_recall_groups_by_state(tmp_path):
    f = _seed(tmp_path, [_mk("closed"), _mk("routed_unclosed"), _mk("failed")])
    g = sr.recall_skills(f, design="gcd", platform="asap7", period=580, endpoint="resp_msg[2]")
    assert len(g["closed"]) == 1 and len(g["routed_unclosed"]) == 1 and len(g["failed"]) == 1


def test_design_match_scores_higher(tmp_path):
    # 同 design(gcd) 应召回; 不同 design(aes) 且无端点重合 score 仍>0(platform+period) 但靠后
    f = _seed(tmp_path, [_mk("closed", design="aes", endpoint="x"), _mk("closed", design="gcd")])
    g = sr.recall_skills(f, design="gcd", platform="asap7", period=580,
                         endpoint="resp_msg[2]", k=1)
    # top-1 closed 应是 gcd(同 design +3 + 端点 +2)
    assert g["closed"][0]["precondition"]["design"] == "gcd"


def test_topk_truncation(tmp_path):
    f = _seed(tmp_path, [_mk("failed") for _ in range(5)])
    g = sr.recall_skills(f, design="gcd", platform="asap7", period=580,
                         endpoint="resp_msg[2]", k=2)
    assert len(g["failed"]) == 2          # 截断到 k


def test_recall_ranks_by_benefit_inside_similarity_pool(tmp_path):
    low_gain = _mk("routed_unclosed", initial=-50.0, final=-45.0)
    high_gain = _mk("routed_unclosed", initial=-50.0, final=-10.0)
    f = _seed(tmp_path, [low_gain, high_gain])
    g = sr.recall_skills(f, design="gcd", platform="asap7", period=580,
                         endpoint="resp_msg[2]", k=1, candidate_k=2)
    fs = g["routed_unclosed"][0]["precondition"]["failure_signature"]
    assert fs["final_wns"] == -10.0


def test_recall_similarity_pool_gates_unrelated_high_benefit(tmp_path):
    similar_low_gain = _mk("routed_unclosed", design="gcd", initial=-50.0, final=-45.0)
    unrelated_high_gain = _mk("routed_unclosed", design="aes", platform="sky130",
                              endpoint="other", initial=-100.0, final=-1.0)
    f = _seed(tmp_path, [similar_low_gain, unrelated_high_gain])
    g = sr.recall_skills(f, design="gcd", platform="asap7", period=580,
                         endpoint="resp_msg[2]", k=1, candidate_k=1)
    pc = g["routed_unclosed"][0]["precondition"]
    assert pc["design"] == "gcd"


def test_format_generalizable_no_raw_wns(tmp_path):
    f = _seed(tmp_path, [_mk("closed"), _mk("failed")])
    g = sr.recall_skills(f, design="gcd", platform="asap7", period=580, endpoint="resp_msg[2]")
    txt = sr.format_recall(g)
    assert "成功配方(closed" in txt and "负向标记(failed" in txt
    assert "vt_faster" in txt              # 泛化路径序列有
    assert "-10.0" not in txt and "final_wns" not in txt   # 裸 WNS / provenance 不喂


def test_format_omits_empty_states(tmp_path):
    f = _seed(tmp_path, [_mk("closed")])
    txt = sr.format_recall(sr.recall_skills(f, design="gcd", platform="asap7",
                                            period=580, endpoint="resp_msg[2]"))
    assert "closed" in txt and "routed_unclosed" not in txt and "failed" not in txt


# ── loopback 召回(P4) ──────────────────────────────────────────────────────────

def _lb_skill(fc, eps, ref):
    import json
    return json.dumps({"skill_name": f"loopback_{fc}_to_frontend",
        "precondition": {"is_loopback_skill": True,
            "backend_failure_signature": {"failure_class": fc, "violating_endpoints": eps}},
        "action_template": {"frontend_skill_ref": ref}})


def test_recall_loopback_by_failure_class(tmp_path):
    from microsurgeon_flow.skill_recall import recall_loopback_skills
    lib = tmp_path / "loopback_lib" / "skill_index.jsonl"
    lib.parent.mkdir(parents=True)
    lib.write_text("\n".join([
        _lb_skill("structural_bottleneck", ["rx->mid"], "frontend_structural_timing"),
        _lb_skill("structural_bottleneck", ["x3->mac"], "frontend_structural_timing"),
        _lb_skill("overconstraint_infeasible", ["a->b"], "frontend_other"),
    ]))
    # 同 failure_class 召回 2 条; 端点重合者排前
    hits = recall_loopback_skills(lib, failure_class="structural_bottleneck", endpoint="x3->mac", k=3)
    assert len(hits) == 2
    assert hits[0]["precondition"]["backend_failure_signature"]["violating_endpoints"] == ["x3->mac"]
    # 不匹配 failure_class → 不召回
    assert recall_loopback_skills(lib, failure_class="sizing_plateau") == []
    # 非 loopback skill 跳过(空库)
    assert recall_loopback_skills(tmp_path / "none.jsonl", failure_class="structural_bottleneck") == []
