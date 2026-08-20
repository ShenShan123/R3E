from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from r3e.arena.fake_adapters import (
    DeterministicEvolutionAdapter,
    FakeRedAdapter,
)
from r3e.arena.portfolio import ArenaCandidatePortfolioAuthority
from r3e.arena.runner import EvolutionRoundRunner, RoundRunnerViolation
from r3e.blue.portfolio.candidate_executor import (
    CandidatePortfolioExecutor,
)
from r3e.blue.portfolio.fake import (
    DeterministicFakeCandidateProvider,
    DeterministicFakeCandidateVerifier,
)
from r3e.blue.portfolio.schema import (
    CandidatePortfolio,
    build_candidate_portfolio_binding,
)
from r3e.memory.schema import FailureDescriptor
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_file, hash_payload
from r3e.red.feedback_packet import (
    CapabilityPacketViolation,
    build_red_search_context,
    verify_red_search_context,
)
from r3e.red.poison_payload import bind_poison_payload
from r3e.red.portfolio_challenge import (
    PORTFOLIO_RED_OPERATORS,
    PortfolioChallengeViolation,
    build_portfolio_red_authority,
    build_portfolio_coverage_packet,
    make_portfolio_challenge_plan,
    materialize_portfolio_challenge,
    verify_portfolio_challenge_execution,
    verify_portfolio_coverage_packet,
)


ROOT = Path(__file__).resolve().parents[1]
H = lambda value: hash_payload({"value": value})


def _portfolio(path: str = "adaptive_portfolio_v1.json"):
    return CandidatePortfolio.from_dict(json.loads((
        ROOT / "configs/blue" / path
    ).read_text(encoding="utf-8")))


