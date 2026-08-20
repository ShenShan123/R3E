"""Sanitized blue capability packets exposed to red search."""
from __future__ import annotations

from collections import Counter
from typing import Any

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload
from r3e.red.portfolio_challenge import (
    PortfolioChallengeViolation,
    verify_portfolio_coverage_packet,
)


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
    memory_capability: dict[str, Any] | None = None,
    portfolio_capability: dict[str, Any] | None = None,
    grounded_proposal_plan: dict[str, Any] | None = None,
    grounded_population_schedule: dict[str, Any] | None = None,
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
    if grounded_population_schedule is not None:
        schema_version = "r3e-red-search-context-v7"
    elif grounded_proposal_plan is not None:
        schema_version = "r3e-red-search-context-v6"
    elif memory_capability is not None and portfolio_capability is not None:
        schema_version = "r3e-red-search-context-v5"
    elif portfolio_capability is not None:
        schema_version = "r3e-red-search-context-v4"
    elif memory_capability is not None:
        schema_version = "r3e-red-search-context-v3"
    else:
        schema_version = "r3e-red-search-context-v2"
    context = {
        "schema_version": schema_version,
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
    if memory_capability is not None:
        leaked = FORBIDDEN_INPUT_KEYS & memory_capability.keys()
        if leaked:
            raise CapabilityPacketViolation(
                f"hidden memory fields supplied to red search: {sorted(leaked)}"
            )
        if memory_capability.get("challenged_policy_hash") != policy.policy_hash:
            raise CapabilityPacketViolation(
                "memory capability is not bound to challenged policy"
            )
        context["memory_capability"] = memory_capability
    if portfolio_capability is not None:
        try:
            context["portfolio_capability"] = (
                verify_portfolio_coverage_packet(
                    portfolio_capability, policy=policy
                )
            )
        except PortfolioChallengeViolation as exc:
            raise CapabilityPacketViolation(
                "portfolio capability is invalid"
            ) from exc
    if grounded_proposal_plan is not None:
        plan = dict(grounded_proposal_plan)
        if (
            plan.get("schema_version")
            != "r3e-grounded-proposal-authority-v1"
            or plan.get("challenged_policy_hash")
            != policy.policy_hash
            or plan.get("challenged_effective_policy_hash")
            != policy.effective_policy_hash
            or plan.get("plan_hash") != hash_payload({
                key: value for key, value in plan.items()
                if key != "plan_hash"
            })
        ):
            raise CapabilityPacketViolation(
                "grounded proposal plan is invalid"
            )
        context["grounded_proposal_plan"] = plan
    if grounded_population_schedule is not None:
        schedule = dict(grounded_population_schedule)
        if (
            grounded_proposal_plan is None
            or schedule.get("schema_version")
            != "r3e-red-population-schedule-v1"
            or schedule.get("challenged_policy_hash")
            != policy.policy_hash
            or schedule.get("proposal_plan_hash")
            != grounded_proposal_plan["plan_hash"]
            or schedule.get("schedule_hash") != hash_payload({
                key: value for key, value in schedule.items()
                if key != "schedule_hash"
            })
        ):
            raise CapabilityPacketViolation(
                "grounded population schedule is invalid"
            )
        context["grounded_population_schedule"] = schedule
    context["context_hash"] = hash_payload(context)
    return context


def verify_red_search_context(context: dict[str, Any]) -> dict[str, Any]:
    schema = context.get("schema_version")
    if schema not in {
        "r3e-red-search-context-v2",
        "r3e-red-search-context-v3",
        "r3e-red-search-context-v4",
        "r3e-red-search-context-v5",
        "r3e-red-search-context-v6",
        "r3e-red-search-context-v7",
    }:
        raise CapabilityPacketViolation("red search context schema mismatch")
    required_fields = {
        "schema_version",
        "challenged_policy_id",
        "challenged_policy_hash",
        "archive_summary",
        "residual_count",
        "covered_count",
        "context_hash",
    }
    has_memory = "memory_capability" in context
    has_portfolio = "portfolio_capability" in context
    has_proposal = "grounded_proposal_plan" in context
    has_population = "grounded_population_schedule" in context
    if schema in {
        "r3e-red-search-context-v3",
        "r3e-red-search-context-v5",
    } or (
        schema in {"r3e-red-search-context-v6", "r3e-red-search-context-v7"}
        and has_memory
    ):
        required_fields.add("memory_capability")
    if schema in {
        "r3e-red-search-context-v4",
        "r3e-red-search-context-v5",
    } or (
        schema in {"r3e-red-search-context-v6", "r3e-red-search-context-v7"}
        and has_portfolio
    ):
        required_fields.add("portfolio_capability")
    if schema in {"r3e-red-search-context-v6", "r3e-red-search-context-v7"}:
        required_fields.add("grounded_proposal_plan")
    if schema == "r3e-red-search-context-v7":
        required_fields.add("grounded_population_schedule")
    if set(context) != required_fields:
        raise CapabilityPacketViolation(
            "red search context fields mismatch"
        )
    if schema in {
        "r3e-red-search-context-v3",
        "r3e-red-search-context-v5",
    } or (
        schema in {"r3e-red-search-context-v6", "r3e-red-search-context-v7"}
        and has_memory
    ):
        memory_capability = context.get("memory_capability")
        if (
            not isinstance(memory_capability, dict)
            or memory_capability.get("challenged_policy_hash")
            != context.get("challenged_policy_hash")
        ):
            raise CapabilityPacketViolation(
                "red search memory capability binding mismatch"
            )
    elif has_memory:
        raise CapabilityPacketViolation("v2 red search cannot carry memory capability")
    if schema in {
        "r3e-red-search-context-v4",
        "r3e-red-search-context-v5",
    } or (
        schema in {"r3e-red-search-context-v6", "r3e-red-search-context-v7"}
        and has_portfolio
    ):
        portfolio_capability = context.get("portfolio_capability")
        if (
            not isinstance(portfolio_capability, dict)
            or portfolio_capability.get("challenged_policy_hash")
            != context.get("challenged_policy_hash")
        ):
            raise CapabilityPacketViolation(
                "red search portfolio capability binding mismatch"
            )
        packet_body = {
            key: value
            for key, value in portfolio_capability.items()
            if key != "packet_hash"
        }
        if portfolio_capability.get("packet_hash") != hash_payload(
            packet_body
        ):
            raise CapabilityPacketViolation(
                "red search portfolio capability hash mismatch"
            )
    elif has_portfolio:
        raise CapabilityPacketViolation(
            "red search context cannot carry portfolio capability"
        )
    if schema in {"r3e-red-search-context-v6", "r3e-red-search-context-v7"}:
        proposal = context.get("grounded_proposal_plan")
        if (
            not isinstance(proposal, dict)
            or proposal.get("challenged_policy_hash")
            != context.get("challenged_policy_hash")
            or proposal.get("plan_hash") != hash_payload({
                key: value for key, value in proposal.items()
                if key != "plan_hash"
            })
        ):
            raise CapabilityPacketViolation(
                "red search proposal plan binding mismatch"
            )
    elif has_proposal:
        raise CapabilityPacketViolation(
            "red search context cannot carry proposal authority"
        )
    if schema == "r3e-red-search-context-v7":
        population = context.get("grounded_population_schedule")
        proposal = context["grounded_proposal_plan"]
        if (
            not isinstance(population, dict)
            or population.get("challenged_policy_hash")
            != context.get("challenged_policy_hash")
            or population.get("proposal_plan_hash")
            != proposal.get("plan_hash")
            or population.get("schedule_hash") != hash_payload({
                key: value for key, value in population.items()
                if key != "schedule_hash"
            })
        ):
            raise CapabilityPacketViolation(
                "red search population schedule binding mismatch"
            )
    elif has_population:
        raise CapabilityPacketViolation(
            "red search context cannot carry population authority"
        )
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
