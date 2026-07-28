from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from r3e.memory.schema import ActiveMemoryBank
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_file, hash_payload
from r3e.red.grounded.memory_materializers import (
    ParserMemoryMaterializationViolation,
    build_controlled_composition_plan,
    build_parser_memory_operator_plan,
    materialize_sequential_plan,
    verify_sequential_materialization,
)
from r3e.red.grounded.registry import load_grounded_registries


ROOT = Path(__file__).resolve().parents[1]
H = lambda value: hash_payload({"value": value})
SOURCE = """module m(
  input wire [3:0] a,
  input wire [3:0] b,
  output wire y
);
assign y = (a < 4) && (b > 2);
endmodule
"""
COMPARATOR_STEP = {
    "operator_id": "replace_comparator",
    "family_id": "combinational.comparator_boundary",
    "expected_runtime_effect_id": "wrong_combinational_value",
    "precondition": "comparison_expression",
    "node_ordinal": 0,
}
BOUNDARY_STEP = {
    "operator_id": "shift_boundary_constant",
    "family_id": "combinational.comparator_boundary",
    "expected_runtime_effect_id": "wrong_combinational_value",
    "precondition": "constant_boundary",
    "node_ordinal": 0,
}


def test_grounded_planner_raam_cross_round_milestone_is_frozen():
    milestone = json.loads((
        ROOT
        / "configs/evolution/"
        "grounded_planner_raam_cross_round_v1.json"
    ).read_text(encoding="utf-8"))
    parent = json.loads((
        ROOT
        / "configs/evolution/grounded_waveform_rejection_v1.json"
    ).read_text(encoding="utf-8"))
    assert milestone["status"] == "frozen"
    assert milestone["milestone_hash"] == hash_payload({
        key: value for key, value in milestone.items()
        if key != "milestone_hash"
    })
    assert milestone["parent_milestone"] == {
        "milestone_id": parent["milestone_id"],
        "milestone_hash": parent["milestone_hash"],
    }
    for relative, expected in milestone["frozen_assets"].items():
        assert hash_file(ROOT / relative) == expected


@pytest.fixture
def policy():
    return PolicyState.from_dict(json.loads(
        (
            ROOT
            / "configs/base_policy/frozen_base_policy_v1.json"
        ).read_text(encoding="utf-8")
    ))


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


def _bank(policy):
    return ActiveMemoryBank.create(
        bank_id="AMB_PARSER_TEST",
        bank_version=1,
        policy_instance_hash=policy.policy_instance_hash,
        effective_policy_hash=policy.effective_policy_hash,
        memories={
            "CM_A": {
                "memory_version": 1,
                "memory_hash": H("memory-a"),
                "memory_definition_hash": H("definition-a"),
                "status": "active_dormant",
            },
            "CM_B": {
                "memory_version": 1,
                "memory_hash": H("memory-b"),
                "memory_definition_hash": H("definition-b"),
                "status": "active_dormant",
            },
        },
        retriever_hash=H("retriever"),
        activation_guard_hash=H("guard"),
        control_whitelist_hash=H("whitelist"),
    )


def _packet(policy, bank):
    value = {
        "schema_version": "r3e-memory-red-capability-v1",
        "challenged_policy_id": policy.policy_id,
        "challenged_policy_hash": policy.policy_hash,
        "challenged_effective_policy_hash": (
            policy.effective_policy_hash
        ),
        "active_memory_bank_hash": bank.bank_hash,
        "effective_memory_bank_hash": (
            bank.effective_memory_bank_hash
        ),
        "active_memory_summaries": [
            {"memory_id": "CM_A"},
            {"memory_id": "CM_B"},
        ],
        "public_memory_regions": [],
        "allowed_operators": [
            "memory_bypass",
            "memory_conflict",
            "memory_deepening",
            "memory_transfer",
        ],
    }
    value["packet_hash"] = hash_payload(value)
    return value


@pytest.mark.parametrize(
    ("operator", "targets", "source_design", "target_design", "steps"),
    [
        (
            "memory_bypass",
            ["CM_A"],
            "design_a",
            "design_a",
            [COMPARATOR_STEP],
        ),
        (
            "memory_deepening",
            ["CM_A"],
            "design_a",
            "design_a",
            [BOUNDARY_STEP],
        ),
        (
            "memory_transfer",
            ["CM_A"],
            "design_a",
            "design_b",
            [COMPARATOR_STEP],
        ),
        (
            "memory_conflict",
            ["CM_A", "CM_B"],
            "design_a",
            "design_a",
            [
                COMPARATOR_STEP,
                {**COMPARATOR_STEP, "node_ordinal": 1},
            ],
        ),
    ],
)
def test_four_memory_operators_are_parser_backed_and_exactly_reversible(
    policy,
    registries,
    operator,
    targets,
    source_design,
    target_design,
    steps,
):
    bank = _bank(policy)
    plan = build_parser_memory_operator_plan(
        policy=policy,
        bank=bank,
        capability_packet=_packet(policy, bank),
        registries=registries,
        operator=operator,
        poison_id=f"P_{operator}",
        target_memory_ids=targets,
        parent_poison_id="P_PARENT",
        source_design=source_design,
        target_design=target_design,
        target_module="m",
        clean_source=SOURCE,
        step_specs=steps,
    )
    result = materialize_sequential_plan(
        plan,
        policy=policy,
        registries=registries,
        clean_source=SOURCE,
    )
    verified = verify_sequential_materialization(
        result,
        plan=plan,
        policy=policy,
        registries=registries,
        clean_source=SOURCE,
    )
    assert verified["poison_source"] != SOURCE
    assert verified["receipt"]["intermediate_admission_complete"]
    assert len(verified["receipt"]["stage_admissions"]) == len(steps)
    assert all(
        value["admitted"]
        for value in verified["receipt"]["stage_admissions"]
    )


def test_controlled_composition_binds_two_parents_and_admits_intermediate(
    policy,
    registries,
):
    plan = build_controlled_composition_plan(
        policy=policy,
        registries=registries,
        poison_id="P_COMPOSED",
        parent_poison_ids=["P_LEFT", "P_RIGHT"],
        parent_authority_hashes={
            "P_LEFT": H("left-authority"),
            "P_RIGHT": H("right-authority"),
        },
        target_design="design_a",
        target_module="m",
        clean_source=SOURCE,
        step_specs=[
            COMPARATOR_STEP,
            {**COMPARATOR_STEP, "node_ordinal": 1},
        ],
    )
    result = materialize_sequential_plan(
        plan,
        policy=policy,
        registries=registries,
        clean_source=SOURCE,
    )
    assert plan["family_id"] == "composition.controlled_pair"
    assert plan["difficulty_band"] == "D4"
    assert plan["composition_depth"] == 2
    assert len(result["receipt"]["stage_admissions"]) == 2

    tampered = deepcopy(plan)
    tampered["parent_authority_hashes"]["P_LEFT"] = H("tampered")
    with pytest.raises(
        ParserMemoryMaterializationViolation,
        match="plan_hash",
    ):
        materialize_sequential_plan(
            tampered,
            policy=policy,
            registries=registries,
            clean_source=SOURCE,
        )
