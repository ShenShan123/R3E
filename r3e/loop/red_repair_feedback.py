"""Capture public repair outputs without changing Blue requests or verdicts."""
from __future__ import annotations

from pathlib import Path
import json
import re

from r3e.protocol.hashing import atomic_write_json, hash_payload

REPAIR_ARTIFACT_VERSION = "public_candidate_rtl_v1"


class PublicCandidateRecorder:
    """Transparent simulator adapter for discovery and isolated seed validation.

    Save source, not evidence, hidden verdicts, prompts, or memory. Consumers
    additionally require a matching completed public attempt and carrier ID.
    """
    def __init__(self, simulator, root):
        self.simulator, self.root = simulator, Path(root)

    def __getattr__(self, name):
        return getattr(self.simulator, name)

    def verdict(self, rtl, carrier, **kwargs):
        verdict = self.simulator.verdict(rtl, carrier, **kwargs)
        identity = hash_payload(rtl)
        key = hash_payload({"candidate_hash": identity, "carrier_id": carrier.carrier_id})
        atomic_write_json(self.root / "public_repair_candidates" / f"{key[7:]}.json", {
            "version": REPAIR_ARTIFACT_VERSION, "candidate_hash": identity,
            "carrier_id": carrier.carrier_id, "rtl": rtl})
        return verdict


def read_public_candidate(root, carrier_id, candidate_hash):
    if not isinstance(candidate_hash, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", candidate_hash):
        return None
    key = hash_payload({"candidate_hash": candidate_hash, "carrier_id": carrier_id})
    path = Path(root) / "public_repair_candidates" / f"{key[7:]}.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (payload.get("version") != REPAIR_ARTIFACT_VERSION or payload.get("carrier_id") != carrier_id
            or payload.get("candidate_hash") != candidate_hash or not isinstance(payload.get("rtl"), str)
            or hash_payload(payload["rtl"]) != candidate_hash):
        raise ValueError("public repair candidate provenance mismatch")
    return payload["rtl"]
