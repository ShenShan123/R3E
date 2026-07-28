"""Sanitized, effective-policy-bound capability packet for Grounded Red."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload

from .coverage import verify_coverage_state


CAPABILITY_PACKET_SCHEMA_VERSION = "r3e-red-capability-packet-v1"
_FORBIDDEN_KEYS = {
    "source_episode",
    "source_episodes",
    "source_episode_ids",
    "historical_patch",
    "historical_patches",
    "prompt",
    "private_rationale",
    "reference_repair",
    "hidden_case",
    "hidden_cases",
    "oracle_truth",
}


class GroundedCapabilityViolation(RuntimeError):
    """Raised when a red capability packet exposes private blue state."""


def _reject_private(value: Any) -> None:
    if isinstance(value, Mapping):
        forbidden = _FORBIDDEN_KEYS & {
            str(key).lower() for key in value.keys()
        }
        if forbidden:
            raise GroundedCapabilityViolation(
                f"private capability fields are forbidden: {sorted(forbidden)}"
            )
        for item in value.values():
            _reject_private(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_private(item)


def _public_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = [deepcopy(dict(row)) for row in rows]
    _reject_private(result)
    return sorted(result, key=hash_payload)


def build_grounded_red_capability_packet(
    policy: PolicyState,
    *,
    coverage_state: Mapping[str, Any],
    covered_failure_regions: Iterable[Mapping[str, Any]] = (),
    known_memory_blind_spots: Iterable[Mapping[str, Any]] = (),
    known_false_activation_regions: Iterable[Mapping[str, Any]] = (),
    stale_memory_regions: Iterable[Mapping[str, Any]] = (),
    memory_conflict_regions: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    coverage = verify_coverage_state(coverage_state)
    binding = policy.memory_binding or {}
    payload = {
        "schema_version": CAPABILITY_PACKET_SCHEMA_VERSION,
        "blue_policy_instance_hash": policy.policy_instance_hash,
        "effective_blue_policy_hash": policy.effective_policy_hash,
        "effective_memory_bank_hash": str(
            binding.get("effective_memory_bank_hash") or ""
        ),
        "covered_failure_regions": _public_rows(covered_failure_regions),
        "known_memory_blind_spots": _public_rows(known_memory_blind_spots),
        "known_false_activation_regions": _public_rows(
            known_false_activation_regions
        ),
        "stale_memory_regions": _public_rows(stale_memory_regions),
        "memory_conflict_regions": _public_rows(memory_conflict_regions),
        "family_coverage_summary": {
            "coverage_hash": coverage["coverage_hash"],
            "cell_count": len(coverage["cells"]),
            "admitted_by_family": {
                family: sum(
                    int(cell["admitted"])
                    for cell in coverage["cells"]
                    if cell["family_id"] == family
                )
                for family in sorted({
                    cell["family_id"] for cell in coverage["cells"]
                })
            },
        },
    }
    payload["packet_hash"] = hash_payload(payload)
    return payload


def verify_grounded_red_capability_packet(
    packet: Mapping[str, Any],
    *,
    policy: PolicyState,
) -> dict[str, Any]:
    payload = deepcopy(dict(packet))
    required = {
        "schema_version",
        "blue_policy_instance_hash",
        "effective_blue_policy_hash",
        "effective_memory_bank_hash",
        "covered_failure_regions",
        "known_memory_blind_spots",
        "known_false_activation_regions",
        "stale_memory_regions",
        "memory_conflict_regions",
        "family_coverage_summary",
        "packet_hash",
    }
    if set(payload) != required:
        raise GroundedCapabilityViolation("red capability packet fields mismatch")
    if payload["schema_version"] != CAPABILITY_PACKET_SCHEMA_VERSION:
        raise GroundedCapabilityViolation("red capability packet schema mismatch")
    if payload["packet_hash"] != hash_payload({
        key: value for key, value in payload.items() if key != "packet_hash"
    }):
        raise GroundedCapabilityViolation("red capability packet hash mismatch")
    binding = policy.memory_binding or {}
    if (
        payload["blue_policy_instance_hash"] != policy.policy_instance_hash
        or payload["effective_blue_policy_hash"] != policy.effective_policy_hash
        or payload["effective_memory_bank_hash"]
        != str(binding.get("effective_memory_bank_hash") or "")
    ):
        raise GroundedCapabilityViolation(
            "red capability packet is bound to a stale blue state"
        )
    _reject_private(payload)
    return payload
