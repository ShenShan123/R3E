import pytest
import inspect
from pathlib import Path
from types import SimpleNamespace
import microsurgeon_flow.orchestrator as orch
from microsurgeon_flow.orfs_reclose import parse_finish_worst_slack
from microsurgeon_frontend.semantic.llm_micro_repair import build_repair_prompt


def _mk_rr(success=True, total=191, proven=191):
    return SimpleNamespace(
        success=success, total=total, proven=proven,
        old_identifier="s_axil_rvalid", target_line=597,
        repaired_rtl_path="/tmp/repaired/i2c.v",
        patch={"file": "i2c.v", "line": 597,
               "old_identifier": "s_axil_rvalid", "new_identifier": "s_axil_read_pending"},
    )


@pytest.fixture
def fe_case(tmp_path):
    rtl = tmp_path / "dut.v"; rtl.write_text("module dut; endmodule\n")
    return {"design_name": "dut", "rtl_path": str(rtl),
            "case_spec": {"buggy_rtl": str(rtl)}, "rtl_rel_path": "rtl/dut.v"}


@pytest.fixture
def be_case(tmp_path):
    rtl = tmp_path / "dut.v"; rtl.write_text("module dut; endmodule\n")
    return {"design_name": "dut", "rtl_path": str(rtl),
            "odb": "x.odb", "sdc": "x.sdc", "period": 0.46,
            "case_spec": {"buggy_rtl": str(rtl)}, "rtl_rel_path": "rtl/dut.v"}


def test_frontend_success_distills(monkeypatch, tmp_path, fe_case):
    calls = {}
    monkeypatch.setattr(orch, "triage", lambda *a, **k: "frontend")
    monkeypatch.setattr(orch, "repair_one_case", lambda spec, wd: _mk_rr())

    def fake_distill(rr, **kw):
        calls["kw"] = kw
        return True, None, None

    monkeypatch.setattr(orch, "distill_frontend_skill", fake_distill)
    monkeypatch.setattr(orch, "run_eco_repair",
                        lambda *a, **k: pytest.fail("backend arm must not run"))

    r = orch.run_pipeline(fe_case, tmp_path / "w")
    assert r.domain == "frontend" and r.repair_outcome == "ok" and r.distilled
    assert r.skill_name == "frontend_undeclared_identifier_llm_single_site_fix_dut_L597"
    assert calls["kw"]["context_pattern"] == "design=dut,file=i2c.v,stage=yosys_synthesis"
    assert calls["kw"]["rtl_rel_path"] == "rtl/dut.v"
    assert calls["kw"]["case_id"] == "CASE-FRONTEND-DUT-L597"


def test_loopback_block_frontend_skill_name_uses_timing_structural(monkeypatch, tmp_path):
    calls = {}
    rr = SimpleNamespace(
        success=True, total=88, proven=88, unproven=0, asserted_ok=True,
        yosys_exit=0, equiv_log_path=str(tmp_path / "equiv.log"),
        old_identifier="sub$out", target_line=744,
        repaired_rtl_path=str(tmp_path / "gcd_serial_sub.v"),
        patch={"file": "gcd_serial_sub.v", "start_line": 744, "end_line": 779},
        repair_mode="block",
    )
    case = {
        "design_name": "gcd",
        "case_spec": {"buggy_rtl": "gcd_serial_sub.v"},
        "rtl_rel_path": "rtl/gcd_serial_sub.v",
        "_loopback_origin": True,
    }
    monkeypatch.setattr(orch, "repair_one_case", lambda spec, wd: rr)

    def fake_distill(repair_result, **kw):
        calls["kw"] = kw
        return True, None, None

    monkeypatch.setattr(orch, "distill_frontend_skill", fake_distill)

    r = orch._run_frontend_arm(case, tmp_path / "fe")
    assert r.skill_name == "frontend_timing_structural_llm_block_rewrite_gcd_L744"
    assert calls["kw"]["skill_name"] == r.skill_name


