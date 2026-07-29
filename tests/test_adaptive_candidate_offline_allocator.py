from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from r3e.arena.fake_adapters import DeterministicEvolutionAdapter
from r3e.arena.manifests import make_manifest
from r3e.arena.portfolio import ArenaCandidatePortfolioAuthority
from r3e.arena.runner import EvolutionRoundRunner
from r3e.blue.portfolio.allocator import (
    OfflineAdaptiveAllocator,
    OfflineAllocatorState,
    OfflineAllocatorViolation,
    build_adaptive_portfolio,
    build_descriptor_cluster,
    build_offline_allocator_state,
    load_offline_allocator_state,
)
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
from r3e.blue.portfolio.lens_registry import load_lens_registry
from r3e.blue.portfolio.schema import (
    CandidatePortfolio,
    build_candidate_portfolio_binding,
)
from r3e.blue.portfolio.semantic_signature import (
    StructuredSemanticSignatureProvider,
)
from r3e.memory.schema import FailureDescriptor
from r3e.policy.schema import PolicyState
from r3e.policy.search import (
    PolicySearchViolation,
    propose_offline_allocator_child,
)
from r3e.protocol.hashing import hash_file, hash_payload
from r3e.red.poison_payload import bind_poison_payload


ROOT = Path(__file__).resolve().parents[1]
H = lambda value: hash_payload({"value": value})


def _registry():
    return load_lens_registry(
        ROOT / "configs/blue/lens_registry_v1.json",
        project_root=ROOT,
    )


def _acp3_portfolio():
    return CandidatePortfolio.from_dict(json.loads((
        ROOT / "configs/blue/semantic_diversity_portfolio_v1.json"
    ).read_text(encoding="utf-8")))


def _adaptive_portfolio():
    return CandidatePortfolio.from_dict(json.loads((
        ROOT / "configs/blue/adaptive_portfolio_v1.json"
    ).read_text(encoding="utf-8")))


def _state():
    return load_offline_allocator_state(
        ROOT / "configs/blue/offline_allocator_state_v1.json"
    )


