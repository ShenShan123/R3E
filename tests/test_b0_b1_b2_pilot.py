from __future__ import annotations

import json
from pathlib import Path

import pytest

from r3e.arena.manifests import ManifestViolation
from r3e.pilot.b0_b1_b2_pilot import (
    B012PilotViolation,
    load_b012_config,
    run_b0_b1_b2_pilot,
)


ROOT = Path(__file__).resolve().parents[1]


def test_b0_b1_b2_three_repetition_pilot_is_deterministic(tmp_path):
    first = run_b0_b1_b2_pilot(
        project_root=ROOT,
        workspace=tmp_path / "b012",
    )
    assert first["round_ids"] == ["R000", "R001", "R002"]
    assert first["promotion_lane_schedule"] == [
        "collect",
        "policy",
        "policy",
    ]
    assert first["repetitions"] == 3
    assert first["design_count"] == 4
    assert first["family_count"] == 8
    assert first["promotion_counts_by_repetition"] == [
        [0, 1, 1],
        [0, 1, 1],
        [0, 1, 1],
    ]
    assert first["direction_consistent"] is True
    assert first["policy_promotion_enabled"] is True
    assert first["memory_promotion_enabled"] is False
    assert first["memory_qualification_enabled"] is False

    for repetition_id in first["repetition_ids"]:
        result = json.loads((
            tmp_path / "b012" / "repetitions" / repetition_id / "summary.json"
        ).read_text(encoding="utf-8"))
        assert result["direction_consistent"] is True
        assert result["promotion_counts"] == [0, 1, 1]
        assert len(result["residual_designs"]) >= 2
        assert result["rollback"]["restored_to_base"] is True
        assert result["rollback"]["exact_parent_snapshot"] is True
        assert result["b1_policy_hash"] != result["parent_policy_hash"]
        assert result["b2_policy_hash"] != result["b1_policy_hash"]
        assert set(result["residual_designs"]).isdisjoint(
            {f"b012_{repetition_id}_non_target", f"b012_{repetition_id}_held_out"}
        )

    resumed = run_b0_b1_b2_pilot(
        project_root=ROOT,
        workspace=tmp_path / "b012",
    )
    assert resumed == first


def test_b0_b1_b2_config_and_split_contract_are_fail_closed(tmp_path):
    config = load_b012_config(
        ROOT / "configs/evolution/b0_b1_b2_pilot_v1.json",
        project_root=ROOT,
    )
    assert config["config_hash"].startswith("sha256:")
    with pytest.raises(B012PilotViolation):
        load_b012_config(
            tmp_path / "missing-config.json",
            project_root=ROOT,
        )

    run_b0_b1_b2_pilot(
        project_root=ROOT,
        workspace=tmp_path / "tamper",
    )
    held_out = (
        tmp_path / "tamper" / "repetitions" / "rep_000"
        / "splits" / "held_out_manifest.json"
    )
    payload = json.loads(held_out.read_text(encoding="utf-8"))
    payload["rows"][0]["design"] = "fake_design_0"
    held_out.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ManifestViolation):
        run_b0_b1_b2_pilot(
            project_root=ROOT,
            workspace=tmp_path / "tamper",
        )
