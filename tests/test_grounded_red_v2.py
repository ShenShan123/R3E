from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_file, hash_payload, read_json
from r3e.red.grounded.admission import (
    GroundedAdmissionViolation,
    decide_grounded_admission,
    verify_grounded_admission_decision,
)
from r3e.red.grounded.archives import (
    GroundedArchiveViolation,
    GroundedRedArchive,
)
from r3e.red.grounded.capability_packet import (
    GroundedCapabilityViolation,
    build_grounded_red_capability_packet,
    verify_grounded_red_capability_packet,
)
from r3e.red.grounded.coverage import (
    freeze_coverage_state,
    select_coverage_targets,
)
from r3e.red.grounded.difficulty import build_difficulty_profile
from r3e.red.grounded.fake import build_deterministic_evidence
from r3e.red.grounded.fake_system import run_fake_grounded_red
from r3e.red.grounded.lineage import (
    GroundedLineageViolation,
    build_lineage,
    validate_lineage_graph,
)
from r3e.red.grounded.mutation_plan import (
    MutationPlanViolation,
    build_mutation_plan,
    verify_mutation_plan,
)
from r3e.red.grounded.registry import (
    GroundedRegistryViolation,
    load_grounded_registries,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def registries():
    return load_grounded_registries(
        family_registry=ROOT / "configs/red/grounded_family_registry_v1.json",
        operator_registry=ROOT / "configs/red/grounded_operator_registry_v1.json",
        effect_registry=ROOT / "configs/red/grounded_effect_registry_v1.json",
    )


@pytest.fixture
def policy():
    return PolicyState.from_dict(
        read_json(ROOT / "configs/base_policy/frozen_base_policy_v1.json")
    )


@pytest.fixture
def plan(policy, registries):
    return build_mutation_plan(
        plan_id="MP_R001_001",
        policy=policy,
        registries=registries,
        target_design="counter",
        target_module="counter_top",
        target_ast_node_hash=hash_payload({"node": "counter.compare"}),
        family_id="combinational.comparator_boundary",
        operator_id="replace_comparator",
        expected_runtime_effect_id="wrong_combinational_value",
        preconditions={"comparison_expression": True},
        scope_limits={
            "maximum_changed_modules": 1,
            "maximum_changed_blocks": 1,
            "maximum_ast_edits": 1,
        },
        difficulty_target={
            "difficulty_band": "D0",
            "dependency_depth_delta": 0,
            "temporal_depth_delta": 0,
        },
    )


def _rehash_receipt(receipt):
    receipt["receipt_hash"] = hash_payload({
        key: value for key, value in receipt.items() if key != "receipt_hash"
    })


def _rehash_evidence(evidence):
    evidence["evidence_hash"] = hash_payload({
        key: value for key, value in evidence.items()
        if key != "evidence_hash"
    })


def _decision(plan, policy, registries, evidence=None):
    evidence = evidence or build_deterministic_evidence(plan)
    return decide_grounded_admission(
        plan=plan,
        policy=policy,
        registries=registries,
        evidence=evidence,
    )


def test_grounded_registries_separate_family_operator_and_effect(registries):
    assert len(registries.families) == 25
    assert len(registries.operators) == 10
    assert len(registries.effects) == 10
    assert (
        "combinational.comparator_boundary"
        in registries.operators["replace_comparator"]["supported_family_ids"]
    )
    assert registries.registry_bundle_hash.startswith("sha256:")


def test_registry_rejects_unknown_family_reference(tmp_path):
    family_path = ROOT / "configs/red/grounded_family_registry_v1.json"
    operator = read_json(
        ROOT / "configs/red/grounded_operator_registry_v1.json"
    )
    operator["operators"][0]["supported_family_ids"] = ["unknown.family"]
    operator_path = tmp_path / "operators.json"
    operator_path.write_text(
        __import__("json").dumps(operator), encoding="utf-8"
    )
    with pytest.raises(GroundedRegistryViolation, match="unknown families"):
        load_grounded_registries(
            family_registry=family_path,
            operator_registry=operator_path,
            effect_registry=ROOT
            / "configs/red/grounded_effect_registry_v1.json",
        )


def test_mutation_plan_rejects_stale_effective_policy(
    plan, policy, registries
):
    configuration = {
        **policy.configuration,
        "evidence_k": 3,
    }
    changed = policy.with_updates(
        configuration=configuration,
        base_policy_hash=hash_payload({
            "schema_version": policy.schema_version,
            "configuration": configuration,
            "budgets": policy.budgets,
            "frozen_assets": policy.frozen_assets,
        }),
    )
    with pytest.raises(MutationPlanViolation, match="stale policy"):
        verify_mutation_plan(plan, policy=changed, registries=registries)


def test_operator_precondition_and_scope_are_hard_limits(
    policy, registries
):
    kwargs = {
        "plan_id": "MP_BAD",
        "policy": policy,
        "registries": registries,
        "target_design": "counter",
        "target_module": "top",
        "target_ast_node_hash": hash_payload({"node": "cmp"}),
        "family_id": "combinational.comparator_boundary",
        "operator_id": "replace_comparator",
        "expected_runtime_effect_id": "wrong_combinational_value",
        "preconditions": {"comparison_expression": False},
        "scope_limits": {
            "maximum_changed_modules": 1,
            "maximum_changed_blocks": 1,
            "maximum_ast_edits": 1,
        },
        "difficulty_target": {
            "difficulty_band": "D0",
            "dependency_depth_delta": 0,
            "temporal_depth_delta": 0,
        },
    }
    with pytest.raises(MutationPlanViolation, match="precondition"):
        build_mutation_plan(**kwargs)
    kwargs["preconditions"]["comparison_expression"] = True
    kwargs["scope_limits"]["maximum_ast_edits"] = 2
    with pytest.raises(MutationPlanViolation, match="exceeds"):
        build_mutation_plan(**kwargs)


def test_valid_grounded_admission_reconstructs_exact_decision(
    plan, policy, registries
):
    evidence = build_deterministic_evidence(plan)
    decision = _decision(plan, policy, registries, evidence)
    assert decision["admitted"]
    assert set(decision["checks"]) == {
        f"G{number}_{name}"
        for number, name in (
            (1, "source_integrity"),
            (2, "parser_elaboration"),
            (3, "clean_baseline"),
            (4, "poison_executability"),
            (5, "functional_failure"),
            (6, "determinism"),
            (7, "revert_proof"),
            (8, "semantic_family_proof"),
            (9, "runtime_effect_proof"),
            (10, "non_triviality"),
            (11, "minimization"),
        )
    }
    assert verify_grounded_admission_decision(
        decision,
        plan=plan,
        policy=policy,
        registries=registries,
        evidence=evidence,
    ) == decision


def test_forged_evidence_hash_is_rejected(plan, policy, registries):
    evidence = build_deterministic_evidence(plan)
    evidence["evidence_hash"] = hash_payload({"forged": True})
    with pytest.raises(GroundedAdmissionViolation, match="evidence hash"):
        _decision(plan, policy, registries, evidence)


@pytest.mark.parametrize(
    ("mutation", "failed_gate"),
    [
        ("clean_fails", "G3_clean_baseline"),
        ("timeout", "G4_poison_executability"),
        ("crash", "G4_poison_executability"),
        ("no_functional_failure", "G5_functional_failure"),
        ("nondeterministic", "G6_determinism"),
        ("revert_fails", "G7_revert_proof"),
        ("family_mismatch", "G8_semantic_family_proof"),
        ("runtime_effect_mismatch", "G9_runtime_effect_proof"),
        ("trivial", "G10_non_triviality"),
        ("minimization_fails", "G11_minimization"),
    ],
)
def test_grounded_admission_gates_fail_closed(
    plan, policy, registries, mutation, failed_gate
):
    evidence = build_deterministic_evidence(plan)
    if mutation == "clean_fails":
        evidence["clean_run"]["observed"]["oracle_pass"] = False
        _rehash_receipt(evidence["clean_run"])
    elif mutation in {"timeout", "crash"}:
        run = evidence["poison_runs"][0]
        run["result_kind"] = mutation
        run["timed_out"] = mutation == "timeout"
        run["crashed"] = mutation == "crash"
        run["exit_code"] = 124 if mutation == "timeout" else 139
        _rehash_receipt(run)
    elif mutation == "no_functional_failure":
        for run in evidence["poison_runs"]:
            run["observed"]["oracle_pass"] = True
            run["observed"]["functional_mismatch"] = False
            _rehash_receipt(run)
    elif mutation == "nondeterministic":
        evidence["poison_runs"][1]["observed"][
            "effect_signature"
        ] = "different-effect"
        _rehash_receipt(evidence["poison_runs"][1])
    elif mutation == "revert_fails":
        evidence["revert_run"]["observed"]["oracle_pass"] = False
        _rehash_receipt(evidence["revert_run"])
    elif mutation == "family_mismatch":
        proof = evidence["semantic_diff"]
        proof["family_id"] = "combinational.predicate_polarity"
        proof["receipt_hash"] = hash_payload({
            key: value for key, value in proof.items()
            if key != "receipt_hash"
        })
    elif mutation == "runtime_effect_mismatch":
        proof = evidence["runtime_effect"]
        proof["effect_id"] = "terminal_event_shifted"
        proof["receipt_hash"] = hash_payload({
            key: value for key, value in proof.items()
            if key != "receipt_hash"
        })
    elif mutation == "trivial":
        evidence["nontriviality"]["constant_output"] = True
    elif mutation == "minimization_fails":
        evidence["minimization"]["succeeded"] = False
    _rehash_evidence(evidence)
    decision = _decision(plan, policy, registries, evidence)
    assert not decision["admitted"]
    assert not decision["checks"][failed_gate]


def test_testbench_or_oracle_tampering_is_rejected(
    plan, policy, registries
):
    evidence = build_deterministic_evidence(plan)
    evidence["source_integrity"]["forbidden_changes"] = [
        "testbench",
        "oracle",
    ]
    _rehash_evidence(evidence)
    decision = _decision(plan, policy, registries, evidence)
    assert not decision["checks"]["G1_source_integrity"]
    assert not decision["admitted"]


def test_semantic_scope_uses_ast_counts_not_line_count(
    plan, policy, registries
):
    evidence = build_deterministic_evidence(plan)
    semantic = evidence["semantic_diff"]
    semantic["changed_block_count"] = 2
    semantic["receipt_hash"] = hash_payload({
        key: value for key, value in semantic.items() if key != "receipt_hash"
    })
    _rehash_evidence(evidence)
    decision = _decision(plan, policy, registries, evidence)
    assert not decision["checks"]["G8_semantic_family_proof"]


def test_difficulty_is_derived_from_semantic_and_runtime_receipts(
    plan, policy, registries
):
    evidence = build_deterministic_evidence(plan)
    semantic = evidence["semantic_diff"]
    effect = evidence["runtime_effect"]
    semantic["dependency_depth"] = 3
    semantic["changed_block_count"] = 2
    semantic["receipt_hash"] = hash_payload({
        key: value for key, value in semantic.items() if key != "receipt_hash"
    })
    profile = build_difficulty_profile(
        current_blue_failure_rate=0.67,
        semantic_diff_receipt=semantic,
        runtime_effect_receipt=effect,
        activation_rarity=0.25,
        repair_locality="cross_block",
        candidate_ambiguity=2,
        composition_depth=1,
    )
    assert profile["difficulty_band"] == "D2"
    assert "changed_line_count" not in profile


def _cell(family, design, admitted):
    return {
        "family_id": family,
        "design_id": design,
        "rtl_role": "control",
        "temporal_context": "combinational",
        "difficulty_band": "D0",
        "effective_blue_policy_hash": hash_payload({"policy": "B0"}),
        "effective_memory_bank_hash": "",
        "proposals": admitted,
        "admitted": admitted,
        "covered": admitted,
        "residual": 0,
        "rejected": 0,
        "last_round_id": "R001",
    }


def test_coverage_planner_selects_uncovered_cells_and_enforces_family_quota():
    covered = _cell("family.saturated", "d0", 5)
    state = freeze_coverage_state([covered])
    candidates = [
        _cell("family.saturated", "d1", 0),
        _cell("family.new", "d2", 0),
        _cell("family.new", "d3", 0),
    ]
    selected = select_coverage_targets(
        candidates,
        coverage_state=state,
        budget=3,
        family_quota=1,
    )
    assert len(selected) == 2
    assert selected[0]["family_id"] == "family.new"
    assert len({row["family_id"] for row in selected}) == 2


def test_grounded_red_capability_is_effective_policy_bound_and_sanitized(
    policy,
):
    state = freeze_coverage_state([])
    packet = build_grounded_red_capability_packet(
        policy,
        coverage_state=state,
        known_memory_blind_spots=[{"family_id": "f", "rtl_role": "state"}],
    )
    assert verify_grounded_red_capability_packet(
        packet, policy=policy
    ) == packet
    with pytest.raises(GroundedCapabilityViolation, match="private"):
        build_grounded_red_capability_packet(
            policy,
            coverage_state=state,
            known_memory_blind_spots=[{"source_episode_ids": ["E1"]}],
        )


def test_grounded_archive_separates_admitted_and_rejected(
    tmp_path, plan, policy, registries
):
    evidence = build_deterministic_evidence(plan)
    decision = _decision(plan, policy, registries, evidence)
    profile = build_difficulty_profile(
        current_blue_failure_rate=1.0,
        semantic_diff_receipt=evidence["semantic_diff"],
        runtime_effect_receipt=evidence["runtime_effect"],
        activation_rarity=0.5,
        repair_locality="expression",
        candidate_ambiguity=1,
        composition_depth=1,
    )
    lineage = build_lineage(
        poison_id="P001",
        parent_poison_ids=[],
        lineage_operator="fresh",
        source_family_ids=[plan["family_id"]],
        target_family_ids=[plan["family_id"]],
        difficulty_delta={"temporal_depth": 0, "dependency_depth": 0},
        semantic_diff_receipt_hash=evidence["semantic_diff"]["receipt_hash"],
    )
    formal_archive = GroundedRedArchive(
        tmp_path / "formal-archives"
    )
    with pytest.raises(
        GroundedArchiveViolation, match="runner-owned grounded execution"
    ):
        formal_archive.add(
            kind="valid",
            poison_id="P001",
            archived_round_id="R001",
            policy=policy,
            registries=registries,
            plan=plan,
            evidence=evidence,
            admission_decision=decision,
            difficulty_profile=profile,
            lineage=lineage,
        )
    archive = GroundedRedArchive(
        tmp_path / "archives",
        require_grounded_execution=False,
    )
    valid = archive.add(
        kind="valid",
        poison_id="P001",
        archived_round_id="R001",
        policy=policy,
        registries=registries,
        plan=plan,
        evidence=evidence,
        admission_decision=decision,
        difficulty_profile=profile,
        lineage=lineage,
    )
    residual = archive.add(
        kind="residual",
        poison_id="P001",
        archived_round_id="R001",
        policy=policy,
        registries=registries,
        plan=plan,
        evidence=evidence,
        admission_decision=decision,
        difficulty_profile=profile,
        lineage=lineage,
    )
    assert valid["archive_kind"] == "valid"
    assert residual["archive_kind"] == "residual"
    assert len(archive.load()) == 2
    with pytest.raises(GroundedArchiveViolation, match="admitted poison"):
        archive.add(
            kind="rejected",
            poison_id="P001",
            archived_round_id="R001",
            policy=policy,
            registries=registries,
            plan=plan,
            evidence=evidence,
            admission_decision=decision,
            difficulty_profile=profile,
            lineage=lineage,
        )
    rejected_evidence = deepcopy(evidence)
    rejected_evidence["nontriviality"]["constant_output"] = True
    _rehash_evidence(rejected_evidence)
    rejected_decision = _decision(
        plan, policy, registries, rejected_evidence
    )
    rejected_lineage = build_lineage(
        poison_id="P_REJECTED",
        parent_poison_ids=[],
        lineage_operator="fresh",
        source_family_ids=[plan["family_id"]],
        target_family_ids=[plan["family_id"]],
        difficulty_delta={"temporal_depth": 0, "dependency_depth": 0},
        semantic_diff_receipt_hash=rejected_evidence["semantic_diff"][
            "receipt_hash"
        ],
    )
    rejected = archive.add(
        kind="rejected",
        poison_id="P_REJECTED",
        archived_round_id="R001",
        policy=policy,
        registries=registries,
        plan=plan,
        evidence=rejected_evidence,
        admission_decision=rejected_decision,
        difficulty_profile=profile,
        lineage=rejected_lineage,
    )
    assert rejected["admission_decision"]["rejection_reasons"] == [
        "G10_non_triviality"
    ]


def test_multi_parent_lineage_cycle_is_rejected():
    receipt_hash = hash_payload({"semantic": "diff"})
    first = build_lineage(
        poison_id="P1",
        parent_poison_ids=["P2"],
        lineage_operator="derived_from",
        source_family_ids=["f"],
        target_family_ids=["f"],
        difficulty_delta={"dependency_depth": 1},
        semantic_diff_receipt_hash=receipt_hash,
    )
    second = build_lineage(
        poison_id="P2",
        parent_poison_ids=["P1"],
        lineage_operator="derived_from",
        source_family_ids=["f"],
        target_family_ids=["f"],
        difficulty_delta={"dependency_depth": 1},
        semantic_diff_receipt_hash=receipt_hash,
    )
    with pytest.raises(GroundedLineageViolation, match="cycle"):
        validate_lineage_graph([first, second])


def test_deterministic_grounded_red_fake_system_is_multiround_and_idempotent(
    tmp_path,
):
    first = run_fake_grounded_red(tmp_path / "grounded-red", rounds=3)
    second = run_fake_grounded_red(tmp_path / "grounded-red", rounds=3)
    assert first == second
    assert first["round_count"] == 3
    assert first["valid_archive_count"] == 3
    assert first["residual_archive_count"] == 3


def test_grounded_red_protocol_foundation_milestone_is_reconstructable():
    milestone = read_json(
        ROOT
        / "configs/evolution/grounded_red_protocol_foundation_v1.json"
    )
    assert (
        milestone["schema_version"]
        == "r3e-grounded-red-protocol-foundation-milestone-v1"
    )
    assert milestone["status"] == "frozen"
    assert milestone["milestone_hash"] == hash_payload({
        key: value for key, value in milestone.items()
        if key != "milestone_hash"
    })
    parent = read_json(
        ROOT / "configs/evolution/raam_authority_closure_v1.json"
    )
    assert (
        milestone["parent_milestone"]["milestone_hash"]
        == parent["milestone_hash"]
    )
    for relative, expected in milestone["frozen_assets"].items():
        assert hash_file(ROOT / relative) == expected
    assert "real_command_execution" in milestone[
        "deferred_grounded_execution"
    ]
