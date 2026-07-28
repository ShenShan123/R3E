from __future__ import annotations

import json
from pathlib import Path

import pytest

from r3e.memory.activation_guard import ActivationGuard, ActivationViolation
from r3e.memory.audit import audit_memory_system
from r3e.memory.bank_store import ActiveBankStore, ActiveBankStoreViolation
from r3e.memory.compatibility import classify_policy_compatibility
from r3e.memory.consolidator import (
    infer_relations,
    merge_control_memories,
    select_bounded_active_memories,
    split_control_memory,
)
from r3e.memory.descriptor import build_failure_descriptor
from r3e.memory.episode_store import EpisodeStore, EpisodeStoreViolation
from r3e.memory.lifecycle import MemoryLifecycleViolation
from r3e.memory.memory_store import MemoryStore, MemoryStoreViolation
from r3e.memory.plan_compiler import MemoryAwarePlanCompiler
from r3e.memory.promotion import build_memory_bank_policy_candidate
from r3e.memory.qualification_gate import decide_memory_qualification
from r3e.memory.relation_graph import MemoryGraphViolation, MemoryRelationGraph
from r3e.memory.retriever import MemoryRetriever
from r3e.memory.runtime import MemoryRuntime
from r3e.memory.execution_trace import ExecutionTraceViolation
from r3e.memory.transition import transition_active_bank
from r3e.memory.schema import (
    BudgetEnvelope,
    ControlMemory,
    MemoryLifecycleEvent,
    MemoryValidationError,
    RuntimeContext,
    VerifiedEpisode,
)
from r3e.memory.shadow_replay import ShadowReplayViolation, run_shadow_replay
from r3e.memory.whitelist import load_whitelist
from r3e.memory.fake_adapter import DeterministicMemoryAdapter
from r3e.memory.qualification_gate import MemoryQualificationViolation
from r3e.policy.registry_v2 import (
    get_active_policy,
    initialize_registry,
    load_registry,
    register_candidate,
)
from r3e.policy.repair import repair_one as formal_repair_one
from r3e.policy.runtime import PolicyRuntimeViolation
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload
from r3e.protocol.events import EventLogger, read_events
from r3e.red.memory_challenge import (
    MemoryChallengeViolation,
    build_memory_capability_packet,
    make_memory_challenge_plan,
    materialize_memory_challenge,
    verify_memory_challenge_execution,
)
from r3e.red.feedback_packet import (
    build_red_search_context,
    verify_red_search_context,
)
from r3e.protocol.hashing import hash_file


ROOT = Path(__file__).resolve().parents[1]
H = lambda value: hash_payload({"value": value})


def _policy() -> PolicyState:
    return PolicyState.from_dict(
        json.loads(
            (ROOT / "configs/base_policy/frozen_base_policy_v1.json").read_text(
                encoding="utf-8"
            )
        )
    )


def _descriptor(**updates):
    fields = {
        "oracle_stage": "functional_compare",
        "sequential_context": True,
        "temporal_relation": "candidate_lags_golden",
        "cycle_offset_bucket": 1,
        "affected_roles": ["output", "state"],
        "observable_artifact_hashes": {
            "waveform": H("waveform"),
            "oracle": H("oracle"),
        },
    }
    fields.update(updates)
    return build_failure_descriptor(fields)


def _episode(policy: PolicyState, *, episode_id: str = "E_R001_001"):
    return VerifiedEpisode.create(
        episode_id=episode_id,
        round_id="R001",
        challenged_policy_instance_hash=policy.policy_hash,
        challenged_effective_policy_hash=policy.policy_hash,
        poison_id="P001",
        poison_payload_hash=H("poison"),
        buggy_rtl_hash=H("buggy"),
        oracle_evidence_hash=H("oracle-evidence"),
        failure_descriptor=_descriptor().to_dict(),
        blue_attempts=[{"attempt": 1, "result_hash": H("attempt")}],
        final_outcome="unresolved",
        successful_patch_hash="",
        activated_memory_ids=[],
        resource_usage={
            "input_tokens": 10,
            "output_tokens": 5,
            "llm_calls": 1,
            "verifier_calls": 1,
            "wall_time_seconds": 0.5,
        },
    )


def _memory(policy: PolicyState, episode: VerifiedEpisode, **updates):
    fields = {
        "memory_id": "CM_temporal_lag_001",
        "memory_version": 1,
        "origin_round_id": "R001",
        "source_episode_ids": [episode.episode_id],
        "source_episode_hashes": {episode.episode_id: episode.episode_hash},
        "created_under_policy_instance_hash": policy.policy_hash,
        "created_under_effective_policy_hash": policy.policy_hash,
        "trigger_predicate": {
            "oracle_stage": "functional_compare",
            "sequential_context": True,
            "temporal_relation": "candidate_lags_golden",
            "cycle_offset_bucket": 1,
            "affected_roles": ["state", "output"],
        },
        "control_delta": {
            "enable_analyzers": ["temporal_alignment", "state_transition_slice"],
            "rtl_slice_mode": "sequential_cone",
            "evidence_window_before": 2,
            "evidence_window_after": 4,
            "candidate_plan": {
                "initial_candidates": 1,
                "revision_rounds": 2,
            },
            "candidate_ranking": "verifier_guided",
            "early_stop": "first_verified",
        },
        "status": "candidate",
        "qualification_summary": {},
        "compatibility": {},
    }
    fields.update(updates)
    return ControlMemory.create(**fields)


