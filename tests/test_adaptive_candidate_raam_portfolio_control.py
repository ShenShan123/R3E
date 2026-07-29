from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from r3e.blue.portfolio.allocator import (
    OfflineAdaptiveAllocator,
    load_offline_allocator_state,
)
from r3e.blue.portfolio.audit import (
    PortfolioAuditViolation,
    verify_blue_evaluation,
)
from r3e.blue.portfolio.candidate_executor import CandidatePortfolioExecutor
from r3e.blue.portfolio.fake import (
    DeterministicFakeCandidateProvider,
    DeterministicFakeCandidateVerifier,
)
from r3e.blue.portfolio.lens_registry import load_lens_registry
from r3e.blue.portfolio.portfolio_control import (
    PORTFOLIO_CONTROL_FIELDS,
    PortfolioControlViolation,
    load_portfolio_template_registry,
)
from r3e.blue.portfolio.schema import (
    CandidatePortfolio,
    build_candidate_portfolio_binding,
)
from r3e.arena.portfolio import ArenaCandidatePortfolioAuthority
from r3e.arena.fake_adapters import DeterministicEvolutionAdapter
from r3e.arena.runner import EvolutionRoundRunner
from r3e.memory.activation_guard import ActivationGuard
from r3e.memory.bank_store import ActiveBankStore
from r3e.memory.candidate_builder import build_portfolio_memory_candidates
from r3e.memory.episode_store import EpisodeStore
from r3e.memory.evidence import evidence_link, freeze_evidence_set
from r3e.memory.memory_store import MemoryStore
from r3e.memory.plan_compiler import MemoryAwarePlanCompiler
from r3e.memory.promotion import build_memory_bank_policy_candidate
from r3e.memory.qualification_gate import (
    MemoryQualificationViolation,
    decide_portfolio_memory_qualification,
)
from r3e.memory.retriever import MemoryRetriever
from r3e.memory.schema import (
    BudgetEnvelope,
    ControlMemory,
    FailureDescriptor,
    MemoryLifecycleEvent,
    MemoryValidationError,
    RuntimeContext,
    VerifiedEpisode,
)
from r3e.memory.shadow_replay import run_shadow_replay
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_file, hash_payload
from r3e.red.poison_payload import bind_poison_payload


ROOT = Path(__file__).resolve().parents[1]
H = lambda value: hash_payload({"value": value})
TEMPLATE_ASSET = "configs/blue/portfolio_template_registry_v1.json"


def _registry():
    return load_lens_registry(
        ROOT / "configs/blue/lens_registry_v1.json",
        project_root=ROOT,
    )


def _templates():
    return load_portfolio_template_registry(
        ROOT / TEMPLATE_ASSET,
        project_root=ROOT,
    )


def _portfolio():
    return CandidatePortfolio.from_dict(json.loads((
        ROOT / "configs/blue/adaptive_portfolio_v1.json"
    ).read_text(encoding="utf-8")))


def _allocator():
    return OfflineAdaptiveAllocator(load_offline_allocator_state(
        ROOT / "configs/blue/offline_allocator_state_v1.json"
    ))


def _policy(*, freeze_templates: bool = True) -> PolicyState:
    base = PolicyState.from_dict(json.loads((
        ROOT / "configs/base_policy/frozen_base_policy_v1.json"
    ).read_text(encoding="utf-8")))
    frozen = dict(base.frozen_assets or {})
    if freeze_templates:
        frozen[TEMPLATE_ASSET] = hash_file(ROOT / TEMPLATE_ASSET)
    return base.with_updates(
        policy_id="B_ACP5_PARENT",
        schema_version="r3e-policy-v3",
        parent_policy_id=base.policy_id,
        parent_policy_hash=base.policy_hash,
        created_round=5,
        status="active",
        configuration={
            **base.configuration,
            "n_candidates": 3,
            "candidate_selection": "verifier_guided",
        },
        frozen_assets=frozen,
        candidate_portfolio_binding=build_candidate_portfolio_binding(
            _portfolio()
        ),
    )


def _descriptor() -> FailureDescriptor:
    return FailureDescriptor.create({
        "oracle_stage": "grounded_simulation_and_formal",
        "sequential_context": True,
        "temporal_relation": "candidate_lags_golden",
        "cycle_offset_bucket": 1,
        "affected_roles": ["observable_output"],
        "assignment_type": "nonblocking",
        "cone_depth_bucket": "depth_2_3",
        "mismatch_pattern": "late_transition",
        "first_divergence_bucket": "cycle_1",
        "first_divergence_signal": "out",
        "observable_artifact_hashes": {
            "oracle": H("oracle"),
            "formal": H("formal"),
        },
    })


