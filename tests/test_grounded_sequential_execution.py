from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from r3e.arena.grounded_authority import (
    execute_grounded_arena_validity,
    verify_arena_grounded_authority,
)
from r3e.memory.schema import ActiveMemoryBank
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_file, hash_payload
from r3e.red.grounded.execution import verify_grounded_execution_bundle
from r3e.red.grounded.memory_materializers import (
    build_controlled_composition_plan,
    build_parser_memory_operator_plan,
)
from r3e.red.grounded.registry import load_grounded_registries
from r3e.red.grounded.sequential_execution import (
    GroundedSequentialExecutionViolation,
    execute_grounded_sequential_admission,
)
from r3e.red.poison_payload import bind_poison_payload


ROOT = Path(__file__).resolve().parents[1]
HAS_ICARUS = bool(shutil.which("iverilog") and shutil.which("vvp"))
HAS_YOSYS = bool(shutil.which("yosys"))
SOURCE = """module m(
  input wire [3:0] a,
  input wire [3:0] b,
  output wire y
);
assign y = (a < 4) && (b > 2);
endmodule
"""
TESTBENCH = """module tb;
reg [3:0] a;
reg [3:0] b;
wire y;
integer i;
integer j;
integer mismatches;
m dut(.a(a), .b(b), .y(y));
initial begin
  mismatches = 0;
  for (i = 0; i < 7; i = i + 1) begin
    for (j = 0; j < 7; j = j + 1) begin
      a = i; b = j; #1;
      if (y !== ((i < 4) && (j > 2)))
        mismatches = mismatches + 1;
    end
  end
  if (mismatches == 0) begin
    $display("R3E_ORACLE pass=1 signature=none first=none topology=none");
    $display("R3E_WAVEFORM signal=none first_cycle=none cycle_offset=none relation=none assignment=none cone_depth=none pattern=none");
  end else begin
    $display("R3E_ORACLE pass=0 signature=composed_boundary first=cycle4 topology=y");
    $display("R3E_WAVEFORM signal=y first_cycle=4 cycle_offset=0 relation=same_cycle assignment=continuous cone_depth=1 pattern=boundary_value_mismatch");
  end
  $finish;
end
endmodule
"""
PASS_ONLY_TESTBENCH = TESTBENCH.replace(
    "if (y !== ((i < 4) && (j > 2)))",
    "if (1'b0)",
)
FORMAL_PROPERTY = """module formal_top;
  (* anyconst *) reg [3:0] a;
  (* anyconst *) reg [3:0] b;
  wire y;
  m dut(.a(a), .b(b), .y(y));
  always @* assert(y == ((a < 4) && (b > 2)));
endmodule
"""
COMPARATOR_STEP = {
    "operator_id": "replace_comparator",
    "family_id": "combinational.comparator_boundary",
    "expected_runtime_effect_id": "wrong_combinational_value",
    "precondition": "comparison_expression",
    "node_ordinal": 0,
}