def _advance(
    store: MemoryStore,
    memory: ControlMemory,
    previous: str,
    new: str,
    policy_hash: str,
) -> None:
    store.update_lifecycle(
        memory.memory_id,
        MemoryLifecycleEvent.create(
            memory_id=memory.memory_id,
            memory_version=memory.memory_version,
            memory_hash=memory.memory_hash,
            previous_status=previous,
            new_status=new,
            effective_policy_hash=policy_hash,
            reason_code=f"test_{new}",
            evidence_hash=H(f"evidence-{new}"),
        ),
    )


def _active_store(tmp_path: Path):
    policy = _policy()
    episodes = EpisodeStore(tmp_path / "episodes")
    episode = _episode(policy)
    episodes.append_episode(episode)
    store = MemoryStore(tmp_path / "library", episode_store=episodes)
    memory = _memory(policy, episode)
    store.add_candidate(memory)
    _advance(store, memory, "candidate", "shadow_testing", policy.policy_hash)
    _advance(store, memory, "shadow_testing", "replay_qualified", policy.policy_hash)
    _advance(store, memory, "replay_qualified", "bank_candidate", policy.policy_hash)
    _advance(store, memory, "bank_candidate", "active_dormant", policy.policy_hash)
    return policy, episodes, store, memory


def test_episode_archive_is_append_only_reconstructable_and_resume_safe(tmp_path):
    policy = _policy()
    store = EpisodeStore(tmp_path / "episodes")
    episode = _episode(policy)
    assert store.append_episode(episode) == episode.episode_hash
    assert store.append_episode(episode) == episode.episode_hash
    assert store.get(episode.episode_id) == episode
    assert store.audit() == [episode]

    changed = _episode(policy)
    payload = changed.to_dict()
    payload["poison_id"] = "P999"
    payload["episode_hash"] = hash_payload(
        {key: value for key, value in payload.items() if key != "episode_hash"}
    )
    with pytest.raises(EpisodeStoreViolation):
        store.append_episode(payload)


def test_episode_lifecycle_and_bank_writes_recover_after_injected_interruptions(
    tmp_path, monkeypatch
):
    import r3e.memory.episode_store as episode_module
    import r3e.memory.memory_store as memory_module
    import r3e.memory.bank_store as bank_module

    policy = _policy()
    episodes = EpisodeStore(tmp_path / "episodes")
    episode = _episode(policy)
    real_episode_append = episode_module.append_ledger
    monkeypatch.setattr(
        episode_module,
        "append_ledger",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("episode interruption")
        ),
    )
    with pytest.raises(RuntimeError, match="episode interruption"):
        episodes.append_episode(episode)
    monkeypatch.setattr(episode_module, "append_ledger", real_episode_append)
    episodes.append_episode(episode)
    assert episodes.audit() == [episode]

    store = MemoryStore(tmp_path / "library", episode_store=episodes)
    memory = _memory(policy, episode)
    store.add_candidate(memory)
    lifecycle_event = MemoryLifecycleEvent.create(
        memory_id=memory.memory_id,
        memory_version=1,
        memory_hash=memory.memory_hash,
        previous_status="candidate",
        new_status="shadow_testing",
        effective_policy_hash=policy.policy_hash,
        reason_code="failure_injection",
        evidence_hash=H("failure-injection"),
    )
    real_memory_append = memory_module.append_ledger
    monkeypatch.setattr(
        memory_module,
        "append_ledger",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("lifecycle interruption")
        ),
    )
    with pytest.raises(RuntimeError, match="lifecycle interruption"):
        store.update_lifecycle(memory.memory_id, lifecycle_event)
    assert store.current_status(memory.memory_id, 1) == "candidate"
    monkeypatch.setattr(memory_module, "append_ledger", real_memory_append)
    store.update_lifecycle(memory.memory_id, lifecycle_event)
    _advance(store, memory, "shadow_testing", "replay_qualified", policy.policy_hash)
    _advance(store, memory, "replay_qualified", "bank_candidate", policy.policy_hash)
    _advance(store, memory, "bank_candidate", "active_dormant", policy.policy_hash)

    retriever = MemoryRetriever(store)
    guard = ActivationGuard(store)
    child, bank = build_memory_bank_policy_candidate(
        parent=policy,
        store=store,
        memory_versions={memory.memory_id: 1},
        bank_id="AMB_FAILURE",
        bank_version=1,
        retriever_hash=retriever.retriever_hash,
        activation_guard_hash=guard.guard_hash,
        control_whitelist_hash=guard.control_whitelist_hash,
        round_id="R2",
        policy_id="B_FAILURE",
    )
    banks = ActiveBankStore(tmp_path / "banks", memory_store=store)
    real_bank_append = bank_module.append_ledger
    monkeypatch.setattr(
        bank_module,
        "append_ledger",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("bank interruption")
        ),
    )
    with pytest.raises(RuntimeError, match="bank interruption"):
        banks.add_candidate(bank)
    monkeypatch.setattr(bank_module, "append_ledger", real_bank_append)
    banks.add_candidate(bank)
    assert banks.load_for_policy(child) == bank


