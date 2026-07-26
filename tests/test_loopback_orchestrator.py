"""刀 B1 回环骨架控制流测(纯 mock, 无真跑/无库写): 阀门 + 前端/reclose/distill 各分支 + rework 接线."""
import pytest

from microsurgeon_flow.loopback_orchestrator import (
    BackendOutcome,
    build_structural_case_spec,
    map_trajectory_to_failure_class,
    run_loopback_closure,
)
from microsurgeon_flow.trajectory_agent import (
    PathEvidence,
    TrajectoryResult,
    TrajectoryStep,
)

# ── 固定件: 注入用 mock + canned rework(避 LLM) ──────────────────────────────
_REWORK = {
    "problem": {"startpoint": "dpath.b_reg$_DFF_", "endpoint": "dpath.a_reg$_DFF_",
                "module": "dpath", "residual_gap_ps": -20, "comb_depth": 18},
    "backend_attempt": {"tried": ["vt_swap"], "improved_ps": 30, "exhausted_reason": "vt_headroom_exhausted"},
    "recommended_fix": {"action": "retime", "rationale": "r", "hint": "h"},
}
_CASE = {"rtl_path": "/x/gcd.v", "top_module": "gcd", "deps": [], "golden_sources": []}


def _mock_rework(evidence, **kw):
    return _REWORK


def _backend(fc="structural_bottleneck"):
    return BackendOutcome(failure_class=fc, evidence=PathEvidence(
        cell_chain=[{"master": "DFF_X1"}], startpoint="dpath.b_reg$_DFF_",
        endpoint="dpath.a_reg$_DFF_", slack=-50.0),
        tried=["vt_swap"], improved_ps=30, residual_gap_ps=-20,
        exhausted_reason="vt_headroom_exhausted", case_id="gcd_t")


def _run(frontend_fn, reclose_fn, distill_fn, fc="structural_bottleneck"):
    calls = {"distill": 0}
    def _distill(**kw):
        calls["distill"] += 1
        return distill_fn(**kw)
    res = run_loopback_closure(
        _backend(fc), _CASE, frontend_fn=frontend_fn, reclose_fn=reclose_fn,
        distill_loopback_fn=_distill, build_rework_fn=_mock_rework)
    return res, calls


# ── 阀门: 非 rollback 类 → forward_only, 不进回环 ────────────────────────────
@pytest.mark.parametrize("fc", [None, "other", "no_action"])
def test_valve_non_rollback_forward_only(fc):
    res, calls = _run(lambda s: pytest.fail("不该调前端"),
                      lambda p: pytest.fail("不该调reclose"),
                      lambda **k: True, fc=fc)
    assert res.outcome == "forward_only" and res.failure_class == fc
    assert calls["distill"] == 0


# ── 全绿路径: abstain结构 → 前端ok → reclose闭 → 蒸 loopback ─────────────────
def test_happy_path_loopback_closed():
    res, calls = _run(
        frontend_fn=lambda s: {"outcome": "ok", "repaired_rtl_path": "/x/gcd_fixed.v", "skill_name": "fe1"},
        reclose_fn=lambda p: (True, {"worst_slack": 5.5}),
        distill_fn=lambda **k: True)
    assert res.outcome == "loopback_closed"
    assert res.loopback_distilled is True and calls["distill"] == 1
    assert res.reclose_worst_slack == 5.5
    assert res.repaired_rtl_path == "/x/gcd_fixed.v"


# ── 前端 formal 未过 → 不蒸 ──────────────────────────────────────────────────
def test_frontend_failed_no_distill():
    res, calls = _run(
        frontend_fn=lambda s: {"outcome": "equiv_failed"},
        reclose_fn=lambda p: pytest.fail("前端未过不该 reclose"),
        distill_fn=lambda **k: True)
    assert res.outcome == "loopback_frontend_failed"
    assert res.frontend_outcome == "equiv_failed" and calls["distill"] == 0


# ── 前端过但 reclose 未闭 → 不蒸 ─────────────────────────────────────────────
def test_reclose_unclosed_no_distill():
    res, calls = _run(
        frontend_fn=lambda s: {"outcome": "ok", "repaired_rtl_path": "/x/gcd_fixed.v"},
        reclose_fn=lambda p: (False, {"worst_slack": -3.2}),
        distill_fn=lambda **k: True)
    assert res.outcome == "loopback_reopen_unclosed"
    assert res.reclose_worst_slack == -3.2 and calls["distill"] == 0


