from __future__ import annotations

import json
from pathlib import Path

from r3e.protocol.hashing import hash_file, hash_payload


ROOT = Path(__file__).resolve().parents[1]
MILESTONE = ROOT / "configs/evolution/shadow_pilot_matrix_v1.json"


def test_shadow_pilot_milestone_is_hash_frozen_and_privacy_bounded():
    payload = json.loads(MILESTONE.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "r3e-shadow-pilot-matrix-milestone-v1"
    assert payload["status"] == "frozen"
    expected = hash_payload({
        key: value for key, value in payload.items()
        if key != "milestone_hash"
    })
    assert payload["milestone_hash"] == expected
    for relative, digest in payload["frozen_assets"].items():
        assert not relative.startswith(("runtime/", "docs/"))
        assert hash_file(ROOT / relative) == digest
    assert "real-provider ACP-7 gain" in payload["claim_boundary"]
    assert "promotion evidence" in payload["claim_boundary"]


def test_shadow_matrix_is_not_a_promotion_or_raam_execution_config():
    matrix = json.loads((
        ROOT / "configs/pilot/shadow_pilot_matrix_v1.json"
    ).read_text(encoding="utf-8"))
    assert matrix["execution_mode"] == "shadow_no_promotion"
    assert matrix["controls"]["promotion_enabled"] is False
    assert matrix["controls"]["raam_execution_enabled"] is False
    assert matrix["red_shadow"]["promotion_enabled"] is False
    assert matrix["red_shadow"]["memory_qualification_enabled"] is False
