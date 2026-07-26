"""Compatibility entry for active-blue-conditioned poison generation."""
from __future__ import annotations

from typing import Any, Callable

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload


def generate_poison(
    case: dict[str, Any],
    challenged_policy: PolicyState,
    capability_packet: dict[str, Any],
    archive: list[dict[str, Any]],
    *,
    mutator: Callable[..., dict[str, Any]],
    parent_poison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate one poison from an explicit policy-bound capability packet.

    ``archive`` is an input to the stable interface so adapters can use
    lineage/novelty context.  Authority remains with the caller's gates.
    """
    packet = dict(capability_packet)
    if packet.get("challenged_policy_hash") != challenged_policy.policy_hash:
        raise ValueError("capability packet is not bound to challenged policy")
    expected_hash = packet.pop("packet_hash", "")
    if expected_hash != hash_payload(packet):
        raise ValueError("capability packet hash mismatch")
    packet["packet_hash"] = expected_hash
    poison = dict(
        mutator(
            case=case,
            capability_packet=packet,
            parent_poison=parent_poison,
            archive=list(archive),
        )
    )
    poison["challenged_policy_id"] = challenged_policy.policy_id
    poison["challenged_policy_hash"] = challenged_policy.policy_hash
    poison["capability_packet_hash"] = packet["packet_hash"]
    return poison