def _episode(policy: PolicyState) -> VerifiedEpisode:
    return VerifiedEpisode.create(
        episode_id="E_ACP5_001",
        round_id="R005",
        challenged_policy_instance_hash=policy.policy_instance_hash,
        challenged_effective_policy_hash=policy.effective_policy_hash,
        poison_id="P_ACP5",
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
            "llm_calls": 3,
            "verifier_calls": 3,
            "wall_time_seconds": 0.5,
        },
    )


def _delta(template_id: str = "general_temporal_control_v1"):
    return {
        "candidate_portfolio_template_id": template_id,
        "specialist_slot_budget": 2,
        "diversity_retry_budget": 0,
        "portfolio_early_stop": "all_candidates",
    }


def _memory(
    policy: PolicyState,
    episode: VerifiedEpisode,
    *,
    delta: dict | None = None,
) -> ControlMemory:
    return ControlMemory.create(
        memory_id="CM_ACP5_TEMPORAL",
        memory_version=1,
        origin_round_id="R005",
        source_episode_ids=[episode.episode_id],
        source_episode_hashes={episode.episode_id: episode.episode_hash},
        created_under_policy_instance_hash=policy.policy_instance_hash,
        created_under_effective_policy_hash=policy.effective_policy_hash,
        trigger_predicate={
            "sequential_context": True,
            "temporal_relation": "candidate_lags_golden",
        },
        control_delta=delta or _delta(),
        status="candidate",
        qualification_summary={},
        compatibility={},
    )


def _advance(
    store: MemoryStore,
    memory: ControlMemory,
    previous: str,
    new: str,
    policy: PolicyState,
) -> None:
    store.update_lifecycle(
        memory.memory_id,
        MemoryLifecycleEvent.create(
            memory_id=memory.memory_id,
            memory_version=memory.memory_version,
            memory_hash=memory.memory_hash,
            previous_status=previous,
            new_status=new,
            effective_policy_hash=policy.effective_policy_hash,
            reason_code=f"acp5_{new}",
            evidence_hash=H(new),
        ),
    )


def _active_plan(tmp_path: Path):
    parent = _policy()
    memory_root = tmp_path / "runtime_memory"
    episodes = EpisodeStore(memory_root / "episodes")
    episode = _episode(parent)
    episodes.append_episode(episode)
    store = MemoryStore(memory_root / "library", episode_store=episodes)
    memory = _memory(parent, episode)
    store.add_candidate(memory)
    for before, after in (
        ("candidate", "shadow_testing"),
        ("shadow_testing", "replay_qualified"),
        ("replay_qualified", "bank_candidate"),
        ("bank_candidate", "active_dormant"),
    ):
        _advance(store, memory, before, after, parent)
    templates = _templates()
    retriever = MemoryRetriever(store)
    guard = ActivationGuard(
        store, portfolio_template_registry=templates
    )
    child, bank = build_memory_bank_policy_candidate(
        parent=parent,
        store=store,
        memory_versions={memory.memory_id: 1},
        bank_id="AMB_ACP5",
        bank_version=1,
        retriever_hash=retriever.retriever_hash,
        activation_guard_hash=guard.guard_hash,
        control_whitelist_hash=guard.control_whitelist_hash,
        round_id="R006",
        policy_id="B_ACP5_MEMORY",
    )
    banks = ActiveBankStore(
        memory_root / "active_banks", memory_store=store
    )
    banks.add_candidate(bank)
    context = RuntimeContext(
        effective_policy_hash=child.effective_policy_hash,
        policy_instance_hash=child.policy_instance_hash,
        available_analyzers=("first_divergence",),
        budget=BudgetEnvelope(3, 3, 48000, 360),
        control_whitelist_hash=guard.control_whitelist_hash,
    )
    matches = retriever.retrieve(_descriptor(), bank)
    decision = guard.reactivate(
        matches=matches,
        active_policy=child,
        active_bank=bank,
        runtime_context=context,
    )
    plan = MemoryAwarePlanCompiler(
        store,
        bank,
        portfolio_template_registry=templates,
    ).compile(base_policy=child, reactivation=decision)
    return child, templates, plan


