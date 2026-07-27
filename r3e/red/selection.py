"""Deterministic MAP-Elites views and residual selection."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from .fitness import LEARNABILITY_SCORES
from .novelty import archive_cell


ELITE_ROLES = ("hardest", "minimal_edit", "most_learnable")
RESIDUAL_CLASSES = {"hard_residual", "borderline_residual"}
ADAPTATION_LABELS = {"reachable", "weakly_reachable"}


def normalized_edit_cost(row: dict[str, Any]) -> float:
    for key in ("normalized_edit_cost", "changed_lines", "edit_size"):
        value = row.get(key)
        if value is not None:
            return max(0.0, float(value))
    return float(
        int(row.get("changed_blocks") or 1)
        + int(row.get("composition_depth") or 1)
    )


def _learnability_label(row: dict[str, Any]) -> str:
    value = row.get("learnability")
    if isinstance(value, dict):
        return str(value.get("label") or "")
    return str(value or "")


def _stable_id(row: dict[str, Any]) -> str:
    return str(row.get("poison_id") or row.get("archive_entry_hash") or "")


def _winner(rows: list[dict[str, Any]], role: str) -> dict[str, Any]:
    if role == "hardest":
        key = lambda row: (
            float(row.get("hardness") or 0.0),
            float(row.get("novelty") or 0.0),
            -normalized_edit_cost(row),
            _stable_id(row),
        )
    elif role == "minimal_edit":
        key = lambda row: (
            -normalized_edit_cost(row),
            float(row.get("hardness") or 0.0),
            float(row.get("novelty") or 0.0),
            _stable_id(row),
        )
    elif role == "most_learnable":
        key = lambda row: (
            LEARNABILITY_SCORES.get(_learnability_label(row), 0.0),
            float(row.get("hardness") or 0.0),
            float(row.get("novelty") or 0.0),
            -normalized_edit_cost(row),
            _stable_id(row),
        )
    else:
        raise ValueError(f"unsupported elite role: {role}")
    return max(rows, key=key)


def materialize_elites(
    rows: Iterable[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Return current role winners for every policy-bound archive cell."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        cell = str(row.get("archive_cell") or archive_cell(row))
        policy_hash = str(row.get("challenged_policy_hash") or "")
        if not policy_hash:
            raise ValueError("archive row missing challenged policy hash")
        grouped[f"{policy_hash}::{cell}"].append(row)
    return {
        key: {role: _winner(cell_rows, role) for role in ELITE_ROLES}
        for key, cell_rows in sorted(grouped.items())
    }


def pareto_frontier(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep rows not dominated on hardness/novelty/learnability/edit cost."""
    values = list(rows)

    def objectives(row: dict[str, Any]) -> tuple[float, float, float, float]:
        return (
            float(row.get("hardness") or 0.0),
            float(row.get("novelty") or 0.0),
            LEARNABILITY_SCORES.get(_learnability_label(row), 0.0),
            -normalized_edit_cost(row),
        )

    result = []
    for index, row in enumerate(values):
        current = objectives(row)
        dominated = False
        for other_index, other in enumerate(values):
            if index == other_index:
                continue
            candidate = objectives(other)
            if all(left >= right for left, right in zip(candidate, current)) and any(
                left > right for left, right in zip(candidate, current)
            ):
                dominated = True
                break
        if not dominated:
            result.append(row)
    return sorted(result, key=_stable_id)


def select_residual_elites(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select the union of Pareto and three role elites within each cell."""
    eligible = [
        row for row in rows
        if row.get("hardness_class") in RESIDUAL_CLASSES
        and _learnability_label(row) in ADAPTATION_LABELS
    ]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        grouped[
            f"{row.get('challenged_policy_hash')}::"
            f"{row.get('archive_cell') or archive_cell(row)}"
        ].append(row)
    views = materialize_elites(eligible)
    selected: dict[str, dict[str, Any]] = {}
    for key, roles in views.items():
        for row in pareto_frontier(grouped[key]):
            selected[_stable_id(row)] = row
        for row in roles.values():
            selected[_stable_id(row)] = row
    return [selected[key] for key in sorted(selected)]
