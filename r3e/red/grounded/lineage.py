"""Hash-bound multi-parent Grounded Red lineage graphs."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from r3e.protocol.hashing import hash_payload


LINEAGE_SCHEMA_VERSION = "r3e-red-lineage-v1"
LINEAGE_RELATIONS = {
    "fresh",
    "derived_from",
    "deepens",
    "bypasses",
    "transfers",
    "composes_with",
    "minimized_from",
    "invalidates_memory",
}


class GroundedLineageViolation(RuntimeError):
    """Raised for missing, cyclic, or malformed poison ancestry."""


def build_lineage(
    *,
    poison_id: str,
    parent_poison_ids: list[str],
    lineage_operator: str,
    source_family_ids: list[str],
    target_family_ids: list[str],
    difficulty_delta: Mapping[str, int],
    semantic_diff_receipt_hash: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "poison_id": poison_id,
        "parent_poison_ids": list(parent_poison_ids),
        "lineage_operator": lineage_operator,
        "source_family_ids": list(source_family_ids),
        "target_family_ids": list(target_family_ids),
        "difficulty_delta": deepcopy(dict(difficulty_delta)),
        "semantic_diff_receipt_hash": semantic_diff_receipt_hash,
    }
    payload["lineage_hash"] = hash_payload(payload)
    return verify_lineage(payload)


def verify_lineage(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(value))
    required = {
        "schema_version",
        "poison_id",
        "parent_poison_ids",
        "lineage_operator",
        "source_family_ids",
        "target_family_ids",
        "difficulty_delta",
        "semantic_diff_receipt_hash",
        "lineage_hash",
    }
    if set(payload) != required:
        raise GroundedLineageViolation("grounded lineage fields mismatch")
    if payload["schema_version"] != LINEAGE_SCHEMA_VERSION:
        raise GroundedLineageViolation("grounded lineage schema mismatch")
    if payload["lineage_hash"] != hash_payload({
        key: value for key, value in payload.items() if key != "lineage_hash"
    }):
        raise GroundedLineageViolation("grounded lineage hash mismatch")
    if not isinstance(payload["poison_id"], str) or not payload["poison_id"]:
        raise GroundedLineageViolation("grounded lineage poison id is missing")
    parents = payload["parent_poison_ids"]
    if (
        not isinstance(parents, list)
        or len(parents) != len(set(parents))
        or any(not isinstance(item, str) or not item for item in parents)
    ):
        raise GroundedLineageViolation("grounded lineage parent ids are invalid")
    operator = payload["lineage_operator"]
    if operator not in LINEAGE_RELATIONS:
        raise GroundedLineageViolation("grounded lineage operator is unknown")
    if (operator == "fresh") != (not parents):
        raise GroundedLineageViolation("fresh lineage cannot have parents")
    if operator == "composes_with" and len(parents) != 2:
        raise GroundedLineageViolation("composition lineage requires two parents")
    if operator != "composes_with" and len(parents) > 1:
        raise GroundedLineageViolation("non-composition lineage has one parent")
    for field in ("source_family_ids", "target_family_ids"):
        values = payload[field]
        if (
            not isinstance(values, list)
            or not values
            or len(values) != len(set(values))
            or any(not isinstance(item, str) or not item for item in values)
        ):
            raise GroundedLineageViolation(f"{field} is invalid")
    delta = payload["difficulty_delta"]
    if not isinstance(delta, dict) or any(
        not isinstance(value, int) or isinstance(value, bool)
        for value in delta.values()
    ):
        raise GroundedLineageViolation("difficulty delta must be integral")
    return payload


def validate_lineage_graph(rows: list[Mapping[str, Any]]) -> None:
    verified = [verify_lineage(row) for row in rows]
    by_id = {row["poison_id"]: row for row in verified}
    if len(by_id) != len(verified):
        raise GroundedLineageViolation("duplicate poison id in lineage graph")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(poison_id: str) -> None:
        if poison_id in visiting:
            raise GroundedLineageViolation(
                f"grounded poison lineage cycle detected at {poison_id}"
            )
        if poison_id in visited:
            return
        visiting.add(poison_id)
        for parent_id in by_id[poison_id]["parent_poison_ids"]:
            if parent_id not in by_id:
                raise GroundedLineageViolation(
                    f"grounded lineage parent is missing: {parent_id}"
                )
            visit(parent_id)
        visiting.remove(poison_id)
        visited.add(poison_id)

    for poison_id in by_id:
        visit(poison_id)
