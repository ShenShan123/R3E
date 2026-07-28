from __future__ import annotations

import json
from pathlib import Path

import pytest

from r3e.arena.manifests import make_manifest
from r3e.arena.fake_system import run_fake_system
from r3e.memory.activation_guard import ActivationGuard
from r3e.memory.bank_store import ActiveBankStore
from r3e.memory.descriptor import build_failure_descriptor
from r3e.memory.episode_store import EpisodeStore
from r3e.memory.fake_adapter import DeterministicMemoryAdapter
from r3e.memory.conformance import (
    MemoryAdapterConformanceGate,
    MemoryAdapterConformanceViolation,
)
from r3e.memory.memory_store import MemoryStore
from r3e.memory.retriever import MemoryRetriever
from r3e.memory.runner import MemoryEvolutionRunner
from r3e.memory.schema import BudgetEnvelope, ExecutionPlan, VerifiedEpisode
from r3e.policy.registry_v2 import (
    get_active_policy,
    initialize_registry,
    load_registry,
    rollback_policy,
)
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload


ROOT = Path(__file__).resolve().parents[1]
H = lambda value: hash_payload({"value": value})


def _policy() -> PolicyState:
    return PolicyState.from_dict(
        json.loads(
            (ROOT / "configs/base_policy/frozen_base_policy_v1.json").read_text()
        )
    )


def _episode(policy: PolicyState, episode_id: str, poison_id: str):
    descriptor = build_failure_descriptor({
        "oracle_stage": "functional_compare",
        "sequential_context": True,
        "affected_roles": ["state"],
        "mismatch_pattern": "one_cycle_lag",
        "first_divergence_bucket": "cycle_1",
        "observable_artifact_hashes": {
            "challenge": H(f"challenge-{episode_id}"),
            "validity": H(f"validity-{episode_id}"),
        },
    })
    return VerifiedEpisode.create(
        episode_id=episode_id,
        round_id="R001",
        challenged_policy_instance_hash=policy.policy_hash,
        challenged_effective_policy_hash=policy.policy_hash,
        poison_id=poison_id,
        poison_payload_hash=H(f"poison-{poison_id}"),
        buggy_rtl_hash=H(f"buggy-{poison_id}"),
        oracle_evidence_hash=H(f"oracle-{poison_id}"),
        failure_descriptor=descriptor.to_dict(),
        blue_attempts=[{"oracle_ok": False}],
        final_outcome="unresolved",
        successful_patch_hash="",
        activated_memory_ids=[],
        resource_usage={
            "input_tokens": 1,
            "output_tokens": 1,
            "llm_calls": 1,
            "verifier_calls": 1,
            "wall_time_seconds": 1,
        },
    )


def _manifests(policy):
    target = make_manifest(
        [
            {
                "case_id": f"t{i}",
                "design": f"target_design_{i}",
                "challenged_policy_hash": policy.policy_hash,
                "sequential_depth": 1,
                "affected_role": "state",
                "effect": "one_cycle_lag",
                "first_divergence_cycle_bucket": "cycle_1",
            }
            for i in range(2)
        ],
        split="target",
    )
    non_target = make_manifest(
        [
            {
                "case_id": f"n{i}",
                "design": f"non_target_design_{i}",
                "sequential_depth": 0,
                "affected_role": "data",
                "effect": "constant_error",
                "first_divergence_cycle_bucket": "combinational",
            }
            for i in range(2)
        ],
        split="non_target",
    )
    return target, non_target


def _runner(tmp_path, adapter=None):
    registry = tmp_path / "registry.json"
    initialize_registry(
        ROOT / "configs/base_policy/frozen_base_policy_v1.json",
        registry,
    )
    parent = get_active_policy(load_registry(registry))
    episodes = EpisodeStore(tmp_path / "memory/episodes")
    episodes.append_episode(_episode(parent, "E1", "P1"))
    episodes.append_episode(_episode(parent, "E2", "P2"))
    memories = MemoryStore(
        tmp_path / "memory/library", episode_store=episodes
    )
    banks = ActiveBankStore(
        tmp_path / "memory/active_banks", memory_store=memories
    )
    retriever = MemoryRetriever(memories)
    guard = ActivationGuard(memories)
    target, non_target = _manifests(parent)
    runner = MemoryEvolutionRunner(
        registry_path=registry,
        episode_store=episodes,
        memory_store=memories,
        bank_store=banks,
        target_manifest=target,
        non_target_manifest=non_target,
        adapter=adapter or DeterministicMemoryAdapter(),
        round_id="MR002",
        work_dir=tmp_path / "memory/rounds/MR002",
        config={
            "minimum_support": 2,
            "maximum_active_memories": 1,
            "shadow_seeds": [101],
            "promotion_seeds": [201],
            "control_whitelist_hash": guard.control_whitelist_hash,
            "retriever_hash": retriever.retriever_hash,
            "activation_guard_hash": guard.guard_hash,
            "code_version": "test-version",
        },
        ledger_path=tmp_path / "decision_ledger.jsonl",
    )
    return runner, registry, episodes, memories, banks, parent