def test_control_memory_rejects_prompt_patch_authority_and_red_truth(policy=None):
    policy = _policy()
    episode = _episode(policy)
    for delta in (
        {"prompt": "reuse old answer"},
        {"patch": "assign y = a;"},
        {"oracle": "weaken"},
        {"registry": "write"},
        {"model_route_id": "unfrozen"},
    ):
        with pytest.raises(MemoryValidationError):
            _memory(policy, episode, control_delta=delta)
    with pytest.raises(MemoryValidationError):
        _memory(policy, episode, trigger_predicate={"mutation_family": "off_by_one"})
    with pytest.raises(MemoryValidationError):
        _descriptor(red_truth="secret")


def test_frozen_control_whitelist_is_hash_reconstructable():
    whitelist = load_whitelist(
        ROOT / "configs/memory/control_whitelist_v1.json"
    )
    assert whitelist["forbid_prompt_payload"] is True
    assert whitelist["forbid_authority_changes"] is True


def test_memory_versions_and_lifecycle_are_append_only(tmp_path):
    policy, _episodes, store, memory = _active_store(tmp_path)
    assert store.current_status(memory.memory_id, 1) == "active_dormant"
    assert store.get_version(memory.memory_id, 1).status == "candidate"
    assert store.audit() == {
        "memory_version_count": 1,
        "lifecycle_event_count": 4,
        "evidence_link_count": 1,
        "qualification_version_count": 0,
    }
    stale = MemoryLifecycleEvent.create(
        memory_id=memory.memory_id,
        memory_version=1,
        memory_hash=memory.memory_hash,
        previous_status="candidate",
        new_status="retired",
        effective_policy_hash=policy.policy_hash,
        reason_code="stale_writer",
        evidence_hash=H("stale"),
    )
    with pytest.raises(MemoryStoreViolation):
        store.update_lifecycle(memory.memory_id, stale)
    with pytest.raises(MemoryLifecycleViolation):
        _advance(store, memory, "active_dormant", "candidate", policy.policy_hash)


def test_effective_delta_and_trigger_are_deduplicated(tmp_path):
    policy = _policy()
    episodes = EpisodeStore(tmp_path / "episodes")
    first_episode = _episode(policy, episode_id="E1")
    second_episode = _episode(policy, episode_id="E2")
    episodes.append_episode(first_episode)
    episodes.append_episode(second_episode)
    store = MemoryStore(tmp_path / "library", episode_store=episodes)
    first = _memory(policy, first_episode)
    second = _memory(
        policy,
        second_episode,
        memory_id=first.memory_id,
    )
    store.add_candidate(first)
    assert store.add_candidate(second) == first.memory_hash
    evidence = store.evidence_set(first.memory_id, first.memory_version)
    assert evidence["support_count"] == 2
    assert {row["episode_id"] for row in evidence["links"]} == {"E1", "E2"}
    # Replaying the same episode link is idempotent.
    assert store.add_candidate(second) == first.memory_hash
    assert store.evidence_set(first.memory_id, first.memory_version) == evidence


def test_shadow_pair_classification_and_cost_gate_are_runner_owned(tmp_path):
    policy = _policy()
    episode = _episode(policy)
    memory = _memory(policy, episode)
    budget = BudgetEnvelope(2, 2, 1000, 10)

    def evaluator(_policy, case, _seed, plan, frozen_budget):
        helped = bool(plan.activated_memory_ids)
        return {
            "oracle_ok": helped,
            "model_id": "fake-blue-v1",
            "budget_hash": frozen_budget.budget_hash,
            "verifier_hash": H("verifier"),
            "toolchain_hash": H("toolchain"),
            "oracle_evidence_hash": H(
                f"oracle-{case['design']}-{'shadow' if helped else 'control'}"
            ),
            "design": case["design"],
            "triggered": helped,
            "resource_usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "llm_calls": 1,
                "verifier_calls": 1,
                "wall_time_seconds": 1.0 if not helped else 1.1,
            },
        }

    rows = [
        run_shadow_replay(
            case={"case_id": f"c{index}", "design": f"d{index}"},
            policy=policy,
            memory=memory,
            seed=1,
            budget=budget,
            evaluator=evaluator,
        )
        for index in range(2)
    ]
    assert {row.outcome for row in rows} == {"helped"}
    decision = decide_memory_qualification(
        memory,
        rows,
        provenance={
            "manifest_hash": H("manifest"),
            "toolchain_fingerprint_hash": H("toolchain"),
            "code_commit_sha": "test-commit",
            "control_whitelist_hash": H("whitelist"),
        },
    )
    assert decision["qualified"] is True
    assert all(decision["gates"].values())

    def mismatched(_policy, case, seed, plan, frozen_budget):
        result = evaluator(_policy, case, seed, plan, frozen_budget)
        if plan.activated_memory_ids:
            result["model_id"] = "other-model"
        return result

    with pytest.raises(ShadowReplayViolation, match="model_id"):
        run_shadow_replay(
            case={"case_id": "c", "design": "d"},
            policy=policy,
            memory=memory,
            seed=1,
            budget=budget,
            evaluator=mismatched,
        )