def _policy(
    portfolio: CandidatePortfolio,
    *,
    policy_id: str,
    created_round: int,
) -> PolicyState:
    base = PolicyState.from_dict(json.loads((
        ROOT / "configs/base_policy/frozen_base_policy_v1.json"
    ).read_text(encoding="utf-8")))
    return base.with_updates(
        policy_id=policy_id,
        schema_version="r3e-policy-v3",
        parent_policy_id=base.policy_id,
        parent_policy_hash=base.policy_hash,
        created_round=created_round,
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


def _descriptor(
    *,
    sequential: bool = True,
    signal: str = "out",
    artifact: str = "a",
) -> dict:
    return FailureDescriptor.create({
        "oracle_stage": "grounded_simulation_and_formal",
        "sequential_context": sequential,
        "temporal_relation": (
            "candidate_lags_golden" if sequential else "same_cycle"
        ),
        "cycle_offset_bucket": 1 if sequential else 0,
        "affected_roles": ["observable_output"],
        "assignment_type": (
            "nonblocking" if sequential else "continuous"
        ),
        "cone_depth_bucket": (
            "depth_2_3" if sequential else "depth_4_plus"
        ),
        "mismatch_pattern": (
            "late_transition"
            if sequential else "wrong_combinational_value"
        ),
        "first_divergence_bucket": (
            "cycle_1" if sequential else "cycle_0"
        ),
        "first_divergence_signal": signal,
        "observable_artifact_hashes": {
            "oracle": H(f"oracle-{artifact}"),
            "formal": H(f"formal-{artifact}"),
        },
    }).to_dict()


def _poison(policy: PolicyState, descriptor: dict) -> dict:
    return bind_poison_payload({
        "poison_id": "P_ACP4_EVIDENCE",
        "case_id": "CASE_ACP4_EVIDENCE",
        "design": "acp4_fixture_design",
        "challenged_policy_hash": policy.policy_hash,
        "family": "hidden_from_blue",
        "effect": "offline_allocator_fixture",
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


def _adaptation_evidence():
    portfolio = _acp3_portfolio()
    policy = _policy(
        portfolio, policy_id="B_ACP3_EVIDENCE", created_round=3
    )
    descriptor = _descriptor()
    config = json.loads((
        ROOT / "configs/evolution/round_acp3_semantic_diversity_v1.json"
    ).read_text(encoding="utf-8"))
    authority = ArenaCandidatePortfolioAuthority(
        project_root=ROOT,
        config=config,
        provider=DeterministicFakeCandidateProvider(
            semantic_groups={0: "shared", 1: "shared", 2: "other"}
        ),
        verifier=DeterministicFakeCandidateVerifier(
            successful_slots={1}
        ),
    )
    challenge = authority.evaluate_challenge(
        policy,
        _poison(policy, descriptor),
        seeds=[1, 2, 3],
    )
    manifest = make_manifest(
        [challenge],
        split="adaptation",
        source_manifest_hash=H("residual"),
        metadata={"source_round_id": "R003"},
    )
    return manifest, policy, descriptor


def test_offline_allocator_milestone_is_frozen():
    milestone = json.loads((
        ROOT / "configs/evolution/offline_adaptive_allocator_v1.json"
    ).read_text(encoding="utf-8"))
    parent = json.loads((
        ROOT / "configs/evolution/semantic_patch_diversity_v1.json"
    ).read_text(encoding="utf-8"))
    successor = json.loads((
        ROOT / "configs/evolution/raam_portfolio_control_v1.json"
    ).read_text(encoding="utf-8"))
    acp6 = json.loads((
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
            assert current in {
                successor["frozen_assets"].get(relative),
                acp6["frozen_assets"].get(relative),
            }


def test_descriptor_cluster_excludes_signal_identity_and_artifact_hashes():
    first = build_descriptor_cluster(
        _descriptor(signal="out_a", artifact="a")
    )
    second = build_descriptor_cluster(
        _descriptor(signal="out_b", artifact="b")
    )
    assert first == second
    assert "first_divergence_signal" not in first["features"]
    assert "observable_artifact_hashes" not in first["features"]


def test_state_is_reconstructable_and_records_adaptation_statistics():
    manifest, policy, _descriptor_value = _adaptation_evidence()
    state = build_offline_allocator_state(
        manifest,
        state_id="ACP4_TEST",
        source_round_id="R003",
        source_effective_policy_hash=policy.effective_policy_hash,
        registry=_registry(),
    )
    assert OfflineAllocatorState.from_dict(state.to_dict()) == state
    assert state.adaptation_manifest_hash == manifest["manifest_hash"]
    cluster = next(iter(state.clusters.values()))
    assert cluster["lens_statistics"]["temporal_v1"][
        "unique_solves"
    ] == 3
    assert cluster["lens_statistics"]["generic_v1"][
        "semantic_duplicates"
    ] == 3
    assert cluster["pair_statistics"]["generic_v1|temporal_v1"][
        "mean_overlap_milli"
    ] == 1000


@pytest.mark.parametrize("split", ["target", "non_target"])
def test_state_builder_rejects_qualification_splits(split):
    manifest, policy, _descriptor_value = _adaptation_evidence()
    forbidden = make_manifest(
        manifest["rows"],
        split=split,
        source_manifest_hash=manifest[
            "source_residual_manifest_hash"
        ],
    )
    with pytest.raises(
        OfflineAllocatorViolation, match="only adaptation"
    ):
        build_offline_allocator_state(
            forbidden,
            state_id="FORBIDDEN",
            source_round_id="R003",
            source_effective_policy_hash=policy.effective_policy_hash,
            registry=_registry(),
        )


def test_state_rejects_manual_statistics_tampering():
    raw = _state().to_dict()
    raw["global_lens_statistics"]["temporal_v1"][
        "oracle_rate_milli"
    ] = 0
    raw["state_hash"] = hash_payload({
        key: value for key, value in raw.items()
        if key != "state_hash"
    })
    with pytest.raises(
        OfflineAllocatorViolation, match="inconsistent"
    ):
        OfflineAllocatorState.from_dict(raw)


def test_allocator_is_deterministic_constrained_and_round_frozen():
    state = _state()
    allocator = OfflineAdaptiveAllocator(state)
    portfolio = _adaptive_portfolio()
    descriptor = _descriptor()
    before = state.to_dict()
    first = allocator.allocate(
        descriptor, portfolio=portfolio, registry=_registry()
    )
    second = allocator.allocate(
        descriptor, portfolio=portfolio, registry=_registry()
    )
    assert first == second
    assert first["allocated_lens_ids"] == [
        "generic_v1",
        "temporal_v1",
        "temporal_v1",
    ]
    assert first["used_global_fallback"] is False
    assert len(first["combination_utilities"]) > 3
    assert first["allocated_lens_ids"].count("generic_v1") >= 1
    assert first["allocated_lens_ids"].count("temporal_v1") <= 2
    assert state.to_dict() == before


def test_unseen_cluster_uses_frozen_global_statistics():
    allocator = OfflineAdaptiveAllocator(_state())
    receipt = allocator.allocate(
        _descriptor(sequential=False),
        portfolio=_adaptive_portfolio(),
        registry=_registry(),
    )
    assert receipt["used_global_fallback"] is True
    assert receipt["adaptation_manifest_hash"] == (
        _state().adaptation_manifest_hash
    )


def test_adaptive_executor_and_offline_audit_reconstruct_allocator():
    portfolio = _adaptive_portfolio()
    policy = _policy(
        portfolio, policy_id="B_ACP4", created_round=4
    )
    registry = _registry()
    allocator = OfflineAdaptiveAllocator(_state())
    provider = DeterministicFakeCandidateProvider()
    verifier = DeterministicFakeCandidateVerifier(
        successful_slots={1}
    )
    executor = CandidatePortfolioExecutor(
        registry=registry,
        portfolio=portfolio,
        provider=provider,
        verifier=verifier,
        semantic_signature_provider=(
            StructuredSemanticSignatureProvider()
        ),
        allocator=allocator,
        project_root=ROOT,
    )
    descriptor = _descriptor()
    result = executor.execute(
        policy=policy,
        case={"case_id": "CASE_ACP4"},
        descriptor=descriptor,
        run_seed=404,
    )
    assert result["allocator_receipt"]["allocated_lens_ids"] == [
        "generic_v1",
        "temporal_v1",
        "temporal_v1",
    ]
    assert [
        row["lens_id"]
        for row in result["candidate_generation_receipts"]
    ] == result["allocator_receipt"]["allocated_lens_ids"]
    assert result["resource_usage"]["provider_calls"] == 3
    assert verify_blue_evaluation(
        result,
        policy=policy,
        registry=registry,
        portfolio=portfolio,
        provider=provider,
        expected_verifier_hash=verifier.verifier_hash,
        allocator=allocator,
        descriptor=descriptor,
    ) == result
    tampered = deepcopy(result)
    tampered["allocator_receipt"]["allocated_lens_ids"] = [
        "generic_v1",
        "control_v1",
        "dataflow_v1",
    ]
    tampered["portfolio_execution_hash"] = hash_payload({
        key: value for key, value in tampered.items()
        if key != "portfolio_execution_hash"
    })
    with pytest.raises(
        PortfolioAuditViolation, match="allocator receipt"
    ):
        verify_blue_evaluation(
            tampered,
            policy=policy,
            registry=registry,
            portfolio=portfolio,
            provider=provider,
            expected_verifier_hash=verifier.verifier_hash,
            allocator=allocator,
            descriptor=descriptor,
        )


def test_policy_search_proposes_exactly_one_allocator_child():
    parent_portfolio = _acp3_portfolio()
    parent = _policy(
        parent_portfolio, policy_id="B_ACP3_FIXTURE", created_round=3
    )
    child = propose_offline_allocator_child(
        parent,
        _adaptive_portfolio(),
        _state(),
        json.loads((
            ROOT / "configs/blue/portfolio_operator_space_v1.json"
        ).read_text(encoding="utf-8")),
        round_id="R004",
        residual_manifest_hash=H("residual"),
    )
    assert child.proposal_operator == (
        "portfolio:descriptor_routed_to_adaptive"
    )
    changed = {
        key for key in (
            "router_hash",
            "allocator_hash",
            "selector_hash",
            "semantic_signature_provider_hash",
        )
        if parent.candidate_portfolio_binding[key]
        != child.candidate_portfolio_binding[key]
    }
    assert changed == {"allocator_hash"}
    stale_parent = _policy(
        parent_portfolio, policy_id="B_ACP3_STALE", created_round=3
    )
    stale_parent = stale_parent.with_updates(configuration={
        **stale_parent.configuration,
        "evidence_k": 6,
    })
    with pytest.raises(
        PolicySearchViolation, match="not bound to its parent policy"
    ):
        propose_offline_allocator_child(
            stale_parent,
            _adaptive_portfolio(),
            _state(),
            json.loads((
                ROOT
                / "configs/blue/portfolio_operator_space_v1.json"
            ).read_text(encoding="utf-8")),
            round_id="R004",
            residual_manifest_hash=H("residual"),
        )


def test_formal_arena_and_round_toolchain_bind_allocator(tmp_path):
    config = json.loads((
        ROOT / "configs/evolution/round_acp4_offline_allocator_v1.json"
    ).read_text(encoding="utf-8"))
    authority = ArenaCandidatePortfolioAuthority(
        project_root=ROOT,
        config=config,
        provider=DeterministicFakeCandidateProvider(),
        verifier=DeterministicFakeCandidateVerifier(
            successful_slots={1}
        ),
    )
    assert authority.allocator is not None
    assert authority.allocator.allocator_hash == _state().state_hash
    config.update({
        "policy_registry": str(tmp_path / "registry.json"),
        "policy_search_space": (
            "configs/base_policy/policy_search_space_v1.json"
        ),
        "rounds_root": str(tmp_path / "rounds"),
        "events_root": str(tmp_path / "events"),
    })
    runner = EvolutionRoundRunner(
        config,
        round_id="R_ACP4",
        adapter=DeterministicEvolutionAdapter(tmp_path / "adapter"),
        project_root=ROOT,
        candidate_provider=DeterministicFakeCandidateProvider(),
        candidate_verifier=DeterministicFakeCandidateVerifier(
            successful_slots={1}
        ),
        semantic_signature_provider=(
            StructuredSemanticSignatureProvider()
        ),
    )
    assert runner.round_toolchain_fingerprint[
        "blue_offline_allocator_hash"
    ] == _state().state_hash


def test_cross_round_state_does_not_update_from_current_challenge():
    state = _state()
    portfolio = _adaptive_portfolio()
    policy = _policy(
        portfolio, policy_id="B_ACP4_ACTIVE", created_round=4
    )
    config = json.loads((
        ROOT / "configs/evolution/round_acp4_offline_allocator_v1.json"
    ).read_text(encoding="utf-8"))
    authority = ArenaCandidatePortfolioAuthority(
        project_root=ROOT,
        config=config,
        provider=DeterministicFakeCandidateProvider(),
        verifier=DeterministicFakeCandidateVerifier(
            successful_slots={2}
        ),
    )
    before = state.to_dict()
    challenge = authority.evaluate_challenge(
        policy,
        _poison(policy, _descriptor()),
        seeds=[41, 42],
    )
    assert challenge["portfolio_statistics"][
        "candidate_success_count"
    ] == 2
    assert load_offline_allocator_state(
        ROOT / "configs/blue/offline_allocator_state_v1.json"
    ).to_dict() == before