def test_memory_round_qualifies_bank_and_uses_atomic_policy_promotion(tmp_path):
    runner, registry, episodes, memories, banks, parent = _runner(tmp_path)
    summary = runner.run()
    assert summary["promoted"] is True
    assert len(summary["qualified_memory_ids"]) == 1
    active = get_active_policy(load_registry(registry))
    assert active.parent_policy_hash == parent.policy_hash
    assert active.memory_binding["active_memory_bank_hash"] == summary["bank_hash"]
    bank = banks.load_for_policy(active)
    assert set(bank.memories) == set(summary["qualified_memory_ids"])
    assert memories.current_status(
        summary["qualified_memory_ids"][0], 1
    ) == "active_dormant"
    assert len(episodes.audit()) == 2

    # Complete rounds are idempotent.
    assert runner.run() == summary

    restored = rollback_policy(
        registry,
        expected_active_policy_hash=active.policy_hash,
        ledger_path=tmp_path / "decision_ledger.jsonl",
    )
    assert get_active_policy(restored).policy_hash == parent.policy_hash
    # Rollback changes execution authority, never physical memory retention.
    assert len(episodes.audit()) == 2
    assert memories.audit()["memory_version_count"] == 1


def test_memory_round_resumes_after_shadow_adapter_failure(tmp_path):
    class FailOnce(DeterministicMemoryAdapter):
        def __init__(self):
            self.calls = 0

        def replay_memory(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("injected shadow interruption")
            return super().replay_memory(*args, **kwargs)

    runner, registry, episodes, memories, banks, parent = _runner(
        tmp_path, FailOnce()
    )
    with pytest.raises(RuntimeError, match="injected"):
        runner.run()
    resumed = MemoryEvolutionRunner(
        registry_path=registry,
        episode_store=episodes,
        memory_store=memories,
        bank_store=banks,
        target_manifest=runner.target,
        non_target_manifest=runner.non_target,
        adapter=DeterministicMemoryAdapter(),
        round_id="MR002",
        work_dir=runner.work_dir,
        config=runner.config,
        ledger_path=tmp_path / "decision_ledger.jsonl",
    )
    summary = resumed.run()
    assert summary["promoted"] is True
    assert get_active_policy(load_registry(registry)).parent_policy_hash == parent.policy_hash


def test_whole_policy_arena_automatically_appends_verified_episodes(tmp_path):
    workspace = tmp_path / "fake-system"
    summaries = run_fake_system(
        project_root=ROOT,
        workspace=workspace,
        start_round=1,
        rounds=2,
    )
    episodes = EpisodeStore(workspace / "memory/episodes").audit()
    assert len(summaries) == 2
    assert len(episodes) == 8
    assert {episode.round_id for episode in episodes} == {"R001", "R002"}
    for round_id in ("R001", "R002"):
        manifest = json.loads(
            (workspace / "rounds" / round_id / "verified_episodes.json").read_text()
        )
        assert len(manifest["episode_hashes"]) == 4


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("model_id", "other-model", "model"),
        ("budget_hash", H("other-budget"), "budget"),
        ("verifier_hash", H("other-verifier"), "verifier"),
        ("toolchain_hash", H("other-toolchain"), "toolchain"),
        ("adapter_command_hash", H("other-command"), "command"),
        ("adapter_result_hash", H("other-result"), "result"),
    ],
)
def test_memory_adapter_conformance_rejects_forged_envelope(
    field, value, message
):
    policy = _policy()
    adapter = DeterministicMemoryAdapter()
    gate = MemoryAdapterConformanceGate(adapter)
    case = {
        "case_id": "c",
        "design": "d",
        "expected_shadow_help": True,
    }
    budget = BudgetEnvelope(3, 3, 48000, 360)
    plan = ExecutionPlan.create(
        effective_policy_hash=policy.policy_hash,
        active_bank_hash="",
        activated_memory_ids=["CM"],
        controls={"enable_analyzers": ["first_divergence"]},
    )
    result = adapter.replay_memory(policy, case, 1, plan, budget)
    result[field] = value
    if field != "adapter_result_hash":
        result["adapter_result_hash"] = hash_payload({
            key: item for key, item in result.items()
            if key != "adapter_result_hash"
        })
    with pytest.raises(MemoryAdapterConformanceViolation, match=message):
        gate.validate_shadow(
            result,
            policy=policy,
            case=case,
            seed=1,
            execution_plan=plan,
            budget=budget,
        )
