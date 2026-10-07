from dataclasses import replace
import json
import shutil

import pytest

from r3e.loop.budget import BudgetedClient, CallBudgetExceeded
from r3e.loop.carriers import Port, generate_testbench, load_carrier_manifest, DEFAULT_CARRIER_MANIFEST
from r3e.loop.corpus import Carrier
from r3e.loop.env import build_client
from r3e.loop.red_design import (
    EditBudget, RedDesignGenerator, RedDesignInputError,
    RedDesignOutputViolation, validate_design,
)
from r3e.loop.sim import Simulator


CLEAN = """module TopModule(input clk, input rst_n, input req, input ack,
                 output busy, output done);
  reg [1:0] state;
  assign busy = state != 2'd0;
  assign done = state == 2'd2;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) state <= 2'd0;
    else begin
      case (state)
        2'd0: if (req) state <= 2'd1;
        2'd1: state <= 2'd2;
        2'd2: if (ack) state <= 2'd0;
        default: state <= 2'd0;
      endcase
    end
  end
endmodule
"""
MUTANT = CLEAN.replace("        2'd2: if (ack) state <= 2'd0;\n", "").replace(
    "assign busy = state != 2'd0;", "assign busy = state == 2'd1;")
SPEC = ("TopModule uses an active-low asynchronous reset to state 0. "
        "At rising clock edges, a request moves state 0 to 1, state 1 moves to 2, "
        "and acknowledgment moves state 2 back to 0. busy means nonzero state; "
        "done means state 2.")
WEAKNESS = {"weakness_id": "W_missing_transition", "mechanism": "misses a missing state transition",
            "causal_chain": [{"failure": "never returns to idle", "attempt": "changes output decode",
                              "why_failed": "transition remains absent"}],
            "mastery_status": "not_mastered", "designs_used": ["another_fsm"]}
CONTEXT = {"mechanism_coverage": {"missing state transition": {"tested": 3, "failed": 2}}}
CARRIER = Carrier("C_test", "test_fsm", CLEAN, "TopModule", (), spec=SPEC)


def output(rtl=MUTANT):
    return {"buggy_rtl": rtl, "target_weakness_id": WEAKNESS["weakness_id"],
            "hypothesis": "Blue may change output decoding but miss the removed return transition.",
            "expected_symptom": "After acknowledgment, done remains asserted.",
            "change_summary": "Remove the return transition and reconnect busy decoding.",
            "spec_basis": "Acknowledgment must move state 2 back to 0."}


def edit_output():
    return {**{k: v for k, v in output().items() if k not in {"buggy_rtl", "target_weakness_id"}},
            "edits": [{"find": "        2'd2: if (ack) state <= 2'd0;\n", "replace": ""},
                      {"find": "assign busy = state != 2'd0;", "replace": "assign busy = state == 2'd1;"}]}


def generator(raw=None, *, cap=1, failure=None):
    requests = []

    def transport(**request):
        requests.append(request)
        if failure:
            raise failure
        return {"content": json.dumps(output() if raw is None else raw),
                "input_tokens": 17, "output_tokens": 29, "provider_request_id": "test-local"}

    client = BudgetedClient(build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=transport), max_calls=cap)
    return RedDesignGenerator(client), requests


def test_freeform_structural_generation_carries_public_context_and_receipt():
    red, requests = generator()
    proposed = red.generate(CARRIER, target_weakness=WEAKNESS, weakness_context=CONTEXT, seed=7)
    user = json.loads(requests[0]["messages"][-1]["content"])
    assert user["clean_rtl"] == CLEAN and user["specification"] == SPEC
    assert user["target_weakness"] == WEAKNESS and user["weakness_context"] == CONTEXT
    assert "sites" not in user and "operator_catalog" not in user
    assert proposed["status"] == "proposal_only" and proposed["buggy_rtl"] == MUTANT
    assert proposed["edit_budget"]["body"]["within_limit"]
    assert proposed["receipt"]["request_hash"] and proposed["receipt"]["input_hash"]
    assert proposed["receipt"]["input_tokens"] == 17
    assert red.client.report()["by_phase"]["red"]["calls"] == 1
    assert red.client.phase == "blue_inference"
    assert "reward" not in proposed and "admitted" not in proposed


