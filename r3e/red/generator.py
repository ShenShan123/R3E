"""Compatibility entry for active-blue-conditioned poison generation."""
from __future__ import annotations

from typing import Any, Callable

from r3e.policy.schema import PolicyState

from .feedback_packet import build_capability_packet


def generate_poison(
    case: dict[str, Any],
    challenged_policy: PolicyState,
    blue_failure_summary: list[dict[str, Any]],
    archive_summary: list[dict[str, Any]],
    *,
    mutator: Callable[..., dict[str, Any]],
    parent_poison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    packet = build_capability_packet(
        challenged_policy,
        design_id=str(case["design_id"]),
        golden_rtl_hash=str(case["golden_rtl_hash"]),
        allowed_mutation_operators=list(case["allowed_mutation_operators"]),
        archive_rows=archive_summary,
        recent_challenges=blue_failure_summary,
    )
    poison = dict(
        mutator(
            case=case,
            capability_packet=packet,
            parent_poison=parent_poison,
        )
    )
    poison["challenged_policy_id"] = challenged_policy.policy_id
    poison["challenged_policy_hash"] = challenged_policy.policy_hash
    poison["capability_packet_hash"] = packet["packet_hash"]
    return poison
