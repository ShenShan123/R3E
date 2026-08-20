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
from r3e.red.grounded.proposal_planner import (
    GroundedProposalViolation,
    build_grounded_proposal_plan,
    build_proposal_archive_view,
    grounded_proposal_protocol_hash,
    verify_grounded_proposal_plan,
    verify_proposal_candidates,
)
from r3e.red.grounded.registry import load_grounded_registries


ROOT = Path(__file__).resolve().parents[1]
H = lambda value: hash_payload({"value": value})


@pytest.fixture
def policy():
    return PolicyState.from_dict(json.loads((
        ROOT / "configs/base_policy/frozen_base_policy_v1.json"
    ).read_text(encoding="utf-8")))


@pytest.fixture
def registries():
    return load_grounded_registries(
        family_registry=(
            ROOT / "configs/red/grounded_family_registry_v1.json"
        ),
        operator_registry=(
            ROOT / "configs/red/grounded_operator_registry_v1.json"
        ),
        effect_registry=(
            ROOT / "configs/red/grounded_effect_registry_v1.json"
        ),
    )


def _cell(policy, *, family, covered=0, admitted=0, rejected=0):
    return {
        "family_id": family,
        "design_id": "design_a",
        "rtl_role": "expression",
        "temporal_context": "combinational",
        "difficulty_band": "D0",
        "effective_blue_policy_hash": policy.effective_policy_hash,
        "effective_memory_bank_hash": "",
        "proposals": max(admitted, rejected),
        "admitted": admitted,
        "covered": covered,
        "residual": admitted - covered,
        "rejected": rejected,
        "last_round_id": "R001",
    }


def _archive(policy):
    return [
        {
            "poison_id": "P_LEFT",
            "design": "design_a",
            "challenged_policy_hash": policy.policy_hash,
            "family": "combinational.comparator_boundary",
            "effect": "wrong_combinational_value",
            "affected_role": "expression",
            "difficulty_band": "D1",
            "grounded_authority_bundle": {
                "authority_hash": H("left-authority"),
            },
        },
        {
            "poison_id": "P_RIGHT",
            "design": "design_a",
            "challenged_policy_hash": policy.policy_hash,
            "family": "sequential.counter_index",
            "effect": "wrong_terminal_count",
            "affected_role": "state",
            "difficulty_band": "D2",
            "grounded_authority_bundle": {
                "authority_hash": H("right-authority"),
            },
        },
    ]


def _memory_packet(policy):
    body = {
        "schema_version": "r3e-memory-red-capability-v1",
        "challenged_policy_id": policy.policy_id,
        "challenged_policy_hash": policy.policy_hash,
        "challenged_effective_policy_hash": (
            policy.effective_policy_hash
        ),
        "active_memory_bank_hash": H("bank"),
        "effective_memory_bank_hash": H("effective-bank"),
        "active_memory_summaries": [
            {"memory_id": "M1"}, {"memory_id": "M2"},
        ],
        "public_memory_regions": [],
        "allowed_operators": [
            "memory_bypass",
            "memory_conflict",
            "memory_deepening",
            "memory_transfer",
        ],
    }
    return {**body, "packet_hash": hash_payload(body)}


