"""Reconstructable authority for promoting a memory-bound whole policy."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload

from .qualification_gate import (
    decide_memory_qualification,
    decide_portfolio_memory_qualification,
)
from r3e.blue.portfolio.portfolio_control import (
    PORTFOLIO_CONTROL_FIELDS,
    load_portfolio_template_registry,
)
from .compatibility import classify_policy_compatibility
from .schema import ActiveMemoryBank, ControlMemory, ShadowPairedResult
from .schema import VerifiedEpisode
from .evidence import memory_definition, verify_memory_evidence_set


MEMORY_AUTHORITY_SCHEMA_VERSION = "r3e-memory-promotion-authority-v1"


class MemoryAuthorityViolation(RuntimeError):
    """Raised when a bank cannot be rebuilt from replay-qualified memories."""


def build_memory_promotion_authority(
    *,
    bank: ActiveMemoryBank,
    memories: list[ControlMemory],
    qualification_bundles: list[dict[str, Any]],
    episodes: list[VerifiedEpisode],
    compatibility_bundles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": MEMORY_AUTHORITY_SCHEMA_VERSION,
        "bank": bank.to_dict(),
        "memories": [memory.to_dict() for memory in memories],
        "qualification_bundles": list(qualification_bundles),
        "episodes": [episode.to_dict() for episode in episodes],
        "compatibility_bundles": list(compatibility_bundles or []),
    }
    payload["authority_hash"] = hash_payload(payload)
    return payload


def verify_memory_promotion_authority(
    authority: dict[str, Any],
    *,
    parent: PolicyState,
    candidate: PolicyState,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    required = {
        "schema_version",
        "bank",
        "memories",
        "qualification_bundles",
        "episodes",
        "compatibility_bundles",
        "authority_hash",
    }
    if not isinstance(authority, dict) or set(authority) != required:
        raise MemoryAuthorityViolation("memory authority fields mismatch")
    if authority.get("schema_version") != MEMORY_AUTHORITY_SCHEMA_VERSION:
        raise MemoryAuthorityViolation("memory authority schema mismatch")
    body = {key: value for key, value in authority.items() if key != "authority_hash"}
    if authority.get("authority_hash") != hash_payload(body):
        raise MemoryAuthorityViolation("memory authority hash mismatch")
    bank = ActiveMemoryBank.from_dict(authority["bank"])
    if candidate.memory_binding != bank.policy_binding:
        raise MemoryAuthorityViolation("candidate policy/bank binding mismatch")
    if bank.effective_policy_hash != candidate.effective_policy_hash:
        raise MemoryAuthorityViolation("bank effective policy hash mismatch")
    if bank.policy_instance_hash != parent.policy_instance_hash:
        raise MemoryAuthorityViolation("bank parent policy hash mismatch")
    memories = {
        memory.memory_id: memory
        for memory in (
            ControlMemory.from_dict(item) for item in authority["memories"]
        )
    }
    qualifications = {
        str(item.get("memory_hash") or ""): item
        for item in authority["qualification_bundles"]
    }
    episodes = {
        episode.episode_id: episode
        for episode in (
            VerifiedEpisode.from_dict(item) for item in authority["episodes"]
        )
    }
    compatibilities = {
        str(item.get("memory_hash") or ""): item
        for item in authority["compatibility_bundles"]
    }
    if set(memories) != set(bank.memories):
        raise MemoryAuthorityViolation("bank memory set differs from authority objects")
    for memory_id, binding in bank.memories.items():
        memory = memories[memory_id]
        if (
            memory.memory_version != int(binding["memory_version"])
            or memory.memory_hash != binding["memory_hash"]
            or memory_definition(memory)["definition_hash"]
            != binding["memory_definition_hash"]
        ):
            raise MemoryAuthorityViolation("bank memory binding mismatch")
        bundle = qualifications.get(memory.memory_hash)
        if not isinstance(bundle, dict) or set(bundle) != {
            "schema_version", "memory_hash", "evidence_set", "results", "decision"
        }:
            raise MemoryAuthorityViolation("bank memory lacks qualification bundle")
        if bundle.get("schema_version") != "r3e-memory-qualification-bundle-v1":
            raise MemoryAuthorityViolation("qualification bundle schema mismatch")
        try:
            verify_memory_evidence_set(
                bundle["evidence_set"],
                memory=memory,
                episodes=episodes,
            )
        except RuntimeError as exc:
            raise MemoryAuthorityViolation(str(exc)) from exc
        results = [ShadowPairedResult(**row) for row in bundle["results"]]
        decision = bundle["decision"]
        if set(memory.control_delta) & PORTFOLIO_CONTROL_FIELDS:
            portfolio_control = decision.get("portfolio_control") or {}
            asset_path = str(
                portfolio_control.get("registry_asset_path") or ""
            )
            if project_root is None or not asset_path:
                raise MemoryAuthorityViolation(
                    "portfolio memory audit requires checked-in registry"
                )
            try:
                registry = load_portfolio_template_registry(
                    Path(project_root) / asset_path,
                    project_root=project_root,
                )
                reconstructed = decide_portfolio_memory_qualification(
                    memory,
                    results,
                    policy=parent,
                    portfolio_template_registry=registry,
                    thresholds=decision.get("thresholds"),
                    provenance=decision.get("provenance") or {},
                    evidence_set=bundle["evidence_set"],
                )
            except Exception as exc:
                raise MemoryAuthorityViolation(
                    "portfolio qualification cannot be reconstructed"
                ) from exc
        else:
            reconstructed = decide_memory_qualification(
                memory,
                results,
                thresholds=decision.get("thresholds"),
                provenance=decision.get("provenance") or {},
                evidence_set=bundle["evidence_set"],
            )
        if reconstructed != decision or not reconstructed.get("qualified"):
            raise MemoryAuthorityViolation(
                "memory qualification decision is not reconstructable"
            )
        qualified_under_instance = reconstructed.get(
            "qualified_under_policy_instance_hash"
        )
        qualified_under_effective = reconstructed.get(
            "qualified_under_effective_policy_hash"
        )
        if qualified_under_effective != parent.effective_policy_hash:
            compatibility = compatibilities.get(memory.memory_hash)
            if not isinstance(compatibility, dict) or set(compatibility) != {
                "memory_hash", "source_policy", "decision", "decision_hash"
            }:
                raise MemoryAuthorityViolation(
                    "cross-policy memory lacks compatibility proof"
                )
            if compatibility["decision_hash"] != hash_payload({
                key: value for key, value in compatibility.items()
                if key != "decision_hash"
            }):
                raise MemoryAuthorityViolation("compatibility proof hash mismatch")
            source_policy = PolicyState.from_dict(compatibility["source_policy"])
            if source_policy.policy_instance_hash != qualified_under_instance:
                raise MemoryAuthorityViolation(
                    "compatibility source differs from qualification policy"
                )
            expected = classify_policy_compatibility(
                memory, source_policy, parent
            )
            if (
                compatibility["decision"] != expected
                or expected["classification"] != "static_compatible"
            ):
                raise MemoryAuthorityViolation(
                    "cross-policy memory is not statically compatible"
                )
    used_episode_ids = {
        link["episode_id"]
        for bundle in authority["qualification_bundles"]
        for link in bundle["evidence_set"]["links"]
    }
    if used_episode_ids != set(episodes):
        raise MemoryAuthorityViolation(
            "memory authority episode set contains missing or unused objects"
        )
    return {"bank_hash": bank.bank_hash, "memory_count": len(memories)}
