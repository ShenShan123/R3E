from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from r3e.blue.portfolio.audit import (
    PortfolioAuditViolation,
    verify_blue_evaluation,
)
from r3e.blue.portfolio.candidate_executor import (
    CandidatePortfolioExecutionViolation,
    CandidatePortfolioExecutor,
)
from r3e.blue.portfolio.fake import (
    DeterministicFakeCandidateProvider,
    DeterministicFakeCandidateVerifier,
)
from r3e.blue.portfolio.fake_system import run_fake_candidate_protocol
from r3e.blue.portfolio.lens_registry import load_lens_registry
from r3e.blue.portfolio.schema import (
    AllocationPlan,
    CandidatePortfolio,
    LensDefinition,
    LensRegistry,
    PortfolioValidationError,
    build_implicit_homogeneous_portfolio,
)
from r3e.memory.schema import FailureDescriptor
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_file, hash_payload


ROOT = Path(__file__).resolve().parents[1]
H = lambda value: hash_payload({"value": value})


@pytest.fixture
def policy() -> PolicyState:
    base = PolicyState.from_dict(json.loads(
        (
            ROOT / "configs/base_policy/frozen_base_policy_v1.json"
        ).read_text(encoding="utf-8")
    ))
    return base.with_updates(
        policy_id="B_ACP0",
        parent_policy_id=base.policy_id,
        parent_policy_hash=base.policy_hash,
        created_round=1,
        status="candidate",
        configuration={
            **base.configuration,
            "n_candidates": 3,
            "candidate_selection": "verifier_guided",
        },
    )


@pytest.fixture
def registry() -> LensRegistry:
    return load_lens_registry(
        ROOT / "configs/blue/lens_registry_v1.json",
        project_root=ROOT,
    )


@pytest.fixture
def descriptor() -> dict:
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


def _portfolio(
    registry: LensRegistry,
    *,
    candidate_budget: int = 3,
    selector: str = "oracle_then_minimality_v1",
) -> CandidatePortfolio:
    return CandidatePortfolio.create(
        portfolio_id=(
            f"implicit-generic_v1-{candidate_budget}-"
            f"{selector}"
        ),
        mode="homogeneous",
        candidate_budget=candidate_budget,
        lens_registry_hash=registry.registry_hash,
        router_hash=hash_payload({"router": "implicit-homogeneous-v1"}),
        allocator_hash=hash_payload({"allocator": "fixed-policy-v2"}),
        selector_hash=hash_payload({"selector": selector}),
        semantic_signature_provider_hash=hash_payload({
            "provider": "measure-only-v1",
        }),
        duplicate_policy="measure_only",
        early_stop_mode="all_candidates",
        lens_ids=["generic_v1"] * candidate_budget,
    )


def _executor(
    policy: PolicyState,
    registry: LensRegistry,
    *,
    provider=None,
    verifier=None,
    portfolio=None,
    clock=None,
) -> CandidatePortfolioExecutor:
    del policy
    kwargs = {}
    if clock is not None:
        kwargs["clock"] = clock
    return CandidatePortfolioExecutor(
        registry=registry,
        portfolio=portfolio or _portfolio(registry),
        provider=provider or DeterministicFakeCandidateProvider(),
        verifier=verifier
        or DeterministicFakeCandidateVerifier(successful_slots={1}),
        project_root=ROOT,
        **kwargs,
    )


class _TamperingProvider:
    def __init__(
        self,
        wrapped: DeterministicFakeCandidateProvider,
        *,
        field: str,
        value,
        rebuild_result_hash: bool,
    ):
        self.wrapped = wrapped
        self.field = field
        self.value = value
        self.rebuild_result_hash = rebuild_result_hash
        self.toolchain_fingerprint = wrapped.toolchain_fingerprint

    def generate_candidate(self, **kwargs):
        result = self.wrapped.generate_candidate(**kwargs)
        result[self.field] = self.value
        if self.rebuild_result_hash:
            result["result_hash"] = hash_payload({
                key: value for key, value in result.items()
                if key != "result_hash"
            })
        return result


def test_lens_registry_binds_all_prompt_assets(registry):
    assert set(registry.lenses) == {
        "generic_v1",
        "temporal_v1",
        "control_v1",
        "dataflow_v1",
    }
    assert LensRegistry.from_dict(registry.to_dict()) == registry