def test_cost_regression_blocks_memory_qualification():
    policy = _policy()
    memory = _memory(policy, _episode(policy))
    budget = BudgetEnvelope(2, 2, 1000, 10)

    def evaluator(_policy, case, _seed, plan, frozen_budget):
        shadow = bool(plan.activated_memory_ids)
        return {
            "oracle_ok": shadow,
            "model_id": "fake",
            "budget_hash": frozen_budget.budget_hash,
            "verifier_hash": H("verifier"),
            "toolchain_hash": H("toolchain"),
            "oracle_evidence_hash": H(
                f"oracle-{case['design']}-{'shadow' if shadow else 'control'}"
            ),
            "design": case["design"],
            "resource_usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "llm_calls": 1,
                "verifier_calls": 1,
                "wall_time_seconds": 1 if not shadow else 2,
            },
        }

    rows = [
        run_shadow_replay(
            case={"case_id": f"c{i}", "design": f"d{i}"},
            policy=policy,
            memory=memory,
            seed=1,
            budget=budget,
            evaluator=evaluator,
        )
        for i in range(2)
    ]
    decision = decide_memory_qualification(
        memory,
        rows,
        provenance={
            "manifest_hash": H("manifest"),
            "toolchain_fingerprint_hash": H("toolchain"),
            "code_commit_sha": "test",
            "control_whitelist_hash": H("whitelist"),
        },
    )
    assert decision["qualified"] is False
    assert decision["gates"]["cost"] is False


def test_active_bank_requires_whole_policy_candidate_and_current_reactivation(tmp_path):
    parent, _episodes, store, memory = _active_store(tmp_path)
    retriever = MemoryRetriever(store)
    guard = ActivationGuard(store)
    whitelist_hash = guard.control_whitelist_hash
    candidate, bank = build_memory_bank_policy_candidate(
        parent=parent,
        store=store,
        memory_versions={memory.memory_id: 1},
        bank_id="AMB_R002",
        bank_version=1,
        retriever_hash=retriever.retriever_hash,
        activation_guard_hash=guard.guard_hash,
        control_whitelist_hash=whitelist_hash,
        round_id="R002",
        policy_id="B0_R002_MEMORY",
    )
    assert candidate.parent_policy_hash == parent.policy_hash
    assert candidate.memory_binding["active_memory_bank_hash"] == bank.bank_hash
    assert bank.effective_policy_hash == candidate.policy_hash

    matches = retriever.retrieve(_descriptor(), bank)
    context = RuntimeContext(
        effective_policy_hash=candidate.policy_hash,
        policy_instance_hash=candidate.policy_hash,
        available_analyzers=(
            "first_divergence",
            "temporal_alignment",
            "state_transition_slice",
        ),
        budget=BudgetEnvelope(3, 3, 48000, 360),
        control_whitelist_hash=whitelist_hash,
    )
    decision = guard.reactivate(
        matches=matches,
        active_policy=candidate,
        active_bank=bank,
        runtime_context=context,
    )
    assert decision.abstained is False
    plan = MemoryAwarePlanCompiler(store, bank).compile(
        base_policy=candidate,
        reactivation=decision,
    )
    assert plan.activated_memory_ids == (memory.memory_id,)
    assert plan.controls["rtl_slice_mode"] == "sequential_cone"
    assert plan.memory_token_cost == 0
    assert "prompt" not in json.dumps(plan.to_dict()).lower()

    no_match = retriever.retrieve(
        _descriptor(temporal_relation="candidate_leads_golden"),
        bank,
    )
    abstained = guard.reactivate(
        matches=no_match,
        active_policy=candidate,
        active_bank=bank,
        runtime_context=context,
    )
    assert abstained.abstained is True
    default_plan = MemoryAwarePlanCompiler(store, bank).compile(
        base_policy=candidate,
        reactivation=abstained,
    )
    assert default_plan.activated_memory_ids == ()

    tight = RuntimeContext(
        effective_policy_hash=candidate.policy_hash,
        policy_instance_hash=candidate.policy_hash,
        available_analyzers=(
            "first_divergence",
            "temporal_alignment",
            "state_transition_slice",
        ),
        budget=BudgetEnvelope(2, 2, 48000, 360),
        control_whitelist_hash=whitelist_hash,
    )
    budget_abstention = guard.reactivate(
        matches=matches,
        active_policy=candidate,
        active_bank=bank,
        runtime_context=tight,
    )
    assert budget_abstention.reason_code == "no_eligible_match"

    expanded = RuntimeContext(
        effective_policy_hash=candidate.policy_hash,
        policy_instance_hash=candidate.policy_hash,
        available_analyzers=tight.available_analyzers,
        budget=BudgetEnvelope(4, 4, 48000, 360),
        control_whitelist_hash=whitelist_hash,
    )
    with pytest.raises(ActivationViolation, match="exceeds active policy"):
        guard.reactivate(
            matches=matches,
            active_policy=candidate,
            active_bank=bank,
            runtime_context=expanded,
        )