def test_frontend_equiv_failed_skips_distill(monkeypatch, tmp_path, fe_case):
    monkeypatch.setattr(orch, "triage", lambda *a, **k: "frontend")
    monkeypatch.setattr(orch, "repair_one_case",
                        lambda spec, wd: _mk_rr(success=False, proven=180, total=191))
    monkeypatch.setattr(orch, "distill_frontend_skill",
                        lambda *a, **k: pytest.fail("must not distill when equiv failed"))
    r = orch.run_pipeline(fe_case, tmp_path / "w")
    assert r.repair_outcome == "equiv_failed" and not r.distilled and r.skill_name is None


def test_frontend_propose_fail(monkeypatch, tmp_path, fe_case):
    def boom(spec, wd):
        raise RuntimeError("LLM no patch")

    monkeypatch.setattr(orch, "triage", lambda *a, **k: "frontend")
    monkeypatch.setattr(orch, "repair_one_case", boom)
    monkeypatch.setattr(orch, "distill_frontend_skill",
                        lambda *a, **k: pytest.fail("must not distill on propose fail"))
    r = orch.run_pipeline(fe_case, tmp_path / "w")
    assert r.repair_outcome == "frontend_propose_fail" and not r.distilled


def test_frontend_missing_case_spec_fail_fast(monkeypatch, tmp_path):
    monkeypatch.setattr(orch, "triage", lambda *a, **k: "frontend")
    bad = {"design_name": "dut", "rtl_path": "x.v"}
    with pytest.raises(ValueError, match="frontend arm missing"):
        orch.run_pipeline(bad, tmp_path / "w")


def test_backend_route_no_double_distill(monkeypatch, tmp_path, be_case):
    monkeypatch.setattr(orch, "triage", lambda *a, **k: "backend")
    monkeypatch.setattr(orch, "run_eco_repair",
                        lambda *a, **k: {"status": "routed_unclosed",
                                         "_distill_happened": True,
                                         "skill_name": "failed_sizing_plateau"})
    monkeypatch.setattr(orch, "repair_one_case",
                        lambda *a, **k: pytest.fail("frontend must not run in backend arm"))
    r = orch.run_pipeline(be_case, tmp_path / "w")
    assert r.domain == "backend" and r.distilled and r.skill_name == "failed_sizing_plateau"


def test_backend_missing_fields_fail_fast(monkeypatch, tmp_path):
    monkeypatch.setattr(orch, "triage", lambda *a, **k: "backend")
    with pytest.raises(ValueError, match="backend arm missing"):
        orch.run_pipeline({"design_name": "dut", "rtl_path": "x.v"}, tmp_path / "w")


def test_triage_env_fail(monkeypatch, tmp_path, be_case):
    def boom(*a, **k):
        raise RuntimeError("yosys missing")

    monkeypatch.setattr(orch, "triage", boom)
    r = orch.run_pipeline(be_case, tmp_path / "w")
    assert r.repair_outcome == "triage_env_fail" and r.domain == "?" and not r.distilled


# ── Step5a 回环骨架单测(纯 mock, 零真跑/零出网/零 ORFS) ─────────────────────


def _mk_be_result(
    status="routed_unclosed",
    distill_happened=False, skill_name=None,
    endpoints=None,
    failure_class=None,
):
    """Build a mock run_eco_repair return dict."""
    return {
        "status": status,
        "_distill_happened": distill_happened,
        "skill_name": skill_name,
        "failure_class": failure_class,
        "violating_endpoints": endpoints or [],
    }


def test_backend_no_rollback_keeps_forward(monkeypatch, tmp_path, be_case):
    """failure_class=None → 不在 _ROLLBACK_CLASSES, forward-only, domain=backend."""
    monkeypatch.setattr(orch, "triage", lambda *a, **k: "backend")
    monkeypatch.setattr(orch, "run_eco_repair",
                        lambda *a, **k: _mk_be_result())
    monkeypatch.setattr(orch, "distill_loopback_skill",
                        lambda *a, **k: pytest.fail("loopback must not run"))
    monkeypatch.setattr(orch, "_run_frontend_arm",
                        lambda *a, **k: pytest.fail("frontend arm must not run"))
    r = orch.run_pipeline(be_case, tmp_path / "w")
    assert r.domain == "backend"
    assert r.failure_class is None
    assert not r.loopback_distilled


