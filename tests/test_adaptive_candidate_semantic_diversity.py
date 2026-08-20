from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from r3e.arena.portfolio import (
    ArenaCandidatePortfolioAuthority,
    ArenaPortfolioViolation,
)
from r3e.arena.fake_adapters import DeterministicEvolutionAdapter
from r3e.arena.runner import EvolutionRoundRunner
from r3e.red.poison_payload import bind_poison_payload
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
from r3e.blue.portfolio.lens_registry import load_lens_registry
from r3e.blue.portfolio.router import load_descriptor_router
from r3e.blue.portfolio.schema import (
    CandidatePortfolio,
    build_candidate_portfolio_binding,
)
from r3e.blue.portfolio.semantic_signature import (
    STRUCTURED_SIGNATURE_PROVIDER_HASH,
    SemanticPatchSignature,
    SemanticSignatureViolation,
    StructuredSemanticSignatureProvider,
    verify_semantic_signature_receipt,
)
from r3e.blue.portfolio.statistics import (
    build_portfolio_diversity_receipt,
    build_semantic_relation,
)
from r3e.memory.schema import FailureDescriptor
from r3e.policy.schema import PolicyState
from r3e.policy.search import portfolio_conditioned_neighbors
from r3e.protocol.hashing import hash_payload
from r3e.protocol.hashing import hash_file


ROOT = Path(__file__).resolve().parents[1]
H = lambda value: hash_payload({"value": value})


