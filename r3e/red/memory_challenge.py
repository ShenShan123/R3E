"""Policy- and bank-bound red challenges targeting RAAM behavior."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from r3e.memory.memory_store import MemoryStore
from r3e.memory.schema import ActiveMemoryBank
from r3e.memory.evidence import memory_definition
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_file, hash_payload


MEMORY_RED_OPERATORS = {
    "memory_bypass",
    "memory_deepening",
    "memory_conflict",
    "memory_transfer",
}
_PRIVATE_FIELDS = {
    "source_episode_ids",
    "source_episode_hashes",
    "qualification_evidence",
    "oracle_evidence",
    "successful_patch",
    "reference_patch",
    "target_replay_result",
    "child_validation",
    "control_delta",
}


class MemoryChallengeViolation(RuntimeError):
    """Raised when red sees private evidence or breaks policy/bank binding."""


def _reject_private(value: Any) -> None:
    if isinstance(value, dict):
        leaked = set(value) & _PRIVATE_FIELDS
        if leaked:
            raise MemoryChallengeViolation(
                f"private memory evidence leaked to red: {sorted(leaked)}"
            )
        for item in value.values():
            _reject_private(item)
    elif isinstance(value, list):
        for item in value:
            _reject_private(item)


def build_memory_capability_packet(
    policy: PolicyState,
    bank: ActiveMemoryBank,
    store: MemoryStore,
    *,
    public_regions: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    if (policy.memory_binding or {}) != bank.policy_binding:
        raise MemoryChallengeViolation("policy does not bind active memory bank")
    if bank.effective_policy_hash != policy.effective_policy_hash:
        raise MemoryChallengeViolation("bank effective policy binding mismatch")
    summaries = []
    for memory_id, binding in sorted(bank.memories.items()):
        memory = store.get_version(memory_id, int(binding["memory_version"]))
        if store.current_status(memory_id, memory.memory_version) != "active_dormant":
            raise MemoryChallengeViolation("bank contains revoked memory")
        summaries.append({
            "memory_id": memory_id,
            "memory_version": memory.memory_version,
            "memory_hash": memory.memory_hash,
            "memory_definition_hash": memory_definition(memory)[
                "definition_hash"
            ],
            "trigger_hash": hash_payload(memory.trigger_predicate),
            "effective_delta_hash": memory.effective_delta_hash,
        })
    regions = [dict(row) for row in public_regions]
    _reject_private(regions)
    allowed_region_fields = {
        "region_id", "region_kind", "failure_descriptor_hash",
        "observed_outcome", "policy_hash", "bank_hash",
    }
    if any(set(row) - allowed_region_fields for row in regions):
        raise MemoryChallengeViolation("public memory region has undeclared fields")
    packet = {
        "schema_version": "r3e-memory-red-capability-v1",
        "challenged_policy_id": policy.policy_id,
        "challenged_policy_hash": policy.policy_hash,
        "challenged_effective_policy_hash": policy.effective_policy_hash,
        "active_memory_bank_hash": bank.bank_hash,
        "effective_memory_bank_hash": bank.effective_memory_bank_hash,
        "active_memory_summaries": summaries,
        "public_memory_regions": sorted(
            regions, key=lambda row: str(row.get("region_id") or "")
        ),
        "allowed_operators": sorted(MEMORY_RED_OPERATORS),
    }
    packet["packet_hash"] = hash_payload(packet)
    return packet


def verify_memory_capability_packet(
    packet: dict[str, Any],
    *,
    policy: PolicyState,
    bank: ActiveMemoryBank,
) -> dict[str, Any]:
    _reject_private(packet)
    if packet.get("schema_version") != "r3e-memory-red-capability-v1":
        raise MemoryChallengeViolation("memory capability schema mismatch")
    if (
        packet.get("challenged_policy_hash") != policy.policy_hash
        or packet.get("challenged_effective_policy_hash")
        != policy.effective_policy_hash
        or packet.get("active_memory_bank_hash") != bank.bank_hash
        or packet.get("effective_memory_bank_hash")
        != bank.effective_memory_bank_hash
    ):
        raise MemoryChallengeViolation("memory capability policy/bank binding mismatch")
    body = {key: value for key, value in packet.items() if key != "packet_hash"}
    if packet.get("packet_hash") != hash_payload(body):
        raise MemoryChallengeViolation("memory capability packet hash mismatch")
    allowed_ids = set(bank.memories)
    summary_ids = {
        str(row.get("memory_id") or "")
        for row in packet.get("active_memory_summaries", [])
    }
    if summary_ids != allowed_ids:
        raise MemoryChallengeViolation("memory capability active set mismatch")
    return packet


def make_memory_challenge_plan(
    *,
    policy: PolicyState,
    bank: ActiveMemoryBank,
    packet: dict[str, Any],
    operator: str,
    poison_id: str,
    target_memory_ids: list[str],
    parent_poison_id: str,
    parent_source_semantic_hash: str,
) -> dict[str, Any]:
    verify_memory_capability_packet(packet, policy=policy, bank=bank)
    if operator not in MEMORY_RED_OPERATORS:
        raise MemoryChallengeViolation("unknown memory red operator")
    targets = sorted(set(target_memory_ids))
    if not targets or not set(targets).issubset(bank.memories):
        raise MemoryChallengeViolation("memory challenge target is outside active bank")
    if operator == "memory_conflict" and len(targets) < 2:
        raise MemoryChallengeViolation("memory conflict requires multiple active memories")
    if operator != "memory_conflict" and len(targets) != 1:
        raise MemoryChallengeViolation(
            "bypass/deepening/transfer require one target memory"
        )
    plan = {
        "schema_version": "r3e-memory-red-plan-v1",
        "operator": operator,
        "poison_id": poison_id,
        "challenged_policy_hash": policy.policy_hash,
        "active_memory_bank_hash": bank.bank_hash,
        "capability_packet_hash": packet["packet_hash"],
        "target_memory_ids": targets,
        "parent_poison_id": parent_poison_id,
        "parent_source_semantic_hash": parent_source_semantic_hash,
    }
    plan["plan_hash"] = hash_payload(plan)
    return plan


def materialize_memory_challenge(
    plan: dict[str, Any],
    parent: dict[str, Any],
    *,
    buggy_rtl: str | Path,
) -> dict[str, Any]:
    """Uniform deterministic dispatch after an adapter changes RTL source."""
    source = Path(buggy_rtl)
    if not source.is_file():
        raise MemoryChallengeViolation("materialized memory challenge source missing")
    source_hash = hash_file(source)
    if source_hash == plan.get("parent_source_semantic_hash"):
        raise MemoryChallengeViolation("materialized source is unchanged")
    operator = str(plan.get("operator") or "")
    if operator not in MEMORY_RED_OPERATORS:
        raise MemoryChallengeViolation("unknown memory challenge materializer")
    poison = {
        **parent,
        "poison_id": plan["poison_id"],
        "parent_poison_id": plan["parent_poison_id"],
        "challenged_policy_hash": plan["challenged_policy_hash"],
        "active_memory_bank_hash": plan["active_memory_bank_hash"],
        "memory_challenge_plan_hash": plan["plan_hash"],
        "target_memory_ids": list(plan["target_memory_ids"]),
        "evolution_operator": operator,
        "buggy_rtl": str(source),
        "source_semantic_hash": source_hash,
        "changed_modules": 1,
        "changed_blocks": 1,
    }
    if operator == "memory_bypass":
        poison["mechanism_variant"] = (
            f"bypass_{plan['plan_hash'].split(':', 1)[1][:12]}"
        )
    elif operator == "memory_deepening":
        poison["dependency_depth"] = int(
            parent.get("dependency_depth") or 0
        ) + 1
    elif operator == "memory_transfer":
        poison["transfer_depth"] = int(
            parent.get("transfer_depth") or 0
        ) + 1
    else:
        poison["composition_depth"] = int(
            parent.get("composition_depth") or 1
        ) + 1
    poison["normalized_diff_hash"] = hash_payload({
        "parent_source_semantic_hash": plan["parent_source_semantic_hash"],
        "source_semantic_hash": source_hash,
        "operator": operator,
        "target_memory_ids": plan["target_memory_ids"],
    })
    return poison


def verify_memory_challenge_execution(
    poison: dict[str, Any],
    *,
    plan: dict[str, Any],
    policy: PolicyState,
    bank: ActiveMemoryBank,
    parent: dict[str, Any],
) -> dict[str, Any]:
    body = {key: value for key, value in plan.items() if key != "plan_hash"}
    if (
        plan.get("schema_version") != "r3e-memory-red-plan-v1"
        or plan.get("plan_hash") != hash_payload(body)
    ):
        raise MemoryChallengeViolation("memory challenge plan hash mismatch")
    if (
        plan.get("challenged_policy_hash") != policy.policy_hash
        or plan.get("active_memory_bank_hash") != bank.bank_hash
        or poison.get("challenged_policy_hash") != policy.policy_hash
        or poison.get("active_memory_bank_hash") != bank.bank_hash
    ):
        raise MemoryChallengeViolation("memory challenge execution binding mismatch")
    if poison.get("memory_challenge_plan_hash") != plan["plan_hash"]:
        raise MemoryChallengeViolation("poison does not bind memory challenge plan")
    targets = set(plan["target_memory_ids"])
    if not targets.issubset(bank.memories):
        raise MemoryChallengeViolation("memory challenge targets stale memory")
    source = Path(str(poison.get("buggy_rtl") or ""))
    if not source.is_file() or hash_file(source) != poison.get("source_semantic_hash"):
        raise MemoryChallengeViolation("memory challenge source hash mismatch")
    if poison["source_semantic_hash"] == plan["parent_source_semantic_hash"]:
        raise MemoryChallengeViolation("memory red operator did not change source")
    if (
        poison.get("normalized_diff_hash") == parent.get("normalized_diff_hash")
        or int(poison.get("changed_modules") or 0) != 1
        or int(poison.get("changed_blocks") or 0) != 1
    ):
        raise MemoryChallengeViolation("memory red operator scope/semantics unchanged")
    operator = plan["operator"]
    if poison.get("evolution_operator") != operator:
        raise MemoryChallengeViolation("memory red operator label mismatch")
    if operator == "memory_bypass":
        if poison.get("mechanism_variant") == parent.get("mechanism_variant"):
            raise MemoryChallengeViolation("memory bypass did not change mechanism")
    elif operator == "memory_deepening":
        if int(poison.get("dependency_depth") or 0) <= int(
            parent.get("dependency_depth") or 0
        ):
            raise MemoryChallengeViolation("memory deepening did not increase depth")
    elif operator == "memory_conflict":
        if len(targets) < 2 or int(poison.get("composition_depth") or 0) <= int(
            parent.get("composition_depth") or 0
        ):
            raise MemoryChallengeViolation("memory conflict did not compose targets")
    elif operator == "memory_transfer":
        if int(poison.get("transfer_depth") or 0) <= int(
            parent.get("transfer_depth") or 0
        ):
            raise MemoryChallengeViolation(
                "memory transfer did not change design context"
            )
    return poison