def test_backend_failure_class_sizing_plateau_routes_to_loopback(monkeypatch, tmp_path, be_case):
    """failure_class=sizing_plateau ∈ _ROLLBACK_CLASSES → loopback."""
    monkeypatch.setattr(orch, "triage", lambda *a, **k: "backend")
    monkeypatch.setattr(orch, "run_eco_repair",
                        lambda *a, **k: _mk_be_result(
                            failure_class="sizing_plateau", distill_happened=True, skill_name="failed_sizing_plateau",
                            endpoints=[{"startpoint": "sp", "endpoint": "ep", "slack": -0.1}]))
    monkeypatch.setattr(orch, "_run_frontend_arm",
                        lambda *a, **k: orch.OrchestrationResult(
                            domain="frontend", repair_outcome="ok", distilled=True,
                            skill_name="frontend_repair_i2c_L597",
                            artifacts_root=str(tmp_path / "fe"), case_id="CASE-FE",
                            repaired_rtl_path=str(tmp_path / "repaired.v"),
                        ))
    monkeypatch.setattr(orch, "_reclose_pnr",
                        lambda *a, **k: (True, {"worst_slack": 0.01, "stage": SimpleNamespace(finish_rpt="rpt")}))
    monkeypatch.setattr(orch, "distill_loopback_skill",
                        lambda *a, **k: (True, None, None))

    r = orch.run_pipeline(be_case, tmp_path / "w")
    assert r.domain == "loopback"
    assert r.repair_outcome == "loopback_closed"
    assert r.loopback_distilled is True
    assert r.failure_class == "sizing_plateau"
    assert r.skill_name == "frontend_repair_i2c_L597"


def test_loopback_frontend_repair_fail_no_distill(monkeypatch, tmp_path, be_case):
    """前端 repair_outcome!="ok" → loopback_frontend_failed, 不蒸 loopback."""
    monkeypatch.setattr(orch, "triage", lambda *a, **k: "backend")
    monkeypatch.setattr(orch, "run_eco_repair",
                        lambda *a, **k: _mk_be_result(
                            failure_class="structural_bottleneck", distill_happened=True, skill_name="failed_structural_bottleneck"))
    monkeypatch.setattr(orch, "_run_frontend_arm",
                        lambda *a, **k: orch.OrchestrationResult(
                            domain="frontend", repair_outcome="equiv_failed", distilled=False,
                            skill_name=None, artifacts_root=str(tmp_path / "fe"),
                        ))
    monkeypatch.setattr(orch, "distill_loopback_skill",
                        lambda *a, **k: pytest.fail("loopback distill must not run"))
    r = orch.run_pipeline(be_case, tmp_path / "w")
    assert r.repair_outcome == "loopback_frontend_failed"
    assert not r.loopback_distilled


def test_loopback_reopen_unclosed_no_distill(monkeypatch, tmp_path, be_case):
    """真 PnR reclosed=False → loopback_reopen_unclosed, 不蒸."""
    monkeypatch.setattr(orch, "triage", lambda *a, **k: "backend")
    monkeypatch.setattr(orch, "run_eco_repair",
                        lambda *a, **k: _mk_be_result(
                            failure_class="tool_no_effect", distill_happened=True, skill_name="failed_tool_no_effect"))
    monkeypatch.setattr(orch, "_run_frontend_arm",
                        lambda *a, **k: orch.OrchestrationResult(
                            domain="frontend", repair_outcome="ok", distilled=True,
                            skill_name="frontend_repair_i2c_L597",
                            artifacts_root=str(tmp_path / "fe"), case_id="CASE-FE",
                            repaired_rtl_path=str(tmp_path / "repaired.v"),
                        ))
    monkeypatch.setattr(orch, "_reclose_pnr",
                        lambda *a, **k: (False, {"worst_slack": -0.01, "stage": SimpleNamespace(finish_rpt="rpt")}))
    monkeypatch.setattr(orch, "distill_loopback_skill",
                        lambda *a, **k: pytest.fail("loopback distill must not run"))
    r = orch.run_pipeline(be_case, tmp_path / "w")
    assert r.repair_outcome == "loopback_reopen_unclosed"
    assert not r.loopback_distilled
    assert r.reclose_worst_slack == -0.01


# ── 刀一: §4.3 failure_class 五类路由 ────────────────────────────────