@pytest.mark.skipif(not all(shutil.which(t) for t in ("iverilog", "vvp")), reason="Icarus unavailable")
def test_generated_structural_bug_compiles_and_fails_visible_simulation(tmp_path):
    red, _ = generator()
    proposed = red.generate(CARRIER, target_weakness=WEAKNESS, direction="same")
    ports = [Port(n, "input", 1) for n in ("clk", "rst_n", "req", "ack")]
    ports += [Port(n, "output", 1) for n in ("busy", "done")]
    tb = tmp_path / "tb.sv"
    tb.write_text(generate_testbench(top="TopModule", ports=ports, clock="clk", reset="rst_n",
                                    seed=11, cycles=64, output_name="trace_visible.txt"))
    carrier = replace(CARRIER, visible_tb=(tb,))
    sim = Simulator(tmp_path / "sim")
    assert sim.verdict(CLEAN, carrier).visible_ok
    verdict = sim.verdict(proposed["buggy_rtl"], carrier)
    assert verdict.feedback.compile_ok and verdict.tier == "visible_fail"


def test_budget_cannot_be_inflated_by_comments_or_formatting():
    normal = validate_design(CLEAN, MUTANT, "TopModule")
    expanded = "\n\n// padding\n" * 100 + MUTANT.replace(";", ";\n\n")
    assert validate_design(CLEAN, expanded, "TopModule") == normal
    with pytest.raises(RedDesignOutputViolation, match="no_token_change"):
        validate_design(CLEAN, "// only formatting\n" + CLEAN.replace(";", ";\n"), "TopModule")


def test_body_budget_prevents_interface_padding_from_hiding_destruction():
    ports = ", ".join(f"input [127:0] a{i}" for i in range(100))
    clean = f"module TopModule({ports}, output [127:0] y);\nassign y = a0 + a1 + a2 + a3 + a4;\nendmodule"
    mutated = clean.replace("a0 + a1 + a2 + a3 + a4", "0")
    with pytest.raises(RedDesignOutputViolation, match="edit_budget_exceeded") as err:
        validate_design(clean, mutated, "TopModule")
    assert err.value.details["all_code"]["within_limit"]
    assert not err.value.details["body"]["within_limit"]


def test_whole_module_deletion_and_body_replacement_rejected():
    with pytest.raises(RedDesignOutputViolation):
        validate_design(CLEAN, "", "TopModule")
    shell = CLEAN[:CLEAN.index("  reg")] + "assign busy=0; assign done=0; endmodule\n"
    with pytest.raises(RedDesignOutputViolation, match="edit_budget_exceeded"):
        validate_design(CLEAN, shell, "TopModule")
    # Other modules must not mask an entirely erased top body.
    helper = "module helper(input a, output y); assign y=a; endmodule\n"
    empty_top = CLEAN[:CLEAN.index("  reg")] + "endmodule\n"
    with pytest.raises(RedDesignOutputViolation, match="empty_top_module_body"):
        validate_design(CLEAN + helper, empty_top + helper, "TopModule")


def test_insertions_cost_original_budget_and_limit_is_configurable():
    extra = MUTANT.replace("endmodule", "wire " + ",".join(f"padding{i}" for i in range(200)) + "; endmodule")
    with pytest.raises(RedDesignOutputViolation, match="edit_budget_exceeded"):
        validate_design(CLEAN, extra, "TopModule")
    with pytest.raises(RedDesignOutputViolation, match="edit_budget_exceeded"):
        validate_design(CLEAN, MUTANT, "TopModule", EditBudget(0.01))
    assert validate_design(CLEAN, MUTANT, "TopModule", EditBudget(0.5))["body"]["within_limit"]


@pytest.mark.parametrize("fraction", [0, 1, -0.1, 1.1, float("nan"), float("inf"), True, "0.4"])
def test_invalid_budget_rejected(fraction):
    with pytest.raises(ValueError):
        EditBudget(fraction)