def test_candidate_memory_and_unpromoted_bank_cannot_enter_runtime(tmp_path):
    parent = _policy()
    episodes = EpisodeStore(tmp_path / "episodes")
    episode = _episode(parent)
    episodes.append_episode(episode)
    store = MemoryStore(tmp_path / "library", episode_store=episodes)
    candidate_memory = _memory(parent, episode)
    store.add_candidate(candidate_memory)
    with pytest.raises(MemoryValidationError, match="active_dormant"):
        from r3e.memory.schema import ActiveMemoryBank

        ActiveMemoryBank.create(
            bank_id="bad",
            bank_version=1,
            policy_instance_hash=parent.policy_hash,
            effective_policy_hash=parent.policy_hash,
            memories={
                candidate_memory.memory_id: {
                    "memory_version": 1,
                    "memory_hash": candidate_memory.memory_hash,
                    "status": "candidate",
                }
            },
            retriever_hash=H("retriever"),
            activation_guard_hash=H("guard"),
            control_whitelist_hash=H("whitelist"),
        )

    parent, _episodes, store, memory = _active_store(tmp_path / "qualified")
    retriever = MemoryRetriever(store)
    guard = ActivationGuard(store)
    candidate, bank = build_memory_bank_policy_candidate(
        parent=parent,
        store=store,
        memory_versions={memory.memory_id: 1},
        bank_id="AMB",
        bank_version=1,
        retriever_hash=retriever.retriever_hash,
        activation_guard_hash=guard.guard_hash,
        control_whitelist_hash=guard.control_whitelist_hash,
        round_id="R2",
        policy_id="B_MEMORY",
    )
    context = RuntimeContext(
        effective_policy_hash=parent.policy_hash,
        policy_instance_hash=parent.policy_hash,
        available_analyzers=(
            "temporal_alignment",
            "state_transition_slice",
        ),
        budget=BudgetEnvelope(3, 3, 1000, 10),
        control_whitelist_hash=guard.control_whitelist_hash,
    )
    with pytest.raises(ActivationViolation, match="effective policy"):
        guard.reactivate(
            matches=retriever.retrieve(_descriptor(), bank),
            active_policy=parent,
            active_bank=bank,
            runtime_context=context,
        )


@pytest.mark.parametrize("revoked_status", ["stale", "harmful"])
def test_stale_or_harmful_memory_cannot_reactivate(tmp_path, revoked_status):
    parent, _episodes, store, memory = _active_store(tmp_path)
    retriever = MemoryRetriever(store)
    guard = ActivationGuard(store)
    whitelist_hash = guard.control_whitelist_hash
    candidate, bank = build_memory_bank_policy_candidate(
        parent=parent,
        store=store,
        memory_versions={memory.memory_id: 1},
        bank_id="AMB",
        bank_version=1,
        retriever_hash=retriever.retriever_hash,
        activation_guard_hash=guard.guard_hash,
        control_whitelist_hash=whitelist_hash,
        round_id="R2",
        policy_id="B_MEMORY",
    )
    _advance(store, memory, "active_dormant", revoked_status, candidate.policy_hash)
    with pytest.raises(Exception, match="non-executable"):
        retriever.retrieve(_descriptor(), bank)


def test_conflicting_memories_force_deterministic_abstention(tmp_path):
    parent, episodes, store, first = _active_store(tmp_path)
    second_episode = _episode(parent, episode_id="E_SECOND")
    episodes.append_episode(second_episode)
    second = _memory(
        parent,
        second_episode,
        memory_id="CM_conflict",
        control_delta={
            "enable_analyzers": ["temporal_alignment"],
            "rtl_slice_mode": "local_block",
        },
    )
    store.add_candidate(second)
    _advance(store, second, "candidate", "shadow_testing", parent.policy_hash)
    _advance(store, second, "shadow_testing", "replay_qualified", parent.policy_hash)
    _advance(store, second, "replay_qualified", "bank_candidate", parent.policy_hash)
    _advance(store, second, "bank_candidate", "active_dormant", parent.policy_hash)
    retriever = MemoryRetriever(store)
    guard = ActivationGuard(store)
    whitelist = guard.control_whitelist_hash
    candidate, bank = build_memory_bank_policy_candidate(
        parent=parent,
        store=store,
        memory_versions={first.memory_id: 1, second.memory_id: 1},
        bank_id="AMB_CONFLICT",
        bank_version=1,
        retriever_hash=retriever.retriever_hash,
        activation_guard_hash=guard.guard_hash,
        control_whitelist_hash=whitelist,
        round_id="R2",
        policy_id="B_CONFLICT",
    )
    matches = retriever.retrieve(_descriptor(), bank)
    assert len(matches) == 2
    decision = guard.reactivate(
        matches=matches,
        active_policy=candidate,
        active_bank=bank,
        runtime_context=RuntimeContext(
            effective_policy_hash=candidate.policy_hash,
            policy_instance_hash=candidate.policy_hash,
            available_analyzers=(
                "temporal_alignment",
                "state_transition_slice",
            ),
            budget=BudgetEnvelope(3, 3, 1000, 10),
            control_whitelist_hash=whitelist,
        ),
    )
    assert decision.abstained is True
    assert decision.reason_code == "control_delta_conflict"


def test_policy_registry_accepts_hash_bound_memory_candidate(tmp_path):
    parent, _episodes, store, memory = _active_store(tmp_path)
    retriever = MemoryRetriever(store)
    guard = ActivationGuard(store)
    candidate, _bank = build_memory_bank_policy_candidate(
        parent=parent,
        store=store,
        memory_versions={memory.memory_id: 1},
        bank_id="AMB",
        bank_version=1,
        retriever_hash=retriever.retriever_hash,
        activation_guard_hash=guard.guard_hash,
        control_whitelist_hash=guard.control_whitelist_hash,
        round_id="R2",
        policy_id="B_MEMORY",
    )
    registry = tmp_path / "registry.json"
    initialize_registry(
        ROOT / "configs/base_policy/frozen_base_policy_v1.json",
        registry,
    )
    registered = register_candidate(registry, candidate)
    stored = PolicyState.from_dict(registered["policies"][candidate.policy_id]["policy"])
    assert stored.memory_binding == candidate.memory_binding
    assert get_active_policy(load_registry(registry)).policy_id == "B0"


