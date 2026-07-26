"""Sanitized blue capability packets exposed to red search."""
from __future__ import annotations

from collections import Counter
from typing import Any

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload


ALLOWED_PACKET_KEYS = {
    "challenged_policy_id",
    "challenged_policy_hash",
    "design_id",
    "golden_rtl_hash",
    "allowed_mutation_operators",
    "covered_archive_cells",
    "recent_easy_variants",
    "current_failure_summary",
    "search_objective",
    "packet_hash",
}
FORBIDDEN_INPUT_KEYS = {
    "target_oracle_label",
    "reference_repair",
    "reference_patch",
    "child_validation",
    "target_replay_result",
}


class CapabilityPacketViolation(RuntimeError):
    """Raised when hidden promotion information would leak to red search."""


def build_capability_packet(
    policy: PolicyState,
    *,
    design_id: str,
    golden_rtl_hash: str,
    allowed_mutation_operators: list[str],
    archive_rows: list[dict[str, Any]],
    recent_challenges: list[dict[str, Any]],
    max_composition_depth: int = 2,
) -> dict[str, Any]:
    for row in recent_challenges:
        leaked = FORBIDDEN_INPUT_KEYS & row.keys()
        if leaked:
            raise CapabilityPacketViolation(f"hidden fields supplied to red packet: {sorted(leaked)}")
    covered = sorted({
        str(row.get("archive_cell") or "")
        for row in archive_rows
        if row.get("challenged_policy_hash") == policy.policy_hash
        and row.get("archive_cell")
    })
    easy = [
        str(row.get("variant_summary") or row.get("failure_signature") or "")
        for row in recent_challenges
        if int(row.get("repair_successes") or 0) >= int(row.get("repair_attempts") or 1)
    ]
    failures = [
        row for row in recent_challenges
        if row.get("challenged_policy_hash") in {None, "", policy.policy_hash}
    ]
    stages = [str(row.get("failure_stage") or "") for row in failures if row.get("failure_stage")]
    signals = [
        str(row.get("first_divergence_signal") or "")
        for row in failures if row.get("first_divergence_signal")
    ]
    cycles = [
        int(row["first_divergence_cycle"])
        for row in failures if row.get("first_divergence_cycle") is not None
    ]
    scopes = Counter(
        str(row.get("blue_patch_scope") or "unknown") for row in failures
    )
    packet = {
        "challenged_policy_id": policy.policy_id,
        "challenged_policy_hash": policy.policy_hash,
        "design_id": design_id,
        "golden_rtl_hash": golden_rtl_hash,
        "allowed_mutation_operators": list(allowed_mutation_operators),
        "covered_archive_cells": covered,
        "recent_easy_variants": [item for item in easy if item][-20:],
        "current_failure_summary": {
            "repair_attempts": sum(int(row.get("repair_attempts") or 0) for row in failures),
            "repair_successes": sum(int(row.get("repair_successes") or 0) for row in failures),
            "failure_stages": stages[-20:],
            "first_divergence_signals": signals[-20:],
            "first_divergence_cycles": cycles[-20:],
            "blue_patch_scope_histogram": dict(sorted(scopes.items())),
        },
        "search_objective": {
            "prefer_uncovered_effects": True,
            "prefer_uncovered_roles": True,
            "max_composition_depth": int(max_composition_depth),
        },
    }
    packet["packet_hash"] = hash_payload(packet)
    if set(packet) - ALLOWED_PACKET_KEYS:
        raise CapabilityPacketViolation("capability packet contains an undeclared field")
    return packet
