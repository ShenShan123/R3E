"""Construct a whole-policy child that atomically binds an active memory bank."""
from __future__ import annotations

from copy import deepcopy

from r3e.policy.schema import PolicyState

from .memory_store import MemoryStore
from .schema import ActiveMemoryBank
from .evidence import memory_definition


class MemoryPromotionViolation(RuntimeError):
    """Raised when an active bank candidate lacks replay-qualified authority."""


def build_memory_bank_policy_candidate(
    *,
    parent: PolicyState,
    store: MemoryStore,
    memory_versions: dict[str, int],
    bank_id: str,
    bank_version: int,
    retriever_hash: str,
    activation_guard_hash: str,
    control_whitelist_hash: str,
    round_id: str,
    policy_id: str,
    source_manifest_hash: str = "",
) -> tuple[PolicyState, ActiveMemoryBank]:
    memories = {}
    for memory_id, version in sorted(memory_versions.items()):
        memory = store.get_version(memory_id, version)
        status = store.current_status(memory_id, version)
        if status not in {"bank_candidate", "active_dormant"}:
            raise MemoryPromotionViolation(
                f"memory lacks bank authority: {memory_id}@{version}"
            )
        memories[memory_id] = {
            "memory_version": version,
            "memory_hash": memory.memory_hash,
            "memory_definition_hash": memory_definition(memory)[
                "definition_hash"
            ],
            "status": status,
        }
    provisional_bank = ActiveMemoryBank.create(
        bank_id=bank_id,
        bank_version=bank_version,
        policy_instance_hash=parent.policy_instance_hash,
        effective_policy_hash="sha256:" + "0" * 64,
        memories=memories,
        retriever_hash=retriever_hash,
        activation_guard_hash=activation_guard_hash,
        control_whitelist_hash=control_whitelist_hash,
    )
    round_number = int(
        "".join(character for character in round_id if character.isdigit())
        or parent.created_round + 1
    )
    candidate = PolicyState.from_dict({
        "policy_id": policy_id,
        "schema_version": parent.schema_version,
        "parent_policy_id": parent.policy_id,
        "parent_policy_hash": parent.policy_hash,
        "base_policy_hash": parent.base_policy_hash,
        "created_round": round_number,
        "created_from_residual_manifest_hash": (
            source_manifest_hash
            or parent.created_from_residual_manifest_hash
        ),
        "configuration": deepcopy(parent.configuration),
        "budgets": deepcopy(parent.budgets),
        "status": "candidate",
        "validation_manifest_hash": "",
        "promotion_decision_hash": "",
        "rollback_policy_id": parent.policy_id,
        "frozen_assets": deepcopy(parent.frozen_assets or {}),
        "proposal_operator": "memory_bank_update",
        "memory_binding": provisional_bank.policy_binding,
    })
    bank = ActiveMemoryBank.create(
        bank_id=bank_id,
        bank_version=bank_version,
        policy_instance_hash=parent.policy_instance_hash,
        effective_policy_hash=candidate.effective_policy_hash,
        memories=memories,
        retriever_hash=retriever_hash,
        activation_guard_hash=activation_guard_hash,
        control_whitelist_hash=control_whitelist_hash,
    )
    if bank.bank_hash != provisional_bank.bank_hash:
        raise MemoryPromotionViolation("derived effective policy binding changed bank identity")
    return candidate, bank
