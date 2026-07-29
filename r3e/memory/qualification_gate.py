"""Runner-owned qualification decision reconstructed from paired replay."""
from __future__ import annotations

from typing import Any, Iterable

from r3e.protocol.hashing import hash_payload

from .schema import ControlMemory, ShadowPairedResult
from r3e.blue.portfolio.portfolio_control import (
    PORTFOLIO_CONTROL_FIELDS,
    PortfolioControlViolation,
    PortfolioTemplateRegistry,
)
from .evidence import (
    evidence_link,
    freeze_evidence_set,
    verify_memory_evidence_set,
)


class MemoryQualificationViolation(RuntimeError):
    """Raised when qualification evidence is incomplete or mismatched."""


DEFAULT_THRESHOLDS = {
    "min_helped": 2,
    "min_designs": 2,
    "max_harmed": 0,
    "max_cost_ratio": 1.5,
}


def decide_memory_qualification(
    memory: ControlMemory,
    results: Iterable[ShadowPairedResult],
    *,
    thresholds: dict[str, Any] | None = None,
    provenance: dict[str, Any],
    evidence_set: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = list(results)
    if not rows:
        raise MemoryQualificationViolation("qualification requires replay evidence")
    if any(row.memory_hash != memory.memory_hash for row in rows):
        raise MemoryQualificationViolation("qualification memory hash mismatch")
    replay_policy_hashes = {row.policy_hash for row in rows}
    if len(replay_policy_hashes) != 1:
        raise MemoryQualificationViolation("qualification uses multiple policy hashes")
    replay_policy_hash = next(iter(replay_policy_hashes))
    replay_effective_policy_hash = str(
        provenance.get("effective_policy_hash") or ""
    )
    if (
        not replay_effective_policy_hash
        and replay_policy_hash == memory.created_under_policy_instance_hash
    ):
        replay_effective_policy_hash = (
            memory.created_under_effective_policy_hash
        )
    if not replay_effective_policy_hash:
        raise MemoryQualificationViolation(
            "qualification effective policy hash is missing"
        )
    if replay_effective_policy_hash != memory.created_under_effective_policy_hash:
        if (
            provenance.get("revalidation_from_effective_policy_hash")
            != memory.created_under_effective_policy_hash
        ):
            raise MemoryQualificationViolation(
                "cross-policy qualification lacks revalidation binding"
            )
    required_provenance = {
        "manifest_hash", "toolchain_fingerprint_hash", "code_commit_sha",
        "control_whitelist_hash",
    }
    if not required_provenance.issubset(provenance) or any(
        not provenance[field] for field in required_provenance
    ):
        raise MemoryQualificationViolation("qualification provenance is incomplete")
    limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    if evidence_set is None:
        evidence_set = freeze_evidence_set(
            memory,
            [
                evidence_link(
                    memory,
                    evidence_source=memory,
                    episode_id=episode_id,
                    episode_hash=memory.source_episode_hashes[episode_id],
                )
                for episode_id in sorted(memory.source_episode_ids)
            ],
        )
    try:
        frozen_evidence = verify_memory_evidence_set(
            evidence_set, memory=memory
        )
    except RuntimeError as exc:
        raise MemoryQualificationViolation(str(exc)) from exc
    evidence_set_hash = frozen_evidence["evidence_set_hash"]
    helped = sum(row.outcome == "helped" for row in rows)
    harmed = sum(row.outcome == "harmed" for row in rows)
    designs = {
        str(row.control["design"])
        for row in rows if row.outcome == "helped"
    }
    cost_ratios = []
    for row in rows:
        control_usage = row.control["resource_usage"]
        shadow_usage = row.shadow["resource_usage"]
        for control_cost, shadow_cost in (
            (
                float(control_usage["input_tokens"] + control_usage["output_tokens"]),
                float(shadow_usage["input_tokens"] + shadow_usage["output_tokens"]),
            ),
            (float(control_usage["llm_calls"]), float(shadow_usage["llm_calls"])),
            (
                float(control_usage["verifier_calls"]),
                float(shadow_usage["verifier_calls"]),
            ),
            (
                float(control_usage["wall_time_seconds"]),
                float(shadow_usage["wall_time_seconds"]),
            ),
        ):
            cost_ratios.append(
                shadow_cost / control_cost
                if control_cost > 0
                else (1.0 if shadow_cost == 0 else float("inf"))
            )
    gates = {
        "retrieval": any(bool(row.shadow.get("triggered")) for row in rows),
        "effect": helped >= int(limits["min_helped"])
        and len(designs) >= int(limits["min_designs"]),
        "safety": harmed <= int(limits["max_harmed"]),
        "cost": max(cost_ratios) <= float(limits["max_cost_ratio"]),
        "provenance": True,
    }
    decision = {
        "schema_version": "r3e-memory-qualification-decision-v1",
        "memory_id": memory.memory_id,
        "memory_version": memory.memory_version,
        "memory_hash": memory.memory_hash,
        "qualified": all(gates.values()),
        "gates": gates,
        "summary": {
            "helped": helped,
            "harmed": harmed,
            "covered_designs": len(designs),
            "max_cost_ratio": max(cost_ratios),
            "outcomes": dict(
                (name, sum(row.outcome == name for row in rows))
                for name in ("helped", "harmed", "neutral_pass", "neutral_fail")
            ),
        },
        "thresholds": limits,
        "paired_result_hash": hash_payload([row.to_dict() for row in rows]),
        "provenance": dict(provenance),
        "qualified_under_policy_hash": replay_policy_hash,
        "qualified_under_policy_instance_hash": replay_policy_hash,
        "qualified_under_effective_policy_hash": replay_effective_policy_hash,
        "evidence_set_hash": evidence_set_hash,
        "support_count": int(frozen_evidence.get("support_count") or 0),
    }
    decision["decision_hash"] = hash_payload(decision)
    return decision


def decide_portfolio_memory_qualification(
    memory: ControlMemory,
    results: Iterable[ShadowPairedResult],
    *,
    policy: Any,
    portfolio_template_registry: PortfolioTemplateRegistry,
    thresholds: dict[str, Any] | None = None,
    provenance: dict[str, Any],
    evidence_set: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Qualify bounded portfolio control with explicit safety partitions."""
    rows = list(results)
    portfolio_delta = {
        key: value
        for key, value in memory.control_delta.items()
        if key in PORTFOLIO_CONTROL_FIELDS
    }
    if set(portfolio_delta) != PORTFOLIO_CONTROL_FIELDS:
        raise MemoryQualificationViolation(
            "portfolio qualification requires complete bounded control"
        )
    try:
        materialized = portfolio_template_registry.materialize(
            portfolio_delta, policy=policy
        )
    except PortfolioControlViolation as exc:
        raise MemoryQualificationViolation(
            "portfolio qualification lacks policy authority"
        ) from exc
    decision = decide_memory_qualification(
        memory,
        rows,
        thresholds=thresholds,
        provenance=provenance,
        evidence_set=evidence_set,
    )
    adaptation = [
        row for row in rows
        if row.shadow.get("qualification_split") == "adaptation"
    ]
    non_target = [
        row for row in rows
        if row.shadow.get("qualification_split") == "non_target"
    ]
    false_activation = [
        row for row in rows
        if row.shadow.get("qualification_split") == "false_activation"
    ]
    portfolio_gates = {
        "adaptation_repair_gain": bool(adaptation)
        and any(row.outcome == "helped" for row in adaptation),
        "non_target_safety": bool(non_target)
        and all(row.outcome != "harmed" for row in non_target),
        "false_activation_safety": bool(false_activation)
        and all(
            row.outcome != "harmed"
            and row.shadow.get("triggered") is False
            for row in false_activation
        ),
        "template_authority": True,
    }
    decision["schema_version"] = (
        "r3e-portfolio-memory-qualification-decision-v1"
    )
    decision["gates"].update(portfolio_gates)
    decision["qualified"] = all(decision["gates"].values())
    decision["portfolio_control"] = {
        "portfolio_template_registry_hash": (
            portfolio_template_registry.registry_hash
        ),
        "portfolio_template_hash": materialized["portfolio_template_hash"],
        "candidate_portfolio_template_id": materialized[
            "candidate_portfolio_template_id"
        ],
        "adaptation_rows": len(adaptation),
        "non_target_rows": len(non_target),
        "false_activation_rows": len(false_activation),
        "registry_asset_path": (
            portfolio_template_registry.source_asset_path
        ),
        "registry_asset_hash": (
            portfolio_template_registry.source_asset_hash
        ),
        "effective_portfolio_hash": (
            portfolio_template_registry.effective_portfolio_hash
        ),
        "lens_registry_hash": (
            portfolio_template_registry.lens_registry_hash
        ),
        "allocator_hash": portfolio_template_registry.allocator_hash,
    }
    decision.pop("decision_hash", None)
    decision["decision_hash"] = hash_payload(decision)
    return decision


def verify_stored_portfolio_qualification(
    memory: ControlMemory,
    results: Iterable[ShadowPairedResult],
    decision: dict[str, Any],
    *,
    evidence_set: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild evidence gates when the checked-in registry is out of scope."""
    rows = list(results)
    base = decide_memory_qualification(
        memory,
        rows,
        thresholds=decision.get("thresholds"),
        provenance=decision.get("provenance") or {},
        evidence_set=evidence_set,
    )
    portfolio = decision.get("portfolio_control")
    if (
        decision.get("schema_version")
        != "r3e-portfolio-memory-qualification-decision-v1"
        or not isinstance(portfolio, dict)
        or portfolio.get("candidate_portfolio_template_id")
        != memory.control_delta.get("candidate_portfolio_template_id")
    ):
        raise MemoryQualificationViolation(
            "stored portfolio qualification authority is incomplete"
        )
    for field in (
        "portfolio_template_registry_hash",
        "portfolio_template_hash",
        "registry_asset_hash",
        "effective_portfolio_hash",
        "lens_registry_hash",
        "allocator_hash",
    ):
        value = str(portfolio.get(field) or "")
        if not value.startswith("sha256:") or len(value) != 71:
            raise MemoryQualificationViolation(
                "stored portfolio qualification hash is invalid"
            )
    adaptation = [
        row for row in rows
        if row.shadow.get("qualification_split") == "adaptation"
    ]
    non_target = [
        row for row in rows
        if row.shadow.get("qualification_split") == "non_target"
    ]
    false_activation = [
        row for row in rows
        if row.shadow.get("qualification_split") == "false_activation"
    ]
    expected_portfolio_gates = {
        "adaptation_repair_gain": bool(adaptation)
        and any(row.outcome == "helped" for row in adaptation),
        "non_target_safety": bool(non_target)
        and all(row.outcome != "harmed" for row in non_target),
        "false_activation_safety": bool(false_activation)
        and all(
            row.outcome != "harmed"
            and row.shadow.get("triggered") is False
            for row in false_activation
        ),
        "template_authority": True,
    }
    expected_gates = {**base["gates"], **expected_portfolio_gates}
    expected_summary = {
        "adaptation_rows": len(adaptation),
        "non_target_rows": len(non_target),
        "false_activation_rows": len(false_activation),
    }
    if (
        decision.get("gates") != expected_gates
        or decision.get("qualified") is not all(expected_gates.values())
        or any(
            portfolio.get(field) != value
            for field, value in expected_summary.items()
        )
    ):
        raise MemoryQualificationViolation(
            "stored portfolio qualification gates are not reconstructable"
        )
    ignored = {
        "schema_version",
        "gates",
        "qualified",
        "portfolio_control",
        "decision_hash",
    }
    if any(
        decision.get(key) != value
        for key, value in base.items()
        if key not in ignored
    ):
        raise MemoryQualificationViolation(
            "stored portfolio qualification base decision differs"
        )
    if decision.get("decision_hash") != hash_payload({
        key: value for key, value in decision.items()
        if key != "decision_hash"
    }):
        raise MemoryQualificationViolation(
            "stored portfolio qualification hash mismatch"
        )
    return decision
