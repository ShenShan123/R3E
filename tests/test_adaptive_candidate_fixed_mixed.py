from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from r3e.arena.fake_adapters import DeterministicEvolutionAdapter
from r3e.arena.portfolio import (
    CANDIDATE_PORTFOLIO_AUTHORITY,
    ArenaCandidatePortfolioAuthority,
    ArenaPortfolioViolation,
)
from r3e.arena.runner import EvolutionRoundRunner, RoundRunnerViolation
from r3e.blue.portfolio.fake import (
    DeterministicFakeCandidateProvider,
    DeterministicFakeCandidateVerifier,
    FAKE_CANDIDATE_TOOLCHAIN,
)
from r3e.blue.portfolio.fake_system import run_fake_candidate_protocol
from r3e.blue.portfolio.lens_registry import load_lens_registry
from r3e.blue.portfolio.schema import (
    AllocationPlan,
    CandidatePortfolio,
    build_candidate_portfolio_binding,
    build_implicit_homogeneous_portfolio,
)
from r3e.memory.schema import FailureDescriptor
from r3e.policy.registry_v2 import initialize_registry
from r3e.policy.schema import PolicyState, PolicyValidationError
from r3e.protocol.hashing import hash_file, hash_payload
from r3e.red.poison_payload import bind_poison_payload


ROOT = Path(__file__).resolve().parents[1]
H = lambda value: hash_payload({"value": value})


@pytest.fixture
def registry():
    return load_lens_registry(
        ROOT / "configs/blue/lens_registry_v1.json",
        project_root=ROOT,
    )


@pytest.fixture
def portfolio():
    return CandidatePortfolio.from_dict(json.loads((
        ROOT / "configs/blue/fixed_mixed_portfolio_v1.json"
    ).read_text(encoding="utf-8")))


@pytest.fixture
def policy(portfolio):
    base = PolicyState.from_dict(json.loads((
        ROOT / "configs/base_policy/frozen_base_policy_v1.json"
    ).read_text(encoding="utf-8")))
    return base.with_updates(
        policy_id="B_ACP1",
        schema_version="r3e-policy-v3",
        parent_policy_id=base.policy_id,
        parent_policy_hash=base.policy_hash,
        created_round=1,
        status="active",
        configuration={
            **base.configuration,
            "n_candidates": 3,
            "candidate_selection": "verifier_guided",
        },
        candidate_portfolio_binding=build_candidate_portfolio_binding(
            portfolio
        ),
    )


@pytest.fixture
def descriptor():
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
    }).to_dict()


def _round_config():
    return json.loads((
        ROOT / "configs/evolution/round_acp1_fixed_mixed_v1.json"
    ).read_text(encoding="utf-8"))