# ── 接线: rework_request 真流进 case_spec(结构修复版) ────────────────────────
def test_rework_flows_into_case_spec():
    captured = {}
    def fe(spec):
        captured.update(spec)
        return {"outcome": "ok", "repaired_rtl_path": "/x/f.v"}
    run_loopback_closure(_backend(), _CASE, frontend_fn=fe,
                         reclose_fn=lambda p: (True, {"worst_slack": 1.0}),
                         distill_loopback_fn=lambda **k: True, build_rework_fn=_mock_rework)
    assert captured["repair_mode"] == "structural_timing"
    assert captured["rework_request"] is _REWORK
    assert captured["target_module"] == "dpath"
    assert captured["endpoints"] == ["dpath.b_reg$_DFF_", "dpath.a_reg$_DFF_"]


def test_build_structural_case_spec_shape():
    spec = build_structural_case_spec(_CASE, _REWORK)
    assert spec["repair_mode"] == "structural_timing"
    assert spec["buggy_rtl"] == "/x/gcd.v" and spec["top_module"] == "gcd"
    assert spec["rework_request"]["recommended_fix"]["action"] == "retime"


# ── map_trajectory_to_failure_class: 各 outcome 映射 ─────────────────────────
def _traj(outcome, abstain_reason=None, wns=(-50.0, -50.0)):
    steps = [TrajectoryStep(step=i, candidates=[], eval_wns=[w], selected_idx=0,
                            select_reason="", wns_after=w)
             for i, w in enumerate(wns[1:], 1)]
    return TrajectoryResult(outcome=outcome, steps=steps, final_wns=wns[-1],
                            initial_wns=wns[0], abstain_reason=abstain_reason,
                            action_class_sequence=["vt_swap"] * len(steps))


def test_map_closed_none():
    assert map_trajectory_to_failure_class(_traj("closed")) is None


def test_map_abstain_structural():
    assert map_trajectory_to_failure_class(
        _traj("abstain", abstain_reason="需前端架构级优化")) == "structural_bottleneck"


def test_map_abstain_llm_error_other():
    assert map_trajectory_to_failure_class(
        _traj("abstain", abstain_reason="llm_error: timeout")) == "other"


def test_map_routed_unclosed_heuristic():
    # WNS 几乎零波动 → tool_no_effect(∈_ROLLBACK_CLASSES)
    fc = map_trajectory_to_failure_class(_traj("exhausted", wns=(-50.0, -50.0, -50.0)))
    assert fc in {"tool_no_effect", "sizing_plateau", "other", "no_action"}


# ── B3a: 真 adapter 工厂(用 mock 底层件验翻译正确) ──────────────────────────────
from types import SimpleNamespace
from microsurgeon_flow.loopback_orchestrator import (
    make_frontend_fn, make_reclose_fn, make_distill_loopback_fn,
)


def test_make_frontend_fn_success_and_fail(tmp_path):
    ok = make_frontend_fn(tmp_path, repair_fn=lambda spec, wd: SimpleNamespace(
        success=True, repaired_rtl_path="/x/fixed.v"))
    assert ok({}) == {"outcome": "ok", "repaired_rtl_path": "/x/fixed.v",
                      "skill_name": "frontend_structural_timing"}
    bad = make_frontend_fn(tmp_path, repair_fn=lambda spec, wd: SimpleNamespace(
        success=False, repaired_rtl_path="/x/bad.v", proven=3, total=10))
    r = bad({})
    assert r["outcome"] == "equiv_failed" and r["proven"] == 3


def test_make_reclose_fn_closed_and_unclosed(tmp_path):
    adapter = SimpleNamespace(run_full_flow=lambda **kw: SimpleNamespace(finish_rpt="/x/finish.rpt"))
    case = {"design_name": "gcd", "sdc": "/x/g.sdc"}
    closed = make_reclose_fn(case, tmp_path, adapter=adapter, parse_slack_fn=lambda p: 0.5)
    ok, meta = closed("/x/fixed.v")
    assert ok is True and meta["worst_slack"] == 0.5
    uncl = make_reclose_fn(case, tmp_path, adapter=adapter, parse_slack_fn=lambda p: -1.2)
    ok2, meta2 = uncl("/x/fixed.v")
    assert ok2 is False and meta2["worst_slack"] == -1.2


