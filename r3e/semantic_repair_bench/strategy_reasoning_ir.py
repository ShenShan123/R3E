"""Strict reasoning-only IR for automatic strategy distillation.

The LLM may select one action family from a frozen catalog. It may not name
RTL locations, signals, replacement text, or executable edits. Only the
deterministic strategy compiler/executor has patch authority.
"""
from __future__ import annotations

from typing import Any


SCHEMA = "r3e-strategy-reasoning-ir-v1"
_REQUIRED = {
    "schema_version", "intent_type", "bug_family", "primitive",
    "search_order", "stop_condition",
}
_CATALOG = {
    "off_by_one": {
        "primitive": "index_range_bound_pm1",
        "search_order": "structural_mismatch_then_source_order",
        "stop_condition": "first_formal_equivalent",
    },
}


class StrategyReasoningViolation(ValueError):
    """Raised when a planner tries to exceed reasoning-only authority."""


def catalog_for_family(family: str) -> dict:
    if family not in _CATALOG:
        raise StrategyReasoningViolation(f"no frozen primitive catalog entry for: {family}")
    return dict(_CATALOG[family])


def validate_strategy_ir(value: Any, trigger: dict) -> dict:
    if not isinstance(value, dict):
        raise StrategyReasoningViolation("strategy reasoning output must be a JSON object")
    if set(value) != _REQUIRED:
        raise StrategyReasoningViolation(
            f"strategy reasoning fields mismatch: expected={sorted(_REQUIRED)} actual={sorted(value)}"
        )
    family = str(trigger.get("bug_type", ""))
    expected = catalog_for_family(family)
    normalized = {
        "schema_version": str(value.get("schema_version", "")),
        "intent_type": str(value.get("intent_type", "")),
        "bug_family": str(value.get("bug_family", "")),
        "primitive": str(value.get("primitive", "")),
        "search_order": str(value.get("search_order", "")),
        "stop_condition": str(value.get("stop_condition", "")),
    }
    if normalized["schema_version"] != SCHEMA:
        raise StrategyReasoningViolation("unexpected strategy reasoning schema")
    if normalized["intent_type"] != "select_repair_primitive":
        raise StrategyReasoningViolation("LLM authority is limited to primitive selection")
    if normalized["bug_family"] != family:
        raise StrategyReasoningViolation("reasoning family does not match canonical trace group")
    for field, expected_value in expected.items():
        if normalized[field] != expected_value:
            raise StrategyReasoningViolation(
                f"unapproved {field}: {normalized[field]} (expected {expected_value})"
            )
    return normalized
