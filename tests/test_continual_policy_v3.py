import json
from pathlib import Path

from semantic_repair_bench.formal_gate import FormalOutcome
from semantic_repair_bench.formal_protocol import empty_registry, hash_payload, policy_hash
from semantic_repair_bench.formal_llm_response_cache import cached_call_llm
from semantic_repair_bench.shadow_first_strategy_memory import PromotionThresholds
from semantic_repair_bench.shadow_first_strategy_memory_v2 import ShadowFirstStrategyMemoryV2
from semantic_repair_bench.strategy_action_primitives import (
    compile_action_policy,
    enumerate_decimal_literal_pm1,
    enumerate_index_range_bound_pm1,
    enumerate_operator_inverse_pairs,
)
from semantic_repair_bench.strategy_reasoning_ir import (
    SCHEMA,
    StrategyReasoningViolation,
    validate_strategy_ir,
)


def _sha(label):
    return hash_payload(label)


def _trajectory(name="t1"):
    return {
        "trajectory_id": name, "case_id": f"case-{name}", "design_id": "d1",
        "red_generation": 1, "seed": 101, "effect": "formal-non-equivalence",
        "bug_type": "off_by_one", "scope": "local_block",
        "failure_signature": "range width mismatch", "repair_attempt_summary": "failed",
        "red_trace_hash": _sha("trace"), "buggy_rtl_hash": _sha("rtl"),
        "repair_patch_hash": _sha("patch"), "prompt_hash": _sha("prompt"),
        "response_hash": _sha("response"), "evaluator_manifest_hash": _sha("eval"),
        "artifact_manifest_hash": _sha("artifact"), "parent_policy_hash": _sha("policy"),
        "compile_ok": True, "simulation_ok": True, "oracle_ok": False,
        "rebuild_command": "python3 frozen.py",
    }


def _reasoning_ir():
    return {
        "schema_version": SCHEMA,
        "intent_type": "select_repair_primitive",
        "bug_family": "off_by_one",
        "primitive": "index_range_bound_pm1",
        "search_order": "structural_mismatch_then_source_order",
        "stop_condition": "first_formal_equivalent",
    }


def test_off_by_one_compiler_is_generic_and_bounded():
    policy = compile_action_policy(
        {"bug_type": "off_by_one", "scope": "local_block"}, [_trajectory()], _reasoning_ir()
    )
    assert policy["strategy_primitives"] == ["index_range_bound_pm1"]
    assert policy["primitive_max_trials"] == 64
    assert policy["compiled_from_trajectory_count"] == 1
    assert policy["llm_patch_generation_allowed"] is False


def test_reasoning_ir_rejects_patch_authority():
    bad = dict(_reasoning_ir(), start_line=10, new_code="assign y = a;")
    try:
        validate_strategy_ir(bad, {"bug_type": "off_by_one"})
    except StrategyReasoningViolation as exc:
        assert "fields mismatch" in str(exc)
    else:
        raise AssertionError("planner was allowed to emit an RTL patch")


def test_range_primitive_enumerates_inverse_width_edits_first(tmp_path):
    rtl = tmp_path / "buggy.v"
    rtl.write_text("module d;\nreg [1:0] state;\nwire [7:0] data;\nendmodule\n")
    rows = enumerate_index_range_bound_pm1(rtl)
    assert rows[0]["new_code"] == "reg [2:0] state;"
    assert rows[1]["new_code"] == "reg [0:0] state;"
    assert all(row["start_line"] == row["end_line"] for row in rows)


def test_structural_assignment_width_mismatch_precedes_unrelated_ranges(tmp_path):
    rtl = tmp_path / "buggy.v"
    rtl.write_text(
        "module d;\n"
        "reg [31:0] a;\n"
        "reg [15:0] b;\n"
        "always @* a[16-10:0] = b[15:10];\n"
        "endmodule\n"
    )
    rows = enumerate_index_range_bound_pm1(rtl)
    assert rows[0]["start_line"] == 4
    assert any(row["new_code"] == "always @* a[16-10:0] = b[16:10];" for row in rows[:8])


def test_frozen_m0_primitives_generate_only_deterministic_local_edits(tmp_path):
    rtl = tmp_path / "buggy.v"
    rtl.write_text("module d;\nparameter TIMEOUT = 3;\nassign y = a | b;\nendmodule\n")
    literals = enumerate_decimal_literal_pm1(rtl)
    operators = enumerate_operator_inverse_pairs(rtl)
    assert literals[0]["new_code"] == "parameter TIMEOUT = 4;"
    assert literals[1]["new_code"] == "parameter TIMEOUT = 2;"
    assert operators == [{
        "primitive": "operator_inverse_pair", "start_line": 3, "end_line": 3,
        "new_code": "assign y = a & b;", "changed_operator": "|",
        "replacement_operator": "&",
    }]


def test_base_policy_is_bound_into_effective_policy_hash():
    historical = {"schema_version": "r3e-registry-v1", "artifacts": []}
    with_base = dict(historical, base_policy={"schema": "frozen-base", "artifacts": []})
    assert policy_hash(historical) == "ac755351a05668c8d6453eeefce411c25da57b56f14e009edf80d61ad4bc719a"
    assert policy_hash(with_base) != policy_hash(historical)


