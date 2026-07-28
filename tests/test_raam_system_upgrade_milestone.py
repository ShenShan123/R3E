from __future__ import annotations

import json
from pathlib import Path

from r3e.protocol.hashing import hash_file, hash_payload


ROOT = Path(__file__).resolve().parents[1]


def test_raam_system_upgrade_milestone_is_frozen_and_reconstructable():
    milestone = json.loads(
        (
            ROOT
            / "configs/evolution/raam_system_upgrade_complete_v1.json"
        ).read_text(encoding="utf-8")
    )
    assert (
        milestone["schema_version"]
        == "r3e-raam-system-upgrade-milestone-v1"
    )
    assert milestone["status"] == "frozen"
    assert milestone["milestone_hash"] == hash_payload({
        key: value for key, value in milestone.items()
        if key != "milestone_hash"
    })
    parent = json.loads(
        (
            ROOT / "configs/evolution/system_upgrade_complete_v1.json"
        ).read_text(encoding="utf-8")
    )
    assert (
        milestone["parent_milestone"]["milestone_hash"]
        == parent["milestone_hash"]
    )
    for relative, expected in milestone["frozen_assets"].items():
        assert hash_file(ROOT / relative) == expected
    assert set(milestone["phase_scope"]) == {
        f"phase-{number}-{name}"
        for number, name in (
            (1, "immutable-experience-archive"),
            (2, "control-memory-model"),
            (3, "runtime-failure-descriptor"),
            (4, "shadow-retrieval-reactivation"),
            (5, "memory-aware-execution-plan"),
            (6, "shadow-paired-qualification"),
            (7, "active-bank-whole-policy-promotion"),
            (8, "cross-round-compatibility"),
            (9, "red-memory-challenges"),
        )
    }
    assert "real_model_adapter" in milestone["deferred_experiment_bindings"]
