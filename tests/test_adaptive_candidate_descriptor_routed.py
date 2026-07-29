from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from r3e.arena.fake_adapters import DeterministicEvolutionAdapter
from r3e.arena.portfolio import (
    ArenaCandidatePortfolioAuthority,
    ArenaPortfolioViolation,
)
from r3e.arena.runner import EvolutionRoundRunner
from r3e.blue.portfolio.audit import (
    PortfolioAuditViolation,
    verify_blue_evaluation,
)
from r3e.blue.portfolio.candidate_executor import (
    CandidatePortfolioExecutor,
)
from r3e.blue.portfolio.fake import (
    DeterministicFakeCandidateProvider,
    DeterministicFakeCandidateVerifier,
)
from r3e.blue.portfolio.fake_system import run_fake_candidate_protocol
from r3e.blue.portfolio.lens_registry import load_lens_registry
from r3e.blue.portfolio.router import (
    DescriptorRouter,
    DescriptorRouterViolation,
    load_descriptor_router,
)
from r3e.blue.portfolio.schema import (
    AllocationPlan,
    CandidatePortfolio,
    PortfolioValidationError,
    build_candidate_portfolio_binding,
)
from r3e.memory.schema import FailureDescriptor
from r3e.policy.schema import PolicyState
from r3e.policy.search import (
    PolicySearchViolation,
    portfolio_conditioned_neighbors,
)
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
def router():
    return load_descriptor_router(
        ROOT / "configs/blue/descriptor_router_v1.json"
    )


@pytest.fixture
def portfolio():
    return CandidatePortfolio.from_dict(json.loads((
        ROOT / "configs/blue/descriptor_routed_portfolio_v1.json"
    ).read_text(encoding="utf-8")))


