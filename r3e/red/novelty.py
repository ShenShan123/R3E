"""MAP-Elites-style descriptors and novelty scoring."""
from __future__ import annotations

from typing import Any

from r3e.protocol.hashing import hash_payload


DESCRIPTOR_FIELDS = (
    "family",
    "effect",
    "affected_role",
    "edit_scope",
    "composition_depth",
    "sequential_depth",
    "first_divergence_signal",
    "first_divergence_cycle_bucket",
    "failure_signature",
    "normalized_diff_hash",
    "dataflow_cone_hash",
    "challenged_policy_hash",
)


def descriptor(poison: dict[str, Any]) -> dict[str, Any]:
    result = {field: poison.get(field) for field in DESCRIPTOR_FIELDS}
    required = ("family", "effect", "affected_role", "edit_scope", "challenged_policy_hash")
    missing = [field for field in required if not result.get(field)]
    if missing:
        raise ValueError(f"novelty descriptor missing: {missing}")
    result["composition_depth"] = int(result.get("composition_depth") or 1)
    result["sequential_depth"] = int(result.get("sequential_depth") or 0)
    result["descriptor_hash"] = hash_payload(result)
    return result


def archive_cell(poison: dict[str, Any]) -> str:
    item = descriptor(poison)
    return "|".join(str(item.get(field) or "unknown") for field in (
        "family",
        "effect",
        "affected_role",
        "first_divergence_cycle_bucket",
        "edit_scope",
    ))


def novelty_score(poison: dict[str, Any], archive_rows: list[dict[str, Any]]) -> float:
    item = descriptor(poison)
    if not archive_rows:
        return 1.0
    cell = archive_cell(poison)
    if all(row.get("archive_cell") != cell for row in archive_rows):
        return 1.0
    signatures = {
        row.get("failure_signature") for row in archive_rows
        if row.get("archive_cell") == cell
    }
    if item.get("failure_signature") not in signatures:
        return 0.6
    diffs = {
        row.get("normalized_diff_hash") for row in archive_rows
        if row.get("archive_cell") == cell
    }
    return 0.3 if item.get("normalized_diff_hash") not in diffs else 0.0