@pytest.mark.parametrize("mutant,reason", [
    (MUTANT.replace("output busy", "output [7:0] busy"), "top_interface_changed"),
    (MUTANT.replace("TopModule", "AnotherModule"), "missing_top_module"),
    ("`timescale 10ns/1ps\n" + MUTANT, "compiler_directives_changed"),
    (CLEAN, "no_token_change"),
])
def test_invalid_design_rejected_with_billed_receipt(mutant, reason):
    red, requests = generator(output(mutant))
    with pytest.raises(RedDesignOutputViolation, match=reason) as err:
        red.generate(CARRIER, target_weakness=WEAKNESS)
    assert err.value.receipt["output_tokens"] == 29
    assert len(requests) == red.client.total_calls == 1


def test_non_ansi_ports_and_directive_operands_are_protected():
    clean = 'module TopModule(a,y); input [7:0] a; output [7:0] y; assign y=a+1; endmodule'
    mutant = clean.replace('input [7:0]', 'input [6:0]')
    with pytest.raises(RedDesignOutputViolation, match="top_interface_changed"):
        validate_design(clean, mutant, "TopModule")
    with pytest.raises(RedDesignOutputViolation, match="compiler_directives_changed"):
        validate_design('`include "original.vh"\n' + CLEAN,
                        '`include "different.vh"\n' + MUTANT, "TopModule")


def test_internal_function_ports_and_helper_definitions_are_editable():
    helper = """  function logic helper;
    input [1:0] value;
    helper = value[0];
  endfunction
"""
    clean = CLEAN.replace("  reg", helper + "  reg", 1)
    changed = clean.replace("input [1:0] value", "input [2:0] value")
    assert validate_design(clean, changed, "TopModule")["body"]["within_limit"]
    assert validate_design(clean, CLEAN, "TopModule")["body"]["within_limit"]
    small_module = "module helper(input a, output y); assign y=a; endmodule\n"
    assert validate_design(CLEAN + small_module, CLEAN, "TopModule")["body"]["within_limit"]


@pytest.mark.parametrize("raw,reason", [
    ({**output(), "admitted": True}, "output_fields_mismatch"),
    ({**output(), "target_weakness_id": "invented"}, "target_weakness_mismatch"),
    ({**output(), "hypothesis": " "}, "output_fields_must_be_nonempty_strings"),
    ({"buggy_rtl": MUTANT}, "output_fields_mismatch"),
])
def test_malformed_proposal_is_not_admitted_or_retried(raw, reason):
    red, requests = generator(raw)
    with pytest.raises(RedDesignOutputViolation, match=reason):
        red.generate(CARRIER, target_weakness=WEAKNESS)
    assert len(requests) == 1


def test_bad_inputs_fail_before_model_call():
    red, requests = generator()
    for carrier, kwargs in [
        (replace(CARRIER, spec=""), {"target_weakness": WEAKNESS}),
        (CARRIER, {"target_weakness": {"weakness_id": "missing mechanism"}}),
        (CARRIER, {"target_weakness": WEAKNESS, "direction": "unregistered"}),
        (CARRIER, {"target_weakness": WEAKNESS, "weakness_context": {"bad": object()}}),
        (CARRIER, {"target_weakness": WEAKNESS, "weakness_context": {"bad": float("nan")}}),
    ]:
        with pytest.raises(RedDesignInputError):
            red.generate(carrier, **kwargs)
    assert not requests and red.client.total_calls == 0


def test_receipt_binds_pre_call_snapshot_of_public_history():
    from r3e.protocol.hashing import hash_payload
    red, _ = generator()
    context = {"history": [{"failed_runs": 2}]}
    before = json.loads(json.dumps(context))
    inner = red.client.inner.complete_json

    def update_history_during_call(**kwargs):
        context["history"][0]["failed_runs"] = 3
        return inner(**kwargs)

    red.client.inner.complete_json = update_history_during_call
    result = red.generate(CARRIER, target_weakness=WEAKNESS, weakness_context=context)
    assert result["receipt"]["weakness_view_hash"] == hash_payload({"target": WEAKNESS, "context": before})