def test_make_distill_loopback_fn_passes_args():
    captured = {}
    def fake(**kw):
        captured.update(kw)
        return True, None, None
    fn = make_distill_loopback_fn(distill_skill_fn=fake)
    got = fn(backend=_backend(), frontend={"skill_name": "fe_struct"}, rework=_REWORK)
    assert got is True
    assert captured["frontend_skill_name"] == "fe_struct"
    assert captured["backend_failure_class"] == "structural_bottleneck"
    assert captured["case_id"] == "gcd_t"


def test_b3a_endtoend_di_with_real_factories(tmp_path):
    """3 真工厂(全喂 mock 底层) 串进 run_loopback_closure → loopback_closed."""
    fe = make_frontend_fn(tmp_path, repair_fn=lambda spec, wd: SimpleNamespace(
        success=True, repaired_rtl_path="/x/fixed.v"))
    adapter = SimpleNamespace(run_full_flow=lambda **kw: SimpleNamespace(finish_rpt="/x/f.rpt"))
    rc = make_reclose_fn({"design_name": "gcd", "sdc": "/x/g.sdc"}, tmp_path,
                         adapter=adapter, parse_slack_fn=lambda p: 0.1)
    dl = make_distill_loopback_fn(distill_skill_fn=lambda **kw: (True, None, None))
    res = run_loopback_closure(_backend(), _CASE, frontend_fn=fe, reclose_fn=rc,
                              distill_loopback_fn=dl, build_rework_fn=_mock_rework)
    assert res.outcome == "loopback_closed" and res.loopback_distilled is True
    assert res.reclose_worst_slack == 0.1


# ── P2 感知层: 违例端点 → block_bounds 自动框选(模块体) ──────────────────────────

def test_derive_structural_block_bounds_module_body(tmp_path):
    from microsurgeon_flow.loopback_orchestrator import derive_structural_block_bounds
    rtl = tmp_path / "m.v"
    rtl.write_text(
        "module m (\n"          # 1
        "  input clk,\n"        # 2
        "  output reg y\n"      # 3
        ");\n"                  # 4  port list end
        "  reg a;\n"            # 5  body start
        "  always @(posedge clk) a <= clk;\n"  # 6
        "  always @(posedge clk) y <= a;\n"    # 7  body end
        "endmodule\n"          # 8
    )
    assert derive_structural_block_bounds(rtl, "m", "a") == (5, 7)


def test_derive_structural_block_bounds_hierarchical_endpoint(tmp_path):
    # 层级端点 "sub.reg" → 取模块前缀 sub(不是 top)
    from microsurgeon_flow.loopback_orchestrator import derive_structural_block_bounds
    rtl = tmp_path / "h.v"
    rtl.write_text(
        "module top (input clk);\n  sub s(clk);\nendmodule\n"   # 1-3
        "module sub (\n  input clk\n);\n  reg z;\n"             # 4-7
        "  always @(posedge clk) z <= clk;\n"                   # 8 body end
        "endmodule\n"                                           # 9
    )
    assert derive_structural_block_bounds(rtl, "top", "sub.z") == (7, 8)


def test_build_structural_case_spec_autoderives_block_bounds(tmp_path):
    from microsurgeon_flow.loopback_orchestrator import build_structural_case_spec
    rtl = tmp_path / "m.v"
    rtl.write_text("module m (\n  input clk\n);\n  reg a;\n  always @(posedge clk) a<=clk;\nendmodule\n")
    rework = {"problem": {"startpoint": "x", "endpoint": "a", "module": "m"},
              "recommended_fix": {"action": "retime"}}
    case = {"rtl_path": str(rtl), "top_module": "m", "golden_sources": [str(rtl)], "deps": []}
    spec = build_structural_case_spec(case, rework)
    assert spec["block_bounds"] == [4, 5]          # 感知自动补(body)
    assert spec["equiv_method"] == "seq_miter"      # retime → seq_miter
