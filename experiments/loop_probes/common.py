"""Shared helpers for the loop probes: carriers and Red bugs rebuilt from a run's records."""
from __future__ import annotations

import json
from pathlib import Path

from r3e.loop.carriers import DEFAULT_CARRIER_MANIFEST, REPO_ROOT, load_carrier_manifest
from r3e.loop.corpus import Challenge, load_public_manifest
from r3e.loop.operators import apply_edits, resolve_recorded_edits
from r3e.protocol.hashing import hash_payload


def all_carriers() -> dict:
    """The generated corpus plus the CirFix designs, by carrier id."""
    carriers = load_carrier_manifest(DEFAULT_CARRIER_MANIFEST)
    carriers += load_public_manifest(REPO_ROOT / "datasets/manifests/cirfix39.jsonl", REPO_ROOT)[0]
    return {c.carrier_id: c for c in carriers}


def red_rows(run_dir: Path) -> dict:
    """Admitted Red proposals of a run, by challenge id."""
    rows = (json.loads(line) for line in (Path(run_dir) / "red.jsonl").read_text().splitlines() if line.strip())
    return {r["challenge_id"]: r for r in rows if r.get("admitted")}


def rebuild(row: dict, carriers: dict) -> Challenge:
    """Re-create a Red bug from its recorded edits and check it against the recorded hash."""
    carrier = carriers[row["carrier_id"]]
    rtl = apply_edits(carrier, resolve_recorded_edits(carrier, row["chosen"], row.get("mutant_hash")))
    if hash_payload(rtl) != row["mutant_hash"]:
        raise ValueError(f"{row['challenge_id']}: rebuilt design does not match the recorded hash")
    return Challenge(challenge_id=row["challenge_id"], carrier=carrier, buggy_rtl=rtl, origin="red",
                     provenance={"ops": row["chosen"]["edit_kinds"]})