def _poison(policy, descriptor):
    return bind_poison_payload({
        "poison_id": "P_ACP1",
        "case_id": "CASE_ACP1",
        "challenged_policy_hash": policy.policy_hash,
        "family": "hidden_from_blue",
        "effect": "late_transition",
        "affected_role": "control",
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


def test_fixed_mixed_portfolio_milestone_is_frozen():
    milestone = json.loads((
        ROOT / "configs/evolution/fixed_mixed_portfolio_v1.json"
    ).read_text(encoding="utf-8"))
    parent = json.loads((
        ROOT / "configs/evolution/adaptive_candidate_protocol_v1.json"
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
        ROOT / "configs/evolution/descriptor_routed_portfolio_v1.json"
    ).read_text(encoding="utf-8"))
    acp3 = json.loads((
        ROOT / "configs/evolution/semantic_patch_diversity_v1.json"
    ).read_text(encoding="utf-8"))
    acp4 = json.loads((
        ROOT / "configs/evolution/offline_adaptive_allocator_v1.json"
    ).read_text(encoding="utf-8"))
    acp5 = json.loads((
        ROOT / "configs/evolution/raam_portfolio_control_v1.json"
    ).read_text(encoding="utf-8"))
    acp6 = json.loads((
        ROOT
        / "configs/evolution/portfolio_aware_red_challenge_v1.json"
    ).read_text(encoding="utf-8"))
    assert successor["parent_milestone"] == {
        "milestone_id": milestone["milestone_id"],
        "milestone_hash": milestone["milestone_hash"],
    }
    assert acp3["parent_milestone"] == {
        "milestone_id": successor["milestone_id"],
        "milestone_hash": successor["milestone_hash"],
    }
    assert acp4["parent_milestone"] == {
        "milestone_id": acp3["milestone_id"],
        "milestone_hash": acp3["milestone_hash"],
    }
    assert acp5["parent_milestone"] == {
        "milestone_id": acp4["milestone_id"],
        "milestone_hash": acp4["milestone_hash"],
    }
    for relative, expected in milestone["frozen_assets"].items():
        current = hash_file(ROOT / relative)
        if current != expected:
            assert current in {
                successor["frozen_assets"].get(relative),
                    acp3["frozen_assets"].get(relative),
                    acp4["frozen_assets"].get(relative),
                    acp5["frozen_assets"].get(relative),
                    acp6["frozen_assets"].get(relative),
                }


def test_policy_v3_binds_fixed_mixed_portfolio(policy, portfolio):
    assert policy.schema_version == "r3e-policy-v3"
    assert policy.candidate_portfolio_binding == (
        build_candidate_portfolio_binding(portfolio)
    )
    raw = policy.to_dict()
    raw["candidate_portfolio_binding"]["effective_portfolio_hash"] = H(
        "tampered"
    )
    tampered = PolicyState.from_dict(raw)
    assert tampered.effective_policy_hash != policy.effective_policy_hash


def test_effective_portfolio_identity_excludes_bookkeeping_id(portfolio):
    renamed = CandidatePortfolio.create(
        portfolio_id="fixed-mixed-renamed-v1",
        mode=portfolio.mode,
        candidate_budget=portfolio.candidate_budget,
        lens_registry_hash=portfolio.lens_registry_hash,
        router_hash=portfolio.router_hash,
        allocator_hash=portfolio.allocator_hash,
        selector_hash=portfolio.selector_hash,
        semantic_signature_provider_hash=(
            portfolio.semantic_signature_provider_hash
        ),
        duplicate_policy=portfolio.duplicate_policy,
        early_stop_mode=portfolio.early_stop_mode,
        lens_ids=portfolio.lens_ids,
    )
    assert renamed.portfolio_hash != portfolio.portfolio_hash
    assert (
        renamed.effective_portfolio_hash
        == portfolio.effective_portfolio_hash
    )


def test_policy_v3_requires_complete_portfolio_binding(policy):
    raw = policy.to_dict()
    raw["candidate_portfolio_binding"] = {}
    with pytest.raises(
        PolicyValidationError, match="requires candidate_portfolio_binding"
    ):
        PolicyState.from_dict(raw)


def test_fixed_mixed_plan_has_three_distinct_lenses(
    policy, registry, portfolio, descriptor
):
    plan = AllocationPlan.create(
        effective_policy_hash=policy.effective_policy_hash,
        descriptor_hash=descriptor["descriptor_hash"],
        portfolio=portfolio,
        registry=registry,
        run_seed=101,
    )
    assert [slot["lens_id"] for slot in plan.slots] == [
        "temporal_v1",
        "control_v1",
        "dataflow_v1",
    ]
    assert len({slot["candidate_seed"] for slot in plan.slots}) == 3


def test_fixed_mixed_and_generic_control_have_equal_budget(
    registry, portfolio
):
    base = PolicyState.from_dict(json.loads((
        ROOT / "configs/base_policy/frozen_base_policy_v1.json"
    ).read_text(encoding="utf-8")))
    v2 = base.with_updates(
        policy_id="B_ACP1_CONTROL",
        parent_policy_id="B0",
        parent_policy_hash=base.policy_hash,
        created_round=1,
        status="candidate",
        configuration={
            **base.configuration,
            "n_candidates": 3,
            "prompt_lens_id": "generic_v1",
            "candidate_selection": "verifier_guided",
        },
    )
    control = build_implicit_homogeneous_portfolio(v2, registry)
    assert portfolio.candidate_budget == control.candidate_budget == 3
    assert control.lens_ids == ("generic_v1",) * 3
    assert portfolio.lens_ids == (
        "temporal_v1",
        "control_v1",
        "dataflow_v1",
    )
    assert (
        portfolio.effective_portfolio_hash
        != control.effective_portfolio_hash
    )


def test_acp1_fake_system_executes_explicit_fixed_mixed_portfolio(tmp_path):
    out = tmp_path / "fixed_mixed_blue_evaluation.json"
    summary = run_fake_candidate_protocol(
        project_root=ROOT,
        out=out,
        run_seed=101,
        portfolio_path="configs/blue/fixed_mixed_portfolio_v1.json",
    )
    assert summary["portfolio_mode"] == "fixed_mixed"
    assert summary["lens_ids"] == [
        "temporal_v1",
        "control_v1",
        "dataflow_v1",
    ]
    assert summary["candidate_count"] == 3
    assert summary["oracle_ok"] is True


def test_arena_fixed_mixed_challenge_uses_portfolio_executor(
    policy, descriptor
):
    provider = DeterministicFakeCandidateProvider()
    verifier = DeterministicFakeCandidateVerifier(successful_slots={1})
    authority = ArenaCandidatePortfolioAuthority(
        project_root=ROOT,
        config=_round_config(),
        provider=provider,
        verifier=verifier,
    )
    challenge = authority.evaluate_challenge(
        policy,
        _poison(policy, descriptor),
        seeds=[11, 12],
    )
    assert challenge["repair_attempts"] == 2
    assert challenge["repair_successes"] == 2
    assert len(challenge["blue_results"]) == 2
    for seed, result in zip([11, 12], challenge["blue_results"], strict=True):
        assert result["schema_version"] == "r3e-blue-evaluation-v2"
        assert result["seed"] == seed
        assert [
            row["lens_id"]
            for row in result["candidate_generation_receipts"]
        ] == ["temporal_v1", "control_v1", "dataflow_v1"]
        assert result["selection_receipt"][
            "selected_candidate_id"
        ].endswith("_1")
        assert result["resource_usage"]["llm_calls"] == 3
    assert (
        challenge["blue_results"][0]["allocation_plan_hash"]
        != challenge["blue_results"][1]["allocation_plan_hash"]
    )


def test_arena_rejects_descriptor_not_bound_to_grounded_validity(
    policy, descriptor
):
    authority = ArenaCandidatePortfolioAuthority(
        project_root=ROOT,
        config=_round_config(),
        provider=DeterministicFakeCandidateProvider(),
        verifier=DeterministicFakeCandidateVerifier(successful_slots={1}),
    )
    poison = _poison(policy, descriptor)
    poison["validity"]["evidence"]["failure_descriptor_hash"] = H("wrong")
    with pytest.raises(ArenaPortfolioViolation, match="not bound"):
        authority.evaluate(policy, poison, 1)


def test_arena_rejects_unproven_grounded_validity(policy, descriptor):
    authority = ArenaCandidatePortfolioAuthority(
        project_root=ROOT,
        config=_round_config(),
        provider=DeterministicFakeCandidateProvider(),
        verifier=DeterministicFakeCandidateVerifier(successful_slots={1}),
    )
    poison = _poison(policy, descriptor)
    poison["validity"]["proven_valid"] = False
    with pytest.raises(ArenaPortfolioViolation, match="not proven valid"):
        authority.evaluate(policy, poison, 1)


def test_arena_rejects_portfolio_not_authorized_by_policy(
    policy, portfolio, descriptor
):
    raw = policy.to_dict()
    raw["candidate_portfolio_binding"] = {
        **raw["candidate_portfolio_binding"],
        "selector_hash": H("different-selector"),
    }
    mismatched = PolicyState.from_dict(raw)
    authority = ArenaCandidatePortfolioAuthority(
        project_root=ROOT,
        config=_round_config(),
        provider=DeterministicFakeCandidateProvider(),
        verifier=DeterministicFakeCandidateVerifier(successful_slots={1}),
    )
    with pytest.raises(
        Exception, match="not authorized by active Policy V3"
    ):
        authority.evaluate(
            mismatched,
            _poison(mismatched, descriptor),
            1,
        )


def test_round_runner_requires_grounded_validity_for_portfolio(tmp_path):
    config = {
        **_round_config(),
        "validity_authority": "legacy_adapter_evidence_v1",
        "policy_registry": "runtime/registry.json",
        "policy_search_space": "configs/base_policy/policy_search_space_v1.json",
    }
    with pytest.raises(
        RoundRunnerViolation, match="requires Grounded validity"
    ):
        EvolutionRoundRunner(
            config,
            round_id="R_ACP1_BAD",
            adapter=DeterministicEvolutionAdapter(tmp_path / "adapter"),
            project_root=ROOT,
            candidate_provider=DeterministicFakeCandidateProvider(),
            candidate_verifier=DeterministicFakeCandidateVerifier(
                successful_slots={1}
            ),
        )


def test_round_runner_constructs_formal_portfolio_authority(tmp_path):
    config = {
        **_round_config(),
        "policy_registry": str(tmp_path / "registry.json"),
        "policy_search_space": "configs/base_policy/policy_search_space_v1.json",
        "rounds_root": str(tmp_path / "rounds"),
        "events_root": str(tmp_path / "events"),
    }
    runner = EvolutionRoundRunner(
        config,
        round_id="R_ACP1",
        adapter=DeterministicEvolutionAdapter(tmp_path / "adapter"),
        project_root=ROOT,
        candidate_provider=DeterministicFakeCandidateProvider(),
        candidate_verifier=DeterministicFakeCandidateVerifier(
            successful_slots={1}
        ),
    )
    assert runner.blue_evaluation_authority == CANDIDATE_PORTFOLIO_AUTHORITY
    assert runner.candidate_portfolio_authority is not None
    assert runner.round_toolchain_fingerprint[
        "blue_candidate_provider"
    ] == FAKE_CANDIDATE_TOOLCHAIN


def test_formal_runner_challenge_bypasses_opaque_blue_adapter(
    tmp_path, policy, descriptor
):
    config = {
        **_round_config(),
        "policy_registry": str(tmp_path / "registry.json"),
        "policy_search_space": "configs/base_policy/policy_search_space_v1.json",
        "rounds_root": str(tmp_path / "rounds"),
        "events_root": str(tmp_path / "events"),
    }
    adapter = DeterministicEvolutionAdapter(tmp_path / "adapter")

    def forbidden_evaluate_blue(*args, **kwargs):
        del args, kwargs
        raise AssertionError("opaque evaluate_blue must not be called")

    adapter.evaluate_blue = forbidden_evaluate_blue
    runner = EvolutionRoundRunner(
        config,
        round_id="R_ACP1_FORMAL_CHALLENGE",
        adapter=adapter,
        project_root=ROOT,
        candidate_provider=DeterministicFakeCandidateProvider(),
        candidate_verifier=DeterministicFakeCandidateVerifier(
            successful_slots={1}
        ),
    )
    results = runner._evaluate_blue_challenges(
        policy,
        [_poison(policy, descriptor)],
        seeds=[7],
    )
    assert len(results) == 1
    assert results[0]["blue_results"][0]["schema_version"] == (
        "r3e-blue-evaluation-v2"
    )


def test_formal_runner_fails_before_challenge_for_active_policy_v2(tmp_path):
    registry_path = tmp_path / "registry.json"
    initialize_registry(
        ROOT / "configs/base_policy/frozen_base_policy_v1.json",
        registry_path,
    )
    config = {
        **_round_config(),
        "policy_registry": str(registry_path),
        "policy_search_space": "configs/base_policy/policy_search_space_v1.json",
        "rounds_root": str(tmp_path / "rounds"),
        "events_root": str(tmp_path / "events"),
    }
    runner = EvolutionRoundRunner(
        config,
        round_id="R_ACP1_POLICY_V2",
        adapter=DeterministicEvolutionAdapter(tmp_path / "adapter"),
        project_root=ROOT,
        candidate_provider=DeterministicFakeCandidateProvider(),
        candidate_verifier=DeterministicFakeCandidateVerifier(
            successful_slots={1}
        ),
    )
    with pytest.raises(RoundRunnerViolation, match="requires Policy V3"):
        runner.run()
