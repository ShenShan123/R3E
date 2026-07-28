"""Stable memory definitions and strictly reconstructable evidence sets."""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Mapping

from r3e.protocol.hashing import hash_payload

from .schema import ControlMemory, VerifiedEpisode


DEFINITION_SCHEMA_VERSION = "r3e-memory-definition-v1"
EVIDENCE_LINK_SCHEMA_VERSION = "r3e-memory-evidence-link-v1"
EVIDENCE_SET_SCHEMA_VERSION = "r3e-memory-evidence-set-v1"
MEMORY_COMPILER_VERSION = "r3e-control-memory-compiler-v1"
MEMORY_WHITELIST_SCHEMA_VERSION = "r3e-memory-control-whitelist-v1"
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class MemoryEvidenceViolation(RuntimeError):
    """Raised when memory evidence cannot be independently reconstructed."""


def definition_hash_for_parts(
    trigger_predicate: Mapping[str, Any],
    control_delta: Mapping[str, Any],
) -> str:
    return hash_payload({
        "schema_version": DEFINITION_SCHEMA_VERSION,
        "trigger_predicate": deepcopy(dict(trigger_predicate)),
        "control_delta": deepcopy(dict(control_delta)),
        "compiler_version": MEMORY_COMPILER_VERSION,
        "whitelist_schema_version": MEMORY_WHITELIST_SCHEMA_VERSION,
    })


def memory_definition(memory: ControlMemory) -> dict[str, Any]:
    """Return policy-independent semantic identity for one control memory."""
    payload = {
        "schema_version": DEFINITION_SCHEMA_VERSION,
        "trigger_predicate": deepcopy(memory.trigger_predicate),
        "control_delta": deepcopy(memory.control_delta),
        "compiler_version": MEMORY_COMPILER_VERSION,
        "whitelist_schema_version": MEMORY_WHITELIST_SCHEMA_VERSION,
    }
    payload["definition_hash"] = definition_hash_for_parts(
        memory.trigger_predicate, memory.control_delta
    )
    return payload


def verify_memory_definition(
    definition: Mapping[str, Any],
    *,
    memory: ControlMemory | None = None,
) -> dict[str, Any]:
    required = {
        "schema_version",
        "trigger_predicate",
        "control_delta",
        "compiler_version",
        "whitelist_schema_version",
        "definition_hash",
    }
    payload = deepcopy(dict(definition))
    if set(payload) != required:
        raise MemoryEvidenceViolation("memory definition fields mismatch")
    if payload["schema_version"] != DEFINITION_SCHEMA_VERSION:
        raise MemoryEvidenceViolation("memory definition schema mismatch")
    body = {key: value for key, value in payload.items() if key != "definition_hash"}
    if payload["definition_hash"] != hash_payload(body):
        raise MemoryEvidenceViolation("memory definition hash mismatch")
    if memory is not None:
        expected = memory_definition(memory)
        if payload != expected:
            raise MemoryEvidenceViolation("memory definition/object mismatch")
    return payload


def evidence_link(
    memory: ControlMemory,
    *,
    evidence_source: ControlMemory,
    episode_id: str,
    episode_hash: str,
    observed_round_id: str | None = None,
    observed_policy_instance_hash: str | None = None,
    observed_effective_policy_hash: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": EVIDENCE_LINK_SCHEMA_VERSION,
        "memory_id": memory.memory_id,
        "memory_version": memory.memory_version,
        "memory_hash": memory.memory_hash,
        "memory_definition_hash": memory_definition(memory)["definition_hash"],
        "episode_id": episode_id,
        "episode_hash": episode_hash,
        "observed_round_id": (
            observed_round_id or evidence_source.origin_round_id
        ),
        "observed_policy_instance_hash": (
            observed_policy_instance_hash
            or evidence_source.created_under_policy_instance_hash
        ),
        "observed_effective_policy_hash": (
            observed_effective_policy_hash
            or evidence_source.created_under_effective_policy_hash
        ),
    }
    payload["link_hash"] = hash_payload(payload)
    return payload


def freeze_evidence_set(
    memory: ControlMemory,
    links: list[dict[str, Any]],
) -> dict[str, Any]:
    canonical = sorted(
        (deepcopy(row) for row in links),
        key=lambda row: (row["episode_id"], row["link_hash"]),
    )
    payload = {
        "schema_version": EVIDENCE_SET_SCHEMA_VERSION,
        "memory_id": memory.memory_id,
        "memory_version": memory.memory_version,
        "memory_hash": memory.memory_hash,
        "memory_definition_hash": memory_definition(memory)["definition_hash"],
        "links": canonical,
        "support_count": len(canonical),
    }
    payload["evidence_set_hash"] = hash_payload(payload)
    return payload