def test_budget_and_provider_errors_propagate_without_fake_bug():
    red, requests = generator(cap=0)
    with pytest.raises(CallBudgetExceeded):
        red.generate(CARRIER, target_weakness=WEAKNESS)
    assert not requests
    red, requests = generator()
    def configuration_error(**kwargs):
        raise ValueError("bad provider configuration")
    red.client.inner.complete_json = configuration_error
    with pytest.raises(ValueError, match="bad provider configuration"):
        red.generate(CARRIER, target_weakness=WEAKNESS)
    assert not requests and red.client.phase == "blue_inference"


def test_current_generated_pool_can_be_preflighted_without_model_calls():
    from r3e.loop.red_design import _layout
    carriers = load_carrier_manifest(DEFAULT_CARRIER_MANIFEST)
    assert len(carriers) == 148
    for carrier in carriers:
        all_tokens, body_tokens, interface = _layout(carrier.clean_rtl, carrier.top_module)
        assert all_tokens and body_tokens and interface and carrier.spec


def test_explicit_structural_edits_are_materialized_and_identity_is_runner_owned():
    raw = edit_output()
    red, requests = generator(raw)
    proposal = red.generate(CARRIER, target_weakness=WEAKNESS)
    assert proposal["buggy_rtl"] == MUTANT and proposal["answer_form"] == "edits"
    assert proposal["target_weakness_id"] == WEAKNESS["weakness_id"]
    assert "target_weakness_id" not in proposal["raw_output"]
    assert "-        2'd2: if (ack)" in proposal["actual_diff"]
    user = json.loads(requests[0]["messages"][-1]["content"])
    assert "target_weakness_id" not in user["required_output_schema"]
    assert len(requests) == 1


def test_over_budget_plan_feedback_allows_correction_in_existing_generation_call():
    raw = edit_output()
    red, requests = generator(raw)
    stub = CLEAN[:CLEAN.index("  reg")] + "assign busy=0; assign done=0; endmodule\n"
    plan = {"edits": [{"find": CLEAN, "replace": stub}],
            "invariant": "state-dependent outputs", "activation": "request then ack", "spec_basis": SPEC}
    result = red.generate(CARRIER, target_weakness=WEAKNESS, mutation_plan=plan)
    user = json.loads(requests[0]["messages"][-1]["content"])
    assert user["planning_feedback"]["reason"] == "edit_budget_exceeded"
    assert result["buggy_rtl"] == MUTANT and len(requests) == 1


@pytest.mark.parametrize("edits,reason", [
    ([{"find": "missing signal text", "replace": "x"}], "edits_not_applicable"),
    ([{"find": CLEAN, "replace": CLEAN}], "no_token_change"),
    ([{"find": "state", "replace": "x"}], "edits_not_applicable"),
])
def test_rejected_edit_outputs_retain_model_claims_and_receipt(edits, reason):
    raw = {**edit_output(), "edits": edits}
    red, requests = generator(raw)
    with pytest.raises(RedDesignOutputViolation, match=reason) as err:
        red.generate(CARRIER, target_weakness=WEAKNESS)
    assert err.value.raw_output == raw and err.value.receipt["output_tokens"] == 29
    assert len(requests) == 1
    if reason == "no_token_change":
        assert err.value.materialized_rtl == CLEAN


def test_edit_form_cannot_bypass_original_destruction_budget():
    stub = CLEAN[:CLEAN.index("  reg")] + "assign busy=0; assign done=0; endmodule\n"
    raw = {**edit_output(), "edits": [{"find": CLEAN, "replace": stub}]}
    red, _ = generator(raw)
    with pytest.raises(RedDesignOutputViolation, match="edit_budget_exceeded") as err:
        red.generate(CARRIER, target_weakness=WEAKNESS)
    assert err.value.materialized_rtl == stub
    assert err.value.raw_output == raw


def test_edits_and_complete_source_together_are_rejected():
    red, _ = generator({**edit_output(), "buggy_rtl": MUTANT})
    with pytest.raises(RedDesignOutputViolation, match="output_fields_mismatch"):
        red.generate(CARRIER, target_weakness=WEAKNESS)
