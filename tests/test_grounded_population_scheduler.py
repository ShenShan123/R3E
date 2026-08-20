from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from r3e.arena.fake_adapters import (
    DeterministicEvolutionAdapter,
    FakeRedAdapter,
)
from r3e.arena.runner import EvolutionRoundRunner
from r3e.policy.registry_v2 import initialize_registry
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_file, hash_payload
from r3e.red.feedback_packet import (
    build_red_search_context,
    verify_red_search_context,
)
from r3e.red.grounded.coverage import freeze_coverage_state
from r3e.red.grounded.population_scheduler import (
    GroundedPopulationViolation,
    build_population_schedule,
    load_population_config,
    verify_population_candidates,
    verify_population_config,
    verify_population_schedule,
)
from r3e.red.grounded.proposal_planner import (
    build_grounded_proposal_plan,
)
from r3e.red.grounded.registry import load_grounded_registries


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def policy():
    return PolicyState.from_dict(json.loads((
        ROOT / "configs/base_policy/frozen_base_policy_v1.json"
    ).read_text(encoding="utf-8")))


@pytest.fixture
def registries():
    return load_grounded_registries(
        family_registry=ROOT / "configs/red/grounded_family_registry_v1.json",
        operator_registry=ROOT / "configs/red/grounded_operator_registry_v1.json",
        effect_registry=ROOT / "configs/red/grounded_effect_registry_v1.json",
    )


@pytest.fixture
def population_config():
    return load_population_config(
        ROOT / "configs/red/deterministic_population_v1.json"
    )


def _proposal(policy, registries, *, budget=6):
    return build_grounded_proposal_plan(
        policy=policy,
        registries=registries,
        coverage_state=freeze_coverage_state([]),
        archive_view=[],
        budget=budget,
        family_quota=2,
        archive_quota=0,
        maximum_difficulty_band="D3",
        memory_capability=None,
        allow_composition=False,
    )


def _rehash_config(config):
    result = deepcopy(config)
    result["config_hash"] = hash_payload({
        key: value
        for key, value in result.items()
        if key != "config_hash"
    })
    return verify_population_config(result)


def test_grounded_deterministic_population_milestone_is_frozen():
    milestone = json.loads((
        ROOT
        / "configs/evolution/grounded_deterministic_population_v1.json"
    ).read_text(encoding="utf-8"))
    parent = json.loads((
        ROOT
        / "configs/evolution/grounded_sequential_runtime_gate_v1.json"
    ).read_text(encoding="utf-8"))
    assert milestone["status"] == "frozen"
    assert milestone["parent_milestone"] == {
        "milestone_id": parent["milestone_id"],
        "milestone_hash": parent["milestone_hash"],
    }
    assert milestone["milestone_hash"] == hash_payload({
        key: value
        for key, value in milestone.items()
        if key != "milestone_hash"
    })
    integrated = json.loads((
        ROOT
        / "configs/evolution/integrated_deterministic_coevolution_v1.json"
    ).read_text(encoding="utf-8"))
    pilot = json.loads((
        ROOT / "configs/evolution/real_adapter_pilot_entry_v1.json"
    ).read_text(encoding="utf-8"))
    assert integrated["parent_milestone"] == {
        "milestone_id": milestone["milestone_id"],
        "milestone_hash": milestone["milestone_hash"],
    }
    for relative, expected in milestone["frozen_assets"].items():
        current = hash_file(ROOT / relative)
        if current != expected:
            assert current in {
                integrated["frozen_assets"].get(relative),
                pilot["frozen_assets"].get(relative),
            }
    assert "real-provider-population" in milestone["deferred_scope"]


def test_population_schedule_routes_one_generator_per_intent_and_is_stable(
    policy,
    registries,
    population_config,
):
    proposal = _proposal(policy, registries)
    first = build_population_schedule(
        policy=policy,
        proposal_plan=proposal,
        config=population_config,
    )
    second = build_population_schedule(
        policy=policy,
        proposal_plan=proposal,
        config=population_config,
    )
    assert first == second
    assert len(first["assignments"]) == len(
        proposal["selected_intent_ids"]
    )
    assert len({
        row["intent_id"] for row in first["assignments"]
    }) == len(first["assignments"])
    assert all(
        row["validator_authority"]
        == "runner_owned_grounded_execution"
        and row["minimizer_authority"]
        == "runner_owned_structural_minimizer"
        and row["provider_role"] != "generalist"
        for row in first["assignments"]
    )
    assert any(
        row["provider_role"] == "family_specialist"
        for row in first["assignments"]
    )
    assert verify_population_schedule(
        first,
        policy=policy,
        proposal_plan=proposal,
        config=population_config,
    ) == first


def test_population_arm_is_a_single_factor_over_same_proposal(
    policy,
    registries,
    population_config,
):
    proposal = _proposal(policy, registries)
    routed = build_population_schedule(
        policy=policy,
        proposal_plan=proposal,
        config=population_config,
    )
    generalist_config = deepcopy(population_config)
    generalist_config["scheduler_mode"] = "generalist_only"
    generalist_config = _rehash_config(generalist_config)
    control = build_population_schedule(
        policy=policy,
        proposal_plan=proposal,
        config=generalist_config,
    )
    assert routed["proposal_plan_hash"] == control["proposal_plan_hash"]
    assert routed["scheduler_mode"] == "routed_population"
    assert control["scheduler_mode"] == "generalist_only"
    assert {
        row["intent_hash"] for row in routed["assignments"]
    } == {
        row["intent_hash"] for row in control["assignments"]
    }
    assert {
        row["provider_role"] for row in control["assignments"]
    } == {"generalist"}
    assert routed["ablation_factor_hash"] != control[
        "ablation_factor_hash"
    ]


