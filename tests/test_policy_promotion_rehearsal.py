from __future__ import annotations

import json
from pathlib import Path

from r3e.pilot.policy_promotion_rehearsal import (
    run_policy_promotion_rehearsal,
)


ROOT = Path(__file__).resolve().parents[1]


def test_policy_only_rehearsal_promotes_once_and_resumes(tmp_path):
    first = run_policy_promotion_rehearsal(
        project_root=ROOT,
        workspace=tmp_path / "rehearsal",
    )
    assert first["round_ids"] == ["R000", "R001"]
    assert first["promotion_lane_schedule"] == ["collect", "policy"]
    assert first["promotion_count"] == 1
    assert first["policy_promotion_enabled"] is True
    assert first["memory_promotion_enabled"] is False
    assert first["memory_qualification_enabled"] is False
    assert len(first["residual_designs"]) >= 2
    assert len(first["target_designs"]) >= 1
    assert first["parent_policy_hash"] != first["child_policy_hash"]
    assert first["registry_hash_before"] != first["registry_hash_after"]

    non_target = (tmp_path / "rehearsal" / "non_target_manifest.json").read_text(
        encoding="utf-8"
    )
    assert "policy_rehearsal_non_target_design_0" in non_target

    resumed = run_policy_promotion_rehearsal(
        project_root=ROOT,
        workspace=tmp_path / "rehearsal",
    )
    assert resumed == first

    r1_summary = json.loads((
        tmp_path / "rehearsal" / "integrated/arena/R001/round_summary.json"
    ).read_text(encoding="utf-8"))
    assert r1_summary["promoted"] is True
    assert r1_summary["promotion_eligible"] is True
