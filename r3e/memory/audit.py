"""Cross-store reconstruction for the formal RAAM authority chain."""
from __future__ import annotations

from typing import Any

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload

from .bank_store import ActiveBankStore
from .episode_store import EpisodeStore
from .memory_store import MemoryStore


def audit_memory_system(
    *,
    episode_store: EpisodeStore,
    memory_store: MemoryStore,
    bank_store: ActiveBankStore,
    active_policy: PolicyState | None = None,
) -> dict[str, Any]:
    episodes = episode_store.audit()
    memory_summary = memory_store.audit()
    bank_count = bank_store.audit()
    active_bank_hash = ""
    if active_policy and active_policy.memory_binding:
        active_bank_hash = bank_store.load_for_policy(active_policy).bank_hash
    summary = {
        "schema_version": "r3e-raam-audit-v1",
        "episode_count": len(episodes),
        **memory_summary,
        "active_bank_version_count": bank_count,
        "active_policy_hash": active_policy.policy_hash if active_policy else "",
        "active_memory_bank_hash": active_bank_hash,
    }
    summary["audit_hash"] = hash_payload(summary)
    return summary