def test_backend_failure_class_other_skips_rollback(monkeypatch, tmp_path, be_case):
    """"other" ∉ _ROLLBACK_CLASSES → forward-only, domain=backend."""
    monkeypatch.setattr(orch, "triage", lambda *a, **k: "backend")
    monkeypatch.setattr(orch, "run_eco_repair",
                        lambda *a, **k: _mk_be_result(failure_class="other"))
    monkeypatch.setattr(orch, "distill_loopback_skill",
                        lambda *a, **k: pytest.fail("loopback must not run"))
    monkeypatch.setattr(orch, "_run_frontend_arm",
                        lambda *a, **k: pytest.fail("frontend arm must not run"))
    r = orch.run_pipeline(be_case, tmp_path / "w")
    assert r.domain == "backend"
    assert r.failure_class == "other"


def test_backend_failure_class_none_skips_rollback(monkeypatch, tmp_path, be_case):
    """None ∉ _ROLLBACK_CLASSES → forward-only (fail-safe: 无失败/不可判)."""
    monkeypatch.setattr(orch, "triage", lambda *a, **k: "backend")
    monkeypatch.setattr(orch, "run_eco_repair",
                        lambda *a, **k: _mk_be_result(failure_class=None))
    monkeypatch.setattr(orch, "distill_loopback_skill",
                        lambda *a, **k: pytest.fail("loopback must not run"))
    monkeypatch.setattr(orch, "_run_frontend_arm",
                        lambda *a, **k: pytest.fail("frontend arm must not run"))
    r = orch.run_pipeline(be_case, tmp_path / "w")
    assert r.domain == "backend"
    assert r.failure_class is None


def test_backend_failure_class_garbage_skips_rollback(monkeypatch, tmp_path, be_case):
    """未知 failure_class ∉ _ROLLBACK_CLASSES → forward-only (fail-safe)."""
    monkeypatch.setattr(orch, "triage", lambda *a, **k: "backend")
    monkeypatch.setattr(orch, "run_eco_repair",
                        lambda *a, **k: _mk_be_result(failure_class="garbage"))
    monkeypatch.setattr(orch, "distill_loopback_skill",
                        lambda *a, **k: pytest.fail("loopback must not run"))
    monkeypatch.setattr(orch, "_run_frontend_arm",
                        lambda *a, **k: pytest.fail("frontend arm must not run"))
    r = orch.run_pipeline(be_case, tmp_path / "w")
    assert r.domain == "backend"
    assert r.failure_class == "garbage"


def test_backend_failure_class_tool_no_effect_rolls_back(monkeypatch, tmp_path, be_case):
    """"tool_no_effect" ∈ _ROLLBACK_CLASSES → loopback."""
    calls = []
    monkeypatch.setattr(orch, "triage", lambda *a, **k: "backend")
    monkeypatch.setattr(orch, "run_eco_repair",
                        lambda *a, **k: _mk_be_result(failure_class="tool_no_effect"))
    monkeypatch.setattr(orch, "_run_frontend_arm",
                        lambda *a, **k: orch.OrchestrationResult(
                            domain="frontend", repair_outcome="ok", distilled=True,
                            skill_name="fe_skill", artifacts_root=str(tmp_path / "fe"),
                            repaired_rtl_path=str(tmp_path / "repaired.v"),
                            failure_class=None,
                        ))
    monkeypatch.setattr(orch, "_reclose_pnr",
                        lambda *a, **k: (True, {"worst_slack": 0.0, "stage": SimpleNamespace(finish_rpt="rpt")}))
    monkeypatch.setattr(orch, "distill_loopback_skill",
                        lambda *a, **k: (True, None, None))
    r = orch.run_pipeline(be_case, tmp_path / "w")
    assert r.domain == "loopback"
    assert r.failure_class == "tool_no_effect"


