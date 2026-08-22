from __future__ import annotations

import json
from pathlib import Path

from r3e.pilot.promotion_readiness import assess_policy_promotion_readiness
from r3e.protocol.hashing import hash_file, hash_payload


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _hashed(payload: dict, field: str) -> dict:
    value = dict(payload)
    value[field] = hash_payload({key: item for key, item in value.items() if key != field})
    return value


def _binding(root: Path) -> dict:
    target = root / "target.jsonl"
    target.write_text(
        '{"case_id":"c0","design":"d0"}\n'
        '{"case_id":"c1","design":"d1"}\n',
        encoding="utf-8",
    )
    non_target = root / "non_target.jsonl"
    non_target.write_text('{"case_id":"n0","design":"n0"}\n', encoding="utf-8")
    before = root / "registry-before.json"
    after = root / "registry-after.json"
    registry = {"registry_hash": "same"}
    _write_json(before, registry)
    _write_json(after, registry)
    return {
        "schema_version": "r3e-real-policy-promotion-rehearsal-binding-v1",
        "execution_mode": "real_provider",
        "round_ids": ["R000", "R001"],
        "challenge_seeds": [17],
        "promotion_seeds": [17],
        "policy_promotion_enabled": True,
        "memory_promotion_enabled": False,
        "memory_qualification_enabled": False,
        "at_most_one_registry_commit": True,
        "model_binding": {
            "provider_id": "provider",
            "model_id": "model",
            "model_version": "1",
        },
        "toolchain_fingerprint_hash": "sha256:" + "a" * 64,
        "budget_binding": {
            "maximum_llm_calls_per_case": 3,
            "maximum_input_tokens_per_case": 2048,
            "maximum_output_tokens_per_case": 4096,
            "maximum_wall_time_ms_per_case": 60000,
        },
        "promotion_thresholds": {"min_target_recovery_ratio": 0.5},
        "target_manifest": {"path": "target.jsonl", "file_hash": hash_file(target)},
        "non_target_manifest": {
            "path": "non_target.jsonl",
            "file_hash": hash_file(non_target),
        },
        "registry_snapshots": {
            "before": {"path": "registry-before.json", "file_hash": hash_file(before)},
            "after": {"path": "registry-after.json", "file_hash": hash_file(after)},
        },
    }


