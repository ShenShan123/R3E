"""Runner-owned qualification decision reconstructed from paired replay."""
from __future__ import annotations

from typing import Any, Iterable

from r3e.protocol.hashing import hash_payload

from .schema import ControlMemory, ShadowPairedResult


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
    if replay_policy_hash != memory.created_under_effective_policy_hash:
        if (
            provenance.get("revalidation_from_policy_hash")
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
    frozen_evidence = dict(evidence_set or {
        "memory_id": memory.memory_id,
        "memory_version": memory.memory_version,
        "memory_hash": memory.memory_hash,
        "support_count": len(memory.source_episode_ids),
        "links": [
            {
                "episode_id": episode_id,
                "episode_hash": memory.source_episode_hashes[episode_id],
            }
            for episode_id in sorted(memory.source_episode_ids)
        ],
    })
    if (
        frozen_evidence.get("memory_id") != memory.memory_id
        or int(frozen_evidence.get("memory_version") or 0) != memory.memory_version
        or frozen_evidence.get("memory_hash") != memory.memory_hash
    ):
        raise MemoryQualificationViolation("qualification evidence set mismatch")
    evidence_set_hash = str(
        frozen_evidence.get("evidence_set_hash")
        or hash_payload(frozen_evidence)
    )
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
        "evidence_set_hash": evidence_set_hash,
        "support_count": int(frozen_evidence.get("support_count") or 0),
    }
    decision["decision_hash"] = hash_payload(decision)
    return decision