def test_v2_distillation_binds_executable_policy_into_candidate_hash(tmp_path):
    registry = tmp_path / "registry.json"; empty_registry(registry)
    memory = ShadowFirstStrategyMemoryV2(
        tmp_path / "a1", registry,
        [{"case_id": "target"}], [{"case_id": "non-target"}],
        PromotionThresholds(1, 1, 0.5, 0.0, 0.0),
    )
    accepted = memory.ingest([_trajectory()])
    candidate = memory.distill(accepted, lambda _prompt, _rows: _reasoning_ir())[0]
    assert candidate["generation_mode"] == "distilled"
    assert candidate["action_policy"]["strategy_primitives"] == ["index_range_bound_pm1"]
    assert candidate["strategy_reasoning_ir"] == _reasoning_ir()
    stored = json.loads(memory.candidates_path.read_text().strip())
    assert stored["candidate_hash"] == candidate["candidate_hash"]
    assert memory.audit_ledger()[-1]["event"] == "candidate-distilled-and-compiled"


def test_v2_candidate_passes_provenance_and_promotes_with_policy_transition(tmp_path):
    registry = tmp_path / "registry.json"; empty_registry(registry)
    memory = ShadowFirstStrategyMemoryV2(
        tmp_path / "a1", registry,
        [{"case_id": "target"}], [{"case_id": "non-target"}],
        PromotionThresholds(1, 1, 0.5, 0.0, 0.0),
    )
    accepted = memory.ingest([_trajectory()])
    candidate = memory.distill(accepted, lambda _prompt, _rows: _reasoning_ir())[0]
    replay_rows = [
        {"split": "target", "arm": "baseline", "case_id": "target",
         "design_id": "target-design", "seed": 101, "oracle_ok": False},
        {"split": "target", "arm": "candidate", "case_id": "target",
         "design_id": "target-design", "seed": 101, "oracle_ok": True},
        {"split": "non_target", "arm": "baseline", "case_id": "non-target",
         "design_id": "non-target-design", "seed": 101, "oracle_ok": True},
        {"split": "non_target", "arm": "candidate", "case_id": "non-target",
         "design_id": "non-target-design", "seed": 101, "oracle_ok": True},
    ]
    decision = memory.decide(candidate, replay_rows)
    assert decision["checks"]["provenance_ok"] is True
    assert decision["eligible"] is True
    policy_before = memory.load_active()[1]
    registry_before, registry_after = memory.promote(candidate, decision)
    active, policy_after = memory.load_active()
    assert registry_before != registry_after
    assert policy_before != policy_after
    assert active[0]["candidate_hash"] == candidate["candidate_hash"]


def test_executable_primitive_reaches_formal_gate_without_golden_read(monkeypatch, tmp_path):
    import semantic_repair_bench.continual_policy_repair_v2 as repair

    buggy = tmp_path / "buggy.v"
    golden = tmp_path / "golden.v"
    buggy.write_text("module d;\nreg [1:0] state;\nendmodule\n")
    golden.write_text("opaque reference not inspected by primitive")

    def fake_judge(_golden, _deps, candidate, _top, _work, timeout):
        del timeout
        ok = "reg [2:0] state;" in Path(candidate).read_text()
        return FormalOutcome(ok, 1 if ok else 0, 1, 0 if ok else 1, "")

    monkeypatch.setattr(repair, "formal_judge", fake_judge)

    class Accumulator:
        template_stage = "promoted"
        def template_for(self, _case):
            return {"template_id": "p1", "strategy_context": "repair widths", "action_policy": {
                "patch_scope": "local_block", "strategy_primitives": ["index_range_bound_pm1"],
                "primitive_max_trials": 2,
                "planner_authority": "select_repair_primitive_only",
                "executor_authority": "deterministic_patch_generation_only",
                "llm_patch_generation_allowed": False,
            }}
        def shadow_template_for(self, _case):
            return None

    result = repair.blue_repair_formal_v2(
        {"buggy": str(buggy), "golden": str(golden), "deps": [], "top": "d",
         "mutation_type": "索引/位宽偏移(off-by-one)"},
        tmp_path / "work", registry=[], accumulator=Accumulator(),
        use_accumulated_templates=True, formal_timeout=1, candidate_budget=1,
    )
    assert result["repaired"] is True
    assert result["candidates"][0]["source"] == "strategy_primitive"
    assert result["n_llm_revisions"] == 0
    assert result["llm_patch_generation_allowed"] is False
    assert result["patch_authority"] == "deterministic_primitive_executor"


def test_runtime_without_active_primitive_never_asks_llm_for_patch(tmp_path):
    import semantic_repair_bench.continual_policy_repair_v2 as repair

    class EmptyAccumulator:
        template_stage = "promoted"
        def template_for(self, _case):
            return None
        def shadow_template_for(self, _case):
            return None

    result = repair.blue_repair_formal_v2(
        {"buggy": str(tmp_path / "missing-never-read.v"), "golden": "unused",
         "deps": [], "top": "d", "mutation_type": "off-by-one"},
        tmp_path / "work", registry=[], accumulator=EmptyAccumulator(),
        use_accumulated_templates=True, formal_timeout=1, candidate_budget=3,
    )
    assert result["repaired"] is False
    assert result["candidates"] == []
    assert result["n_llm_revisions"] == 0


def test_common_response_cache_is_hash_verified(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_MODEL", "test-model")
    calls = []
    first = cached_call_llm("prompt", lambda prompt: calls.append(prompt) or {"answer": 1}, tmp_path)
    second = cached_call_llm("prompt", lambda _prompt: {"answer": 2}, tmp_path)
    assert first == second == {"answer": 1}
    assert calls == ["prompt"]
    cache_file = next(tmp_path.glob("*.json"))
    row = json.loads(cache_file.read_text())
    row["response"] = {"answer": 999}
    cache_file.write_text(json.dumps(row))
    try:
        cached_call_llm("prompt", lambda _prompt: {"answer": 3}, tmp_path)
    except RuntimeError as exc:
        assert "response hash mismatch" in str(exc)
    else:
        raise AssertionError("tampered cache was accepted")

