from __future__ import annotations

import json
from pathlib import Path

from r3e.protocol.hashing import hash_file, hash_payload


ROOT = Path(__file__).resolve().parents[1]


def test_system_upgrade_milestone_is_frozen_and_reconstructable():
    path = ROOT / "configs/evolution/system_upgrade_complete_v1.json"
    milestone = json.loads(path.read_text(encoding="utf-8"))
    assert milestone["schema_version"] == "r3e-system-upgrade-milestone-v1"
    assert milestone["status"] == "frozen"
    assert milestone["milestone_hash"] == hash_payload({
        key: value for key, value in milestone.items()
        if key != "milestone_hash"
    })
    for relative, expected in milestone["frozen_assets"].items():
        assert hash_file(ROOT / relative) == expected
    assert "real_model_adapter" in milestone["deferred_experiment_bindings"]
    assert "no real-model" in milestone["claim_boundary"]