def test_bank_store_runtime_and_audit_reconstruct_full_chain(tmp_path):
    parent, episodes, store, memory = _active_store(tmp_path)
    retriever = MemoryRetriever(store)
    guard = ActivationGuard(store)
    whitelist = guard.control_whitelist_hash
    candidate, bank = build_memory_bank_policy_candidate(
        parent=parent,
        store=store,
        memory_versions={memory.memory_id: 1},
        bank_id="AMB",
        bank_version=1,
        retriever_hash=retriever.retriever_hash,
        activation_guard_hash=guard.guard_hash,
        control_whitelist_hash=whitelist,
        round_id="R2",
        policy_id="B_MEMORY",
    )
    banks = ActiveBankStore(tmp_path / "banks", memory_store=store)
    banks.add_candidate(bank)
    assert banks.load_for_policy(candidate) == bank
    events = EventLogger(tmp_path / "events", code_version="test-version")
    runtime = MemoryRuntime(
        bank_store=banks,
        retriever=retriever,
        activation_guard=guard,
        event_logger=events,
    )
    plan = runtime.compile_plan(
        observable_failure={
            key: value
            for key, value in _descriptor().features.items()
        },
        active_policy=candidate,
        runtime_context=RuntimeContext(
            effective_policy_hash=candidate.policy_hash,
            policy_instance_hash=candidate.policy_hash,
            available_analyzers=(
                "first_divergence",
                "temporal_alignment",
                "state_transition_slice",
            ),
            budget=BudgetEnvelope(3, 3, 48000, 360),
            control_whitelist_hash=whitelist,
        ),
        round_id="R2",
    )
    assert plan.activated_memory_ids == (memory.memory_id,)
    event = read_events(tmp_path / "events/memory.jsonl")[0]
    assert event["execution_plan_hash"] == plan.plan_hash

    def executor(*, case, work_dir, policy, execution_plan, trace_recorder):
        assert case["case_id"] == "current"
        assert work_dir == tmp_path / "work"
        assert execution_plan.activated_memory_ids == (memory.memory_id,)
        for analyzer in execution_plan.controls.get("enable_analyzers", []):
            trace_recorder.run_analyzer(
                analyzer, lambda analyzer=analyzer: {"analyzer": analyzer}
            )
        slice_mode = execution_plan.controls.get("rtl_slice_mode", "none")
        if slice_mode != "none":
            trace_recorder.run_slice(
                slice_mode, lambda: {"slice": slice_mode}
            )
        trace_recorder.run_candidate_generation(
            execution_plan.controls.get("initial_candidates", 0),
            lambda: ["candidate"],
        )
        for index in range(
            1, execution_plan.controls.get("revision_rounds", 0) + 1
        ):
            trace_recorder.run_revision(
                index, lambda index=index: {"revision": index}
            )
        for verifier in execution_plan.controls.get("verifier_order", []):
            trace_recorder.run_verifier(
                verifier, lambda verifier=verifier: {"verifier": verifier}
            )
        trace_recorder.stop(execution_plan.controls["early_stop"])
        return {
            "effective_policy_hash": policy.policy_hash,
            "execution_plan_hash": execution_plan.plan_hash,
            "memory_prompt_tokens": 0,
            "oracle_ok": True,
        }

    result = runtime.repair_one(
        case={"case_id": "current"},
        work_dir=tmp_path / "work",
        observable_failure=dict(_descriptor().features),
        active_policy=candidate,
        runtime_context=RuntimeContext(
            effective_policy_hash=candidate.policy_hash,
            policy_instance_hash=candidate.policy_hash,
            available_analyzers=(
                "first_divergence",
                "temporal_alignment",
                "state_transition_slice",
            ),
            budget=BudgetEnvelope(3, 3, 48000, 360),
            control_whitelist_hash=whitelist,
        ),
        executor=executor,
        round_id="R2",
    )
    assert result["memory_token_cost"] == 0
    assert result["execution_trace"]["execution_plan_hash"] == plan.plan_hash

    def echo_only(*, policy, execution_plan, **_kwargs):
        return {
            "effective_policy_hash": policy.policy_hash,
            "execution_plan_hash": execution_plan.plan_hash,
            "memory_prompt_tokens": 0,
            "oracle_ok": True,
        }

    with pytest.raises(ExecutionTraceViolation, match="analyzer set"):
        runtime.repair_one(
            case={"case_id": "echo"},
            work_dir=tmp_path / "echo",
            observable_failure=dict(_descriptor().features),
            active_policy=candidate,
            runtime_context=RuntimeContext(
                effective_policy_hash=candidate.policy_hash,
                policy_instance_hash=candidate.policy_hash,
                available_analyzers=(
                    "first_divergence",
                    "temporal_alignment",
                    "state_transition_slice",
                ),
                budget=BudgetEnvelope(3, 3, 48000, 360),
                control_whitelist_hash=whitelist,
            ),
            executor=echo_only,
        )
    with pytest.raises(PolicyRuntimeViolation, match="cannot silently ignore"):
        formal_repair_one({"case_id": "current"}, tmp_path / "bad", candidate)

    summary = audit_memory_system(
        episode_store=episodes,
        memory_store=store,
        bank_store=banks,
        active_policy=candidate,
    )
    assert summary["episode_count"] == 1
    assert summary["active_memory_bank_hash"] == bank.bank_hash