def test_backend_failure_class_structural_bottleneck_rolls_back(monkeypatch, tmp_path, be_case):
    """"structural_bottleneck" ∈ _ROLLBACK_CLASSES → loopback."""
    monkeypatch.setattr(orch, "triage", lambda *a, **k: "backend")
    monkeypatch.setattr(orch, "run_eco_repair",
                        lambda *a, **k: _mk_be_result(failure_class="structural_bottleneck"))
    monkeypatch.setattr(orch, "_run_frontend_arm",
                        lambda *a, **k: orch.OrchestrationResult(
                            domain="frontend", repair_outcome="ok", distilled=True,
                            skill_name="fe_skill", artifacts_root=str(tmp_path / "fe"),
                            repaired_rtl_path=str(tmp_path / "repaired.v"),
                        ))
    monkeypatch.setattr(orch, "_reclose_pnr",
                        lambda *a, **k: (True, {"worst_slack": 0.0, "stage": SimpleNamespace(finish_rpt="rpt")}))
    monkeypatch.setattr(orch, "distill_loopback_skill",
                        lambda *a, **k: (True, None, None))
    r = orch.run_pipeline(be_case, tmp_path / "w")
    assert r.domain == "loopback"
    assert r.failure_class == "structural_bottleneck"


def test_backend_failure_class_overconstraint_infeasible_rolls_back(monkeypatch, tmp_path, be_case):
    """"overconstraint_infeasible" ∈ _ROLLBACK_CLASSES → loopback."""
    monkeypatch.setattr(orch, "triage", lambda *a, **k: "backend")
    monkeypatch.setattr(orch, "run_eco_repair",
                        lambda *a, **k: _mk_be_result(failure_class="overconstraint_infeasible"))
    monkeypatch.setattr(orch, "_run_frontend_arm",
                        lambda *a, **k: orch.OrchestrationResult(
                            domain="frontend", repair_outcome="ok", distilled=True,
                            skill_name="fe_skill", artifacts_root=str(tmp_path / "fe"),
                            repaired_rtl_path=str(tmp_path / "repaired.v"),
                        ))
    monkeypatch.setattr(orch, "_reclose_pnr",
                        lambda *a, **k: (True, {"worst_slack": 0.0, "stage": SimpleNamespace(finish_rpt="rpt")}))
    monkeypatch.setattr(orch, "distill_loopback_skill",
                        lambda *a, **k: (True, None, None))
    r = orch.run_pipeline(be_case, tmp_path / "w")
    assert r.domain == "loopback"
    assert r.failure_class == "overconstraint_infeasible"


# ── Step5a 回环骨架单测(续) ──────────────────────────────────────────

def test_loopback_payload_schema_redlines(monkeypatch, tmp_path):
    """_distill_loopback payload: is_loopback_skill True / 无空键 / skill_name 不带 design."""
    payloads = []

    def capture(*, case_id, violating_endpoints, frontend_skill_name, backend_failure_class):
        payloads.append(dict(case_id=case_id, violating_endpoints=violating_endpoints,
                             frontend_skill_name=frontend_skill_name, backend_failure_class=backend_failure_class))
        return True, None, None

    monkeypatch.setattr(orch, "distill_loopback_skill", capture)
    monkeypatch.setattr(orch, "triage", lambda *a, **k: "backend")
    monkeypatch.setattr(orch, "run_eco_repair",
                        lambda *a, **k: _mk_be_result(
                            failure_class="overconstraint_infeasible", distill_happened=True, skill_name="failed_overconstraint_infeasible",
                            endpoints=[{"startpoint": "sp", "endpoint": "ep", "slack": -0.1}]))
    monkeypatch.setattr(orch, "_run_frontend_arm",
                        lambda *a, **k: orch.OrchestrationResult(
                            domain="frontend", repair_outcome="ok", distilled=True,
                            skill_name="frontend_repair_i2c_L597",
                            artifacts_root=str(tmp_path / "fe"), case_id="CASE-FE",
                            repaired_rtl_path=str(tmp_path / "repaired.v"),
                        ))
    monkeypatch.setattr(orch, "_reclose_pnr",
                        lambda *a, **k: (True, {"worst_slack": 0.0, "stage": SimpleNamespace(finish_rpt="rpt")}))

    orch.run_pipeline({
        "design_name": "dut", "rtl_path": str(tmp_path / "x.v"),
        "odb": "x.odb", "sdc": "x.sdc", "period": 0.46,
        "case_spec": {"buggy_rtl": "x.v"}, "rtl_rel_path": "rtl/x.v",
    }, tmp_path / "w")

    assert len(payloads) == 1, f"expected 1 distill call, got {len(payloads)}"
    p = payloads[0]
    # is_loopback_skill 在 distill_loopback_skill 内部硬编码, 这里验 caller 传参正确性:
    assert p["backend_failure_class"] == "overconstraint_infeasible"  # 刀一: 真 failure_class 透传
    assert p["violating_endpoints"] == [{"startpoint": "sp", "endpoint": "ep", "slack": -0.1}]
    assert p["frontend_skill_name"] == "frontend_repair_i2c_L597"


