"""后端三态技能 builder(A) 纯逻辑测: 归类 / 三态明显区分 / 不被 guard veto / 路径蒸馏.

边界校验: 三态都在 backend_lib(domain=backend), 不碰 loopback; closed 是 guard 此前
没见过的成功路径, 必须不被 hollow/broad/cheat veto.
"""
from memory_integrity_guard import SkillVetting
from microsurgeon_flow.failed_skill_builder import build_backend_skill_payload
from microsurgeon_flow.trajectory_agent import classify_backend_metric


def _payload(metric, initial, final):
    return build_backend_skill_payload(
        backend_metric=metric, case_id="c1", design="gcd", platform="asap7",
        variant="base", poison_param="clk_period=580", period=580.0,
        initial_wns=initial, final_wns=final, wns_trajectory=[final],
        action_class_sequence=["vt_faster", "vt_faster"], rounds=2,
        select_reasons=["改善最大 <num>"], violating_endpoints=[],
        failure_class=None if metric == "closed" else "other",
        cross_domain_trigger=(metric != "closed"),
        abstain_reason=None if metric == "closed" else "需回前端 <num>")


# ── 三态归类(WNS 驱动) ────────────────────────────────────────────────────────
def test_classify_backend_metric():
    assert classify_backend_metric(-55.9, 1.36) == "closed"          # final≥0
    assert classify_backend_metric(-325.9, -296.7) == "routed_unclosed"  # 改善但<0
    assert classify_backend_metric(-100.0, -100.0) == "failed"       # 无改善
    assert classify_backend_metric(-100.0, -120.0) == "failed"       # 恶化


# ── 三态明显区分(三重冗余标注, LLM 召回无歧义) ───────────────────────────────
def test_three_state_markers_distinct():
    for metric in ("closed", "routed_unclosed", "failed"):
        p = _payload(metric, -55.9, 1.36 if metric == "closed" else -10.0)
        assert p["skill_name"] == f"{metric}_trajectory"                     # 前缀
        assert p["precondition"]["backend_outcome"] == metric               # 显式 marker
        assert p["validation"]["backend_metric"] == metric                  # 权威字段
        assert p["precondition"]["is_failed_skill"] == (metric == "failed")
        assert p["precondition"]["is_success_skill"] == (metric == "closed")


# ── 关键: 三态都不被 guard veto(closed 是新成功路径) ──────────────────────────
def test_vet_passes_all_three_states():
    guard = SkillVetting(audit_log=None, domain="backend")
    for metric in ("closed", "routed_unclosed", "failed"):
        p = _payload(metric, -55.9, 1.36 if metric == "closed" else -10.0)
        vet = guard.vet_skill(p)
        assert vet.approved, f"{metric} 被 veto: {vet.veto_code} - {vet.reason}"


# ── 路径蒸馏 + 泛化/专有分字段位 ──────────────────────────────────────────────
def test_path_distillation_and_field_separation():
    p = _payload("closed", -55.9, 1.36)
    # 路径蒸馏: key_actions = 粗粒度序列, 无 cell 类型
    assert p["action_template"]["key_actions"] == ["vt_faster", "vt_faster"]
    # 泛化(可复用): repair_strategy 在 action_template
    assert p["action_template"]["repair_strategy"] == "blue_llm_eco_vtswap"
    # 专有 provenance(时序数值)在 failure_signature(precondition), 不进 validation 扫描面
    assert "final_wns" not in p["validation"]
    assert p["precondition"]["failure_signature"]["final_wns"] == 1.36
    # hollow-safe: 不写 wns_delta 键
    import json
    blob = json.dumps(p["action_template"]) + json.dumps(p["validation"])
    assert "wns_delta" not in blob


# ── 跨域信号(abstain)记在 backend skill, 非写 loopback ────────────────────────
def test_cross_domain_trigger_recorded_not_loopback():
    p = _payload("routed_unclosed", -325.9, -296.7)
    fs = p["precondition"]["failure_signature"]
    assert fs["cross_domain_trigger"] is True       # 后端发回退信号
    assert fs["abstain_reason"] == "需回前端 <num>"  # 端点诊断供阀门, 但不写 loopback
    # backend skill, 域内
    assert p["precondition"]["source"] == "blue_llm_trajectory_eco"