def test_grounded_sequential_runtime_gate_milestone_is_frozen():
    milestone = json.loads((
        ROOT
        / "configs/evolution/grounded_sequential_runtime_gate_v1.json"
    ).read_text(encoding="utf-8"))
    parent = json.loads((
        ROOT
        / "configs/evolution/grounded_proposal_authority_v1.json"
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
    successor = json.loads((
        ROOT
        / "configs/evolution/grounded_deterministic_population_v1.json"
    ).read_text(encoding="utf-8"))
    integrated = json.loads((
        ROOT
        / "configs/evolution/integrated_deterministic_coevolution_v1.json"
    ).read_text(encoding="utf-8"))
    pilot = json.loads((
        ROOT / "configs/evolution/real_adapter_pilot_entry_v1.json"
    ).read_text(encoding="utf-8"))
    assert successor["parent_milestone"] == {
        "milestone_id": milestone["milestone_id"],
        "milestone_hash": milestone["milestone_hash"],
    }
    for relative, expected in milestone["frozen_assets"].items():
        current = hash_file(ROOT / relative)
        if current != expected:
            assert current in {
                successor["frozen_assets"].get(relative),
                integrated["frozen_assets"].get(relative),
                pilot["frozen_assets"].get(relative),
            }


@pytest.fixture
def policy():
    return PolicyState.from_dict(json.loads(
        (
            ROOT / "configs/base_policy/frozen_base_policy_v1.json"
        ).read_text(encoding="utf-8")
    ))


@pytest.fixture
def registries():
    return load_grounded_registries(
        family_registry=ROOT / "configs/red/grounded_family_registry_v1.json",
        operator_registry=ROOT / "configs/red/grounded_operator_registry_v1.json",
        effect_registry=ROOT / "configs/red/grounded_effect_registry_v1.json",
    )


def _bank(policy):
    digest = lambda value: hash_payload({"value": value})
    return ActiveMemoryBank.create(
        bank_id="AMB_FINAL_GATE",
        bank_version=1,
        policy_instance_hash=policy.policy_instance_hash,
        effective_policy_hash=policy.effective_policy_hash,
        memories={
            "CM_A": {
                "memory_version": 1,
                "memory_hash": digest("memory"),
                "memory_definition_hash": digest("definition"),
                "status": "active_dormant",
            }
        },
        retriever_hash=digest("retriever"),
        activation_guard_hash=digest("guard"),
        control_whitelist_hash=digest("whitelist"),
    )


def _memory_plan(policy, registries):
    bank = _bank(policy)
    packet = {
        "schema_version": "r3e-memory-red-capability-v1",
        "challenged_policy_id": policy.policy_id,
        "challenged_policy_hash": policy.policy_hash,
        "challenged_effective_policy_hash": policy.effective_policy_hash,
        "active_memory_bank_hash": bank.bank_hash,
        "effective_memory_bank_hash": bank.effective_memory_bank_hash,
        "active_memory_summaries": [{"memory_id": "CM_A"}],
        "public_memory_regions": [],
        "allowed_operators": ["memory_bypass"],
    }
    packet["packet_hash"] = hash_payload(packet)
    return build_parser_memory_operator_plan(
        policy=policy,
        bank=bank,
        capability_packet=packet,
        registries=registries,
        operator="memory_bypass",
        poison_id="P_MEMORY_FINAL",
        target_memory_ids=["CM_A"],
        parent_poison_id="P_PARENT",
        source_design="design_a",
        target_design="design_a",
        target_module="m",
        clean_source=SOURCE,
        step_specs=[COMPARATOR_STEP],
    )


def _composition_plan(policy, registries):
    return build_controlled_composition_plan(
        policy=policy,
        registries=registries,
        poison_id="P_COMPOSITION_FINAL",
        parent_poison_ids=["P_LEFT", "P_RIGHT"],
        parent_authority_hashes={
            "P_LEFT": hash_payload({"authority": "left"}),
            "P_RIGHT": hash_payload({"authority": "right"}),
        },
        target_design="design_a",
        target_module="m",
        clean_source=SOURCE,
        step_specs=[
            COMPARATOR_STEP,
            {**COMPARATOR_STEP, "node_ordinal": 1},
        ],
    )


def _execute(tmp_path, plan, policy, registries, testbench=TESTBENCH):
    workspace = tmp_path / "execution"
    inputs = workspace / "inputs"
    inputs.mkdir(parents=True)
    clean = inputs / "clean.v"
    tb = inputs / "tb.v"
    clean.write_text(SOURCE, encoding="utf-8")
    tb.write_text(testbench, encoding="utf-8")
    return execute_grounded_sequential_admission(
        plan=plan,
        policy=policy,
        registries=registries,
        clean_rtl_path=clean,
        testbench_path=tb,
        top_module="tb",
        workspace=workspace,
        run_context_hash=hash_payload({"run": plan["poison_id"]}),
        frozen_clean_rtl_hash=hash_file(clean),
        frozen_testbench_hash=hash_file(tb),
        allowed_file_manifest_hash=hash_payload({
            "allowed": ["materialized/poison.v"]
        }),
        publish_poison_path=tmp_path / "published" / "poison.v",
    )


@pytest.mark.skipif(not HAS_ICARUS, reason="Icarus Verilog is unavailable")
@pytest.mark.parametrize("plan_factory", [_memory_plan, _composition_plan])
def test_parser_memory_and_composition_reach_final_oracle_gate(
    tmp_path,
    policy,
    registries,
    plan_factory,
):
    plan = plan_factory(policy, registries)
    bundle = _execute(tmp_path, plan, policy, registries)
    assert bundle["admission_decision"]["admitted"]
    assert all(bundle["admission_decision"]["checks"].values())
    assert len(bundle["evidence"]["poison_runs"]) == 2
    assert bundle["materialization_receipt"][
        "intermediate_admission_complete"
    ]
    assert (
        hash_file(tmp_path / "published" / "poison.v")
        == bundle["materialization_receipt"]["poison_source_hash"]
    )
    assert verify_grounded_execution_bundle(
        bundle,
        policy=policy,
        registries=registries,
    )["bundle_hash"] == bundle["bundle_hash"]

    tampered = deepcopy(bundle)
    tampered["stage_materializations"][0]["receipt"][
        "materialization_hash"
    ] = hash_payload({"tampered": True})
    tampered["bundle_hash"] = hash_payload({
        key: value
        for key, value in tampered.items()
        if key != "bundle_hash"
    })
    with pytest.raises(GroundedSequentialExecutionViolation):
        verify_grounded_execution_bundle(
            tampered,
            policy=policy,
            registries=registries,
        )


@pytest.mark.skipif(not HAS_ICARUS, reason="Icarus Verilog is unavailable")
def test_sequential_gate_fails_closed_when_poison_does_not_fail_oracle(
    tmp_path,
    policy,
    registries,
):
    bundle = _execute(
        tmp_path,
        _memory_plan(policy, registries),
        policy,
        registries,
        testbench=PASS_ONLY_TESTBENCH,
    )
    assert not bundle["admission_decision"]["admitted"]
    assert "G5_functional_failure" in bundle[
        "admission_decision"
    ]["rejection_reasons"]


@pytest.mark.skipif(
    not (HAS_ICARUS and HAS_YOSYS),
    reason="Icarus or Yosys is unavailable",
)
def test_controlled_composition_reaches_arena_formal_triplet(
    tmp_path,
    policy,
    registries,
):
    source_root = tmp_path / "sources"
    source_root.mkdir()
    clean = source_root / "clean.v"
    poison_output = source_root / "runner_poison.v"
    tb = source_root / "tb.v"
    prop = source_root / "property.v"
    clean.write_text(SOURCE, encoding="utf-8")
    tb.write_text(TESTBENCH, encoding="utf-8")
    prop.write_text(FORMAL_PROPERTY, encoding="utf-8")
    plan = _composition_plan(policy, registries)
    poison = bind_poison_payload({
        "poison_id": plan["poison_id"],
        "case_id": plan["poison_id"],
        "design": "design_a",
        "golden_rtl": str(clean),
        "buggy_rtl": str(poison_output),
        "challenged_policy_id": policy.policy_id,
        "challenged_policy_hash": policy.policy_hash,
        "family": "composition.controlled_pair",
        "effect": "wrong_combinational_value",
        "affected_role": "observable_output",
        "edit_scope": "two_parser_ast_nodes",
        "normalized_diff_hash": plan["plan_hash"],
        "grounded_sequential_plan": plan,
        "grounded_plan_hash": plan["plan_hash"],
        "grounded_testbench": str(tb),
        "grounded_top_module": "tb",
        "grounded_formal_property": str(prop),
        "grounded_formal_top_module": "formal_top",
        "grounded_formal_depth": 1,
    })
    result = execute_grounded_arena_validity(
        poison=poison,
        policy=policy,
        registries=registries,
        project_root=tmp_path,
        round_dir=tmp_path / "round-R000",
        run_context_hash=hash_payload({"run": "arena-sequential"}),
    )
    authority = verify_arena_grounded_authority(
        result["grounded_authority_bundle"],
        policy=policy,
        registries=registries,
    )
    assert result["validity"]["proven_valid"]
    assert authority["formal_proof_triplet"]["clean"]["verdict"] == "proved"
    assert (
        authority["formal_proof_triplet"]["poison"]["verdict"]
        == "counterexample"
    )
    assert authority["formal_proof_triplet"]["revert"]["verdict"] == "proved"
    assert hash_file(poison_output) == authority["execution_bundle"][
        "materialization_receipt"
    ]["poison_source_hash"]

    rejected_output = source_root / "runner_poison_rejected.v"
    prop.write_text(
        FORMAL_PROPERTY.replace("a < 4", "a <= 4").replace(
            "b > 2", "b >= 2"
        ),
        encoding="utf-8",
    )
    rejected_poison = bind_poison_payload({
        **{
            key: value
            for key, value in poison.items()
            if key not in {
                "poison_payload_hash",
                "poison_payload_schema_version",
            }
        },
        "buggy_rtl": str(rejected_output),
    })
    rejected = execute_grounded_arena_validity(
        poison=rejected_poison,
        policy=policy,
        registries=registries,
        project_root=tmp_path,
        round_dir=tmp_path / "round-R001",
        run_context_hash=hash_payload({
            "run": "arena-sequential-rejected"
        }),
    )
    assert not rejected["validity"]["proven_valid"]
    assert rejected["formal_rejection"]["execution_bundle"][
        "schema_version"
    ] == "r3e-grounded-sequential-execution-bundle-v1"