def test_backend_violation_case_spec_def_use_context(tmp_path):
    rtl = tmp_path / "gcd.v"
    lines = ["module gcd;"]
    lines += [f"  wire filler_{i};" for i in range(2, 385)]
    lines += [
        "  wire   [15:0] sub_out;",
        "  wire   [15:0] b_reg_out;",
        "",
        "  // === SALT-CANCELLATION CHAIN (injected) ===",
        "  // sub_out + SALT - SALT = sub_out (functionally neutral)",
        "  // Forces a carry chain through the adder then subtractor.",
        "  // keep attribute prevents ABC from optimizing away the chain.",
        "  localparam [15:0] SALT_CONST = 16'hA5A5;",
        "  (* keep = 1 *) wire [15:0] sub_salted;",
        "  (* keep = 1 *) wire [15:0] sub_desalted;",
        "  assign sub_salted   = sub_out + SALT_CONST;",
        "  assign sub_desalted = sub_salted - SALT_CONST;",
    ]
    lines += [f"  wire gap_{i};" for i in range(len(lines) + 1, 523)]
    lines += ["  assign a_mux$in_$001 = sub_desalted;"]
    lines += [f"  wire tail_{i};" for i in range(len(lines) + 1, 531)]
    lines += ["endmodule"]
    rtl.write_text("\n".join(lines) + "\n")
    rpt = tmp_path / "route.rpt"
    rpt.write_text("Endpoint: resp_msg[15]\n  ^ sub_desalted[15]\n")

    spec = orch.build_case_spec_from_backend_violation({
        "rtl_path": str(rtl),
        "route_report": str(rpt),
        "violation_endpoint": "resp_msg[15]",
        "violating_signal": "sub_desalted",
        "golden_sources": [tmp_path / "gold.v"],
        "deps": [],
        "top_module": "gcd",
    })

    assert spec["target_line"] == 523
    assert spec["old_identifier"] == "sub_desalted"
    assert spec["context_ranges"] == [(385, 400), (510, 530)]
    assert spec["backend_violation"]["endpoint"] == "resp_msg[15]"
    assert "golden_sources" in spec and spec["golden_sources"] == [str(tmp_path / "gold.v")]


def test_loopback_case_spec_prefers_backend_def_use_even_if_preseeded(tmp_path):
    rtl = tmp_path / "dut.v"
    rtl.write_text(
        "\n".join([
            "module dut;",
            "  wire sub_out;",
            "  wire sub_desalted;",
            "  assign sub_desalted = sub_out;",
            "  assign sink = sub_desalted;",
            "endmodule",
        ]) + "\n"
    )
    rpt = tmp_path / "route.rpt"
    rpt.write_text("Endpoint: sink\nsub_desalted\n")

    loopback_case = orch._case_for_loopback_frontend({
        "rtl_path": str(rtl),
        "route_report": str(rpt),
        "violation_endpoint": "sink",
        "violating_signal": "sub_desalted",
        "golden_sources": [tmp_path / "gold.v"],
        "deps": [],
        "top_module": "dut",
        "case_spec": {
            "buggy_rtl": "wrong.v",
            "target_line": 999,
            "old_identifier": "answer_line_only",
            "context_ranges": [(4, 4)],
        },
    })

    assert loopback_case["case_spec"]["buggy_rtl"] == str(rtl)
    assert loopback_case["case_spec"]["target_line"] == 5
    assert loopback_case["case_spec"]["old_identifier"] == "sub_desalted"
    assert loopback_case["case_spec"]["context_ranges"] != [(4, 4)]
    assert loopback_case["_loopback_origin"] is True