def test_semantic_patch_diversity_milestone_is_frozen():
    milestone = json.loads((
        ROOT / "configs/evolution/semantic_patch_diversity_v1.json"
    ).read_text(encoding="utf-8"))
    parent = json.loads((
        ROOT / "configs/evolution/descriptor_routed_portfolio_v1.json"
    ).read_text(encoding="utf-8"))
    successor = json.loads((
        ROOT / "configs/evolution/offline_adaptive_allocator_v1.json"
    ).read_text(encoding="utf-8"))
    acp5 = json.loads((
        ROOT / "configs/evolution/raam_portfolio_control_v1.json"
    ).read_text(encoding="utf-8"))
    acp6 = json.loads((
        ROOT
        / "configs/evolution/portfolio_aware_red_challenge_v1.json"
    ).read_text(encoding="utf-8"))
    proposal_successor = json.loads((
        ROOT
        / "configs/evolution/grounded_proposal_authority_v1.json"
    ).read_text(encoding="utf-8"))
    runtime_successor = json.loads((
        ROOT
        / "configs/evolution/grounded_sequential_runtime_gate_v1.json"
    ).read_text(encoding="utf-8"))
    population_successor = json.loads((
        ROOT
        / "configs/evolution/grounded_deterministic_population_v1.json"
    ).read_text(encoding="utf-8"))
    pilot_successor = json.loads((
        ROOT / "configs/evolution/real_adapter_pilot_entry_v1.json"
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
    assert acp5["parent_milestone"] == {
        "milestone_id": successor["milestone_id"],
        "milestone_hash": successor["milestone_hash"],
    }
    for relative, expected in milestone["frozen_assets"].items():
        current = hash_file(ROOT / relative)
        if current != expected:
            assert current in {
                successor["frozen_assets"].get(relative),
                acp5["frozen_assets"].get(relative),
                    acp6["frozen_assets"].get(relative),
                    proposal_successor["frozen_assets"].get(relative),
                    runtime_successor["frozen_assets"].get(relative),
                    population_successor["frozen_assets"].get(relative),
                    pilot_successor["frozen_assets"].get(relative),
                }


def _portfolio() -> CandidatePortfolio:
    return CandidatePortfolio.from_dict(json.loads((
        ROOT / "configs/blue/semantic_diversity_portfolio_v1.json"
    ).read_text(encoding="utf-8")))


def _policy(portfolio: CandidatePortfolio) -> PolicyState:
    base = PolicyState.from_dict(json.loads((
        ROOT / "configs/base_policy/frozen_base_policy_v1.json"
    ).read_text(encoding="utf-8")))
    return base.with_updates(
        policy_id="B_ACP3",
        schema_version="r3e-policy-v3",
        parent_policy_id=base.policy_id,
        parent_policy_hash=base.policy_hash,
        created_round=3,
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


def _descriptor() -> dict:
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


def _proposal(group: str, *, shared: bool = False) -> dict:
    prefix = "shared" if shared else group
    return {
        "changed_modules": [f"module:{prefix}"],
        "changed_blocks": [f"block:{prefix}"],
        "changed_ast_nodes": [f"node:{prefix}"],
        "changed_signal_roles": [f"role:{prefix}"],
        "operator_classes": [f"operator:{group}"],
        "patch_scope": "local_block",
        "normalized_ast_patch": {"group": group},
    }


def _signature_receipt(
    candidate_id: str,
    lens_id: str,
    proposal: dict,
) -> dict:
    provider = StructuredSemanticSignatureProvider()
    patch_payload = {"semantic_patch": proposal}
    return provider.materialize(
        candidate_id=candidate_id,
        lens_id=lens_id,
        patch_hash=hash_payload(patch_payload),
        patch_payload=patch_payload,
        expected_patch_scope="local_block",
    )


def _executor(
    *,
    semantic_groups: dict[int, str] | None = None,
    successful_slots: set[int] | None = None,
):
    portfolio = _portfolio()
    registry = load_lens_registry(
        ROOT / "configs/blue/lens_registry_v1.json",
        project_root=ROOT,
    )
    router = load_descriptor_router(
        ROOT / "configs/blue/descriptor_router_v1.json"
    )
    provider = DeterministicFakeCandidateProvider(
        semantic_groups=semantic_groups
    )
    verifier = DeterministicFakeCandidateVerifier(
        successful_slots=successful_slots or {1}
    )
    executor = CandidatePortfolioExecutor(
        registry=registry,
        portfolio=portfolio,
        provider=provider,
        verifier=verifier,
        semantic_signature_provider=(
            StructuredSemanticSignatureProvider()
        ),
        project_root=ROOT,
        router=router,
    )
    return (
        executor,
        _policy(portfolio),
        registry,
        router,
        portfolio,
        provider,
        verifier,
    )


def test_semantic_signature_is_strict_and_hash_reconstructable():
    signature = SemanticPatchSignature.create(**_proposal("a"))
    assert SemanticPatchSignature.from_dict(
        signature.to_dict()
    ) == signature
    tampered = signature.to_dict()
    tampered["changed_ast_nodes"] = ["node:tampered"]
    with pytest.raises(
        SemanticSignatureViolation, match="signature hash mismatch"
    ):
        SemanticPatchSignature.from_dict(tampered)


def test_signature_provider_rejects_scope_escape_and_unknown_fields():
    provider = StructuredSemanticSignatureProvider()
    proposal = _proposal("a")
    proposal["patch_scope"] = "whole_module"
    with pytest.raises(SemanticSignatureViolation, match="scope exceeds"):
        provider.materialize(
            candidate_id="C0",
            lens_id="generic_v1",
            patch_hash=H("patch"),
            patch_payload={"semantic_patch": proposal},
            expected_patch_scope="local_block",
        )
    proposal = _proposal("a")
    proposal["model_claimed_signature_hash"] = H("untrusted")
    with pytest.raises(SemanticSignatureViolation, match="fields mismatch"):
        provider.materialize(
            candidate_id="C0",
            lens_id="generic_v1",
            patch_hash=H("patch"),
            patch_payload={"semantic_patch": proposal},
            expected_patch_scope="local_block",
        )


def test_semantic_relation_classifies_duplicate_near_and_orthogonal():
    duplicate_a = _signature_receipt("C0", "generic_v1", _proposal("a"))
    duplicate_b = _signature_receipt("C1", "temporal_v1", _proposal("a"))
    near = _signature_receipt(
        "C2", "control_v1", _proposal("b", shared=True)
    )
    near_base = _signature_receipt(
        "C3", "dataflow_v1", _proposal("a", shared=True)
    )
    orthogonal = _signature_receipt(
        "C4", "dataflow_v1", _proposal("orthogonal")
    )
    assert build_semantic_relation(
        duplicate_a, duplicate_b
    )["relation"] == "semantic_duplicate"
    assert build_semantic_relation(
        near_base, near
    )["relation"] == "near_duplicate"
    assert build_semantic_relation(
        duplicate_a, orthogonal
    )["relation"] == "orthogonal"


def test_diversity_receipt_tracks_collapse_unique_and_co_solve():
    signatures = [
        _signature_receipt("C0", "generic_v1", _proposal("a")),
        _signature_receipt("C1", "temporal_v1", _proposal("a")),
        _signature_receipt("C2", "dataflow_v1", _proposal("c")),
    ]
    unique = build_portfolio_diversity_receipt(
        signatures,
        [
            {"candidate_id": "C0", "oracle_ok": False},
            {"candidate_id": "C1", "oracle_ok": True},
            {"candidate_id": "C2", "oracle_ok": False},
        ],
        provider_calls=3,
        verifier_calls=3,
        total_tokens=30,
    )
    assert unique["lens_collapse"] is True
    assert unique["semantic_duplicate_pairs"] == 1
    assert unique["unique_solve_lens_ids"] == ["temporal_v1"]
    assert unique["co_solve_lens_ids"] == []
    co_solve = build_portfolio_diversity_receipt(
        signatures,
        [
            {"candidate_id": "C0", "oracle_ok": True},
            {"candidate_id": "C1", "oracle_ok": True},
            {"candidate_id": "C2", "oracle_ok": False},
        ],
        provider_calls=3,
        verifier_calls=3,
        total_tokens=30,
    )
    assert co_solve["unique_solve_lens_ids"] == []
    assert co_solve["co_solve_lens_ids"] == [
        "generic_v1",
        "temporal_v1",
    ]


def test_executor_measures_duplicates_without_retry_or_skipped_verification():
    (
        executor,
        policy,
        registry,
        router,
        portfolio,
        provider,
        verifier,
    ) = _executor(
        semantic_groups={0: "collapse", 1: "collapse", 2: "other"},
        successful_slots={1, 2},
    )
    result = executor.execute(
        policy=policy,
        case={"case_id": "CASE_ACP3"},
        descriptor=_descriptor(),
        run_seed=303,
    )
    diversity = result["portfolio_diversity_receipt"]
    assert len(provider.calls) == 3
    assert len(verifier.calls) == 3
    assert diversity["measurement_only"] is True
    assert diversity["retry_calls"] == 0
    assert diversity["semantic_duplicate_pairs"] == 1
    assert diversity["lens_collapse"] is True
    assert diversity["candidate_success_count"] == 2
    assert len(result["candidate_semantic_signature_receipts"]) == 3
    assert [
        event["event_type"]
        for event in result["execution_trace"]["events"]
    ].count("portfolio_diversity") == 1
    assert verify_blue_evaluation(
        result,
        policy=policy,
        registry=registry,
        portfolio=portfolio,
        provider=provider,
        expected_verifier_hash=verifier.verifier_hash,
        expected_semantic_signature_provider_hash=(
            STRUCTURED_SIGNATURE_PROVIDER_HASH
        ),
        router=router,
        descriptor=_descriptor(),
    ) == result


def test_verifier_cannot_self_report_semantic_authority():
    class SelfReportingVerifier(DeterministicFakeCandidateVerifier):
        def __call__(self, **kwargs):
            result = super().__call__(**kwargs)
            result["semantic_patch_signature_hash"] = H("forged")
            return result

    executor, policy, *_rest = _executor()
    executor.verifier = SelfReportingVerifier(successful_slots={1})
    with pytest.raises(
        CandidatePortfolioExecutionViolation,
        match="semantic signature|verifier",
    ):
        executor.execute(
            policy=policy,
            case={"case_id": "CASE_FORGED"},
            descriptor=_descriptor(),
            run_seed=304,
        )


def test_audit_rejects_diversity_and_signature_tampering():
    (
        executor,
        policy,
        registry,
        router,
        portfolio,
        provider,
        verifier,
    ) = _executor()
    result = executor.execute(
        policy=policy,
        case={"case_id": "CASE_AUDIT"},
        descriptor=_descriptor(),
        run_seed=305,
    )
    tampered = deepcopy(result)
    tampered["portfolio_diversity_receipt"][
        "semantic_diversity_milli"
    ] = 0
    tampered["portfolio_execution_hash"] = hash_payload({
        key: value for key, value in tampered.items()
        if key != "portfolio_execution_hash"
    })
    with pytest.raises(
        PortfolioAuditViolation, match="diversity receipt"
    ):
        verify_blue_evaluation(
            tampered,
            policy=policy,
            registry=registry,
            portfolio=portfolio,
            provider=provider,
            expected_verifier_hash=verifier.verifier_hash,
            router=router,
            descriptor=_descriptor(),
        )
    receipt = deepcopy(result["candidate_semantic_signature_receipts"][0])
    receipt["signature"]["changed_blocks"] = ["forged"]
    with pytest.raises(SemanticSignatureViolation):
        verify_semantic_signature_receipt(
            receipt,
            expected_provider_hash=STRUCTURED_SIGNATURE_PROVIDER_HASH,
        )


def test_arena_binds_semantic_signature_provider():
    config = json.loads((
        ROOT / "configs/evolution/round_acp3_semantic_diversity_v1.json"
    ).read_text(encoding="utf-8"))
    authority = ArenaCandidatePortfolioAuthority(
        project_root=ROOT,
        config=config,
        provider=DeterministicFakeCandidateProvider(),
        verifier=DeterministicFakeCandidateVerifier(
            successful_slots={1}
        ),
        semantic_signature_provider=(
            StructuredSemanticSignatureProvider()
        ),
    )
    assert (
        authority.semantic_signature_provider_hash
        == STRUCTURED_SIGNATURE_PROVIDER_HASH
    )
    policy = _policy(_portfolio())
    descriptor = _descriptor()
    poison = bind_poison_payload({
        "poison_id": "P_ACP3",
        "case_id": "CASE_ACP3_ARENA",
        "challenged_policy_hash": policy.policy_hash,
        "family": "hidden_from_blue",
        "effect": "semantic_diversity",
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
    challenge = authority.evaluate_challenge(
        policy, poison, seeds=[1, 2]
    )
    assert challenge["portfolio_statistics"][
        "candidate_success_count"
    ] == 2
    assert challenge["portfolio_statistics"]["portfolio_cost"][
        "provider_calls"
    ] == 6
    bad = deepcopy(config)
    bad["blue_semantic_signature_provider_hash"] = H("wrong")
    with pytest.raises(
        ArenaPortfolioViolation, match="semantic signature provider"
    ):
        ArenaCandidatePortfolioAuthority(
            project_root=ROOT,
            config=bad,
            provider=DeterministicFakeCandidateProvider(),
            verifier=DeterministicFakeCandidateVerifier(
                successful_slots={1}
            ),
            semantic_signature_provider=(
                StructuredSemanticSignatureProvider()
            ),
        )


def test_policy_child_enables_only_diversity_measurement_component():
    acp2 = CandidatePortfolio.from_dict(json.loads((
        ROOT / "configs/blue/descriptor_routed_portfolio_v1.json"
    ).read_text(encoding="utf-8")))
    parent = _policy(acp2)
    operator_space = json.loads((
        ROOT / "configs/blue/portfolio_operator_space_v1.json"
    ).read_text(encoding="utf-8"))
    child = portfolio_conditioned_neighbors(
        parent,
        [{
            "operator": "enable_diversity_measurement",
            "portfolio": _portfolio(),
        }],
        operator_space,
        round_id="R003",
        residual_manifest_hash=H("residual"),
    )[0]
    assert child.proposal_operator == (
        "portfolio:enable_diversity_measurement"
    )
    parent_binding = parent.candidate_portfolio_binding
    child_binding = child.candidate_portfolio_binding
    changed = {
        key for key in (
            "router_hash",
            "allocator_hash",
            "selector_hash",
            "semantic_signature_provider_hash",
        )
        if parent_binding[key] != child_binding[key]
    }
    assert changed == {"semantic_signature_provider_hash"}


def test_round_toolchain_binds_semantic_provider(tmp_path):
    config = json.loads((
        ROOT / "configs/evolution/round_acp3_semantic_diversity_v1.json"
    ).read_text(encoding="utf-8"))
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
        round_id="R_ACP3",
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
        "blue_semantic_signature_provider_hash"
    ] == STRUCTURED_SIGNATURE_PROVIDER_HASH
