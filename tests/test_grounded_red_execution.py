from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil

import pytest

from r3e.grounded.command_runner import (
    GroundedCommandRunner,
    GroundedCommandViolation,
)
from r3e.grounded.icarus import (
    IcarusGroundedProvider,
    IcarusProviderViolation,
    parse_oracle_output,
)
from r3e.grounded.provider_receipts import (
    GroundedProviderViolation,
    verify_provider_receipt,
)
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_file, hash_payload, read_json
from r3e.red.grounded.admission import (
    GroundedAdmissionViolation,
    decide_grounded_admission,
)
from r3e.red.grounded.execution import (
    GroundedRedExecutionViolation,
    execute_grounded_icarus_admission,
    verify_grounded_execution_bundle,
)
from r3e.red.grounded.materializers import (
    AstMaterializationViolation,
    inverse_materialization,
    materialize_comparator,
    verify_materialization_receipt,
)
from r3e.red.grounded.mutation_plan import build_mutation_plan
from r3e.red.grounded.registry import load_grounded_registries
from r3e.red.grounded.verilog_ast import comparison_nodes


ROOT = Path(__file__).resolve().parents[1]
HAS_ICARUS = bool(shutil.which("iverilog") and shutil.which("vvp"))

CLEAN_RTL = """\
module counter(
    input  wire [3:0] count,
    output wire       done
);
assign done = (count < 4);
endmodule
"""

TESTBENCH = """\
module tb;
reg [3:0] count;
wire done;
integer index;
integer mismatches;
integer first_bad;

counter dut(.count(count), .done(done));

initial begin
  mismatches = 0;
  first_bad = -1;
  count = 0;
  #1;
  for (index = 0; index < 6; index = index + 1) begin
    count = index;
    #1;
    if (done !== (index < 4)) begin
      mismatches = mismatches + 1;
      if (first_bad < 0)
        first_bad = index;
    end
  end
  if (mismatches == 0)
    $display("R3E_ORACLE pass=1 signature=none first=none topology=none");
  else
    $display("R3E_ORACLE pass=0 signature=comparator_boundary first=count4 topology=done");
  $finish;
end
endmodule
"""


@pytest.fixture
def policy():
    return PolicyState.from_dict(
        read_json(ROOT / "configs/base_policy/frozen_base_policy_v1.json")
    )


@pytest.fixture
def registries():
    return load_grounded_registries(
        family_registry=ROOT / "configs/red/grounded_family_registry_v1.json",
        operator_registry=ROOT / "configs/red/grounded_operator_registry_v1.json",
        effect_registry=ROOT / "configs/red/grounded_effect_registry_v1.json",
    )