def _shadow_output(case, plan, budget):
    split = case["qualification_split"]
    activated = bool(plan.activated_memory_ids)
    oracle_ok = (
        activated if split == "adaptation"
        else True
    )
    return {
        "model_id": "deterministic-fake",
        "budget_hash": budget.budget_hash,
        "verifier_hash": H("verifier"),
        "toolchain_hash": H("toolchain"),
        "oracle_ok": oracle_ok,
        "oracle_evidence_hash": H(
            f"{case['case_id']}-{activated}-{oracle_ok}"
        ),
        "resource_usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "llm_calls": 3,
            "verifier_calls": 3,
            "wall_time_seconds": 1.0,
        },
    }


def test_template_registry_is_hash_bound_and_policy_authorized():
    registry = _templates()
    policy = _policy()
    registry.authorize_policy(policy)
    assert registry.candidate_budget == 3
    assert set(registry.templates) == {
        "general_control_dataflow_v1",
        "general_temporal_control_v1",
        "general_temporal_dataflow_v1",
    }
    with pytest.raises(PortfolioControlViolation, match="does not freeze"):
        registry.authorize_policy(_policy(freeze_templates=False))


def test_raam_portfolio_control_milestone_is_frozen():
    milestone = json.loads((
        ROOT / "configs/evolution/raam_portfolio_control_v1.json"
    ).read_text(encoding="utf-8"))
    parent = json.loads((
        ROOT / "configs/evolution/offline_adaptive_allocator_v1.json"
    ).read_text(encoding="utf-8"))
    successor = json.loads((
        ROOT
        / "configs/evolution/portfolio_aware_red_challenge_v1.json"
    ).read_text(encoding="utf-8"))
    assert milestone["status"] == "frozen"
    assert milestone["parent_milestone"] == {
        "milestone_id": parent["milestone_id"],
        "milestone_hash": parent["milestone_hash"],
    }
    assert milestone["milestone_hash"] == hash_payload({
        key: value for key, value in milestone.items()
        if key != "milestone_hash"
    })
    assert successor["parent_milestone"] == {
        "milestone_id": milestone["milestone_id"],
        "milestone_hash": milestone["milestone_hash"],
    }
    for relative, expected in milestone["frozen_assets"].items():
        current = hash_file(ROOT / relative)
        if current != expected:
            assert successor["frozen_assets"].get(relative) == current


@pytest.mark.parametrize(
    "delta",
    [
        {"prompt": "inject a lens"},
        {"model_route_id": "larger-model"},
        {"candidate_budget": 4},
        {
            "candidate_portfolio_template_id":
                "general_temporal_control_v1",
        },
    ],
)
def test_control_schema_rejects_prompt_route_budget_and_partial_control(delta):
    policy = _policy()
    with pytest.raises(MemoryValidationError):
        _memory(policy, _episode(policy), delta=delta)


def test_template_materializer_rejects_unknown_template_and_extra_retry():
    registry = _templates()
    policy = _policy()
    unknown = _delta("unfrozen_template")
    with pytest.raises(PortfolioControlViolation, match="outside"):
        registry.materialize(unknown, policy=policy)
    retry = _delta()
    retry["diversity_retry_budget"] = 1
    with pytest.raises(PortfolioControlViolation, match="differs"):
        registry.materialize(retry, policy=policy)


def test_portfolio_candidate_builder_emits_only_template_id_controls():
    policy = _policy()
    episode = _episode(policy)
    candidates = build_portfolio_memory_candidates(
        [episode],
        policy=policy,
        origin_round_id="R006",
        template_registry=_templates(),
        template_id="general_temporal_control_v1",
    )
    assert len(candidates) == 1
    assert set(candidates[0].control_delta) == PORTFOLIO_CONTROL_FIELDS
    assert candidates[0].control_delta == _delta()


def test_activation_and_plan_bind_exact_template_slots(tmp_path):
    child, registry, plan = _active_plan(tmp_path)
    assert plan.activated_memory_ids == ("CM_ACP5_TEMPORAL",)
    assert plan.controls["portfolio_allocation_mode"] == "raam_template"
    assert plan.controls["portfolio_slots"] == [
        "generic_v1", "temporal_v1", "control_v1"
    ]
    assert plan.controls["portfolio_template_registry_hash"] == (
        registry.registry_hash
    )
    receipt = registry.build_receipt(
        plan.controls,
        policy=child,
        execution_plan_hash=plan.plan_hash,
    )
    assert receipt["effective_policy_hash"] == child.effective_policy_hash
    assert receipt["execution_plan_hash"] == plan.plan_hash


