"""Same-family lineage validation."""
from __future__ import annotations

from typing import Any


LINEAGE_OPERATORS = {"fresh", "deepen", "relocate", "temporalize", "compose", "counterexample_revise"}


def bind_lineage(
    poison: dict[str, Any],
    *,
    parent: dict[str, Any] | None,
    operator: str,
) -> dict[str, Any]:
    if operator not in LINEAGE_OPERATORS:
        raise ValueError(f"unsupported lineage operator: {operator}")
    result = dict(poison)
    if parent is None:
        if operator != "fresh":
            raise ValueError("non-fresh lineage requires a parent poison")
        result.update({
            "parent_poison_id": "",
            "parent_challenged_policy_hash": "",
            "lineage_depth": 0,
            "evolution_operator": "fresh",
        })
        return result
    if operator != "compose" and parent.get("family") != poison.get("family"):
        raise ValueError("lineage deepening must preserve family")
    result.update({
        "parent_poison_id": str(parent.get("poison_id") or ""),
        "parent_challenged_policy_hash": str(
            parent.get("challenged_policy_hash") or ""
        ),
        "lineage_depth": int(parent.get("lineage_depth") or 0) + 1,
        "evolution_operator": operator,
    })
    return result


def validate_lineage_graph(rows: list[dict[str, Any]]) -> None:
    """Reject duplicate ids, missing parents, and poison-lineage cycles."""
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        poison_id = str(row.get("poison_id") or "")
        if not poison_id:
            raise ValueError("lineage row is missing poison_id")
        if poison_id in by_id:
            raise ValueError(f"duplicate poison_id in lineage: {poison_id}")
        by_id[poison_id] = row
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(poison_id: str) -> None:
        if poison_id in visiting:
            raise ValueError(f"poison lineage cycle detected at {poison_id}")
        if poison_id in visited:
            return
        visiting.add(poison_id)
        parent_id = str(by_id[poison_id].get("parent_poison_id") or "")
        if parent_id:
            if parent_id not in by_id:
                raise ValueError(f"lineage parent is missing: {parent_id}")
            visit(parent_id)
        visiting.remove(poison_id)
        visited.add(poison_id)

    for poison_id in by_id:
        visit(poison_id)
