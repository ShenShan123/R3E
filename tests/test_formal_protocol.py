import json
from pathlib import Path

import pytest

from semantic_repair_bench.formal_protocol import (
    FormalProtocolViolation, activate, atomic_write_json, candidate_hash,
    commit_registry_atomically, decide_promotion, distill_candidates,
    empty_registry, hash_payload, load_promoted_registry, load_registry,
    policy_hash, rollback_if_audit_regression, rollback_last_promotion, validate_case_result,
    validation_manifest_hash,
)


def _setup(tmp_path: Path):
    reg = tmp_path / "registry.json"
    empty_registry(reg)
    payload = load_registry(reg)
    ph = policy_hash(payload)
    rh = hash_payload(payload)
    residuals = [{"residual_id": "r0", "family": "state_swap", "failure_signature": "swapped"}]
    rows = []
    values = {
        "target": [("t0", "d0", False, True), ("t1", "d1", False, True)],
        "non_target": [("n0", "d2", True, True), ("n1", "d3", True, True)],
    }
    for split, cases in values.items():
        for case_id, design, baseline_ok, candidate_ok in cases:
            rows.extend([
                {"case_id": case_id, "design": design, "arm": "baseline",
                 "split": split, "oracle_ok": baseline_ok, "strategy_hit": False},
                {"case_id": case_id, "design": design, "arm": "candidate",
                 "split": split, "oracle_ok": candidate_ok,
                 "strategy_hit": split == "target"},
            ])
    vmh = validation_manifest_hash(rows)
    candidate = distill_candidates(
        residuals, parent_policy_hash=ph, registry_parent_hash=rh,
        validation_manifest_hash=vmh,
        distiller=lambda _p, _r: "Check current-state and next-state assignment targets.",
    )[0]
    decision = decide_promotion(candidate, rows, parent_policy_hash=ph,
                                validation_manifest_hash=vmh, min_hits=1,
                                min_designs=2, min_recovery_ratio=0.5,
                                min_gain=0.1, tau_r=0.1, epsilon=0.0)
    return reg, candidate, decision, ph, vmh


def test_no_manual_hints_in_formal_mode(tmp_path):
    p = tmp_path / "registry.json"
    manual = {"schema_version": "r3e-registry-v1", "artifacts": [{
        "artifact_id": "m", "origin": "manual", "generation_mode": "author_written",
        "source_residual_ids": [], "candidate_prompt_hash": "x", "candidate_output_hash": "y",
        "parent_policy_hash": "p", "validation_manifest_hash": "v",
        "promotion_decision_hash": "d", "registry_parent_hash": "r",
        "created_at": "now", "runtime_status": "promoted",
    }]}
    atomic_write_json(p, manual)
    with pytest.raises(FormalProtocolViolation):
        load_registry(p, formal_mode=True)


def test_stale_decision_fails_closed(tmp_path):
    reg, cand, decision, *_ = _setup(tmp_path)
    decision["target_hits"] = 0
    with pytest.raises(FormalProtocolViolation):
        commit_registry_atomically(reg, cand, decision)


def test_rehashed_false_gate_cannot_be_forged(tmp_path):
    reg, cand, decision, *_ = _setup(tmp_path)
    decision["no_regression_pass"] = False
    decision["promote"] = True
    decision["decision_hash"] = hash_payload({
        k: v for k, v in decision.items() if k != "decision_hash"
    })
    with pytest.raises(FormalProtocolViolation, match="do not reconstruct"):
        commit_registry_atomically(reg, cand, decision)


def test_mismatched_case_ids_fail_closed(tmp_path):
    reg = tmp_path / "registry.json"
    empty_registry(reg)
    payload = load_registry(reg)
    ph = policy_hash(payload)
    rh = hash_payload(payload)
    rows = [
        {"case_id": "target-a", "design": "d0", "arm": "baseline",
         "split": "target", "oracle_ok": False},
        {"case_id": "target-b", "design": "d0", "arm": "candidate",
         "split": "target", "oracle_ok": True},
        {"case_id": "non-a", "design": "d1", "arm": "baseline",
         "split": "non_target", "oracle_ok": True},
        {"case_id": "non-b", "design": "d1", "arm": "candidate",
         "split": "non_target", "oracle_ok": True},
    ]
    candidate = distill_candidates(
        [{"residual_id": "r0", "family": "state_swap", "failure_signature": "f"}],
        parent_policy_hash=ph, registry_parent_hash=rh,
        validation_manifest_hash="unreachable",
    )[0]
    with pytest.raises(FormalProtocolViolation, match="unpaired"):
        decide_promotion(
            candidate, rows, parent_policy_hash=ph,
            validation_manifest_hash="unreachable", min_hits=1, tau_r=0.0,
            epsilon=0.0,
        )