def test_policy_transition_preserves_memory_and_requires_incremental_revalidation(tmp_path):
    parent, _episodes, store, memory = _active_store(tmp_path)
    compatible = parent.with_updates(
        policy_id="B1",
        parent_policy_id=parent.policy_id,
        parent_policy_hash=parent.policy_hash,
        status="candidate",
        configuration={**parent.configuration, "prompt_lens_id": "temporal_v1"},
    )
    result = classify_policy_compatibility(memory, parent, compatible)
    assert result["classification"] == "static_compatible"
    assert store.get_version(memory.memory_id, 1).memory_hash == memory.memory_hash

    changed = compatible.with_updates(
        policy_id="B2",
        configuration={**parent.configuration, "evidence_k": 3},
    )
    result = classify_policy_compatibility(memory, parent, changed)
    assert result["classification"] == "incremental_revalidation_required"
    assert result["next_status"] == "revalidation_required"


def test_memory_relation_graph_rejects_lineage_cycle(tmp_path):
    graph = MemoryRelationGraph(tmp_path / "relations.jsonl")
    graph.add(source="M2", target="M1", relation="derived_from", evidence_hash=H("1"))
    graph.add(source="M3", target="M2", relation="refines", evidence_hash=H("2"))
    with pytest.raises(MemoryGraphViolation, match="cycle"):
        graph.add(source="M1", target="M3", relation="generalizes", evidence_hash=H("3"))


def test_policy_transition_suspends_only_affected_memory_and_retains_objects(tmp_path):
    previous, episodes, store, affected = _active_store(tmp_path)
    other_episode = _episode(previous, episode_id="E_STATIC")
    episodes.append_episode(other_episode)
    static = _memory(
        previous,
        other_episode,
        memory_id="CM_static",
        control_delta={"enable_analyzers": ["first_divergence"]},
    )
    store.add_candidate(static)
    _advance(store, static, "candidate", "shadow_testing", previous.policy_hash)
    _advance(store, static, "shadow_testing", "replay_qualified", previous.policy_hash)
    _advance(store, static, "replay_qualified", "bank_candidate", previous.policy_hash)
    _advance(store, static, "bank_candidate", "active_dormant", previous.policy_hash)
    retriever = MemoryRetriever(store)
    guard = ActivationGuard(store)
    bound_policy, bank = build_memory_bank_policy_candidate(
        parent=previous,
        store=store,
        memory_versions={affected.memory_id: 1, static.memory_id: 1},
        bank_id="AMB_TRANSITION",
        bank_version=1,
        retriever_hash=retriever.retriever_hash,
        activation_guard_hash=guard.guard_hash,
        control_whitelist_hash=guard.control_whitelist_hash,
        round_id="R2",
        policy_id="B_BOUND",
    )
    current = bound_policy.with_updates(
        policy_id="B_CHANGED",
        parent_policy_id=bound_policy.policy_id,
        parent_policy_hash=bound_policy.policy_hash,
        configuration={**bound_policy.configuration, "evidence_k": 3},
        memory_binding={},
    )
    transition = transition_active_bank(
        store=store,
        previous_policy=bound_policy,
        current_policy=current,
        previous_bank=bank,
        evidence={"round_id": "R4", "reason": "policy_change"},
    )
    assert transition["revalidation_required_versions"] == {
        affected.memory_id: 1
    }
    assert transition["inherited_memory_versions"] == {static.memory_id: 1}
    assert store.current_status(affected.memory_id, 1) == "revalidation_required"
    assert store.current_status(static.memory_id, 1) == "active_dormant"
    assert store.get_version(affected.memory_id, 1).memory_hash == affected.memory_hash
    assert store.get_version(static.memory_id, 1).memory_hash == static.memory_hash


def test_cross_policy_replay_requires_explicit_revalidation_binding():
    previous = _policy()
    memory = _memory(previous, _episode(previous))
    current = previous.with_updates(
        policy_id="B_REVALIDATE",
        parent_policy_id=previous.policy_id,
        parent_policy_hash=previous.policy_hash,
        configuration={**previous.configuration, "evidence_k": 3},
    )
    adapter = DeterministicMemoryAdapter()
    budget = BudgetEnvelope(3, 3, 48000, 360)
    rows = [
        run_shadow_replay(
            case={
                "case_id": f"t{i}",
                "design": f"d{i}",
                "expected_shadow_help": True,
            },
            policy=current,
            memory=memory,
            seed=1,
            budget=budget,
            evaluator=adapter.replay_memory,
        )
        for i in range(2)
    ]
    provenance = {
        "manifest_hash": H("manifest"),
        "toolchain_fingerprint_hash": H("toolchain"),
        "code_commit_sha": "test",
        "control_whitelist_hash": H("whitelist"),
    }
    with pytest.raises(MemoryQualificationViolation, match="revalidation"):
        decide_memory_qualification(memory, rows, provenance=provenance)
    decision = decide_memory_qualification(
        memory,
        rows,
        provenance={
            **provenance,
            "revalidation_from_policy_hash": previous.policy_hash,
        },
    )
    assert decision["qualified"] is True
    assert decision["qualified_under_policy_hash"] == current.policy_hash


