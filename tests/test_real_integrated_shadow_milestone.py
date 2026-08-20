from __future__ import annotations

import json
from pathlib import Path

from r3e.protocol.hashing import hash_file, hash_payload


ROOT = Path(__file__).resolve().parents[1]
MILESTONE = ROOT / "configs/evolution/real_integrated_coevolution_shadow_v1.json"


def test_real_integrated_shadow_milestone_is_frozen_and_claim_bounded():
    payload = json.loads(MILESTONE.read_text(encoding="utf-8"))
    assert payload["schema_version"] == (
        "r3e-real-integrated-coevolution-shadow-milestone-v1"
    )
    assert payload["status"] == "frozen"
    parent = json.loads((
        ROOT / "configs/evolution/shadow_pilot_matrix_v1.json"
    ).read_text(encoding="utf-8"))
    assert payload["parent_milestone"] == {
        "milestone_id": parent["milestone_id"],
        "milestone_hash": parent["milestone_hash"],
    }
    assert payload["milestone_hash"] == hash_payload({
        key: value for key, value in payload.items()
        if key != "milestone_hash"
    })
    for relative, digest in payload["frozen_assets"].items():
        assert not relative.startswith(("runtime/", "docs/"))
        assert hash_file(ROOT / relative) == digest
    assert payload["shadow_controls"] == {
        "policy_promotion_enabled": False,
        "memory_qualification_enabled": False,
        "provider_calls": (
            "injected-conformance-only-until-explicit-real-provider-authorization"
        ),
        "formal_authority": "runner_owned_grounded_execution",
        "blue_authority": "candidate_portfolio_v1",
    }
    assert "real-provider yield" in payload["claim_boundary"]
    assert "promotion evidence" in payload["claim_boundary"]
