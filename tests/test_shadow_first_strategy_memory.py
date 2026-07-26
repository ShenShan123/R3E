import json
from pathlib import Path

import pytest

from semantic_repair_bench.formal_protocol import (
    FormalProtocolViolation,
    empty_registry,
    hash_payload,
    load_registry,
    policy_hash,
)
from semantic_repair_bench.shadow_first_strategy_memory import (
    PromotionThresholds,
    ShadowFirstStrategyMemory,
)


def _sha(label: str) -> str:
    return hash_payload(label)


def _trajectory(name: str, *, bug_type: str = "state_swap", design: str = "d1") -> dict:
    return {
        "trajectory_id": name,
        "case_id": f"case-{name}",
        "design_id": design,
        "red_generation": 1,
        "seed": 101,
        "effect": "wrong-state-transition",
        "bug_type": bug_type,
        "scope": "local_block",
        "failure_signature": "formal mismatch after bounded blue repair",
        "repair_attempt_summary": "one local candidate rejected by the formal evaluator",
        "red_trace_hash": _sha(f"trace-{name}"),
        "buggy_rtl_hash": _sha(f"rtl-{name}"),
        "repair_patch_hash": _sha(f"patch-{name}"),
        "prompt_hash": _sha(f"prompt-{name}"),
        "response_hash": _sha(f"response-{name}"),
        "evaluator_manifest_hash": _sha("evaluator"),
        "artifact_manifest_hash": _sha(f"artifacts-{name}"),
        "parent_policy_hash": _sha("red-observed-policy"),
        "compile_ok": True,
        "simulation_ok": True,
        "oracle_ok": True,
        "rebuild_command": "python3 frozen_evaluator.py --case manifest.json",
    }


def _memory(tmp_path: Path) -> ShadowFirstStrategyMemory:
    registry = tmp_path / "registry.json"
    empty_registry(registry)
    return ShadowFirstStrategyMemory(
        tmp_path / "a1",
        registry,
        target_replay=[{"case_id": "target-a"}, {"case_id": "target-b"}],
        non_target_replay=[{"case_id": "non-a"}, {"case_id": "non-b"}],
        thresholds=PromotionThresholds(1, 1, 0.5, 0.0, 0.0),
    )


def _rows(*, candidate_non_target=(True, True)) -> list[dict]:
    rows = []
    for arm, values in (("baseline", (False, False)), ("candidate", (True, True))):
        for case_id, design, value in zip(("target-a", "target-b"), ("d1", "d2"), values):
            rows.append({"arm": arm, "split": "target", "case_id": case_id,
                         "design_id": design, "seed": 101, "oracle_ok": value})
    for arm, values in (("baseline", (True, True)), ("candidate", candidate_non_target)):
        for case_id, design, value in zip(("non-a", "non-b"), ("d3", "d4"), values):
            rows.append({"arm": arm, "split": "non_target", "case_id": case_id,
                         "design_id": design, "seed": 101, "oracle_ok": value})
    return rows


def test_rejects_incomplete_trajectory_and_hash_chains_ledger(tmp_path):
    memory = _memory(tmp_path)
    bad = _trajectory("bad")
    del bad["red_trace_hash"]
    assert memory.ingest([bad]) == []
    ledger = [json.loads(line) for line in memory.ledger_path.read_text().splitlines()]
    assert ledger[0]["event"] == "trajectory-rejected"
    assert "red_trace_hash" in ledger[0]["details"]["missing_provenance_fields"]
    assert ledger[0]["previous_ledger_entry_hash"] == "0" * 64


def test_ledger_tampering_is_detected(tmp_path):
    memory = _memory(tmp_path)
    memory.ingest([_trajectory("a")])
    rows = [json.loads(line) for line in memory.ledger_path.read_text().splitlines()]
    rows[0]["event"] = "promoted"
    memory.ledger_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    with pytest.raises(FormalProtocolViolation, match="ledger entry hash"):
        memory.audit_ledger()


def test_ingests_inactive_and_groups_by_effect_bug_type_scope(tmp_path):
    memory = _memory(tmp_path)
    accepted = memory.ingest([_trajectory("a"), _trajectory("b", design="d2")])
    assert len(accepted) == 2
    assert all(row["runtime_status"] == "inactive" for row in accepted)
    candidates = memory.distill(accepted, lambda _prompt, _rows: " Repair  state targets. ")
    assert len(candidates) == 1
    assert candidates[0]["source_trajectory_ids"] == ["a", "b"]
    assert candidates[0]["runtime_status"] == "candidate"
    assert candidates[0]["parent_policy_hash"] == policy_hash(load_registry(memory.registry_path))
    assert Path(candidates[0]["rollback_artifact"]["path"]).is_file()