@pytest.fixture
def policy(portfolio):
    base = PolicyState.from_dict(json.loads((
        ROOT / "configs/base_policy/frozen_base_policy_v1.json"
    ).read_text(encoding="utf-8")))
    return base.with_updates(
        policy_id="B_ACP2",
        schema_version="r3e-policy-v3",
        parent_policy_id=base.policy_id,
        parent_policy_hash=base.policy_hash,
        created_round=2,
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


def _descriptor(kind: str) -> dict:
    features = {
        "oracle_stage": "grounded_simulation_and_formal",
        "sequential_context": True,
        "temporal_relation": "candidate_lags_golden",
        "cycle_offset_bucket": 1,
        "affected_roles": ["observable_output"],
        "assignment_type": "nonblocking",
        "cone_depth_bucket": "depth_1",
        "mismatch_pattern": "value_mismatch",
        "first_divergence_bucket": "cycle_4_7",
        "first_divergence_signal": "out",
        "observable_artifact_hashes": {
            "oracle": H(f"{kind}-oracle"),
            "formal": H(f"{kind}-formal"),
        },
    }
    if kind == "dataflow":
        features.update({
            "sequential_context": False,
            "temporal_relation": "same_cycle",
            "cycle_offset_bucket": 0,
            "assignment_type": "continuous",
            "cone_depth_bucket": "depth_4_plus",
            "mismatch_pattern": "wrong_combinational_value",
            "first_divergence_bucket": "cycle_0",
        })
    elif kind == "control":
        features.update({
            "temporal_relation": "same_cycle",
            "cycle_offset_bucket": 0,
            "cone_depth_bucket": "depth_0",
            "mismatch_pattern": "late_transition",
            "first_divergence_bucket": "cycle_1",
        })
    return FailureDescriptor.create(features).to_dict()


def _round_config():
    return json.loads((
        ROOT / "configs/evolution/round_acp2_descriptor_routed_v1.json"
    ).read_text(encoding="utf-8"))


def _poison(policy, descriptor):
    return bind_poison_payload({
        "poison_id": "P_ACP2",
        "case_id": "CASE_ACP2",
        "challenged_policy_hash": policy.policy_hash,
        "family": "hidden_from_blue",
        "effect": "descriptor_routed",
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


def _executor(
    registry,
    portfolio,
    router,
    *,
    successful_slots=None,
):
    provider = DeterministicFakeCandidateProvider()
    verifier = DeterministicFakeCandidateVerifier(
        successful_slots=set(
            {1} if successful_slots is None else successful_slots
        )
    )
    return (
        CandidatePortfolioExecutor(
            registry=registry,
            portfolio=portfolio,
            provider=provider,
            verifier=verifier,
            project_root=ROOT,
            router=router,
        ),
        provider,
        verifier,
    )


def test_descriptor_routed_portfolio_milestone_is_frozen():
    milestone = json.loads((
        ROOT / "configs/evolution/descriptor_routed_portfolio_v1.json"
    ).read_text(encoding="utf-8"))
    parent = json.loads((
        ROOT / "configs/evolution/fixed_mixed_portfolio_v1.json"
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
    assert acp4["parent_milestone"] == {
        "milestone_id": successor["milestone_id"],
        "milestone_hash": successor["milestone_hash"],
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
                acp4["frozen_assets"].get(relative),
                acp5["frozen_assets"].get(relative),
                acp6["frozen_assets"].get(relative),
            }


def test_descriptor_router_config_is_hash_reconstructable(router):
    assert DescriptorRouter.from_dict(router.to_dict()) == router
    assert router.tie_break_order == (
        "temporal_v1",
        "control_v1",
        "dataflow_v1",
    )


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("temporal", ["generic_v1", "temporal_v1", "dataflow_v1"]),
        ("control", ["generic_v1", "control_v1", "dataflow_v1"]),
        ("dataflow", ["generic_v1", "dataflow_v1", "temporal_v1"]),
    ],
)
def test_descriptor_router_selects_primary_and_orthogonal_specialist(
    kind, expected, router, portfolio, registry
):
    receipt = router.route(
        _descriptor(kind),
        portfolio=portfolio,
        registry=registry,
    )
    assert receipt["allocated_lens_ids"] == expected
    assert receipt["primary_lens_id"] == expected[1]
    assert receipt["orthogonal_lens_id"] == expected[2]
    assert router.verify_receipt(
        receipt,
        _descriptor(kind),
        portfolio=portfolio,
        registry=registry,
    ) == receipt


def test_descriptor_router_tie_break_is_not_model_selected(
    portfolio, registry
):
    router = DescriptorRouter.create(
        router_id="tie-router-v1",
        descriptor_schema_version="r3e-failure-descriptor-v1",
        generic_lens_id="generic_v1",
        specialist_lens_ids=[
            "temporal_v1",
            "control_v1",
            "dataflow_v1",
        ],
        tie_break_order=[
            "temporal_v1",
            "control_v1",
            "dataflow_v1",
        ],
        orthogonal_priority={
            "temporal_v1": ["dataflow_v1", "control_v1"],
            "control_v1": ["dataflow_v1", "temporal_v1"],
            "dataflow_v1": ["temporal_v1", "control_v1"],
        },
        score_rules=[{
            "rule_id": "never-matches",
            "field": "assignment_type",
            "operator": "equals",
            "value": "unknown",
            "scores": {"dataflow_v1": 1},
        }],
    )
    raw = portfolio.to_dict()
    raw["router_hash"] = router.router_hash
    raw.pop("effective_portfolio_hash")
    routed = CandidatePortfolio.create(**{
        key: value
        for key, value in raw.items()
        if key != "schema_version"
    })
    receipt = router.route(
        _descriptor("temporal"),
        portfolio=routed,
        registry=registry,
    )
    assert receipt["specialist_scores"] == {
        "temporal_v1": 0,
        "control_v1": 0,
        "dataflow_v1": 0,
    }
    assert receipt["allocated_lens_ids"] == [
        "generic_v1",
        "temporal_v1",
        "dataflow_v1",
    ]


def test_descriptor_router_rejects_red_truth_rule(router):
    raw = router.to_dict()
    raw.pop("router_hash")
    raw["score_rules"][0]["field"] = "mutation_operator"
    with pytest.raises(
        DescriptorRouterViolation,
        match="exceeds Grounded descriptor authority",
    ):
        DescriptorRouter.create(**{
            key: value
            for key, value in raw.items()
            if key != "schema_version"
        })


def test_descriptor_router_receipt_tamper_is_rejected(
    router, portfolio, registry
):
    descriptor = _descriptor("temporal")
    receipt = router.route(
        descriptor,
        portfolio=portfolio,
        registry=registry,
    )
    receipt["primary_lens_id"] = "control_v1"
    with pytest.raises(
        DescriptorRouterViolation, match="not reconstructable"
    ):
        router.verify_receipt(
            receipt,
            descriptor,
            portfolio=portfolio,
            registry=registry,
        )


def test_descriptor_portfolio_requires_distinct_authorized_pool(portfolio):
    raw = portfolio.to_dict()
    raw.pop("effective_portfolio_hash")
    raw["lens_ids"] = [
        "generic_v1",
        "temporal_v1",
        "temporal_v1",
        "dataflow_v1",
    ]
    with pytest.raises(
        PortfolioValidationError, match="distinct lens pool"
    ):
        CandidatePortfolio.create(**{
            key: value
            for key, value in raw.items()
            if key != "schema_version"
        })


def test_allocation_plan_binds_router_receipt(
    policy, portfolio, registry, router
):
    descriptor = _descriptor("control")
    receipt = router.route(
        descriptor,
        portfolio=portfolio,
        registry=registry,
    )
    plan = AllocationPlan.create(
        effective_policy_hash=policy.effective_policy_hash,
        descriptor_hash=descriptor["descriptor_hash"],
        portfolio=portfolio,
        registry=registry,
        run_seed=19,
        router_receipt=receipt,
    )
    assert [slot["lens_id"] for slot in plan.slots] == (
        receipt["allocated_lens_ids"]
    )
    assert (
        plan.allocation_reason["router_receipt_hash"]
        == receipt["receipt_hash"]
    )
    tampered = deepcopy(receipt)
    tampered["allocated_lens_ids"][1] = "dataflow_v1"
    with pytest.raises(
        PortfolioValidationError, match="receipt authority mismatch"
    ):
        AllocationPlan.create(
            effective_policy_hash=policy.effective_policy_hash,
            descriptor_hash=descriptor["descriptor_hash"],
            portfolio=portfolio,
            registry=registry,
            run_seed=19,
            router_receipt=tampered,
        )


def test_executor_and_offline_audit_reconstruct_descriptor_routing(
    policy, portfolio, registry, router
):
    executor, provider, verifier = _executor(
        registry, portfolio, router
    )
    descriptor = _descriptor("dataflow")
    result = executor.execute(
        policy=policy,
        case={"case_id": "CASE_DATAFLOW"},
        descriptor=descriptor,
        run_seed=23,
    )
    assert result["router_receipt"]["allocated_lens_ids"] == [
        "generic_v1",
        "dataflow_v1",
        "temporal_v1",
    ]
    assert [
        row["lens_id"] for row in result[
            "candidate_generation_receipts"
        ]
    ] == result["router_receipt"]["allocated_lens_ids"]
    assert verify_blue_evaluation(
        result,
        policy=policy,
        registry=registry,
        portfolio=portfolio,
        provider=provider,
        expected_verifier_hash=verifier.verifier_hash,
        router=router,
        descriptor=descriptor,
    ) == result
    tampered = deepcopy(result)
    tampered["router_receipt"]["primary_lens_id"] = "control_v1"
    tampered["portfolio_execution_hash"] = hash_payload({
        key: value for key, value in tampered.items()
        if key != "portfolio_execution_hash"
    })
    with pytest.raises(
        PortfolioAuditViolation, match="router receipt is invalid"
    ):
        verify_blue_evaluation(
            tampered,
            policy=policy,
            registry=registry,
            portfolio=portfolio,
            provider=provider,
            expected_verifier_hash=verifier.verifier_hash,
            router=router,
            descriptor=descriptor,
        )


def test_formal_arena_challenge_uses_descriptor_router(
    policy, portfolio, router
):
    authority = ArenaCandidatePortfolioAuthority(
        project_root=ROOT,
        config=_round_config(),
        provider=DeterministicFakeCandidateProvider(),
        verifier=DeterministicFakeCandidateVerifier(
            successful_slots={1}
        ),
    )
    descriptor = _descriptor("control")
    challenge = authority.evaluate_challenge(
        policy,
        _poison(policy, descriptor),
        seeds=[31],
    )
    result = challenge["blue_results"][0]
    assert result["router_receipt"]["allocated_lens_ids"] == [
        "generic_v1",
        "control_v1",
        "dataflow_v1",
    ]
    assert authority.router == router
    assert (
        authority.authority_hash
        == hash_payload({
            "authority": "candidate_portfolio_v1",
            "lens_registry_hash": authority.registry.registry_hash,
            "portfolio_hash": portfolio.portfolio_hash,
            "effective_portfolio_hash": (
                portfolio.effective_portfolio_hash
            ),
            "descriptor_router_hash": router.router_hash,
            "provider_toolchain_fingerprint": (
                authority.provider_fingerprint
            ),
                "verifier_hash": authority.verifier_hash,
                "semantic_signature_provider_hash": (
                    authority.semantic_signature_provider_hash
                ),
                "offline_allocator_hash": "",
            })
    )


def test_formal_arena_rejects_missing_descriptor_router():
    config = _round_config()
    config.pop("blue_descriptor_router")
    with pytest.raises(
        ArenaPortfolioViolation, match="requires frozen router"
    ):
        ArenaCandidatePortfolioAuthority(
            project_root=ROOT,
            config=config,
            provider=DeterministicFakeCandidateProvider(),
            verifier=DeterministicFakeCandidateVerifier(
                successful_slots={1}
            ),
        )


def test_runner_toolchain_binds_descriptor_router(tmp_path, router):
    config = {
        **_round_config(),
        "policy_registry": str(tmp_path / "registry.json"),
        "policy_search_space": (
            "configs/base_policy/policy_search_space_v1.json"
        ),
        "rounds_root": str(tmp_path / "rounds"),
        "events_root": str(tmp_path / "events"),
    }
    runner = EvolutionRoundRunner(
        config,
        round_id="R_ACP2",
        adapter=DeterministicEvolutionAdapter(tmp_path / "adapter"),
        project_root=ROOT,
        candidate_provider=DeterministicFakeCandidateProvider(),
        candidate_verifier=DeterministicFakeCandidateVerifier(
            successful_slots={1}
        ),
    )
    assert runner.candidate_portfolio_authority is not None
    assert (
        runner.candidate_portfolio_authority.router.router_hash
        == router.router_hash
    )
    assert (
        runner.round_toolchain_fingerprint[
            "blue_portfolio_authority_hash"
        ]
        == runner.candidate_portfolio_authority.authority_hash
    )
    assert (
        runner.round_toolchain_fingerprint[
            "blue_descriptor_router_hash"
        ]
        == router.router_hash
    )


def test_policy_search_builds_one_mechanism_fixed_to_routed_child(
    portfolio,
):
    fixed = CandidatePortfolio.from_dict(json.loads((
        ROOT / "configs/blue/fixed_mixed_portfolio_v1.json"
    ).read_text(encoding="utf-8")))
    base = PolicyState.from_dict(json.loads((
        ROOT / "configs/base_policy/frozen_base_policy_v1.json"
    ).read_text(encoding="utf-8")))
    parent = base.with_updates(
        policy_id="B_ACP1_PARENT",
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
            fixed
        ),
    )
    operator_space = json.loads((
        ROOT / "configs/blue/portfolio_operator_space_v1.json"
    ).read_text(encoding="utf-8"))
    children = portfolio_conditioned_neighbors(
        parent,
        [{
            "operator": "fixed_to_descriptor_routed",
            "portfolio": portfolio.to_dict(),
        }],
        operator_space,
        round_id="R002",
        residual_manifest_hash=H("R002-residual"),
    )
    assert len(children) == 1
    child = children[0]
    assert child.parent_policy_hash == parent.policy_hash
    assert child.configuration == parent.configuration
    assert child.budgets == parent.budgets
    assert child.memory_binding == {}
    assert child.proposal_operator == (
        "portfolio:fixed_to_descriptor_routed"
    )
    assert child.candidate_portfolio_binding == (
        build_candidate_portfolio_binding(portfolio)
    )
    assert child.effective_policy_hash != parent.effective_policy_hash


def test_policy_search_rejects_multi_mechanism_mislabeled_operator(
    portfolio,
):
    fixed = CandidatePortfolio.from_dict(json.loads((
        ROOT / "configs/blue/fixed_mixed_portfolio_v1.json"
    ).read_text(encoding="utf-8")))
    base = PolicyState.from_dict(json.loads((
        ROOT / "configs/base_policy/frozen_base_policy_v1.json"
    ).read_text(encoding="utf-8")))
    parent = base.with_updates(
        policy_id="B_ACP1_PARENT",
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
            fixed
        ),
    )
    operator_space = json.loads((
        ROOT / "configs/blue/portfolio_operator_space_v1.json"
    ).read_text(encoding="utf-8"))
    with pytest.raises(
        PolicySearchViolation,
        match="one-mechanism transition",
    ):
        portfolio_conditioned_neighbors(
            parent,
            [{
                "operator": "replace_router",
                "portfolio": portfolio.to_dict(),
            }],
            operator_space,
            round_id="R002",
            residual_manifest_hash=H("R002-residual"),
        )


def test_acp2_fake_system_executes_descriptor_routed_portfolio(tmp_path):
    summary = run_fake_candidate_protocol(
        project_root=ROOT,
        out=tmp_path / "blue_evaluation.json",
        run_seed=101,
        portfolio_path=(
            "configs/blue/descriptor_routed_portfolio_v1.json"
        ),
        router_path="configs/blue/descriptor_router_v1.json",
    )
    assert summary["portfolio_mode"] == "descriptor_routed"
    assert summary["lens_ids"] == [
        "generic_v1",
        "temporal_v1",
        "dataflow_v1",
    ]
    assert summary["candidate_count"] == 3
