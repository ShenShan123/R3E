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
            "lineage_depth": 0,
            "evolution_operator": "fresh",
        })
        return result
    if parent.get("challenged_policy_hash") != poison.get("challenged_policy_hash"):
        raise ValueError("lineage parent must bind the same challenged policy")
    if operator != "compose" and parent.get("family") != poison.get("family"):
        raise ValueError("lineage deepening must preserve family")
    result.update({
        "parent_poison_id": str(parent.get("poison_id") or ""),
        "lineage_depth": int(parent.get("lineage_depth") or 0) + 1,
        "evolution_operator": operator,
    })
    return result
