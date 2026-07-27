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
RED_SEARCH_CONTEXT_FIELDS = {
    "poison_id",
    "archive_kind",
    "challenged_policy_hash",
    "archive_cell",
    "family",
    "effect",
    "affected_role",
    "failure_signature",
    "hardness_class",
    "hardness",
    "learnability_label",
    "parent_poison_id",
    "parent_challenged_policy_hash",
    "lineage_depth",
    "evolution_operator",
    "composition_depth",
    "sequential_depth",
    "dependency_depth",
}


class CapabilityPacketViolation(RuntimeError):
    """Raised when hidden promotion information would leak to red search."""


def build_red_search_context(
    policy: PolicyState,
    *,
    residual_archive: list[dict[str, Any]],
    covered_archive: list[dict[str, Any]],
) -> dict[str, Any]:
    """Expose only a hash-bound archive summary to conditioned red search."""
    summaries = []
    for kind, rows in (
        ("residual", residual_archive),
        ("covered", covered_archive),
    ):
        for row in rows:
            leaked = FORBIDDEN_INPUT_KEYS & row.keys()
            if leaked:
                raise CapabilityPacketViolation(
                    f"hidden fields supplied by archive: {sorted(leaked)}"
                )
            learnability = row.get("learnability")
            label = (
                str(learnability.get("label") or "")
                if isinstance(learnability, dict)
                else str(learnability or "")
            )
            summary = {
                "poison_id": str(row.get("poison_id") or ""),
                "archive_kind": kind,
                "challenged_policy_hash": str(
                    row.get("challenged_policy_hash") or ""
                ),
                "archive_cell": str(row.get("archive_cell") or ""),
                "family": str(row.get("family") or ""),
                "effect": str(row.get("effect") or ""),
                "affected_role": str(row.get("affected_role") or ""),
                "failure_signature": str(row.get("failure_signature") or ""),
                "hardness_class": str(row.get("hardness_class") or ""),
                "hardness": float(row.get("hardness") or 0.0),
                "learnability_label": label,
                "parent_poison_id": str(row.get("parent_poison_id") or ""),
                "parent_challenged_policy_hash": str(
                    row.get("parent_challenged_policy_hash") or ""
                ),
                "lineage_depth": int(row.get("lineage_depth") or 0),
                "evolution_operator": str(
                    row.get("evolution_operator") or "fresh"
                ),
                "composition_depth": int(row.get("composition_depth") or 1),
                "sequential_depth": int(row.get("sequential_depth") or 0),
                "dependency_depth": int(row.get("dependency_depth") or 0),
            }
            if set(summary) != RED_SEARCH_CONTEXT_FIELDS:
                raise CapabilityPacketViolation("red archive summary schema mismatch")
            summaries.append(summary)
    summaries.sort(key=lambda row: (
        row["archive_kind"],
        row["challenged_policy_hash"],
        row["archive_cell"],
        row["poison_id"],
    ))
    context = {
        "schema_version": "r3e-red-search-context-v2",
        "challenged_policy_id": policy.policy_id,
        "challenged_policy_hash": policy.policy_hash,
        "archive_summary": summaries,
        "residual_count": sum(
            row["archive_kind"] == "residual" for row in summaries
        ),
        "covered_count": sum(
            row["archive_kind"] == "covered" for row in summaries
        ),
    }
    context["context_hash"] = hash_payload(context)
    return context


def verify_red_search_context(context: dict[str, Any]) -> dict[str, Any]:
    if context.get("schema_version") != "r3e-red-search-context-v2":
        raise CapabilityPacketViolation("red search context schema mismatch")
    summaries = context.get("archive_summary")
    if not isinstance(summaries, list):
        raise CapabilityPacketViolation("red search archive summary missing")
    if any(set(row) != RED_SEARCH_CONTEXT_FIELDS for row in summaries):
        raise CapabilityPacketViolation("red search context contains undeclared fields")
    if any(
        row.get("archive_kind") not in {"residual", "covered"}
        or not row.get("poison_id")
        or not row.get("challenged_policy_hash")
        for row in summaries
    ):
        raise CapabilityPacketViolation("red search context summary is malformed")
    canonical = sorted(summaries, key=lambda row: (
        row["archive_kind"],
        row["challenged_policy_hash"],
        row["archive_cell"],
        row["poison_id"],
    ))
    if summaries != canonical:
        raise CapabilityPacketViolation("red search context is not canonical")
    if context.get("residual_count") != sum(
        row["archive_kind"] == "residual" for row in summaries
    ):
        raise CapabilityPacketViolation("red search residual count mismatch")
    if context.get("covered_count") != sum(
        row["archive_kind"] == "covered" for row in summaries
    ):
        raise CapabilityPacketViolation("red search covered count mismatch")
    body = {key: value for key, value in context.items() if key != "context_hash"}
    if context.get("context_hash") != hash_payload(body):
        raise CapabilityPacketViolation("red search context hash mismatch")
    return context


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