def test_backend_violation_block_case_spec_frames_instance_module_body(tmp_path):
    rtl = tmp_path / "gcd_serial_sub.v"
    lines = [
        "module top;",
        "  wire [15:0] sub$out;",
        "  wire [15:0] a_reg$out;",
        "  wire [15:0] b_reg$out;",
        "  Subtractor_0x1 sub",
        "  (",
        "    .in0 ( a_reg$out ),",
        "    .in1 ( b_reg$out ),",
        "    .out ( sub$out )",
        "  );",
        "  assign resp_msg = sub$out;",
        "endmodule",
        "module Subtractor_0x1",
        "(",
        "  input  wire [15:0] in0,",
        "  input  wire [15:0] in1,",
        "  output wire [15:0] out",
        ");",
        "",
        "  wire [16:0] n;",
        "  wire [15:0] y;",
        "  assign n[0] = 1'b0;",
        "  assign y[0] = in0[0] ^ in1[0] ^ n[0];",
        "  assign n[1] = (~in0[0] & in1[0]) | ((~in0[0] | in1[0]) & n[0]);",
        "  assign out = y;",
        "",
        "endmodule",
    ]
    rtl.write_text("\n".join(lines) + "\n")
    rpt = tmp_path / "route.rpt"
    rpt.write_text("Endpoint: resp_msg[15]\nviolating signal sub$out\n")

    spec = orch.build_case_spec_from_backend_violation({
        "rtl_path": str(rtl),
        "route_report": str(rpt),
        "violation_endpoint": "resp_msg[15]",
        "violating_signal": "sub$out",
        "golden_sources": [tmp_path / "gold.v"],
        "deps": [],
        "top_module": "top",
        "repair_mode": "block",
    })

    assert spec["repair_mode"] == "block"
    assert spec["block_bounds"] == [20, 25]
    assert spec["context_ranges"] == [(20, 25)]
    assert spec["target_line"] == 20
    assert spec["old_identifier"] == "sub$out"


def test_build_repair_prompt_has_no_kwargs_guard():
    params = inspect.signature(build_repair_prompt).parameters.values()
    assert all(p.kind is not inspect.Parameter.VAR_KEYWORD for p in params)


def test_reclose_closed_reads_sta_not_returncode(monkeypatch, tmp_path, be_case):
    repaired = tmp_path / "repaired.v"
    repaired.write_text("module dut; endmodule\n")
    rpt = tmp_path / "finish.rpt"
    rpt.write_text("wns 0.00\n")
    calls = {}

    class FakeAdapter:
        def __init__(self, *, flow_root, timeout_sec):
            calls["flow_root"] = flow_root
            calls["timeout_sec"] = timeout_sec

        def run_full_flow(self, **kwargs):
            calls["kwargs"] = kwargs
            return SimpleNamespace(success=False, returncode=2, finish_rpt=str(rpt))

    monkeypatch.setattr(orch, "ORFSRecloseAdapter", FakeAdapter)
    ok, meta = orch._reclose_pnr(
        {**be_case, "platform": "nangate45", "flow_root": "/path/to/OpenROAD-flow-scripts"},
        orch.OrchestrationResult(
            domain="frontend", repair_outcome="ok", distilled=True, skill_name="fe",
            artifacts_root=str(tmp_path), repaired_rtl_path=str(repaired),
        ),
        tmp_path / "w",
    )

    assert ok is True
    assert meta["worst_slack"] == 0.0
    assert calls["flow_root"] == Path("/path/to/OpenROAD-flow-scripts")
    assert calls["kwargs"]["verilog_files"] == [repaired]


def test_parse_finish_worst_slack_prefers_worst_slack_and_missing_is_none(tmp_path):
    rpt = tmp_path / "6_finish.rpt"
    rpt.write_text("finish report_wns\nwns 0.00\nfinish report_worst_slack\nworst slack 0.03\n")
    assert parse_finish_worst_slack(rpt) == 0.03

    missing_line = tmp_path / "no_slack.rpt"
    missing_line.write_text("finish report_wns\n")
    assert parse_finish_worst_slack(missing_line) is None
    assert parse_finish_worst_slack(tmp_path / "missing.rpt") is None


def test_gcd_062_sdc_is_materialized():
    sdc = Path("benchmarks/gcd/gcd_p062.sdc")
    text = sdc.read_text()
    assert "set clk_period 0.62" in text
    assert "create_clock -name $clk_name -period $clk_period $clk_port" in text