def test_candidate_executor_and_audit_consume_memory_template(tmp_path):
    child, templates, plan = _active_plan(tmp_path)
    provider = DeterministicFakeCandidateProvider()
    verifier = DeterministicFakeCandidateVerifier(successful_slots={1})
    executor = CandidatePortfolioExecutor(
        registry=_registry(),
        portfolio=_portfolio(),
        provider=provider,
        verifier=verifier,
        project_root=ROOT,
        allocator=_allocator(),
        portfolio_template_registry=templates,
    )
    result = executor.execute(
        policy=child,
        case={"case_id": "CASE_ACP5"},
        descriptor=_descriptor().to_dict(),
        run_seed=17,
        execution_plan=plan,
    )
    assert [
        slot["lens_id"]
        for slot in result["allocation_plan"]["slots"]
    ] == ["generic_v1", "temporal_v1", "control_v1"]
    assert result["resource_usage"]["provider_calls"] == 3
    assert result["memory_execution_plan_hash"] == plan.plan_hash
    assert verify_blue_evaluation(
        result,
        policy=child,
        registry=_registry(),
        portfolio=_portfolio(),
        provider=provider,
        expected_verifier_hash=verifier.verifier_hash,
        allocator=_allocator(),
        descriptor=_descriptor().to_dict(),
        execution_plan=plan,
        portfolio_template_registry=templates,
    ) == result
    tampered = deepcopy(result)
    tampered["portfolio_control_receipt"]["allocated_lens_ids"][1] = (
        "dataflow_v1"
    )
    with pytest.raises(PortfolioAuditViolation):
        verify_blue_evaluation(
            tampered,
            policy=child,
            registry=_registry(),
            portfolio=_portfolio(),
            provider=provider,
            allocator=_allocator(),
            descriptor=_descriptor().to_dict(),
            execution_plan=plan,
            portfolio_template_registry=templates,
        )


def test_formal_arena_authority_binds_memory_plan_and_template_registry(
    tmp_path,
):
    child, templates, plan = _active_plan(tmp_path)
    config = json.loads((
        ROOT
        / "configs/evolution/round_acp5_raam_portfolio_control_v1.json"
    ).read_text(encoding="utf-8"))
    provider = DeterministicFakeCandidateProvider()
    verifier = DeterministicFakeCandidateVerifier(successful_slots={1})
    authority = ArenaCandidatePortfolioAuthority(
        project_root=ROOT,
        config=config,
        provider=provider,
        verifier=verifier,
    )
    descriptor = _descriptor().to_dict()
    poison = bind_poison_payload({
        "poison_id": "P_ACP5_ARENA",
        "case_id": "CASE_ACP5_ARENA",
        "design": "acp5_fixture",
        "challenged_policy_hash": child.policy_hash,
        "family": "hidden",
        "effect": "template_control",
        "affected_role": "observable_output",
        "grounded_authority_bundle": {
            "failure_descriptor": descriptor,
        },
        "validity": {
            "proven_valid": True,
            "evidence": {
                "failure_descriptor_hash": descriptor["descriptor_hash"],
            },
        },
    })
    result = authority.evaluate(
        child, poison, 19, execution_plan=plan
    )
    assert authority.portfolio_template_registry.registry_hash == (
        templates.registry_hash
    )
    assert result["memory_execution_plan_hash"] == plan.plan_hash
    assert result["portfolio_control_receipt"]["portfolio_template_id"] == (
        "general_temporal_control_v1"
    )


def test_round_runner_compiles_active_bank_plan_before_blue_challenge(
    tmp_path,
):
    child, templates, _plan = _active_plan(tmp_path)
    config = json.loads((
        ROOT
        / "configs/evolution/round_acp5_raam_portfolio_control_v1.json"
    ).read_text(encoding="utf-8"))
    config.update({
        "policy_registry": str(tmp_path / "registry.json"),
        "policy_search_space": (
            "configs/base_policy/policy_search_space_v1.json"
        ),
        "memory_root": str(tmp_path / "runtime_memory"),
        "rounds_root": str(tmp_path / "rounds"),
        "events_root": str(tmp_path / "events"),
    })
    runner = EvolutionRoundRunner(
        config,
        round_id="R_ACP5",
        adapter=DeterministicEvolutionAdapter(tmp_path / "adapter"),
        project_root=ROOT,
        candidate_provider=DeterministicFakeCandidateProvider(),
        candidate_verifier=DeterministicFakeCandidateVerifier(
            successful_slots={1}
        ),
    )
    descriptor = _descriptor().to_dict()
    poison = bind_poison_payload({
        "poison_id": "P_ACP5_RUNNER",
        "case_id": "CASE_ACP5_RUNNER",
        "design": "acp5_runner_fixture",
        "challenged_policy_hash": child.policy_hash,
        "family": "hidden",
        "effect": "template_control",
        "affected_role": "observable_output",
        "grounded_authority_bundle": {
            "failure_descriptor": descriptor,
        },
        "validity": {
            "proven_valid": True,
            "evidence": {
                "failure_descriptor_hash": descriptor["descriptor_hash"],
            },
        },
    })
    challenged = runner._evaluate_blue_challenges(
        child, [poison], seeds=[23]
    )
    result = challenged[0]["blue_results"][0]
    assert runner.round_toolchain_fingerprint[
        "blue_portfolio_template_registry_hash"
    ] == templates.registry_hash
    assert result["memory_execution_plan_hash"]
    assert result["portfolio_control_receipt"]["portfolio_template_id"] == (
        "general_temporal_control_v1"
    )