def test_consolidator_relations_and_active_bank_bound_are_deterministic():
    policy = _policy()
    first_episode = _episode(policy, episode_id="E_CONSOLIDATE_1")
    second_episode = _episode(policy, episode_id="E_CONSOLIDATE_2")
    first = _memory(policy, first_episode)
    second = _memory(
        policy,
        second_episode,
        memory_id="CM_specialized",
        trigger_predicate={
            **first.trigger_predicate,
            "assignment_type": "nonblocking",
        },
        control_delta={"enable_analyzers": ["first_divergence"]},
    )
    relations = infer_relations(second, [first])
    assert relations == [{
        "source": second.memory_id,
        "target": first.memory_id,
        "relation": "specializes",
    }]
    selected = select_bounded_active_memories(
        [
            (first, {
                "qualified": True,
                "summary": {"helped": 3, "harmed": 0, "max_cost_ratio": 1},
            }),
            (second, {
                "qualified": True,
                "summary": {"helped": 1, "harmed": 0, "max_cost_ratio": 1},
            }),
        ],
        maximum_active=1,
    )
    assert selected == {first.memory_id: 1}

    merge_peer = _memory(
        policy,
        second_episode,
        memory_id="CM_merge_peer",
        trigger_predicate={
            **first.trigger_predicate,
            "assignment_type": "blocking",
        },
    )
    merged = merge_control_memories(
        [first, merge_peer],
        memory_id="CM_merged",
        origin_round_id="R5",
        policy=policy,
    )
    assert merged.trigger_predicate == first.trigger_predicate
    assert set(merged.source_episode_ids) == {
        first_episode.episode_id,
        second_episode.episode_id,
    }
    split = split_control_memory(
        merged,
        specialized_triggers=[
            {**merged.trigger_predicate, "assignment_type": "blocking"},
            {**merged.trigger_predicate, "assignment_type": "nonblocking"},
        ],
        memory_ids=["CM_split_blocking", "CM_split_nonblocking"],
        origin_round_id="R6",
        policy=policy,
    )
    assert len(split) == 2
    assert all(
        child.compatibility["parent_memory_hash"] == merged.memory_hash
        for child in split
    )


def test_red_memory_bypass_deepening_and_conflict_are_policy_bank_bound(tmp_path):
    parent, episodes, store, first = _active_store(tmp_path)
    second_episode = _episode(parent, episode_id="E_RED_SECOND")
    episodes.append_episode(second_episode)
    second = _memory(
        parent,
        second_episode,
        memory_id="CM_red_conflict",
        control_delta={
            "enable_analyzers": ["first_divergence"],
            "rtl_slice_mode": "combinational_cone",
        },
    )
    store.add_candidate(second)
    _advance(store, second, "candidate", "shadow_testing", parent.policy_hash)
    _advance(store, second, "shadow_testing", "replay_qualified", parent.policy_hash)
    _advance(store, second, "replay_qualified", "bank_candidate", parent.policy_hash)
    _advance(store, second, "bank_candidate", "active_dormant", parent.policy_hash)
    retriever = MemoryRetriever(store)
    guard = ActivationGuard(store)
    policy, bank = build_memory_bank_policy_candidate(
        parent=parent,
        store=store,
        memory_versions={first.memory_id: 1, second.memory_id: 1},
        bank_id="AMB_RED",
        bank_version=1,
        retriever_hash=retriever.retriever_hash,
        activation_guard_hash=guard.guard_hash,
        control_whitelist_hash=guard.control_whitelist_hash,
        round_id="R3",
        policy_id="B_RED_MEMORY",
    )
    packet = build_memory_capability_packet(policy, bank, store)
    assert "control_delta" not in json.dumps(packet)
    red_context = build_red_search_context(
        policy,
        residual_archive=[],
        covered_archive=[],
        memory_capability=packet,
    )
    assert verify_red_search_context(red_context)["schema_version"].endswith("v3")
    assert (
        red_context["memory_capability"]["active_memory_bank_hash"]
        == bank.bank_hash
    )
    with pytest.raises(MemoryChallengeViolation, match="private"):
        build_memory_capability_packet(
            policy,
            bank,
            store,
            public_regions=[{
                "region_id": "private",
                "control_delta": {"prompt": "leak"},
            }],
        )

    parent_source = tmp_path / "parent.v"
    parent_source.write_text(
        "module top(input a, output y); assign y = a; endmodule\n"
    )
    parent_poison = {
        "poison_id": "PARENT",
        "normalized_diff_hash": H("parent-diff"),
        "mechanism_variant": "direct",
        "dependency_depth": 0,
        "composition_depth": 1,
    }
    ids = sorted(bank.memories)
    for operator in (
        "memory_bypass",
        "memory_deepening",
        "memory_conflict",
    ):
        targets = ids if operator == "memory_conflict" else ids[:1]
        plan = make_memory_challenge_plan(
            policy=policy,
            bank=bank,
            packet=packet,
            operator=operator,
            poison_id=f"P_{operator}",
            target_memory_ids=targets,
            parent_poison_id="PARENT",
            parent_source_semantic_hash=hash_file(parent_source),
        )
        source = tmp_path / f"{operator}.v"
        source.write_text(
            "module top(input a, input b, output y); "
            f"assign y = a ^ b; // {operator}\nendmodule\n"
        )
        poison = materialize_memory_challenge(
            plan, parent_poison, buggy_rtl=source
        )
        verified = verify_memory_challenge_execution(
            poison,
            plan=plan,
            policy=policy,
            bank=bank,
            parent=parent_poison,
        )
        assert verified["source_semantic_hash"] == hash_file(source)
        assert verified["challenged_policy_hash"] == policy.policy_hash
        assert verified["active_memory_bank_hash"] == bank.bank_hash