def _policy(
    portfolio: CandidatePortfolio | None = None,
    *,
    policy_id: str = "B_ACP6",
) -> PolicyState:
    portfolio = portfolio or _portfolio()
    base = PolicyState.from_dict(json.loads((
        ROOT / "configs/base_policy/frozen_base_policy_v1.json"
    ).read_text(encoding="utf-8")))
    return base.with_updates(
        policy_id=policy_id,
        schema_version="r3e-policy-v3",
        parent_policy_id=base.policy_id,
        parent_policy_hash=base.policy_hash,
        created_round=6,
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


def _poison(policy: PolicyState, descriptor: dict) -> dict:
    return bind_poison_payload({
        "poison_id": "P_ACP6_SOURCE",
        "case_id": "CASE_ACP6_SOURCE",
        "design": "acp6_fixture",
        "challenged_policy_hash": policy.policy_hash,
        "family": "hidden_from_blue",
        "effect": "portfolio_fixture",
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


def _challenge(
    policy: PolicyState,
    *,
    successful_slots: set[int] = frozenset(),
) -> dict:
    config = json.loads((
        ROOT / "configs/evolution/round_acp4_offline_allocator_v1.json"
    ).read_text(encoding="utf-8"))
    authority = ArenaCandidatePortfolioAuthority(
        project_root=ROOT,
        config=config,
        provider=DeterministicFakeCandidateProvider(),
        verifier=DeterministicFakeCandidateVerifier(
            successful_slots=successful_slots
        ),
    )
    return authority.evaluate_challenge(
        policy,
        _poison(policy, _descriptor()),
        seeds=[1, 2, 3],
    )


def _memory_binding() -> dict[str, str]:
    return {
        "active_memory_bank_hash": H("bank"),
        "effective_memory_bank_hash": H("effective-bank"),
        "retriever_hash": H("retriever"),
        "activation_guard_hash": H("guard"),
        "memory_control_whitelist_hash": H("whitelist"),
    }


def _keys(value):
    if isinstance(value, dict):
        result = set(value)
        for item in value.values():
            result.update(_keys(item))
        return result
    if isinstance(value, list):
        result = set()
        for item in value:
            result.update(_keys(item))
        return result
    return set()


def test_portfolio_aware_red_challenge_milestone_is_frozen():
    milestone = json.loads((
        ROOT
        / "configs/evolution/portfolio_aware_red_challenge_v1.json"
    ).read_text(encoding="utf-8"))
    parent = json.loads((
        ROOT / "configs/evolution/raam_portfolio_control_v1.json"
    ).read_text(encoding="utf-8"))
    successor = json.loads((
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
    for relative, expected in milestone["frozen_assets"].items():
        current = hash_file(ROOT / relative)
        if current != expected:
            assert current in {
                successor["frozen_assets"].get(relative),
                runtime_successor["frozen_assets"].get(relative),
                population_successor["frozen_assets"].get(relative),
                pilot_successor["frozen_assets"].get(relative),
            }
    assert milestone["claim_boundary"].startswith(
        "Frozen deterministic ACP-6"
    )
    assert {
        "real-red-model-effectiveness",
        "real-model-multi-round-coevolution",
    } <= set(milestone["deferred_scope"])


def test_portfolio_coverage_packet_is_sanitized_and_reconstructable():
    policy = _policy()
    challenge = _challenge(policy)
    packet = build_portfolio_coverage_packet(
        policy, archive_rows=[challenge]
    )
    assert verify_portfolio_coverage_packet(
        packet, policy=policy
    ) == packet
    assert packet["coverage_summary"] == {
        "region_count": 1,
        "blind_spot_count": 1,
        "collapse_region_count": 0,
        "high_cost_low_gain_count": 1,
    }
    region = packet["coverage_regions"][0]
    assert region["portfolio_attempts"] == 3
    assert region["portfolio_successes"] == 0
    assert region["provider_calls"] == 9
    assert region["high_cost_low_gain"] is True
    assert not _keys(packet) & {
        "prompt",
        "patch_payload",
        "blue_results",
        "candidate_provider_receipts",
        "candidate_verification_receipts",
        "successful_lens_ids",
        "control_delta",
        "reference_repair",
    }


def test_coverage_rejects_tamper_and_drops_incompatible_portfolio():
    policy = _policy()
    challenge = _challenge(policy)
    tampered = deepcopy(challenge)
    tampered["portfolio_statistics"]["portfolio_successes"] = 3
    with pytest.raises(
        PortfolioChallengeViolation, match="not reconstructable"
    ):
        build_portfolio_coverage_packet(
            policy, archive_rows=[tampered]
        )
    changed = _policy(
        _portfolio("fixed_mixed_portfolio_v1.json"),
        policy_id="B_ACP6_CHANGED",
    )
    packet = build_portfolio_coverage_packet(
        changed, archive_rows=[challenge]
    )
    assert packet["coverage_regions"] == []


def test_cross_round_reuses_same_portfolio_under_new_memory_policy():
    first = _policy()
    challenge = _challenge(first)
    second = first.with_updates(
        policy_id="B_ACP6_MEMORY_CHILD",
        parent_policy_id=first.policy_id,
        parent_policy_hash=first.policy_hash,
        created_round=7,
        memory_binding=_memory_binding(),
    )
    packet = build_portfolio_coverage_packet(
        second, archive_rows=[challenge]
    )
    assert packet["challenged_policy_hash"] == second.policy_hash
    assert packet["coverage_regions"][0][
        "source_challenged_policy_hashes"
    ] == [first.policy_hash]


@pytest.mark.parametrize("operator", sorted(PORTFOLIO_RED_OPERATORS))
def test_four_portfolio_operators_materialize_and_verify(operator):
    policy = _policy()
    packet = build_portfolio_coverage_packet(
        policy, archive_rows=[_challenge(policy)]
    )
    target = packet["coverage_regions"][0]["region_hash"]
    plan = make_portfolio_challenge_plan(
        policy=policy,
        packet=packet,
        operator=operator,
        poison_id=f"P_{operator}",
        target_region_hashes=[target],
    )
    poison = materialize_portfolio_challenge(
        plan,
        {
            "poison_id": f"P_{operator}",
            "challenged_policy_hash": policy.policy_hash,
            "dependency_depth": 0,
            "sequential_depth": 0,
            "composition_depth": 1,
        },
    )
    assert verify_portfolio_challenge_execution(
        poison,
        plan=plan,
        packet=packet,
        policy=policy,
    ) == poison


def test_portfolio_plan_rejects_unknown_region_and_semantic_tamper():
    policy = _policy()
    packet = build_portfolio_coverage_packet(
        policy, archive_rows=[_challenge(policy)]
    )
    with pytest.raises(
        PortfolioChallengeViolation, match="unknown region"
    ):
        make_portfolio_challenge_plan(
            policy=policy,
            packet=packet,
            operator="portfolio_bypass",
            poison_id="P_BAD_REGION",
            target_region_hashes=[H("unknown")],
        )
    target = packet["coverage_regions"][0]["region_hash"]
    plan = make_portfolio_challenge_plan(
        policy=policy,
        packet=packet,
        operator="router_ambiguity",
        poison_id="P_TAMPER",
        target_region_hashes=[target],
    )
    poison = materialize_portfolio_challenge(
        plan,
        {
            "poison_id": "P_TAMPER",
            "challenged_policy_hash": policy.policy_hash,
        },
    )
    poison["portfolio_stress"]["symptom_classes"] = ["dataflow"]
    with pytest.raises(
        PortfolioChallengeViolation, match="ambiguity"
    ):
        verify_portfolio_challenge_execution(
            poison,
            plan=plan,
            packet=packet,
            policy=policy,
        )


def test_red_search_context_supports_portfolio_only_and_memory_plus_portfolio():
    policy = _policy()
    packet = build_portfolio_coverage_packet(
        policy, archive_rows=[_challenge(policy)]
    )
    context = build_red_search_context(
        policy,
        residual_archive=[],
        covered_archive=[],
        portfolio_capability=packet,
    )
    assert context["schema_version"] == "r3e-red-search-context-v4"
    assert verify_red_search_context(context) == context
    combined = build_red_search_context(
        policy,
        residual_archive=[],
        covered_archive=[],
        memory_capability={
            "challenged_policy_hash": policy.policy_hash,
            "packet_hash": H("memory-packet"),
        },
        portfolio_capability=packet,
    )
    assert combined["schema_version"] == "r3e-red-search-context-v5"
    assert verify_red_search_context(combined) == combined
    leaked = deepcopy(packet)
    leaked["private_raam_evidence"] = {"episode": "hidden"}
    leaked["packet_hash"] = hash_payload({
        key: value for key, value in leaked.items()
        if key != "packet_hash"
    })
    with pytest.raises(
        CapabilityPacketViolation, match="portfolio capability is invalid"
    ):
        build_red_search_context(
            policy,
            residual_archive=[],
            covered_archive=[],
            portfolio_capability=leaked,
        )


def test_fake_red_and_formal_runner_enforce_all_four_plans(tmp_path):
    policy = _policy()
    challenge = _challenge(policy)
    config = json.loads((
        ROOT / "configs/evolution/round_acp6_portfolio_red_v1.json"
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
        round_id="R_ACP6",
        adapter=DeterministicEvolutionAdapter(tmp_path / "adapter"),
        project_root=ROOT,
        candidate_provider=DeterministicFakeCandidateProvider(),
        candidate_verifier=DeterministicFakeCandidateVerifier(
            successful_slots={1}
        ),
    )
    packet = runner._build_portfolio_red_capability(
        policy,
        residual_archive=[challenge],
        covered_archive=[],
    )
    context = build_red_search_context(
        policy,
        residual_archive=[],
        covered_archive=[],
        portfolio_capability=packet,
    )
    rows = list(
        FakeRedAdapter(tmp_path / "red").generate_red(
            policy, config, context
        )
    )
    assert {
        row["portfolio_challenge_operator"] for row in rows
    } == PORTFOLIO_RED_OPERATORS
    for row in rows:
        runner._verify_portfolio_red_candidate(
            row,
            parent=policy,
            portfolio_capability=packet,
        )
    authority = build_portfolio_red_authority(
        policy=policy,
        packet=packet,
        poisons=rows,
        toolchain_fingerprint=runner.round_toolchain_fingerprint,
    )
    assert authority["candidate_count"] == 4
    assert authority["operator_counts"] == {
        operator: 1 for operator in sorted(PORTFOLIO_RED_OPERATORS)
    }
    assert runner.round_toolchain_fingerprint[
        "portfolio_red_authority_hash"
    ] == hash_payload({
        "schema_version": "r3e-red-portfolio-coverage-v1",
        "operators": sorted(PORTFOLIO_RED_OPERATORS),
    })
    missing = deepcopy(rows[0])
    missing.pop("portfolio_challenge_plan")
    with pytest.raises(RoundRunnerViolation):
        runner._verify_portfolio_red_candidate(
            missing,
            parent=policy,
            portfolio_capability=packet,
        )
    wrong_toolchain = deepcopy(runner.round_toolchain_fingerprint)
    wrong_toolchain["portfolio_red_authority_hash"] = H("tampered")
    with pytest.raises(
        PortfolioChallengeViolation, match="toolchain"
    ):
        build_portfolio_red_authority(
            policy=policy,
            packet=packet,
            poisons=rows,
            toolchain_fingerprint=wrong_toolchain,
        )
