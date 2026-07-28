"""Hash-bound coverage matrix and deterministic uncovered-cell planner."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import (
    atomic_write_json,
    hash_payload,
    read_json,
)


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


PLANNER_SCHEMA_VERSION = "r3e-grounded-coverage-plan-v1"


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


def load_coverage_state(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    return (
        verify_coverage_state(read_json(source))
        if source.is_file()
        else freeze_coverage_state([])
    )


def save_coverage_state(
    path: str | Path,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    verified = verify_coverage_state(state)
    atomic_write_json(path, verified)
    return verified


def candidate_coverage_cell(
    poison: Mapping[str, Any],
    *,
    policy: PolicyState,
) -> dict[str, Any]:
    plan = dict(poison.get("grounded_mutation_plan") or {})
    difficulty = dict(plan.get("difficulty_target") or {})
    family_id = str(
        plan.get("family_id") or poison.get("family") or ""
    )
    design_id = str(
        plan.get("target_design")
        or poison.get("design")
        or poison.get("case_id")
        or ""
    )
    rtl_role = str(
        poison.get("affected_role")
        or poison.get("edit_scope")
        or "unknown"
    )
    sequential = (
        int(poison.get("sequential_depth") or 0) > 0
        or family_id.startswith(("sequential.", "temporal."))
    )
    cell = {
        "family_id": family_id,
        "design_id": design_id,
        "rtl_role": rtl_role,
        "temporal_context": (
            "sequential" if sequential else "combinational"
        ),
        "difficulty_band": str(
            difficulty.get("difficulty_band") or "D0"
        ),
        "effective_blue_policy_hash": (
            policy.effective_policy_hash
        ),
        "effective_memory_bank_hash": str(
            (policy.memory_binding or {}).get(
                "effective_memory_bank_hash"
            )
            or ""
        ),
        "proposals": 0,
        "admitted": 0,
        "covered": 0,
        "residual": 0,
        "rejected": 0,
        "last_round_id": "",
    }
    # Reuse the strict state verifier for cell identity and counts.
    freeze_coverage_state([cell])
    return cell


def _curriculum_band(
    family_id: str,
    state: Mapping[str, Any],
    *,
    maximum_band: str,
) -> str:
    bands = ["D0", "D1", "D2", "D3"]
    if maximum_band not in bands:
        raise CoverageStateViolation(
            "curriculum maximum band must be D0-D3"
        )
    family_rows = [
        row for row in state["cells"]
        if row["family_id"] == family_id
    ]
    if not family_rows:
        return "D0"
    latest = max(
        family_rows,
        key=lambda row: (
            bands.index(row["difficulty_band"])
            if row["difficulty_band"] in bands else -1,
            row["last_round_id"],
        ),
    )
    current = (
        latest["difficulty_band"]
        if latest["difficulty_band"] in bands
        else "D0"
    )
    admitted = sum(int(row["admitted"]) for row in family_rows)
    covered = sum(int(row["covered"]) for row in family_rows)
    proposals = sum(int(row["proposals"]) for row in family_rows)
    rejected = sum(int(row["rejected"]) for row in family_rows)
    index = bands.index(current)
    maximum = bands.index(maximum_band)
    if admitted and covered / admitted >= 2 / 3:
        index = min(index + 1, maximum)
    elif proposals and rejected / proposals >= 1 / 2:
        index = max(index - 1, 0)
    return bands[index]


def _construct_coverage_plan(
    candidates: Iterable[Mapping[str, Any]],
    *,
    coverage_state: Mapping[str, Any],
    policy: PolicyState,
    budget: int,
    family_quota: int,
    maximum_difficulty_band: str = "D3",
) -> dict[str, Any]:
    state = verify_coverage_state(coverage_state)
    rows = [deepcopy(dict(row)) for row in candidates]
    if budget < 1 or family_quota < 1:
        raise CoverageStateViolation(
            "coverage budget and family quota must be positive"
        )
    entries = []
    seen_ids = set()
    unique_cells: dict[tuple[str, ...], dict[str, Any]] = {}
    duplicate_ids = []
    desired_by_family = {}
    for poison in sorted(
        rows, key=lambda row: str(row.get("poison_id") or "")
    ):
        poison_id = str(poison.get("poison_id") or "")
        if not poison_id or poison_id in seen_ids:
            raise CoverageStateViolation(
                "coverage candidates require unique poison ids"
            )
        seen_ids.add(poison_id)
        cell = candidate_coverage_cell(poison, policy=policy)
        desired = desired_by_family.setdefault(
            cell["family_id"],
            _curriculum_band(
                cell["family_id"],
                state,
                maximum_band=maximum_difficulty_band,
            ),
        )
        entry = {
            "poison_id": poison_id,
            "cell": cell,
            "desired_difficulty_band": desired,
        }
        entries.append(entry)
        key = coverage_cell_key(cell)
        if key in unique_cells:
            duplicate_ids.append(poison_id)
        else:
            unique_cells[key] = entry
    historical = {
        coverage_cell_key(row): row for row in state["cells"]
    }
    ordered = sorted(
        unique_cells.values(),
        key=lambda entry: (
            entry["cell"]["difficulty_band"]
            != entry["desired_difficulty_band"],
            int(
                historical.get(
                    coverage_cell_key(entry["cell"]), {}
                ).get("admitted", 0)
            ),
            entry["cell"]["family_id"],
            entry["poison_id"],
        ),
    )
    selected = []
    family_counts: dict[str, int] = {}
    for entry in ordered:
        family = entry["cell"]["family_id"]
        if family_counts.get(family, 0) >= family_quota:
            continue
        selected.append(entry["poison_id"])
        family_counts[family] = family_counts.get(family, 0) + 1
        if len(selected) >= budget:
            break
    selected_set = set(selected)
    payload = {
        "schema_version": PLANNER_SCHEMA_VERSION,
        "challenged_policy_hash": policy.policy_hash,
        "challenged_effective_policy_hash": (
            policy.effective_policy_hash
        ),
        "coverage_state_hash_before": state["coverage_hash"],
        "budget": int(budget),
        "family_quota": int(family_quota),
        "maximum_difficulty_band": maximum_difficulty_band,
        "candidate_entries": entries,
        "selected_poison_ids": selected,
        "deferred_poison_ids": sorted(
            seen_ids - selected_set
        ),
        "duplicate_cell_poison_ids": sorted(duplicate_ids),
        "curriculum_by_family": dict(
            sorted(desired_by_family.items())
        ),
    }
    payload["plan_hash"] = hash_payload(payload)
    return payload


def build_coverage_plan(
    candidates: Iterable[Mapping[str, Any]],
    *,
    coverage_state: Mapping[str, Any],
    policy: PolicyState,
    budget: int,
    family_quota: int,
    maximum_difficulty_band: str = "D3",
) -> dict[str, Any]:
    rows = [deepcopy(dict(row)) for row in candidates]
    payload = _construct_coverage_plan(
        rows,
        coverage_state=coverage_state,
        policy=policy,
        budget=budget,
        family_quota=family_quota,
        maximum_difficulty_band=maximum_difficulty_band,
    )
    return verify_coverage_plan(
        payload,
        candidates=rows,
        coverage_state=coverage_state,
        policy=policy,
    )


def verify_coverage_plan(
    plan: Mapping[str, Any],
    *,
    candidates: Iterable[Mapping[str, Any]],
    coverage_state: Mapping[str, Any],
    policy: PolicyState,
) -> dict[str, Any]:
    payload = deepcopy(dict(plan))
    body = {
        key: value for key, value in payload.items()
        if key != "plan_hash"
    }
    if (
        payload.get("schema_version") != PLANNER_SCHEMA_VERSION
        or payload.get("plan_hash") != hash_payload(body)
        or payload.get("challenged_policy_hash") != policy.policy_hash
        or payload.get("challenged_effective_policy_hash")
        != policy.effective_policy_hash
        or payload.get("coverage_state_hash_before")
        != verify_coverage_state(coverage_state)["coverage_hash"]
    ):
        raise CoverageStateViolation(
            "coverage plan envelope or binding mismatch"
        )
    candidate_ids = {
        str(row.get("poison_id") or "") for row in candidates
    }
    entry_ids = [
        str(row.get("poison_id") or "")
        for row in payload.get("candidate_entries") or []
    ]
    selected = list(payload.get("selected_poison_ids") or [])
    deferred = list(payload.get("deferred_poison_ids") or [])
    if (
        set(entry_ids) != candidate_ids
        or len(entry_ids) != len(set(entry_ids))
        or set(selected) | set(deferred) != candidate_ids
        or set(selected) & set(deferred)
        or len(selected) > int(payload.get("budget") or 0)
    ):
        raise CoverageStateViolation(
            "coverage plan candidate partition mismatch"
        )
    rebuilt = _construct_coverage_plan(
        candidates,
        coverage_state=coverage_state,
        policy=policy,
        budget=int(payload["budget"]),
        family_quota=int(payload["family_quota"]),
        maximum_difficulty_band=str(
            payload["maximum_difficulty_band"]
        ),
    )
    if payload != rebuilt:
        raise CoverageStateViolation(
            "coverage plan cannot be deterministically reconstructed"
        )
    return payload


def update_coverage_state(
    coverage_state: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    validity_rows: Iterable[Mapping[str, Any]],
    challenge_rows: Iterable[Mapping[str, Any]],
    round_id: str,
) -> dict[str, Any]:
    state = verify_coverage_state(coverage_state)
    entries = {
        str(row["poison_id"]): deepcopy(dict(row["cell"]))
        for row in plan["candidate_entries"]
    }
    selected = set(plan["selected_poison_ids"])
    valid_by_id = {
        str(row.get("poison_id") or ""): bool(
            (row.get("validity") or {}).get("proven_valid")
        )
        for row in validity_rows
    }
    challenge_by_id = {
        str(row.get("poison_id") or ""): row
        for row in challenge_rows
    }
    if set(valid_by_id) != selected or not set(
        challenge_by_id
    ).issubset(selected):
        raise CoverageStateViolation(
            "coverage outcome rows do not match selected candidates"
        )
    cells = {
        coverage_cell_key(row): deepcopy(dict(row))
        for row in state["cells"]
    }
    for poison_id, proposed in entries.items():
        key = coverage_cell_key(proposed)
        row = cells.setdefault(key, proposed)
        row["proposals"] += 1
        if poison_id in selected:
            if valid_by_id[poison_id]:
                row["admitted"] += 1
                challenge = challenge_by_id.get(poison_id)
                if challenge is not None:
                    hardness = str(
                        challenge.get("hardness_class") or ""
                    )
                    if hardness in {
                        "hard_residual",
                        "borderline_residual",
                    }:
                        row["residual"] += 1
                    elif hardness in {"mostly_covered", "covered"}:
                        row["covered"] += 1
            else:
                row["rejected"] += 1
        row["last_round_id"] = str(round_id)
    return freeze_coverage_state(cells.values())
