"""Deterministic graph relations and bounded active-bank selection."""
from __future__ import annotations

from typing import Iterable

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload

from .schema import ControlMemory


def infer_relations(
    candidate: ControlMemory,
    existing: Iterable[ControlMemory],
) -> list[dict[str, str]]:
    relations = []
    candidate_fields = {
        (key, hash_payload(value))
        for key, value in candidate.trigger_predicate.items()
    }
    for memory in existing:
        existing_fields = {
            (key, hash_payload(value))
            for key, value in memory.trigger_predicate.items()
        }
        if candidate.effective_delta_hash == memory.effective_delta_hash:
            relation = "equivalent_effective_delta"
        elif candidate_fields > existing_fields:
            relation = "specializes"
        elif candidate_fields < existing_fields:
            relation = "generalizes"
        elif set(candidate.trigger_predicate) & set(memory.trigger_predicate):
            relation = "refines"
        else:
            continue
        relations.append({
            "source": candidate.memory_id,
            "target": memory.memory_id,
            "relation": relation,
        })
    return relations


def select_bounded_active_memories(
    rows: Iterable[tuple[ControlMemory, dict]],
    *,
    maximum_active: int,
) -> dict[str, int]:
    if maximum_active < 1:
        raise ValueError("maximum_active must be positive")
    ranked = sorted(
        rows,
        key=lambda item: (
            -int(item[1].get("summary", {}).get("helped", 0)),
            int(item[1].get("summary", {}).get("harmed", 0)),
            float(item[1].get("summary", {}).get("max_cost_ratio", 1.0)),
            item[0].memory_id,
        ),
    )
    return {
        memory.memory_id: memory.memory_version
        for memory, decision in ranked[:maximum_active]
        if decision.get("qualified")
    }


def merge_control_memories(
    memories: Iterable[ControlMemory],
    *,
    memory_id: str,
    origin_round_id: str,
    policy: PolicyState,
) -> ControlMemory:
    rows = list(memories)
    if len(rows) < 2:
        raise ValueError("memory merge requires at least two memories")
    if len({row.effective_delta_hash for row in rows}) != 1:
        raise ValueError("only equivalent control deltas may be merged")
    common_keys = set.intersection(
        *(set(row.trigger_predicate) for row in rows)
    )
    trigger = {
        key: rows[0].trigger_predicate[key]
        for key in sorted(common_keys)
        if all(
            row.trigger_predicate[key] == rows[0].trigger_predicate[key]
            for row in rows[1:]
        )
    }
    if not trigger:
        raise ValueError("memory merge has no safe common trigger")
    sources = {
        episode_id: digest
        for row in rows
        for episode_id, digest in row.source_episode_hashes.items()
    }
    return ControlMemory.create(
        memory_id=memory_id,
        memory_version=1,
        origin_round_id=origin_round_id,
        source_episode_ids=sorted(sources),
        source_episode_hashes=sources,
        created_under_policy_instance_hash=policy.policy_hash,
        created_under_effective_policy_hash=policy.policy_hash,
        trigger_predicate=trigger,
        control_delta=rows[0].control_delta,
        status="candidate",
        qualification_summary={},
        compatibility={"operation": "merge"},
    )


def split_control_memory(
    memory: ControlMemory,
    *,
    specialized_triggers: list[dict],
    memory_ids: list[str],
    origin_round_id: str,
    policy: PolicyState,
) -> list[ControlMemory]:
    if (
        not specialized_triggers
        or len(specialized_triggers) != len(memory_ids)
        or len(memory_ids) != len(set(memory_ids))
    ):
        raise ValueError("memory split ids/triggers mismatch")
    children = []
    for memory_id, trigger in zip(memory_ids, specialized_triggers):
        if any(
            key not in trigger or trigger[key] != value
            for key, value in memory.trigger_predicate.items()
        ):
            raise ValueError("split trigger must specialize its parent")
        children.append(ControlMemory.create(
            memory_id=memory_id,
            memory_version=1,
            origin_round_id=origin_round_id,
            source_episode_ids=list(memory.source_episode_ids),
            source_episode_hashes=dict(memory.source_episode_hashes),
            created_under_policy_instance_hash=policy.policy_hash,
            created_under_effective_policy_hash=policy.policy_hash,
            trigger_predicate=trigger,
            control_delta=memory.control_delta,
            status="candidate",
            qualification_summary={},
            compatibility={
                "operation": "split",
                "parent_memory_hash": memory.memory_hash,
            },
        ))
    return children