@pytest.fixture
def comparator_plan(policy, registries):
    node = comparison_nodes(CLEAN_RTL, module="counter")[0]
    return build_mutation_plan(
        plan_id="MP_REAL_001",
        policy=policy,
        registries=registries,
        target_design="counter_fixture",
        target_module="counter",
        target_ast_node_hash=node.node_hash,
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


def test_parser_backed_comparator_materializer_has_exact_inverse(
    comparator_plan,
):
    materialized = materialize_comparator(
        comparator_plan,
        clean_source=CLEAN_RTL,
    )
    assert "count <= 4" in materialized.poison_source
    assert materialized.receipt["ast_edit_count"] == 1
    restored = inverse_materialization(
        materialized.receipt,
        poison_source=materialized.poison_source,
    )
    assert restored == CLEAN_RTL
    stale = deepcopy(comparator_plan)
    stale["target_ast_node_hash"] = hash_payload({"stale": "node"})
    stale["plan_hash"] = hash_payload({
        key: value for key, value in stale.items() if key != "plan_hash"
    })
    with pytest.raises(AstMaterializationViolation, match="missing"):
        materialize_comparator(stale, clean_source=CLEAN_RTL)


def test_command_runner_rejects_workspace_escape_and_kills_timeout(tmp_path):
    sleep = shutil.which("sleep")
    cat = shutil.which("cat")
    yes = shutil.which("yes")
    assert sleep and cat and yes
    root = tmp_path / "workspace"
    root.mkdir()
    runner = GroundedCommandRunner(
        allowed_root=root,
        artifact_root=root / "artifacts",
        allowed_executables={"sleep": sleep, "cat": cat, "yes": yes},
        run_context_hash=hash_payload({"run": "context"}),
        toolchain_fingerprint_hash=hash_payload({"tool": "python"}),
        timeout_seconds=0.1,
        max_output_bytes=10_000,
        max_memory_bytes=512_000_000,
    )
    with pytest.raises(GroundedCommandViolation, match="escapes"):
        runner.run(
            receipt_id="escape",
            phase="compile",
            subject_hash=hash_payload({"subject": "x"}),
            executable="sleep",
            arguments=["1"],
            cwd=tmp_path,
        )
    with pytest.raises(GroundedCommandViolation, match="argument path escapes"):
        runner.run(
            receipt_id="argument-escape",
            phase="compile",
            subject_hash=hash_payload({"subject": "x"}),
            executable="cat",
            arguments=["/etc/passwd"],
            cwd=root,
        )
    result = runner.run(
        receipt_id="../../timeout",
        phase="compile",
        subject_hash=hash_payload({"subject": "x"}),
        executable="sleep",
        arguments=["2"],
        cwd=root,
    )
    assert result.receipt["result_kind"] == "timeout"
    assert result.receipt["timed_out"]
    with pytest.raises(
        GroundedCommandViolation, match="already has persisted"
    ):
        runner.run(
            receipt_id="../../timeout",
            phase="compile",
            subject_hash=hash_payload({"subject": "x"}),
            executable="sleep",
            arguments=["0"],
            cwd=root,
        )
    assert not (tmp_path / "timeout").exists()
    limited = runner.run(
        receipt_id="output-limit",
        phase="simulation",
        subject_hash=hash_payload({"subject": "x"}),
        executable="yes",
        arguments=[],
        cwd=root,
    )
    assert limited.receipt["result_kind"] == "resource_limit"


def test_oracle_parser_requires_one_exact_record():
    valid = (
        b"trace\n"
        b"R3E_ORACLE pass=0 signature=wrong_value "
        b"first=cycle4 topology=done\n"
        b"R3E_WAVEFORM signal=done first_cycle=4 cycle_offset=1 "
        b"relation=candidate_lags_golden assignment=nonblocking "
        b"cone_depth=2 pattern=lagging_value\n"
    )
    parsed = parse_oracle_output(valid)
    assert not parsed["oracle_pass"]
    assert parsed["functional_mismatch"]
    assert parsed["waveform_observation_complete"]
    assert parsed["first_divergence_signal"] == "done"
    assert parsed["first_divergence_cycle"] == 4
    assert parsed["cycle_offset"] == 1
    assert parsed["temporal_relation"] == "candidate_lags_golden"
    assert parsed["assignment_type"] == "nonblocking"
    assert parsed["cone_depth"] == 2
    assert parsed["mismatch_pattern"] == "lagging_value"
    with pytest.raises(IcarusProviderViolation, match="exactly one"):
        parse_oracle_output(b"ordinary simulation output\n")
    with pytest.raises(IcarusProviderViolation, match="exactly one"):
        parse_oracle_output(valid + valid)
    with pytest.raises(IcarusProviderViolation, match="exactly one"):
        parse_oracle_output(
            valid + b"R3E_ORACLE malformed\n"
        )
    with pytest.raises(IcarusProviderViolation, match="inconsistent"):
        parse_oracle_output(
            b"R3E_ORACLE pass=1 signature=wrong_value "
            b"first=cycle4 topology=done\n"
        )
    with pytest.raises(IcarusProviderViolation, match="malformed"):
        parse_oracle_output(
            b"R3E_ORACLE pass=0 signature=wrong_value "
            b"first=cycle4 topology=done\n"
            b"R3E_WAVEFORM malformed\n"
        )


@pytest.mark.skipif(not HAS_ICARUS, reason="Icarus Verilog is unavailable")
def test_real_icarus_chain_admits_clean_pass_poison_fail_revert_pass(
    tmp_path,
    comparator_plan,
    policy,
    registries,
):
    workspace = tmp_path / "real-grounded"
    workspace.mkdir()
    clean = workspace / "counter.v"
    testbench = workspace / "tb.v"
    clean.write_text(CLEAN_RTL, encoding="utf-8")
    testbench.write_text(TESTBENCH, encoding="utf-8")
    bundle = execute_grounded_icarus_admission(
        plan=comparator_plan,
        policy=policy,
        registries=registries,
        clean_rtl_path=clean,
        testbench_path=testbench,
        top_module="tb",
        workspace=workspace,
        run_context_hash=hash_payload({"run": "real-icarus"}),
        frozen_clean_rtl_hash=hash_file(clean),
        frozen_testbench_hash=hash_file(testbench),
        allowed_file_manifest_hash=hash_payload({
            "allowed_changed_files": ["materialized/poison.v"],
        }),
    )
    decision = bundle["admission_decision"]
    assert decision["schema_version"] == "r3e-red-admission-decision-v2"
    assert decision["authority_mode"] == "runner_owned_grounded_execution"
    assert decision["admitted"]
    evidence = bundle["evidence"]
    assert evidence["clean_run"]["observed"]["oracle_pass"]
    assert all(
        not row["observed"]["oracle_pass"]
        and row["observed"]["functional_mismatch"]
        for row in evidence["poison_runs"]
    )
    assert evidence["revert_run"]["observed"]["oracle_pass"]
    assert evidence["schema_version"] == "r3e-red-admission-evidence-v2"
    assert (
        evidence["materialization_receipt"]
        == bundle["materialization_receipt"]
    )
    assert verify_grounded_execution_bundle(
        bundle,
        policy=policy,
        registries=registries,
    )["bundle_hash"] == bundle["bundle_hash"]
    assert (workspace / "grounded_execution_bundle.json").is_file()
    assert verify_grounded_execution_bundle(
        bundle,
        policy=policy,
        registries=registries,
    )["bundle_hash"] == bundle["bundle_hash"]


@pytest.mark.skipif(not HAS_ICARUS, reason="Icarus Verilog is unavailable")
def test_real_execution_rejects_frozen_input_mismatch_before_running(
    tmp_path,
    comparator_plan,
    policy,
    registries,
):
    workspace = tmp_path / "frozen-input"
    workspace.mkdir()
    clean = workspace / "counter.v"
    testbench = workspace / "tb.v"
    clean.write_text(CLEAN_RTL, encoding="utf-8")
    testbench.write_text(TESTBENCH, encoding="utf-8")
    with pytest.raises(
        GroundedRedExecutionViolation,
        match="testbench differs",
    ):
        execute_grounded_icarus_admission(
            plan=comparator_plan,
            policy=policy,
            registries=registries,
            clean_rtl_path=clean,
            testbench_path=testbench,
            top_module="tb",
            workspace=workspace,
            run_context_hash=hash_payload({"run": "frozen-input"}),
            frozen_clean_rtl_hash=hash_file(clean),
            frozen_testbench_hash=hash_payload({"wrong": "testbench"}),
            allowed_file_manifest_hash=hash_payload({
                "allowed_changed_files": ["materialized/poison.v"],
            }),
        )
    assert not (workspace / "command_artifacts").exists()


@pytest.mark.skipif(not HAS_ICARUS, reason="Icarus Verilog is unavailable")
def test_compile_failure_is_reconstructable_and_never_functional_bug(
    tmp_path,
    comparator_plan,
    policy,
    registries,
):
    workspace = tmp_path / "compile-failure"
    workspace.mkdir()
    clean = workspace / "counter.v"
    testbench = workspace / "tb.v"
    clean.write_text(CLEAN_RTL, encoding="utf-8")
    testbench.write_text(
        TESTBENCH.replace("endmodule\n", "", 1),
        encoding="utf-8",
    )
    bundle = execute_grounded_icarus_admission(
        plan=comparator_plan,
        policy=policy,
        registries=registries,
        clean_rtl_path=clean,
        testbench_path=testbench,
        top_module="tb",
        workspace=workspace,
        run_context_hash=hash_payload({"run": "compile-failure"}),
        frozen_clean_rtl_hash=hash_file(clean),
        frozen_testbench_hash=hash_file(testbench),
        allowed_file_manifest_hash=hash_payload({
            "allowed_changed_files": ["materialized/poison.v"],
        }),
    )
    decision = bundle["admission_decision"]
    assert not decision["admitted"]
    assert not decision["checks"]["G3_clean_baseline"]
    assert not decision["checks"]["G4_poison_executability"]
    assert not decision["checks"]["G5_functional_failure"]
    assert verify_grounded_execution_bundle(
        bundle,
        policy=policy,
        registries=registries,
    )["admission_decision"] == decision


def test_grounded_red_execution_milestone_is_reconstructable():
    milestone = read_json(
        ROOT / "configs/evolution/grounded_red_execution_v1.json"
    )
    parent = read_json(
        ROOT
        / "configs/evolution/grounded_red_protocol_foundation_v1.json"
    )
    assert milestone["status"] == "frozen"
    assert milestone["milestone_hash"] == hash_payload({
        key: value for key, value in milestone.items()
        if key != "milestone_hash"
    })
    assert (
        milestone["parent_milestone"]["milestone_hash"]
        == parent["milestone_hash"]
    )
    for relative, expected in milestone["frozen_assets"].items():
        assert hash_file(ROOT / relative) == expected


@pytest.mark.skipif(not HAS_ICARUS, reason="Icarus Verilog is unavailable")
def test_real_provider_receipt_tampering_is_rejected(
    tmp_path,
    comparator_plan,
    policy,
    registries,
):
    workspace = tmp_path / "tamper"
    workspace.mkdir()
    clean = workspace / "counter.v"
    testbench = workspace / "tb.v"
    clean.write_text(CLEAN_RTL, encoding="utf-8")
    testbench.write_text(TESTBENCH, encoding="utf-8")
    bundle = execute_grounded_icarus_admission(
        plan=comparator_plan,
        policy=policy,
        registries=registries,
        clean_rtl_path=clean,
        testbench_path=testbench,
        top_module="tb",
        workspace=workspace,
        run_context_hash=hash_payload({"run": "tamper"}),
        frozen_clean_rtl_hash=hash_file(clean),
        frozen_testbench_hash=hash_file(testbench),
        allowed_file_manifest_hash=hash_payload({
            "allowed_changed_files": ["materialized/poison.v"],
        }),
    )
    evidence = deepcopy(bundle["evidence"])
    evidence["poison_runs"][0]["observed"]["oracle_provider_receipt"][
        "result"
    ]["oracle_pass"] = True
    evidence["evidence_hash"] = hash_payload({
        key: value for key, value in evidence.items()
        if key != "evidence_hash"
    })
    with pytest.raises(
        (GroundedAdmissionViolation, RuntimeError), match="receipt hash"
    ):
        decide_grounded_admission(
            plan=comparator_plan,
            policy=policy,
            registries=registries,
            evidence=evidence,
        )


@pytest.mark.skipif(not HAS_ICARUS, reason="Icarus Verilog is unavailable")
def test_bundle_rejects_self_consistent_outer_materialization_tamper(
    tmp_path,
    comparator_plan,
    policy,
    registries,
):
    workspace = tmp_path / "materialization-tamper"
    workspace.mkdir()
    clean = workspace / "counter.v"
    testbench = workspace / "tb.v"
    clean.write_text(CLEAN_RTL, encoding="utf-8")
    testbench.write_text(TESTBENCH, encoding="utf-8")
    bundle = execute_grounded_icarus_admission(
        plan=comparator_plan,
        policy=policy,
        registries=registries,
        clean_rtl_path=clean,
        testbench_path=testbench,
        top_module="tb",
        workspace=workspace,
        run_context_hash=hash_payload({"run": "materialization-tamper"}),
        frozen_clean_rtl_hash=hash_file(clean),
        frozen_testbench_hash=hash_file(testbench),
        allowed_file_manifest_hash=hash_payload({
            "allowed_changed_files": ["materialized/poison.v"],
        }),
    )
    tampered = deepcopy(bundle)
    receipt = tampered["materialization_receipt"]
    receipt["new_operator"] = "!="
    receipt["materialization_hash"] = hash_payload({
        key: value for key, value in receipt.items()
        if key != "materialization_hash"
    })
    tampered["bundle_hash"] = hash_payload({
        key: value for key, value in tampered.items()
        if key != "bundle_hash"
    })
    with pytest.raises(
        (GroundedRedExecutionViolation, AstMaterializationViolation),
        match="contract|cross-bound",
    ):
        verify_grounded_execution_bundle(
            tampered,
            policy=policy,
            registries=registries,
        )


def test_materialization_and_provider_receipts_reject_tampering(
    comparator_plan,
):
    materialized = materialize_comparator(
        comparator_plan,
        clean_source=CLEAN_RTL,
    )
    tampered_materialization = deepcopy(materialized.receipt)
    tampered_materialization["ast_edit_count"] = 2
    with pytest.raises(AstMaterializationViolation):
        verify_materialization_receipt(tampered_materialization)
    tampered_provider = deepcopy(
        materialized.parser_provider_receipt
    )
    tampered_provider["provider_implementation_hash"] = hash_payload({
        "forged": True,
    })
    tampered_provider["provider_hash"] = hash_payload({
        "provider_kind": tampered_provider["provider_kind"],
        "provider_id": tampered_provider["provider_id"],
        "provider_version": tampered_provider["provider_version"],
        "provider_implementation_hash": tampered_provider[
            "provider_implementation_hash"
        ],
    })
    tampered_provider["receipt_hash"] = hash_payload({
        key: value for key, value in tampered_provider.items()
        if key != "receipt_hash"
    })
    with pytest.raises(
        GroundedProviderViolation, match="implementation hash"
    ):
        verify_provider_receipt(tampered_provider)


@pytest.mark.skipif(not HAS_ICARUS, reason="Icarus Verilog is unavailable")
def test_icarus_provider_fails_closed_on_compile_and_oracle_errors(tmp_path):
    workspace = tmp_path / "provider-fail-closed"
    workspace.mkdir()
    invalid_rtl = workspace / "invalid.v"
    invalid_rtl.write_text(
        "module broken(input wire a) assign x = a; endmodule\n",
        encoding="utf-8",
    )
    silent_tb = workspace / "silent_tb.v"
    silent_tb.write_text(
        "module tb; initial begin $display(\"no oracle\"); $finish; end endmodule\n",
        encoding="utf-8",
    )
    provider = IcarusGroundedProvider(
        workspace=workspace,
        run_context_hash=hash_payload({"run": "fail-closed"}),
    )
    with pytest.raises(IcarusProviderViolation, match="unsafe path"):
        provider.execute(
            receipt_prefix="../../escape",
            mode="clean_baseline",
            rtl_path=invalid_rtl,
            testbench_path=silent_tb,
            top_module="tb",
            semantic_hash=hash_payload({"semantic": "invalid"}),
        )
    compile_failure = provider.execute(
        receipt_prefix="invalid",
        mode="clean_baseline",
        rtl_path=invalid_rtl,
        testbench_path=silent_tb,
        top_module="tb",
        semantic_hash=hash_payload({"semantic": "invalid"}),
    )
    assert compile_failure["result_kind"] == "tool_error"
    assert not compile_failure["observed"]["oracle_pass"]
    valid_rtl = workspace / "valid.v"
    valid_rtl.write_text(
        "module unused(input wire a, output wire y); assign y = a; endmodule\n",
        encoding="utf-8",
    )
    invalid_oracle = provider.execute(
        receipt_prefix="invalid-oracle",
        mode="clean_baseline",
        rtl_path=valid_rtl,
        testbench_path=silent_tb,
        top_module="tb",
        semantic_hash=hash_payload({"semantic": "valid"}),
    )
    assert invalid_oracle["result_kind"] == "completed"
    assert not invalid_oracle["observed"]["oracle_pass"]
    assert (
        invalid_oracle["observed"]["oracle_provider_receipt"]["result"][
            "effect_signature"
        ]
        == "invalid_oracle_output"
    )