def verify_memory_evidence_set(
    evidence_set: Mapping[str, Any],
    *,
    memory: ControlMemory,
    episodes: Mapping[str, VerifiedEpisode | Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Recompute a frozen evidence set and optionally every source episode."""
    required = {
        "schema_version",
        "memory_id",
        "memory_version",
        "memory_hash",
        "memory_definition_hash",
        "links",
        "support_count",
        "evidence_set_hash",
    }
    payload = deepcopy(dict(evidence_set))
    if set(payload) != required:
        raise MemoryEvidenceViolation("memory evidence set fields mismatch")
    if payload["schema_version"] != EVIDENCE_SET_SCHEMA_VERSION:
        raise MemoryEvidenceViolation("memory evidence set schema mismatch")
    if (
        payload["memory_id"] != memory.memory_id
        or int(payload["memory_version"]) != memory.memory_version
        or payload["memory_hash"] != memory.memory_hash
        or payload["memory_definition_hash"]
        != memory_definition(memory)["definition_hash"]
    ):
        raise MemoryEvidenceViolation("memory evidence set binding mismatch")
    links = payload["links"]
    if not isinstance(links, list) or not links:
        raise MemoryEvidenceViolation("memory evidence links must be non-empty")
    if int(payload["support_count"]) != len(links):
        raise MemoryEvidenceViolation("memory evidence support count mismatch")
    link_fields = {
        "schema_version",
        "memory_id",
        "memory_version",
        "memory_hash",
        "memory_definition_hash",
        "episode_id",
        "episode_hash",
        "observed_round_id",
        "observed_policy_instance_hash",
        "observed_effective_policy_hash",
        "link_hash",
    }
    seen_episode_ids: set[str] = set()
    normalized_links = []
    for link in links:
        if not isinstance(link, Mapping) or set(link) != link_fields:
            raise MemoryEvidenceViolation("memory evidence link fields mismatch")
        value = deepcopy(dict(link))
        episode_id = str(value.get("episode_id") or "")
        if not episode_id or episode_id in seen_episode_ids:
            raise MemoryEvidenceViolation("duplicate or empty evidence episode id")
        seen_episode_ids.add(episode_id)
        if (
            value["schema_version"] != EVIDENCE_LINK_SCHEMA_VERSION
            or value["memory_id"] != memory.memory_id
            or int(value["memory_version"]) != memory.memory_version
            or value["memory_hash"] != memory.memory_hash
            or value["memory_definition_hash"]
            != payload["memory_definition_hash"]
        ):
            raise MemoryEvidenceViolation("memory evidence link binding mismatch")
        for field in (
            "episode_hash",
            "observed_policy_instance_hash",
            "observed_effective_policy_hash",
        ):
            if not _HASH_RE.fullmatch(str(value.get(field) or "")):
                raise MemoryEvidenceViolation(
                    f"memory evidence link {field} is invalid"
                )
        body = {key: item for key, item in value.items() if key != "link_hash"}
        if value["link_hash"] != hash_payload(body):
            raise MemoryEvidenceViolation("memory evidence link hash mismatch")
        if episodes is not None:
            raw_episode = episodes.get(episode_id)
            if raw_episode is None:
                raise MemoryEvidenceViolation("memory evidence episode is unknown")
            episode = (
                raw_episode
                if isinstance(raw_episode, VerifiedEpisode)
                else VerifiedEpisode.from_dict(raw_episode)
            )
            if (
                episode.episode_hash != value["episode_hash"]
                or episode.round_id != value["observed_round_id"]
                or episode.challenged_policy_instance_hash
                != value["observed_policy_instance_hash"]
                or episode.challenged_effective_policy_hash
                != value["observed_effective_policy_hash"]
            ):
                raise MemoryEvidenceViolation(
                    "memory evidence link/source episode mismatch"
                )
        normalized_links.append(value)
    canonical = sorted(
        normalized_links, key=lambda row: (row["episode_id"], row["link_hash"])
    )
    if links != canonical:
        raise MemoryEvidenceViolation("memory evidence links are not canonical")
    body = {key: value for key, value in payload.items() if key != "evidence_set_hash"}
    if payload["evidence_set_hash"] != hash_payload(body):
        raise MemoryEvidenceViolation("memory evidence set hash mismatch")
    return payload
