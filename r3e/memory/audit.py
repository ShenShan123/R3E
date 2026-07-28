"""Cross-store reconstruction for the formal RAAM authority chain."""
from __future__ import annotations

import json
from typing import Any

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload
from r3e.protocol.ledger import read_ledger

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
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    for episode in episodes:
        nodes.append({
            "node_type": "episode",
            "node_id": episode.episode_hash,
            "episode_id": episode.episode_id,
        })
    for row in read_ledger(memory_store.index_path):
        nodes.append({
            "node_type": "memory_definition",
            "node_id": row["memory_hash"],
            "memory_id": row["memory_id"],
        })
    for row in read_ledger(memory_store.evidence_path):
        nodes.append({
            "node_type": "evidence_link",
            "node_id": row["link_hash"],
        })
        edges.extend([
            {
                "from": row["episode_hash"],
                "to": row["link_hash"],
                "relation": "supports",
            },
            {
                "from": row["link_hash"],
                "to": row["memory_hash"],
                "relation": "evidence_for",
            },
        ])
    if memory_store.qualifications.exists():
        for path in sorted(memory_store.qualifications.glob("*.json")):
            bundle = json.loads(path.read_text(encoding="utf-8"))
            decision = bundle["decision"]
            nodes.append({
                "node_type": "qualification",
                "node_id": decision["decision_hash"],
                "evidence_set_hash": decision["evidence_set_hash"],
            })
            edges.append({
                "from": bundle["memory_hash"],
                "to": decision["decision_hash"],
                "relation": "qualified_by",
            })
    for row in read_ledger(memory_store.lifecycle_path):
        nodes.append({
            "node_type": "lifecycle",
            "node_id": row["event_hash"],
            "status": row["new_status"],
        })
        edges.append({
            "from": row["memory_hash"],
            "to": row["event_hash"],
            "relation": "lifecycle",
        })
    if active_policy and active_policy.memory_binding:
        active_bank = bank_store.load_for_policy(active_policy)
        active_bank_hash = active_bank.bank_hash
        nodes.append({
            "node_type": "active_bank",
            "node_id": active_bank.bank_hash,
        })
        for memory_hash in (
            binding["memory_hash"] for binding in active_bank.memories.values()
        ):
            edges.append({
                "from": memory_hash,
                "to": active_bank.bank_hash,
                "relation": "member_of",
            })
        nodes.append({
            "node_type": "policy",
            "node_id": active_policy.policy_hash,
        })
        edges.append({
            "from": active_bank.bank_hash,
            "to": active_policy.policy_hash,
            "relation": "authorized_by",
        })
    canonical_nodes = sorted(nodes, key=lambda row: (row["node_type"], row["node_id"]))
    canonical_edges = sorted(
        edges, key=lambda row: (row["from"], row["to"], row["relation"])
    )
    summary = {
        "schema_version": "r3e-raam-audit-v1",
        "episode_count": len(episodes),
        **memory_summary,
        "active_bank_version_count": bank_count,
        "active_policy_hash": active_policy.policy_hash if active_policy else "",
        "active_memory_bank_hash": active_bank_hash,
        "authority_dag": {
            "nodes": canonical_nodes,
            "edges": canonical_edges,
            "dag_hash": hash_payload({
                "nodes": canonical_nodes,
                "edges": canonical_edges,
            }),
        },
    }
    summary["audit_hash"] = hash_payload(summary)
    return summary
