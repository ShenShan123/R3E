from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from r3e.pilot.shadow_aggregate import (
    ShadowPilotAggregateViolation,
    aggregate_cell_events,
)
from r3e.pilot.shadow_matrix import (
    ShadowPilotMatrixViolation,
    load_shadow_pilot_matrix,
)
from r3e.protocol.hashing import hash_payload


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "configs/pilot/shadow_pilot_matrix_v1.json"


def _event(case_id: str, seed: int, arm_id: str) -> dict:
    body = {
        "schema_version": "r3e-shadow-pilot-cell-event-v1",
        "matrix_id": "matrix",
        "matrix_hash": "sha256:" + "1" * 64,
        "case_id": case_id,
        "seed": seed,
        "arm_id": arm_id,
        "portfolio_hash": "sha256:" + arm_id.lower() * 64,
        "policy_hash": "sha256:" + "2" * 64,
        "model_binding_hash": "sha256:" + "3" * 64,
        "blue_evaluation_hash": "sha256:" + "4" * 64,
        "provider_calls": 3,
        "verifier_calls": 3,
        "input_tokens": 30,
        "output_tokens": 6,
        "candidate_count": 3,
        "candidate_success_count": 1,
        "portfolio_success": True,
        "semantic_unique_candidate_count": 2,
        "semantic_duplicate_pairs": 1,
        "lens_collapse": arm_id != "A",
        "selected_candidate_id": "C0",
    }
    body["event_hash"] = hash_payload(body)
    return body


def test_shadow_matrix_freezes_call_matched_abcd_authority():
    matrix = load_shadow_pilot_matrix(MATRIX, project_root=ROOT)
    assert [row["arm_id"] for row in matrix.arms] == ["A", "B", "C", "D"]
    assert len(matrix.case_ids) == 4
    assert len(matrix.seeds) == 3
    assert matrix.controls["call_budget_per_cell"] == 3
    assert matrix.controls["promotion_enabled"] is False
    assert matrix.controls["raam_execution_enabled"] is False
    assert matrix.red_shadow["provider_calls_per_round"] == 1
    assert matrix.red_shadow["maximum_output_tokens"] == 4096
    assert matrix.red_case_id == "r3e:real_integrated_shadow_comparator"
    assert matrix.red_target_manifest_path.name == (
        "real_integrated_shadow_manifest_v1.jsonl"
    )


def test_shadow_matrix_hash_tamper_is_rejected(tmp_path):
    project = tmp_path / "project"
    for relative in (
        "configs/pilot/shadow_pilot_matrix_v1.json",
        "configs/blue/homogeneous_generic_portfolio_v1.json",
        "configs/blue/fixed_mixed_portfolio_v1.json",
        "configs/blue/descriptor_routed_portfolio_v1.json",
        "configs/blue/adaptive_portfolio_v1.json",
        "datasets/manifests/strider14.jsonl",
    ):
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    path = project / "configs/pilot/shadow_pilot_matrix_v1.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["seeds"][0] = 18
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ShadowPilotMatrixViolation, match="hash mismatch"):
        load_shadow_pilot_matrix(path, project_root=project)


def test_shadow_aggregate_rejects_duplicates_and_hash_tamper():
    rows = [_event("case", 17, arm_id) for arm_id in "ABCD"]
    expected = {("case", 17, arm_id) for arm_id in "ABCD"}
    aggregate = aggregate_cell_events(
        rows,
        matrix_id="matrix",
        matrix_hash="sha256:" + "1" * 64,
        expected_cells=expected,
        expected_calls_per_cell=3,
    )
    assert aggregate["call_matched"] is True
    assert aggregate["completed_cells"] == 4
    with pytest.raises(ShadowPilotAggregateViolation, match="duplicate"):
        aggregate_cell_events(
            rows + [deepcopy(rows[0])],
            matrix_id="matrix",
            matrix_hash="sha256:" + "1" * 64,
            expected_cells=expected,
            expected_calls_per_cell=3,
        )
    tampered = deepcopy(rows)
    tampered[0]["provider_calls"] = 2
    with pytest.raises(ShadowPilotAggregateViolation, match="hash mismatch"):
        aggregate_cell_events(
            tampered,
            matrix_id="matrix",
            matrix_hash="sha256:" + "1" * 64,
            expected_cells=expected,
            expected_calls_per_cell=3,
        )