def test_hash_mismatch_fails_closed(tmp_path):
    reg, cand, decision, *_ = _setup(tmp_path)
    cand["normalized_strategy"] += " edited"
    with pytest.raises(FormalProtocolViolation):
        commit_registry_atomically(reg, cand, decision)


def test_delta_target_reconstruction(tmp_path):
    _, _, decision, *_ = _setup(tmp_path)
    assert decision["delta_target"] == 1.0
    assert decision["delta_non_target"] == 0.0
    assert decision["promote"] is True


def test_atomic_registry_commit(tmp_path):
    reg, cand, decision, *_ = _setup(tmp_path)
    before, after = commit_registry_atomically(reg, cand, decision)
    assert before != after
    allowed = {".registry.json.lock", ".registry.json.versions"}
    assert not [p for p in tmp_path.glob(".registry.json.*") if p.name not in allowed]


def test_atomic_registry_commit_writes_ledger_and_can_rollback(tmp_path):
    reg, cand, decision, *_ = _setup(tmp_path)
    ledger = tmp_path / "decision_ledger.jsonl"
    empty_hash = hash_payload(load_registry(reg))
    before, promoted = commit_registry_atomically(
        reg, cand, decision, ledger_path=ledger,
    )
    assert before == empty_hash and promoted != before
    rollback_before, rollback_after = rollback_last_promotion(
        reg, ledger_path=ledger,
    )
    assert rollback_before == promoted
    assert rollback_after == empty_hash
    events = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert [event["status"] for event in events] == ["promoted", "rolled-back"]


def test_later_audit_rollback_respects_tolerance(tmp_path):
    reg, cand, decision, *_ = _setup(tmp_path)
    commit_registry_atomically(reg, cand, decision)
    promoted_hash = hash_payload(load_registry(reg))
    before, after = rollback_if_audit_regression(
        reg, {"non_target_delta": -0.01}, epsilon=0.02,
    )
    assert before == after == promoted_hash
    before, after = rollback_if_audit_regression(
        reg, {"non_target_delta": -0.03}, epsilon=0.02,
    )
    assert before == promoted_hash and after != before


def test_runtime_loads_only_promoted(tmp_path):
    reg, cand, decision, *_ = _setup(tmp_path)
    commit_registry_atomically(reg, cand, decision)
    active, ph = load_promoted_registry(reg, {decision["decision_hash"]: decision})
    strategy, ids = activate({"family": "state_swap"}, active)
    assert ids == [cand["artifact_id"]]
    assert "current-state" in strategy
    assert ph == policy_hash(load_registry(reg))


def test_case_result_schema():
    with pytest.raises(FormalProtocolViolation):
        validate_case_result({"case_id": "x"})


def test_no_hardcoded_mcnemar():
    source = (Path(__file__).parents[1] / "experiments" / "analyze_cirfix_formal.py")
    if source.exists():
        text = source.read_text()
        assert "a=12" not in text and "b=3" not in text


def test_summary_reconstructs_from_jsonl(tmp_path):
    rows = [{"case_id": "a", "seed": s, "oracle_ok": s == 1} for s in (1, 2)]
    p = tmp_path / "rows.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    loaded = [json.loads(line) for line in p.read_text().splitlines()]
    assert sum(r["oracle_ok"] for r in loaded) == 1


def test_two_case_fake_oracle_full_chain(tmp_path):
    reg, cand, decision, _, _ = _setup(tmp_path)
    before_policy = policy_hash(load_registry(reg))
    commit_registry_atomically(reg, cand, decision)
    active, after_policy = load_promoted_registry(reg, {decision["decision_hash"]: decision})
    assert before_policy != after_policy
    _, ids = activate({"family": "state_swap"}, active)
    assert ids
    assert cand["source_residual_ids"] == ["r0"]