def test_full_gate_promotes_records_use_and_rolls_back(tmp_path):
    memory = _memory(tmp_path)
    before_policy = policy_hash(load_registry(memory.registry_path))
    candidates, decisions = memory.run(
        [_trajectory("a"), _trajectory("b", design="d2")],
        lambda _prompt, _rows: "Repair state targets with the smallest local edit.",
        lambda _candidate, _target, _non_target: _rows(),
    )
    assert decisions[0]["eligible"] is True
    assert decisions[0]["metrics"]["validation_hits"] == 2
    assert decisions[0]["metrics"]["covered_designs"] == 2
    active, after_policy = memory.load_active()
    assert after_policy != before_policy
    assert [row["artifact_id"] for row in active] == [candidates[0]["artifact_id"]]
    use = memory.record_runtime_use("fresh-red-generation-2", [active[0]["artifact_id"]])
    assert use["policy_hash"] == after_policy

    assert memory.rollback_if_regressed(_rows(candidate_non_target=(True, False))) is True
    assert memory.load_active()[1] == before_policy
    ledger = [json.loads(line) for line in memory.ledger_path.read_text().splitlines()]
    assert "promoted" in [row["event"] for row in ledger]
    assert ledger[-1]["event"] == "rolled-back"


def test_two_automatic_policy_transitions_are_supported(tmp_path):
    memory = _memory(tmp_path)
    m0 = memory.load_active()[1]
    first, _ = memory.run(
        [_trajectory("g1-a"), _trajectory("g1-b", design="d2")],
        lambda _prompt, _rows: "Repair state targets with a bounded edit.",
        lambda _candidate, _target, _non_target: _rows(),
    )
    m1 = memory.load_active()[1]
    memory.record_runtime_use("fresh-red-generation-2", [first[0]["artifact_id"]])

    accepted = memory.ingest([
        _trajectory("g2-a", bug_type="off_by_one"),
        _trajectory("g2-b", bug_type="off_by_one", design="d2"),
    ])
    second = memory.distill(accepted, lambda _prompt, _rows: "Repair bounds locally.")[0]
    decision = memory.decide(second, _rows())
    memory.promote(second, decision)
    active, m2 = memory.load_active()
    assert len(active) == 2
    assert len({m0, m1, m2}) == 3


def test_stale_parent_policy_is_kept_inactive(tmp_path):
    memory = _memory(tmp_path)
    accepted = memory.ingest([_trajectory("a")])
    candidate = memory.distill(accepted, lambda _prompt, _rows: "A local strategy.")[0]
    decision = memory.decide(candidate, _rows())
    registry = load_registry(memory.registry_path)
    registry["artifacts"].append({
        "artifact_id": "concurrent",
        "origin": "automatic",
        "generation_mode": "distilled",
        "source_residual_ids": ["other"],
        "source_residual_hashes": {"other": _sha("other")},
        "candidate_prompt_hash": _sha("p"),
        "candidate_output_hash": _sha("o"),
        "parent_policy_hash": policy_hash(registry),
        "validation_manifest_hash": _sha("v"),
        "promotion_decision_hash": _sha("d"),
        "registry_parent_hash": hash_payload(registry),
        "created_at": "now",
        "runtime_status": "promoted",
    })
    from semantic_repair_bench.formal_protocol import atomic_write_json
    atomic_write_json(memory.registry_path, registry)
    before, after = memory.promote(candidate, decision)
    assert before == after
    ledger = memory.audit_ledger()
    assert ledger[-1]["event"] == "stale-parent-policy"


def test_target_and_non_target_manifests_must_be_disjoint(tmp_path):
    registry = tmp_path / "registry.json"
    empty_registry(registry)
    with pytest.raises(FormalProtocolViolation, match="overlap"):
        ShadowFirstStrategyMemory(
            tmp_path / "a1", registry,
            target_replay=[{"case_id": "same"}],
            non_target_replay=[{"case_id": "same"}],
            thresholds=PromotionThresholds(1, 1, 0.5, 0.0, 0.0),
        )