def _successful_shadow(root: Path) -> Path:
    workspace = root / "shadow"
    matrix_id = "matrix-v1"
    matrix_hash = "sha256:" + "b" * 64
    blue = _hashed({
        "schema_version": "r3e-shadow-pilot-run-summary-v1",
        "matrix_id": matrix_id,
        "matrix_hash": matrix_hash,
        "execution_mode": "smoke",
        "completed_cells": 4,
        "call_matched": True,
        "promotion_executed": False,
        "raam_execution_executed": False,
        "claim_scope": "shadow",
    }, "summary_hash")
    aggregate = _hashed({
        "schema_version": "r3e-shadow-pilot-aggregate-v1",
        "matrix_id": matrix_id,
        "matrix_hash": matrix_hash,
        "completed_cells": 4,
        "expected_cells": 4,
        "call_matched": True,
        "arms": {
            arm: {"provider_calls": 3}
            for arm in ("A", "B", "C", "D")
        },
        "claim_scope": "shadow",
    }, "aggregate_hash")
    _write_json(workspace / "blue_matrix" / "summary.json", blue)
    _write_json(workspace / "blue_matrix" / "aggregate.json", aggregate)
    result = {
        "admitted": True,
        "formal_triplet": {
            "clean": "proved",
            "poison": "counterexample",
            "revert": "proved",
        },
    }
    _write_json(workspace / "grounded_red" / "red_result.json", result)
    event = _hashed({
        "schema_version": "r3e-grounded-red-shadow-event-v1",
        "matrix_id": matrix_id,
        "matrix_hash": matrix_hash,
        "round_id": "matrix-v1-red-seed-17",
        "seed": 17,
        "policy_hash": "sha256:" + "c" * 64,
        "model_binding_hash": "sha256:" + "d" * 64,
        "red_result_file_hash": hash_file(workspace / "grounded_red" / "red_result.json"),
        "provider_calls": 1,
        "admitted": True,
        "formal_triplet": result["formal_triplet"],
        "authority_hash": "sha256:" + "e" * 64,
        "failure_descriptor_hash": "sha256:" + "f" * 64,
        "promotion_executed": False,
        "memory_qualification_executed": False,
    }, "event_hash")
    _write_json(workspace / "grounded_red" / "event.json", event)
    _write_json(workspace / "grounded_red" / "events.jsonl", event)
    red = _hashed({
        "schema_version": "r3e-grounded-red-shadow-summary-v1",
        "matrix_id": matrix_id,
        "matrix_hash": matrix_hash,
        "round_id": "matrix-v1-red-seed-17",
        "seed": 17,
        "status": "admitted",
        "event_hash": event["event_hash"],
        "events_file_hash": hash_file(workspace / "grounded_red" / "events.jsonl"),
        "promotion_executed": False,
        "memory_qualification_executed": False,
        "claim_scope": "shadow",
    }, "summary_hash")
    _write_json(workspace / "grounded_red" / "summary.json", red)
    summary = _hashed({
        "schema_version": "r3e-real-provider-shadow-admission-v1",
        "matrix_id": matrix_id,
        "matrix_hash": matrix_hash,
        "execution_mode": "smoke",
        "blue_summary_hash": blue["summary_hash"],
        "red_summary_hash": red["summary_hash"],
        "blue_completed_cells": 4,
        "blue_call_matched": True,
        "red_status": "admitted",
        "expected_blue_provider_calls": 12,
        "expected_red_provider_calls": 1,
        "expected_total_provider_calls": 13,
        "promotion_executed": False,
        "memory_qualification_executed": False,
        "resume_additional_calls": 0,
        "claim_scope": "shadow",
    }, "summary_hash")
    _write_json(workspace / "summary.json", summary)
    return workspace


def test_terminal_shadow_is_not_promotion_input(tmp_path):
    workspace = tmp_path / "failed"
    _write_json(workspace / "terminal_failure.json", {"terminal": True})
    result = assess_policy_promotion_readiness(
        shadow_workspace=workspace,
        rehearsal_binding={},
        project_root=tmp_path,
    )
    assert result["ready"] is False
    assert "shadow_terminal_failure" in result["blockers"]
    assert "rehearsal_binding_schema" in result["blockers"]


def test_complete_shadow_and_fixed_binding_can_pass_readiness(tmp_path):
    workspace = _successful_shadow(tmp_path)
    result = assess_policy_promotion_readiness(
        shadow_workspace=workspace,
        rehearsal_binding=_binding(tmp_path),
        project_root=tmp_path,
    )
    assert result["ready"] is True
    assert result["blockers"] == []
    assert result["readiness_hash"].startswith("sha256:")


def test_registry_change_and_formal_triplet_drift_fail_closed(tmp_path):
    workspace = _successful_shadow(tmp_path)
    binding = _binding(tmp_path)
    after = tmp_path / "registry-after.json"
    _write_json(after, {"registry_hash": "changed"})
    binding["registry_snapshots"]["after"]["file_hash"] = hash_file(after)
    result_path = workspace / "grounded_red" / "red_result.json"
    _write_json(result_path, {
        "admitted": True,
        "formal_triplet": {
            "clean": "proved",
            "poison": "inconclusive",
            "revert": "proved",
        },
    })
    result = assess_policy_promotion_readiness(
        shadow_workspace=workspace,
        rehearsal_binding=binding,
        project_root=tmp_path,
    )
    assert result["ready"] is False
    assert "registry_changed" in result["blockers"]
    assert "red_formal_triplet" in result["blockers"]