def test_population_candidate_provenance_and_budget_are_fail_closed(
    tmp_path,
    policy,
    registries,
    population_config,
):
    proposal = _proposal(policy, registries, budget=4)
    schedule = build_population_schedule(
        policy=policy,
        proposal_plan=proposal,
        config=population_config,
    )
    context = build_red_search_context(
        policy,
        residual_archive=[],
        covered_archive=[],
        grounded_proposal_plan=proposal,
        grounded_population_schedule=schedule,
    )
    assert context["schema_version"] == "r3e-red-search-context-v7"
    assert verify_red_search_context(context) == context
    candidates = list(FakeRedAdapter(tmp_path).generate_red(
        policy, {}, context
    ))
    execution = verify_population_candidates(
        schedule, candidates, policy=policy
    )
    assert len(execution["candidate_bindings"]) == 4
    assert execution["population_arm"] == "routed_population"

    widened = deepcopy(candidates)
    widened[0]["validity"] = {"proven_valid": True}
    with pytest.raises(
        GroundedPopulationViolation,
        match="provenance or budget",
    ):
        verify_population_candidates(
            schedule, widened, policy=policy
        )
    over_budget = deepcopy(candidates)
    assignment = schedule["assignments"][0]
    over_budget[0]["grounded_generation_usage"][
        "input_tokens"
    ] = assignment["input_token_budget"] + 1
    with pytest.raises(
        GroundedPopulationViolation,
        match="provenance or budget",
    ):
        verify_population_candidates(
            schedule, over_budget, policy=policy
        )


def test_population_resource_exhaustion_and_schedule_tamper_are_rejected(
    policy,
    registries,
    population_config,
):
    proposal = _proposal(policy, registries, budget=4)
    insufficient = deepcopy(population_config)
    insufficient["total_assignment_budget"] = 1
    insufficient = _rehash_config(insufficient)
    with pytest.raises(
        GroundedPopulationViolation,
        match="assignment budget",
    ):
        build_population_schedule(
            policy=policy,
            proposal_plan=proposal,
            config=insufficient,
        )
    schedule = build_population_schedule(
        policy=policy,
        proposal_plan=proposal,
        config=population_config,
    )
    tampered = deepcopy(schedule)
    tampered["assignments"][0]["provider_id"] = "other-provider"
    tampered["schedule_hash"] = hash_payload({
        key: value
        for key, value in tampered.items()
        if key != "schedule_hash"
    })
    with pytest.raises(
        GroundedPopulationViolation,
        match="cannot be deterministically reconstructed",
    ):
        verify_population_schedule(
            tampered,
            policy=policy,
            proposal_plan=proposal,
            config=population_config,
        )


def test_runner_freezes_population_before_generation_and_checkpoints(
    tmp_path,
    monkeypatch,
    population_config,
):
    registry = tmp_path / "registry.json"
    initialize_registry(
        ROOT / "configs/base_policy/frozen_base_policy_v1.json",
        registry,
    )
    config = json.loads((
        ROOT / "configs/evolution/round_grounded_runtime_v3.json"
    ).read_text(encoding="utf-8"))
    config.update({
        "grounded_pre_generation_planner": True,
        "grounded_population_scheduler": True,
        "grounded_red_proposal_budget": 4,
        "grounded_family_quota": 2,
        "grounded_archive_quota": 0,
        "policy_registry": str(registry),
        "policy_search_space": (
            "configs/base_policy/policy_search_space_v1.json"
        ),
        "rounds_root": str(tmp_path / "rounds"),
        "events_root": str(tmp_path / "events"),
        "red_archive": str(tmp_path / "residual.jsonl"),
        "covered_archive": str(tmp_path / "covered.jsonl"),
        "red_rejected_archive": str(tmp_path / "rejected.jsonl"),
        "grounded_coverage_state": str(tmp_path / "coverage.json"),
        "decision_ledger": str(tmp_path / "decisions.jsonl"),
        "round_ledger": str(tmp_path / "round_ledger.jsonl"),
    })
    runner = EvolutionRoundRunner(
        config,
        round_id="R_POPULATION_CHECKPOINT",
        adapter=DeterministicEvolutionAdapter(tmp_path / "adapter"),
        project_root=ROOT,
    )
    assert runner.round_toolchain_fingerprint[
        "grounded_population_config_hash"
    ] == population_config["config_hash"]

    def stop_after_red(**_kwargs):
        raise RuntimeError("stop after population generation")

    monkeypatch.setattr(
        "r3e.arena.runner.execute_grounded_arena_validity",
        stop_after_red,
    )
    with pytest.raises(RuntimeError, match="population generation"):
        runner.run()
    round_dir = tmp_path / "rounds/R_POPULATION_CHECKPOINT"
    schedule = json.loads((
        round_dir / "grounded_population_schedule.json"
    ).read_text(encoding="utf-8"))
    execution = json.loads((
        round_dir / "grounded_population_execution.json"
    ).read_text(encoding="utf-8"))
    context = json.loads((
        round_dir / "red_search_context.json"
    ).read_text(encoding="utf-8"))
    assert context["schema_version"] == "r3e-red-search-context-v7"
    assert context["grounded_population_schedule"] == schedule
    assert execution["schedule_hash"] == schedule["schedule_hash"]
    assert {
        row["provider_role"] for row in schedule["assignments"]
    } >= {"family_specialist"}
    runner._verify_persisted_checkpoints()
    events = [
        json.loads(line)
        for line in (
            tmp_path / "events/red.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    generated = next(
        row for row in events
        if row["event_type"] == "red_candidates_generated"
    )
    assert generated["grounded_population_schedule_hash"] == schedule[
        "schedule_hash"
    ]
    assert generated["grounded_population_execution_hash"] == execution[
        "execution_hash"
    ]