def test_portfolio_shadow_qualification_requires_three_safety_partitions():
    policy = _policy()
    episode = _episode(policy)
    memory = _memory(policy, episode)
    budget = BudgetEnvelope(3, 3, 48000, 360)
    cases = [
        {
            "case_id": "A1", "design": "d1",
            "qualification_split": "adaptation",
            "trigger_matched": True,
        },
        {
            "case_id": "A2", "design": "d2",
            "qualification_split": "adaptation",
            "trigger_matched": True,
        },
        {
            "case_id": "N1", "design": "n1",
            "qualification_split": "non_target",
            "trigger_matched": True,
        },
        {
            "case_id": "F1", "design": "f1",
            "qualification_split": "false_activation",
            "trigger_matched": False,
        },
    ]
    rows = [
        run_shadow_replay(
            case=case,
            policy=policy,
            memory=memory,
            seed=101,
            budget=budget,
            evaluator=lambda _policy, case, _seed, plan, budget: (
                _shadow_output(case, plan, budget)
            ),
            portfolio_template_registry=_templates(),
        )
        for case in cases
    ]
    provenance = {
        "manifest_hash": H("paired-manifests"),
        "toolchain_fingerprint_hash": H("toolchain"),
        "code_commit_sha": "deterministic-fixture",
        "control_whitelist_hash": H("whitelist"),
        "effective_policy_hash": policy.effective_policy_hash,
    }
    decision = decide_portfolio_memory_qualification(
        memory,
        rows,
        policy=policy,
        portfolio_template_registry=_templates(),
        provenance=provenance,
        evidence_set=freeze_evidence_set(
            memory,
            [
                evidence_link(
                    memory,
                    evidence_source=memory,
                    episode_id=episode.episode_id,
                    episode_hash=episode.episode_hash,
                )
            ],
        ),
    )
    assert decision["qualified"] is True
    assert decision["gates"]["non_target_safety"] is True
    assert decision["gates"]["false_activation_safety"] is True
    incomplete = decide_portfolio_memory_qualification(
        memory,
        rows[:2],
        policy=policy,
        portfolio_template_registry=_templates(),
        provenance=provenance,
    )
    assert incomplete["qualified"] is False
    assert incomplete["gates"]["non_target_safety"] is False


def test_cross_round_plan_is_deterministic_and_policy_change_revalidates(
    tmp_path,
):
    child, templates, first = _active_plan(tmp_path)
    second = templates.build_receipt(
        first.controls,
        policy=child,
        execution_plan_hash=first.plan_hash,
    )
    third = templates.build_receipt(
        first.controls,
        policy=child,
        execution_plan_hash=first.plan_hash,
    )
    assert second == third
    changed = child.with_updates(
        policy_id="B_ACP5_STALE",
        parent_policy_id=child.policy_id,
        parent_policy_hash=child.policy_hash,
        created_round=7,
        candidate_portfolio_binding={
            **child.candidate_portfolio_binding,
            "allocator_hash": H("changed-allocator"),
        },
    )
    with pytest.raises(PortfolioControlViolation, match="not authorized"):
        templates.build_receipt(
            first.controls,
            policy=changed,
            execution_plan_hash=first.plan_hash,
        )


def test_portfolio_control_field_set_is_exactly_frozen():
    assert PORTFOLIO_CONTROL_FIELDS == {
        "candidate_portfolio_template_id",
        "specialist_slot_budget",
        "diversity_retry_budget",
        "portfolio_early_stop",
    }