def test_adaptive_candidate_protocol_milestone_is_frozen():
    milestone = json.loads((
        ROOT / "configs/evolution/adaptive_candidate_protocol_v1.json"
    ).read_text(encoding="utf-8"))
    parent = json.loads((
        ROOT
        / "configs/evolution/grounded_planner_raam_cross_round_v1.json"
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
    acp1 = json.loads((
        ROOT / "configs/evolution/fixed_mixed_portfolio_v1.json"
    ).read_text(encoding="utf-8"))
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
    assert acp1["parent_milestone"] == {
        "milestone_id": milestone["milestone_id"],
        "milestone_hash": milestone["milestone_hash"],
    }
    assert successor["parent_milestone"] == {
        "milestone_id": acp1["milestone_id"],
        "milestone_hash": acp1["milestone_hash"],
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
                acp1["frozen_assets"].get(relative),
                successor["frozen_assets"].get(relative),
                acp3["frozen_assets"].get(relative),
                acp4["frozen_assets"].get(relative),
                acp5["frozen_assets"].get(relative),
                acp6["frozen_assets"].get(relative),
            }


def test_lens_definition_rejects_history_and_red_truth_fields():
    with pytest.raises(
        PortfolioValidationError, match="allowed evidence fields"
    ):
        LensDefinition.create(
            lens_id="leaky_v1",
            prompt_asset_path="configs/base_policy/prompt_templates/generic_v1.txt",
            prompt_asset_hash=H("asset"),
            lens_group="leaky",
            orthogonality_group="leaky",
            allowed_evidence_fields=["mutation_operator"],
        )


def test_portfolio_hash_binds_all_lenses_and_components(registry):
    base = _portfolio(registry)
    changed = _portfolio(registry, selector="first_verified_v1")
    assert base.effective_portfolio_hash != changed.effective_portfolio_hash
    raw = base.to_dict()
    raw["lens_ids"] = ["temporal_v1"] * raw["candidate_budget"]
    with pytest.raises(PortfolioValidationError, match="hash mismatch"):
        CandidatePortfolio.from_dict(raw)


def test_allocation_plan_is_deterministic_and_slot_specific(
    policy, registry, descriptor
):
    portfolio = _portfolio(registry)
    first = AllocationPlan.create(
        effective_policy_hash=policy.effective_policy_hash,
        descriptor_hash=descriptor["descriptor_hash"],
        portfolio=portfolio,
        registry=registry,
        run_seed=101,
    )
    second = AllocationPlan.create(
        effective_policy_hash=policy.effective_policy_hash,
        descriptor_hash=descriptor["descriptor_hash"],
        portfolio=portfolio,
        registry=registry,
        run_seed=101,
    )
    assert first == second
    assert first.candidate_budget == len(first.slots) == 3
    assert len({slot["candidate_seed"] for slot in first.slots}) == 3
    assert all(
        slot["lens_hash"] == registry.lenses["generic_v1"].definition_hash
        for slot in first.slots
    )
    assert AllocationPlan.from_dict(first.to_dict()) == first


def test_policy_v2_maps_to_implicit_homogeneous_portfolio(policy, registry):
    portfolio = build_implicit_homogeneous_portfolio(policy, registry)
    assert portfolio.mode == "homogeneous"
    assert portfolio.candidate_budget == policy.configuration["n_candidates"]
    assert set(portfolio.lens_ids) == {policy.configuration["prompt_lens_id"]}


def test_executor_records_every_slot_and_runner_selects_oracle_candidate(
    policy, registry, descriptor
):
    provider = DeterministicFakeCandidateProvider()
    verifier = DeterministicFakeCandidateVerifier(successful_slots={1, 2})
    portfolio = _portfolio(registry)
    executor = _executor(
        policy,
        registry,
        provider=provider,
        verifier=verifier,
        portfolio=portfolio,
    )
    result = executor.execute(
        policy=policy,
        case={"case_id": "CASE_A", "mutation_family": "must_not_leak"},
        descriptor=descriptor,
        run_seed=101,
    )
    assert result["schema_version"] == "r3e-blue-evaluation-v2"
    assert result["oracle_ok"] is True
    assert result["selection_receipt"]["selection_policy"] == (
        "oracle_then_minimality_v1"
    )
    assert result["selection_receipt"]["selected_candidate_id"].endswith("_1")
    assert len(result["candidate_provider_receipts"]) == 3
    assert len(result["candidate_generation_receipts"]) == 3
    assert len(result["candidate_verification_receipts"]) == 3
    assert result["execution_trace"]["provider_calls"] == 3
    assert result["execution_trace"]["schema_version"] == (
        "r3e-memory-execution-trace-v2"
    )
    assert len(result["execution_trace"]["events"]) == (3 * 8) + 2
    assert all(
        "mutation_family" not in call["current_case_evidence"]
        for call in provider.calls
    )
    assert verify_blue_evaluation(
        result,
        policy=policy,
        registry=registry,
        portfolio=portfolio,
        provider=provider,
    ) == result


def test_provider_cannot_select_winner(policy, registry, descriptor):
    provider = DeterministicFakeCandidateProvider(
        forbidden_authority_field="selected_candidate_id"
    )
    executor = _executor(policy, registry, provider=provider)
    with pytest.raises(
        CandidatePortfolioExecutionViolation,
        match="provider failed conformance",
    ):
        executor.execute(
            policy=policy,
            case={"case_id": "CASE_AUTHORITY"},
            descriptor=descriptor,
            run_seed=5,
        )


@pytest.mark.parametrize(
    ("field", "value", "rebuild", "message"),
    [
        ("model_id", "undeclared-model", True, "provider failed conformance"),
        ("verifier_hash", H("undeclared-verifier"), True, "provider failed conformance"),
        ("budget_hash", H("wrong-budget"), True, "budget binding mismatch"),
        ("output_schema_version", "wrong-schema", True, "provider failed conformance"),
        ("command_hash", H("changed-command"), False, "provider failed conformance"),
    ],
)
def test_provider_model_budget_verifier_command_and_schema_are_bound(
    policy,
    registry,
    descriptor,
    field,
    value,
    rebuild,
    message,
):
    provider = _TamperingProvider(
        DeterministicFakeCandidateProvider(),
        field=field,
        value=value,
        rebuild_result_hash=rebuild,
    )
    executor = _executor(policy, registry, provider=provider)
    with pytest.raises(CandidatePortfolioExecutionViolation, match=message):
        executor.execute(
            policy=policy,
            case={"case_id": f"CASE_{field}"},
            descriptor=descriptor,
            run_seed=13,
        )


def test_selector_rejects_non_oracle_candidates(policy, registry, descriptor):
    executor = _executor(
        policy,
        registry,
        verifier=DeterministicFakeCandidateVerifier(successful_slots=set()),
    )
    result = executor.execute(
        policy=policy,
        case={"case_id": "CASE_FAIL"},
        descriptor=descriptor,
        run_seed=7,
    )
    assert result["oracle_ok"] is False
    assert result["selection_receipt"]["selected_candidate_id"] is None
    assert result["selection_receipt"]["verified_candidate_ids"] == []


def test_token_budget_is_enforced_across_slots(policy, registry, descriptor):
    restricted = replace(
        policy,
        budgets={
            **policy.budgets,
            "max_tokens_per_case": 20,
        },
    )
    provider = DeterministicFakeCandidateProvider(
        input_tokens_per_call=7,
        output_tokens_per_call=3,
    )
    executor = _executor(restricted, registry, provider=provider)
    with pytest.raises(
        CandidatePortfolioExecutionViolation, match="token budget exceeded"
    ):
        executor.execute(
            policy=restricted,
            case={"case_id": "CASE_TOKEN"},
            descriptor=descriptor,
            run_seed=1,
        )
    assert len(provider.calls) == 3


def test_candidate_budget_cannot_exceed_policy(policy, registry, descriptor):
    restricted = replace(
        policy,
        budgets={
            **policy.budgets,
            "max_llm_calls_per_case": 1,
        },
    )
    provider = DeterministicFakeCandidateProvider()
    executor = _executor(restricted, registry, provider=provider)
    with pytest.raises(
        CandidatePortfolioExecutionViolation,
        match="candidate budget exceeds",
    ):
        executor.execute(
            policy=restricted,
            case={"case_id": "CASE_CALLS"},
            descriptor=descriptor,
            run_seed=1,
        )
    assert provider.calls == []


def test_wall_time_has_post_provider_hard_gate(
    policy, registry, descriptor
):
    restricted = replace(
        policy,
        budgets={
            **policy.budgets,
            "max_wall_seconds_per_case": 10,
        },
    )
    readings = iter([0.0, 11.0])
    executor = _executor(
        restricted,
        registry,
        clock=lambda: next(readings),
    )
    with pytest.raises(
        CandidatePortfolioExecutionViolation, match="wall-time budget exceeded"
    ):
        executor.execute(
            policy=restricted,
            case={"case_id": "CASE_WALL"},
            descriptor=descriptor,
            run_seed=1,
        )


def test_descriptor_red_truth_is_rejected_before_provider(
    policy, registry, descriptor
):
    leaky = deepcopy(descriptor)
    leaky["mutation_operator"] = "replace_comparator"
    leaky["descriptor_hash"] = hash_payload({
        key: value for key, value in leaky.items()
        if key != "descriptor_hash"
    })
    provider = DeterministicFakeCandidateProvider()
    executor = _executor(policy, registry, provider=provider)
    with pytest.raises(
        CandidatePortfolioExecutionViolation,
        match="Grounded FailureDescriptor",
    ):
        executor.execute(
            policy=policy,
            case={"case_id": "CASE_LEAK"},
            descriptor=leaky,
            run_seed=1,
        )
    assert provider.calls == []


def test_audit_rejects_selection_and_trace_tampering(
    policy, registry, descriptor
):
    provider = DeterministicFakeCandidateProvider()
    portfolio = _portfolio(registry)
    result = _executor(
        policy,
        registry,
        provider=provider,
        portfolio=portfolio,
    ).execute(
        policy=policy,
        case={"case_id": "CASE_AUDIT"},
        descriptor=descriptor,
        run_seed=3,
    )
    tampered = deepcopy(result)
    tampered["selection_receipt"]["selected_candidate_id"] = None
    with pytest.raises(PortfolioAuditViolation):
        verify_blue_evaluation(
            tampered,
            policy=policy,
            registry=registry,
            portfolio=portfolio,
            provider=provider,
        )
    tampered = deepcopy(result)
    tampered["execution_trace"]["events"][0]["lens_id"] = "temporal_v1"
    tampered_event = tampered["execution_trace"]["events"][0]
    tampered_event["event_hash"] = hash_payload({
        key: value for key, value in tampered_event.items()
        if key != "event_hash"
    })
    tampered["execution_trace"]["trace_hash"] = hash_payload({
        key: value for key, value in tampered["execution_trace"].items()
        if key != "trace_hash"
    })
    tampered["portfolio_execution_hash"] = hash_payload({
        key: value for key, value in tampered.items()
        if key != "portfolio_execution_hash"
    })
    with pytest.raises(
        PortfolioAuditViolation, match="not reconstructable"
    ):
        verify_blue_evaluation(
            tampered,
            policy=policy,
            registry=registry,
            portfolio=portfolio,
            provider=provider,
        )


def test_candidate_execution_is_deterministically_replayable(
    policy, registry, descriptor
):
    portfolio = _portfolio(registry)
    first_provider = DeterministicFakeCandidateProvider()
    second_provider = DeterministicFakeCandidateProvider()
    first = _executor(
        policy,
        registry,
        provider=first_provider,
        portfolio=portfolio,
    ).execute(
        policy=policy,
        case={"case_id": "CASE_REPLAY"},
        descriptor=descriptor,
        run_seed=17,
    )
    second = _executor(
        policy,
        registry,
        provider=second_provider,
        portfolio=portfolio,
    ).execute(
        policy=policy,
        case={"case_id": "CASE_REPLAY"},
        descriptor=descriptor,
        run_seed=17,
    )
    assert first == second


def test_acp0_fake_system_writes_audited_evaluation(tmp_path):
    out = tmp_path / "blue_evaluation_v2.json"
    summary = run_fake_candidate_protocol(
        project_root=ROOT,
        out=out,
        run_seed=101,
    )
    assert summary["schema_version"] == "r3e-blue-evaluation-v2"
    assert summary["candidate_count"] == 3
    assert summary["oracle_ok"] is True
    assert out.is_file()
