"""Hash-bound coverage matrix and deterministic uncovered-cell planner."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from r3e.protocol.hashing import hash_payload


COVERAGE_SCHEMA_VERSION = "r3e-red-coverage-state-v1"
_CELL_FIELDS = {
    "family_id",
    "design_id",
    "rtl_role",
    "temporal_context",
    "difficulty_band",
    "effective_blue_policy_hash",
    "effective_memory_bank_hash",
    "proposals",
    "admitted",
    "covered",
    "residual",
    "rejected",
    "last_round_id",
}


class CoverageStateViolation(RuntimeError):
    """Raised when coverage state or a planner result is inconsistent."""


def coverage_cell_key(cell: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(cell.get(field) or "") for field in (
        "family_id",
        "design_id",
        "rtl_role",
        "temporal_context",
        "difficulty_band",
        "effective_blue_policy_hash",
        "effective_memory_bank_hash",
    ))


def freeze_coverage_state(cells: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    canonical = sorted(
        (deepcopy(dict(cell)) for cell in cells),
        key=coverage_cell_key,
    )
    payload = {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "cells": canonical,
    }
    payload["coverage_hash"] = hash_payload(payload)
    return verify_coverage_state(payload)


def verify_coverage_state(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(value))
    if set(payload) != {"schema_version", "cells", "coverage_hash"}:
        raise CoverageStateViolation("coverage state fields mismatch")
    if payload["schema_version"] != COVERAGE_SCHEMA_VERSION:
        raise CoverageStateViolation("coverage state schema mismatch")
    if payload["coverage_hash"] != hash_payload({
        "schema_version": payload["schema_version"],
        "cells": payload["cells"],
    }):
        raise CoverageStateViolation("coverage state hash mismatch")
    cells = payload["cells"]
    if not isinstance(cells, list):
        raise CoverageStateViolation("coverage cells must be a list")
    seen = set()
    for cell in cells:
        if not isinstance(cell, dict) or set(cell) != _CELL_FIELDS:
            raise CoverageStateViolation("coverage cell fields mismatch")
        key = coverage_cell_key(cell)
        if "" in key[:-1] or key in seen:
            raise CoverageStateViolation("coverage cell identity is invalid")
        seen.add(key)
        for field in ("proposals", "admitted", "covered", "residual", "rejected"):
            count = cell[field]
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise CoverageStateViolation("coverage counts must be non-negative")
        if cell["admitted"] > cell["proposals"]:
            raise CoverageStateViolation("admission count exceeds proposals")
        if cell["covered"] + cell["residual"] > cell["admitted"]:
            raise CoverageStateViolation("archive outcomes exceed admissions")
    if cells != sorted(cells, key=coverage_cell_key):
        raise CoverageStateViolation("coverage cells are not canonical")
    return payload


def select_coverage_targets(
    candidates: Iterable[Mapping[str, Any]],
    *,
    coverage_state: Mapping[str, Any],
    budget: int,
    family_quota: int,
) -> list[dict[str, Any]]:
    """Choose least-covered cells deterministically without family monopoly."""
    if budget < 1 or family_quota < 1:
        raise CoverageStateViolation("planner budget and quota must be positive")
    state = verify_coverage_state(coverage_state)
    counts = {
        coverage_cell_key(cell): cell
        for cell in state["cells"]
    }
    rows = [deepcopy(dict(row)) for row in candidates]
    identities = [coverage_cell_key(row) for row in rows]
    if any("" in key[:-1] for key in identities) or len(identities) != len(
        set(identities)
    ):
        raise CoverageStateViolation("planner candidates must be unique cells")
    family_counts = {
        family: sum(
            int(cell["admitted"])
            for cell in state["cells"]
            if cell["family_id"] == family
        )
        for family in {row["family_id"] for row in rows}
    }
    ordered = sorted(
        rows,
        key=lambda row: (
            int(counts.get(coverage_cell_key(row), {}).get("admitted", 0)),
            family_counts.get(str(row["family_id"]), 0),
            coverage_cell_key(row),
        ),
    )
    selected = []
    selected_by_family: dict[str, int] = {}
    for row in ordered:
        family = str(row["family_id"])
        if selected_by_family.get(family, 0) >= family_quota:
            continue
        selected.append(row)
        selected_by_family[family] = selected_by_family.get(family, 0) + 1
        if len(selected) == budget:
            break
    return selected