def test_grounded_proposal_authority_milestone_is_frozen():
    milestone = json.loads((
        ROOT
        / "configs/evolution/grounded_proposal_authority_v1.json"
    ).read_text(encoding="utf-8"))
    parent = json.loads((
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
    successor = json.loads((
        ROOT
        / "configs/evolution/grounded_sequential_runtime_gate_v1.json"
    ).read_text(encoding="utf-8"))
    population_successor = json.loads((
        ROOT
        / "configs/evolution/grounded_deterministic_population_v1.json"
    ).read_text(encoding="utf-8"))
    integrated_successor = json.loads((
        ROOT
        / "configs/evolution/integrated_deterministic_coevolution_v1.json"
    ).read_text(encoding="utf-8"))
    pilot_successor = json.loads((
        ROOT / "configs/evolution/real_adapter_pilot_entry_v1.json"
    ).read_text(encoding="utf-8"))
    assert successor["parent_milestone"] == {
        "milestone_id": milestone["milestone_id"],
        "milestone_hash": milestone["milestone_hash"],
    }
    assert population_successor["parent_milestone"] == {
        "milestone_id": successor["milestone_id"],
        "milestone_hash": successor["milestone_hash"],
    }
    for relative, expected in milestone["frozen_assets"].items():
        current = hash_file(ROOT / relative)
        if current != expected:
            assert current in {
                successor["frozen_assets"].get(relative),
                population_successor["frozen_assets"].get(relative),
                integrated_successor["frozen_assets"].get(relative),
                pilot_successor["frozen_assets"].get(relative),
            }
    assert "real-model-proposal-yield" in milestone["deferred_scope"]


def test_pre_generation_plan_is_deterministic_and_quota_bound(
    policy, registries
):
    coverage = freeze_coverage_state([
        _cell(
            policy,
            family="combinational.comparator_boundary",
            covered=2,
            admitted=3,
        )
    ])
    archive = build_proposal_archive_view(
        residual_archive=_archive(policy),
        covered_archive=[],
    )
    kwargs = {
        "policy": policy,
        "registries": registries,
        "coverage_state": coverage,
        "archive_view": archive,
        "budget": 5,
        "family_quota": 1,
        "archive_quota": 1,
        "maximum_difficulty_band": "D3",
        "memory_capability": _memory_packet(policy),
        "allow_composition": True,
    }
    first = build_grounded_proposal_plan(**kwargs)
    second = build_grounded_proposal_plan(**kwargs)
    assert first == second
    selected = [
        row for row in first["candidate_intents"]
        if row["intent_id"] in first["selected_intent_ids"]
    ]
    assert len(selected) == 5
    assert len({row["family_id"] for row in selected}) == 5
    assert sum(bool(row["parent_poison_ids"]) for row in selected) <= 1
    comparator = next(
        row for row in first["candidate_intents"]
        if row["family_id"] == "combinational.comparator_boundary"
        and row["dispatch_kind"] == "single_ast"
    )
    assert comparator["difficulty_target"]["difficulty_band"] == "D1"
    assert {
        row["dispatch_kind"] for row in first["candidate_intents"]
    } >= {"single_ast", "parser_memory", "controlled_composition"}


def test_proposal_archive_view_rejects_private_patch(policy):
    leaked = _archive(policy)
    leaked[0]["patch_payload"] = "private"
    with pytest.raises(
        GroundedProposalViolation, match="private archive"
    ):
        build_proposal_archive_view(
            residual_archive=leaked,
            covered_archive=[],
        )


def test_plan_rejects_stale_policy_and_tamper(policy, registries):
    coverage = freeze_coverage_state([])
    archive = build_proposal_archive_view(
        residual_archive=_archive(policy),
        covered_archive=[],
    )
    plan = build_grounded_proposal_plan(
        policy=policy,
        registries=registries,
        coverage_state=coverage,
        archive_view=archive,
        budget=3,
        family_quota=1,
        archive_quota=1,
        maximum_difficulty_band="D3",
        memory_capability=None,
    )
    tampered = deepcopy(plan)
    tampered["candidate_intents"][0]["family_id"] = (
        "control.fsm_transition"
    )
    tampered["plan_hash"] = hash_payload({
        key: value for key, value in tampered.items()
        if key != "plan_hash"
    })
    with pytest.raises(
        GroundedProposalViolation, match="deterministically"
    ):
        verify_grounded_proposal_plan(
            tampered,
            policy=policy,
            registries=registries,
            coverage_state=coverage,
            archive_view=archive,
            memory_capability=None,
        )
    stale = policy.with_updates(
        policy_id="B_STALE",
        parent_policy_id=policy.policy_id,
        parent_policy_hash=policy.policy_hash,
        created_round=1,
    )
    with pytest.raises(
        GroundedProposalViolation, match="authority mismatch"
    ):
        verify_grounded_proposal_plan(
            plan,
            policy=stale,
            registries=registries,
            coverage_state=coverage,
            archive_view=archive,
            memory_capability=None,
        )


def test_fake_adapter_executes_exact_selected_intents(
    tmp_path, policy, registries
):
    coverage = freeze_coverage_state([])
    archive = build_proposal_archive_view(
        residual_archive=[],
        covered_archive=[],
    )
    plan = build_grounded_proposal_plan(
        policy=policy,
        registries=registries,
        coverage_state=coverage,
        archive_view=archive,
        budget=4,
        family_quota=1,
        archive_quota=0,
        maximum_difficulty_band="D3",
        memory_capability=None,
    )
    context = build_red_search_context(
        policy,
        residual_archive=[],
        covered_archive=[],
        grounded_proposal_plan=plan,
    )
    assert context["schema_version"] == "r3e-red-search-context-v6"
    assert verify_red_search_context(context) == context
    rows = list(FakeRedAdapter(tmp_path).generate_red(
        policy, {}, context
    ))
    authority = verify_proposal_candidates(
        plan, rows, policy=policy
    )
    assert authority["proposal_plan_hash"] == plan["plan_hash"]
    assert len(authority["candidate_bindings"]) == 4
    widened = deepcopy(rows)
    widened[0]["grounded_difficulty_target"][
        "dependency_depth_delta"
    ] += 1
    with pytest.raises(
        GroundedProposalViolation, match="widens"
    ):
        verify_proposal_candidates(plan, widened, policy=policy)


def test_runner_toolchain_binds_pre_generation_authority(
    tmp_path, registries
):
    config = json.loads((
        ROOT / "configs/evolution/round_grounded_runtime_v3.json"
    ).read_text(encoding="utf-8"))
    config.update({
        "grounded_pre_generation_planner": True,
        "policy_registry": str(tmp_path / "registry.json"),
        "policy_search_space": (
            "configs/base_policy/policy_search_space_v1.json"
        ),
        "rounds_root": str(tmp_path / "rounds"),
        "events_root": str(tmp_path / "events"),
    })
    runner = EvolutionRoundRunner(
        config,
        round_id="R_GROUNDED_PROPOSAL",
        adapter=DeterministicEvolutionAdapter(tmp_path / "adapter"),
        project_root=ROOT,
    )
    assert runner.round_toolchain_fingerprint == {
        "schema_version": "r3e-arena-grounded-planner-toolchain-v1",
        "evolution_adapter": (
            DeterministicEvolutionAdapter(
                tmp_path / "other"
            ).toolchain_fingerprint
        ),
        "grounded_proposal_authority_hash": (
            grounded_proposal_protocol_hash(registries)
        ),
    }


def test_runner_freezes_proposal_before_validity_and_resumes(
    tmp_path, monkeypatch
):
    registry_path = tmp_path / "registry.json"
    initialize_registry(
        ROOT / "configs/base_policy/frozen_base_policy_v1.json",
        registry_path,
    )
    config = json.loads((
        ROOT / "configs/evolution/round_grounded_runtime_v3.json"
    ).read_text(encoding="utf-8"))
    config.update({
        "grounded_pre_generation_planner": True,
        "grounded_archive_quota": 1,
        "grounded_allow_controlled_composition": True,
        "policy_registry": str(registry_path),
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
        round_id="R_PROPOSAL_CHECKPOINT",
        adapter=DeterministicEvolutionAdapter(tmp_path / "adapter"),
        project_root=ROOT,
    )

    def stop_after_red(**_kwargs):
        raise RuntimeError("stop after proposal generation")

    monkeypatch.setattr(
        "r3e.arena.runner.execute_grounded_arena_validity",
        stop_after_red,
    )
    with pytest.raises(RuntimeError, match="stop after proposal"):
        runner.run()
    round_dir = tmp_path / "rounds/R_PROPOSAL_CHECKPOINT"
    plan = json.loads((
        round_dir / "grounded_proposal_plan.json"
    ).read_text(encoding="utf-8"))
    execution = json.loads((
        round_dir / "grounded_proposal_execution.json"
    ).read_text(encoding="utf-8"))
    assert execution["proposal_plan_hash"] == plan["plan_hash"]
    state = json.loads((
        round_dir / "round_state.json"
    ).read_text(encoding="utf-8"))
    assert "RED_GENERATE" in {
        row["stage"] for row in state["checkpoints"]
    }
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
    assert generated["grounded_proposal_plan_hash"] == plan["plan_hash"]
