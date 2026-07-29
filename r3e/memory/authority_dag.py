"""Complete, reconstructable authority DAG for the formal RAAM stores."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload, read_json
from r3e.protocol.ledger import read_ledger

from .bank_store import ActiveBankStore
from .episode_store import EpisodeStore
from .evidence import verify_memory_evidence_set
from .memory_store import MemoryStore
from .qualification_gate import (
    decide_memory_qualification,
    verify_stored_portfolio_qualification,
)
from r3e.blue.portfolio.portfolio_control import (
    PORTFOLIO_CONTROL_FIELDS,
)
from .schema import ShadowPairedResult


AUTHORITY_DAG_SCHEMA = "r3e-raam-authority-dag-v1"


class AuthorityDagViolation(RuntimeError):
    """Raised when an authority object is missing, dangling, or cyclic."""


def _node_id(kind: str, identity: str) -> str:
    return f"{kind}:{identity}"


def _acyclic(nodes: set[str], edges: list[dict[str, str]]) -> list[str]:
    outgoing = {node: [] for node in nodes}
    indegree = {node: 0 for node in nodes}
    for edge in edges:
        source = edge["from"]
        target = edge["to"]
        if source not in nodes or target not in nodes:
            raise AuthorityDagViolation("authority DAG has a dangling edge")
        outgoing[source].append(target)
        indegree[target] += 1
    ready = sorted(node for node, count in indegree.items() if count == 0)
    ordered = []
    while ready:
        node = ready.pop(0)
        ordered.append(node)
        for target in sorted(outgoing[node]):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort()
    if len(ordered) != len(nodes):
        raise AuthorityDagViolation("authority DAG contains a cycle")
    return ordered


def _reachable(
    edges: list[dict[str, str]],
    roots: set[str],
) -> set[str]:
    outgoing: dict[str, list[str]] = {}
    for edge in edges:
        outgoing.setdefault(edge["from"], []).append(edge["to"])
    seen = set(roots)
    pending = list(roots)
    while pending:
        source = pending.pop()
        for target in outgoing.get(source, []):
            if target not in seen:
                seen.add(target)
                pending.append(target)
    return seen


def verify_authority_dag(dag: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(dag))
    required = {
        "schema_version",
        "complete",
        "nodes",
        "edges",
        "root_node_ids",
        "active_policy_node_id",
        "topological_order",
        "node_count",
        "edge_count",
        "dag_hash",
    }
    if set(payload) != required:
        raise AuthorityDagViolation("authority DAG fields mismatch")
    if (
        payload["schema_version"] != AUTHORITY_DAG_SCHEMA
        or payload["complete"] is not True
        or payload["dag_hash"] != hash_payload({
            key: value for key, value in payload.items()
            if key != "dag_hash"
        })
    ):
        raise AuthorityDagViolation("authority DAG envelope mismatch")
    nodes = payload["nodes"]
    edges = payload["edges"]
    if (
        not isinstance(nodes, list)
        or not isinstance(edges, list)
        or nodes != sorted(nodes, key=lambda row: row["node_id"])
        or edges != sorted(
            edges,
            key=lambda row: (
                row["from"], row["to"], row["relation"]
            ),
        )
    ):
        raise AuthorityDagViolation("authority DAG is not canonical")
    node_ids = [str(row.get("node_id") or "") for row in nodes]
    if (
        not all(node_ids)
        or len(node_ids) != len(set(node_ids))
        or int(payload["node_count"]) != len(nodes)
        or int(payload["edge_count"]) != len(edges)
    ):
        raise AuthorityDagViolation("authority DAG node identity mismatch")
    for node in nodes:
        node_type = str(node.get("node_type") or "")
        authority_hash = str(node.get("authority_hash") or "")
        if (
            not node_type
            or not authority_hash.startswith("sha256:")
            or len(authority_hash) != 71
            or node["node_id"] != _node_id(
                node_type, authority_hash
            )
        ):
            raise AuthorityDagViolation(
                "authority DAG node binding mismatch"
            )
    for edge in edges:
        if (
            set(edge) != {"from", "to", "relation"}
            or not str(edge.get("relation") or "")
        ):
            raise AuthorityDagViolation(
                "authority DAG edge fields mismatch"
            )
    ordered = _acyclic(set(node_ids), edges)
    if ordered != payload["topological_order"]:
        raise AuthorityDagViolation(
            "authority DAG topological order mismatch"
        )
    roots = sorted(
        node for node in node_ids
        if not any(edge["to"] == node for edge in edges)
    )
    if roots != payload["root_node_ids"]:
        raise AuthorityDagViolation("authority DAG roots mismatch")
    active = str(payload["active_policy_node_id"] or "")
    if active and active not in node_ids:
        raise AuthorityDagViolation("active policy node is missing")
    return payload


def build_authority_dag(
    *,
    episode_store: EpisodeStore,
    memory_store: MemoryStore,
    bank_store: ActiveBankStore,
    active_policy: PolicyState | None = None,
) -> dict[str, Any]:
    """Audit all stores and freeze the complete evidence-to-policy graph."""
    episodes = episode_store.audit()
    memory_store.audit()
    bank_store.audit()
    episode_by_id = {episode.episode_id: episode for episode in episodes}
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []

    def add_node(kind: str, identity: str, **metadata: Any) -> str:
        node_id = _node_id(kind, identity)
        value = {
            "node_id": node_id,
            "node_type": kind,
            "authority_hash": identity,
            **metadata,
        }
        previous = nodes.get(node_id)
        if previous is not None and previous != value:
            raise AuthorityDagViolation("authority node collision")
        nodes[node_id] = value
        return node_id

    def add_edge(
        source: str,
        target: str,
        relation: str,
    ) -> None:
        edges.append({
            "from": source,
            "to": target,
            "relation": relation,
        })

    episode_nodes = {}
    for episode in episodes:
        episode_nodes[episode.episode_id] = add_node(
            "episode",
            episode.episode_hash,
            episode_id=episode.episode_id,
            round_id=episode.round_id,
            final_outcome=episode.final_outcome,
        )

    memories = {}
    memory_nodes = {}
    definition_nodes = {}
    for row in read_ledger(memory_store.index_path):
        memory = memory_store.get_version(
            str(row["memory_id"]), int(row["memory_version"])
        )
        memories[memory.memory_hash] = memory
        memory_node = add_node(
            "memory",
            memory.memory_hash,
            memory_id=memory.memory_id,
            memory_version=memory.memory_version,
        )
        definition_hash = str(row["definition_hash"])
        definition_node = add_node(
            "memory_definition",
            definition_hash,
        )
        memory_nodes[memory.memory_hash] = memory_node
        definition_nodes[memory.memory_hash] = definition_node
        add_edge(definition_node, memory_node, "defines")

    evidence_link_nodes = {}
    evidence_links_by_memory: dict[str, list[dict[str, Any]]] = {}
    for row in read_ledger(memory_store.evidence_path):
        memory_hash = str(row["memory_hash"])
        memory = memories.get(memory_hash)
        episode = episode_by_id.get(str(row["episode_id"]))
        if memory is None or episode is None:
            raise AuthorityDagViolation(
                "memory evidence references an unknown authority object"
            )
        link = {
            key: value for key, value in row.items()
            if key not in {
                "operation",
                "timestamp",
                "ledger_index",
                "previous_entry_hash",
                "ledger_entry_hash",
            }
        }
        if (
            link["episode_hash"] != episode.episode_hash
            or link["memory_hash"] != memory.memory_hash
        ):
            raise AuthorityDagViolation(
                "memory evidence source hash mismatch"
            )
        link_node = add_node(
            "evidence_link",
            str(link["link_hash"]),
            episode_id=episode.episode_id,
            memory_hash=memory.memory_hash,
        )
        evidence_link_nodes[str(link["link_hash"])] = link_node
        evidence_links_by_memory.setdefault(memory_hash, []).append(link)
        add_edge(
            episode_nodes[episode.episode_id],
            link_node,
            "observed_as",
        )
        add_edge(
            link_node,
            definition_nodes[memory_hash],
            "supports_definition",
        )

    qualification_rows = read_ledger(
        memory_store.qualification_index_path
    )
    indexed_paths = {
        str(row["object_path"]) for row in qualification_rows
    }
    object_paths = {
        str(path.relative_to(memory_store.root))
        for path in sorted(
            memory_store.qualifications.glob("*.json")
        )
    } if memory_store.qualifications.exists() else set()
    if indexed_paths != object_paths:
        raise AuthorityDagViolation(
            "qualification index/object set mismatch"
        )
    qualified_decisions_by_parent: dict[
        tuple[str, str], list[str]
    ] = {}
    for row in qualification_rows:
        bundle = read_json(memory_store.root / row["object_path"])
        if set(bundle) != {
            "schema_version",
            "memory_hash",
            "evidence_set",
            "results",
            "decision",
        } or bundle["schema_version"] != (
            "r3e-memory-qualification-bundle-v1"
        ):
            raise AuthorityDagViolation(
                "qualification bundle schema mismatch"
            )
        memory_hash = str(bundle["memory_hash"])
        memory = memories.get(memory_hash)
        if memory is None:
            raise AuthorityDagViolation(
                "qualification references unknown memory"
            )
        evidence_set = verify_memory_evidence_set(
            bundle["evidence_set"],
            memory=memory,
            episodes=episode_by_id,
        )
        results = [
            ShadowPairedResult(**value)
            for value in bundle["results"]
        ]
        if set(memory.control_delta) & PORTFOLIO_CONTROL_FIELDS:
            decision = verify_stored_portfolio_qualification(
                memory,
                results,
                bundle["decision"],
                evidence_set=evidence_set,
            )
        else:
            decision = decide_memory_qualification(
                memory,
                results,
                thresholds=bundle["decision"].get("thresholds"),
                provenance=bundle["decision"].get("provenance") or {},
                evidence_set=evidence_set,
            )
        if decision != bundle["decision"]:
            raise AuthorityDagViolation(
                "qualification decision cannot be reconstructed"
            )
        if (
            row["memory_hash"] != memory_hash
            or row["decision_hash"] != decision["decision_hash"]
            or row["evidence_set_hash"]
            != evidence_set["evidence_set_hash"]
        ):
            raise AuthorityDagViolation(
                "qualification index binding mismatch"
            )
        set_node = add_node(
            "evidence_set",
            evidence_set["evidence_set_hash"],
            memory_hash=memory_hash,
            support_count=evidence_set["support_count"],
        )
        for link in evidence_set["links"]:
            link_node = evidence_link_nodes.get(link["link_hash"])
            if link_node is None:
                raise AuthorityDagViolation(
                    "qualification evidence link is not in the ledger"
                )
            add_edge(link_node, set_node, "included_in")
        decision_node = add_node(
            "qualification_decision",
            decision["decision_hash"],
            memory_hash=memory_hash,
            qualified=bool(decision["qualified"]),
        )
        add_edge(set_node, decision_node, "evaluated_by")
        add_edge(
            memory_nodes[memory_hash],
            decision_node,
            "qualified_as",
        )
        if decision["qualified"]:
            key = (
                memory_hash,
                str(decision[
                    "qualified_under_policy_instance_hash"
                ]),
            )
            qualified_decisions_by_parent.setdefault(
                key, []
            ).append(decision_node)

    lifecycle_previous: dict[str, str] = {}
    for row in read_ledger(memory_store.lifecycle_path):
        memory_hash = str(row["memory_hash"])
        if memory_hash not in memory_nodes:
            raise AuthorityDagViolation(
                "lifecycle references unknown memory"
            )
        event_node = add_node(
            "lifecycle_event",
            str(row["event_hash"]),
            memory_hash=memory_hash,
            new_status=str(row["new_status"]),
        )
        add_edge(
            lifecycle_previous.get(
                memory_hash, memory_nodes[memory_hash]
            ),
            event_node,
            "transitions_to",
        )
        lifecycle_previous[memory_hash] = event_node

    bank_nodes = {}
    active_bank_hash = ""
    for row in read_ledger(bank_store.index_path):
        bank = bank_store.get_by_hash(str(row["bank_hash"]))
        bank_node = add_node(
            "memory_bank",
            bank.bank_hash,
            bank_id=bank.bank_id,
            bank_version=bank.bank_version,
        )
        bank_nodes[bank.bank_hash] = bank_node
        for memory_id, binding in bank.memories.items():
            memory_hash = str(binding["memory_hash"])
            if memory_hash not in memory_nodes:
                raise AuthorityDagViolation(
                    "bank references unknown memory"
                )
            add_edge(
                memory_nodes[memory_hash],
                bank_node,
                "member_of",
            )
            decisions = qualified_decisions_by_parent.get(
                (memory_hash, bank.policy_instance_hash), []
            )
            for decision_node in decisions:
                add_edge(
                    decision_node,
                    bank_node,
                    "authorizes_member",
                )

    active_policy_node = ""
    if active_policy is not None:
        active_policy_node = add_node(
            "policy",
            active_policy.policy_hash,
            policy_id=active_policy.policy_id,
        )
        if active_policy.memory_binding:
            bank = bank_store.load_for_policy(active_policy)
            active_bank_hash = bank.bank_hash
            add_edge(
                bank_nodes[bank.bank_hash],
                active_policy_node,
                "authorized_by",
            )

    canonical_nodes = sorted(
        nodes.values(), key=lambda row: row["node_id"]
    )
    canonical_edges = sorted(
        edges,
        key=lambda row: (
            row["from"], row["to"], row["relation"]
        ),
    )
    if len(canonical_edges) != len({
        (row["from"], row["to"], row["relation"])
        for row in canonical_edges
    }):
        raise AuthorityDagViolation("authority DAG has duplicate edges")
    node_ids = set(nodes)
    topological_order = _acyclic(node_ids, canonical_edges)
    roots = sorted(
        node for node in node_ids
        if not any(edge["to"] == node for edge in canonical_edges)
    )
    if active_policy_node and active_bank_hash:
        reachable = _reachable(canonical_edges, set(episode_nodes.values()))
        if active_policy_node not in reachable:
            raise AuthorityDagViolation(
                "active policy is not reachable from episode evidence"
            )
        active_bank = bank_store.get_by_hash(active_bank_hash)
        for binding in active_bank.memories.values():
            memory_node = memory_nodes[str(binding["memory_hash"])]
            if memory_node not in reachable:
                raise AuthorityDagViolation(
                    "active memory lacks an episode authority path"
                )
    body = {
        "schema_version": AUTHORITY_DAG_SCHEMA,
        "complete": True,
        "nodes": canonical_nodes,
        "edges": canonical_edges,
        "root_node_ids": roots,
        "active_policy_node_id": active_policy_node,
        "topological_order": topological_order,
        "node_count": len(canonical_nodes),
        "edge_count": len(canonical_edges),
    }
    return verify_authority_dag({
        **body,
        "dag_hash": hash_payload(body),
    })
