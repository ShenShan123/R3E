"""Cross-policy lifecycle management without deleting historical memories."""
from __future__ import annotations

from typing import Any

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload

from .compatibility import classify_policy_compatibility
from .memory_store import MemoryStore
from .schema import ActiveMemoryBank, MemoryLifecycleEvent


def transition_active_bank(
    *,
    store: MemoryStore,
    previous_policy: PolicyState,
    current_policy: PolicyState,
    previous_bank: ActiveMemoryBank,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Suspend only affected memories; preserve all immutable versions."""
    evidence_hash = hash_payload(evidence)
    inherited = {}
    revalidation_required = {}
    for memory_id, binding in previous_bank.memories.items():
        memory = store.get_version(memory_id, int(binding["memory_version"]))
        result = classify_policy_compatibility(
            memory, previous_policy, current_policy
        )
        if result["classification"] == "static_compatible":
            inherited[memory_id] = binding["memory_version"]
            continue
        current_status = store.current_status(memory_id, memory.memory_version)
        if current_status == "active_dormant":
            store.update_lifecycle(
                memory_id,
                MemoryLifecycleEvent.create(
                    memory_id=memory_id,
                    memory_version=memory.memory_version,
                    memory_hash=memory.memory_hash,
                    previous_status=current_status,
                    new_status="revalidation_required",
                    effective_policy_hash=current_policy.effective_policy_hash,
                    reason_code="policy_transition_changed_dependency",
                    evidence_hash=evidence_hash,
                ),
            )
        revalidation_required[memory_id] = memory.memory_version
    result = {
        "schema_version": "r3e-memory-policy-transition-v1",
        "previous_policy_hash": previous_policy.policy_hash,
        "current_policy_hash": current_policy.policy_hash,
        "previous_bank_hash": previous_bank.bank_hash,
        "inherited_memory_versions": inherited,
        "revalidation_required_versions": revalidation_required,
        "evidence_hash": evidence_hash,
    }
    result["transition_hash"] = hash_payload(result)
    return result
