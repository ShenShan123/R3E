"""Stable memory definitions and append-only episode evidence links."""
from __future__ import annotations

from typing import Any

from r3e.protocol.hashing import hash_payload

from .schema import ControlMemory


DEFINITION_SCHEMA_VERSION = "r3e-memory-definition-v1"
EVIDENCE_LINK_SCHEMA_VERSION = "r3e-memory-evidence-link-v1"
EVIDENCE_SET_SCHEMA_VERSION = "r3e-memory-evidence-set-v1"


def memory_definition(memory: ControlMemory) -> dict[str, Any]:
    payload = {
        "schema_version": DEFINITION_SCHEMA_VERSION,
        "memory_id": memory.memory_id,
        "memory_version": memory.memory_version,
        "memory_hash": memory.memory_hash,
        "created_under_effective_policy_hash": (
            memory.created_under_effective_policy_hash
        ),
        "trigger_hash": hash_payload(memory.trigger_predicate),
        "effective_delta_hash": memory.effective_delta_hash,
    }
    payload["definition_hash"] = hash_payload(payload)
    return payload


def evidence_link(
    memory: ControlMemory,
    *,
    episode_id: str,
    episode_hash: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": EVIDENCE_LINK_SCHEMA_VERSION,
        "memory_id": memory.memory_id,
        "memory_version": memory.memory_version,
        "memory_hash": memory.memory_hash,
        "episode_id": episode_id,
        "episode_hash": episode_hash,
    }
    payload["link_hash"] = hash_payload(payload)
    return payload


def freeze_evidence_set(
    memory: ControlMemory,
    links: list[dict[str, Any]],
) -> dict[str, Any]:
    canonical = sorted(links, key=lambda row: (row["episode_id"], row["link_hash"]))
    payload = {
        "schema_version": EVIDENCE_SET_SCHEMA_VERSION,
        "memory_id": memory.memory_id,
        "memory_version": memory.memory_version,
        "memory_hash": memory.memory_hash,
        "links": canonical,
        "support_count": len(canonical),
    }
    payload["evidence_set_hash"] = hash_payload(payload)
    return payload

