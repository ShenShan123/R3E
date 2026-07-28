from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil

import pytest

from r3e.grounded.command_runner import GroundedCommandRunner
from r3e.grounded.yosys_formal import (
    YosysFormalProvider,
    YosysFormalProviderViolation,
    formal_triplet_from_assessment,
    verify_formal_execution_receipt,
    verify_formal_proof_assessment,
    verify_formal_proof_triplet,
)
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_file, hash_payload, read_json
from r3e.red.grounded.materializers import (
    AstMaterializationViolation,
    inverse_materialization,
    materialize_operator,
    verify_materialization_receipt,
)
from r3e.red.grounded.mutation_plan import build_mutation_plan
from r3e.red.grounded.operator_ast import operator_nodes
from r3e.red.grounded.registry import load_grounded_registries


ROOT = Path(__file__).resolve().parents[1]
HAS_ICARUS = bool(shutil.which("iverilog"))
HAS_YOSYS = bool(shutil.which("yosys"))

OPERATOR_CASES = [
    (
        "replace_comparator",
        "combinational.comparator_boundary",
        "wrong_combinational_value",
        "comparison_expression",
        """module m(input wire [3:0] a, output wire y);
assign y = (a < 4);
endmodule
""",
    ),
    (
        "negate_predicate",
        "combinational.predicate_polarity",
        "wrong_combinational_value",
        "boolean_predicate",
        """module m(input wire a, input wire b, output reg y);
always @* begin if (a) y = b; else y = 1'b0; end
endmodule
""",
    ),
    (
        "shift_boundary_constant",
        "combinational.comparator_boundary",
        "wrong_combinational_value",
        "constant_boundary",
        """module m(input wire [3:0] a, output wire y);
assign y = (a < 4);
endmodule
""",
    ),
    (
        "shift_slice",
        "datapath.width_truncation",
        "wrong_width_or_extension",
        "part_select",
        """module m(input wire [8:0] data, output wire [3:0] y);
assign y = data[7:4];
endmodule
""",
    ),
    (
        "change_signedness_cast",
        "datapath.signedness_casting",
        "wrong_width_or_extension",
        "typed_expression",
        """module m(input wire [3:0] data, output wire signed [3:0] y);
assign y = $signed(data);
endmodule
""",
    ),
    (
        "change_assignment_kind",
        "sequential.assignment_semantics",
        "state_transition_diverges",
        "procedural_assignment",
        """module m(input wire clk, input wire d, output reg q);
always @(posedge clk) begin q <= d; end
endmodule
""",
    ),
    (
        "change_reset_semantics",
        "sequential.reset_semantics",
        "reset_trace_diverges",
        "reset_signal",
        """module m(input wire clk, input wire rst_n, input wire d, output reg q);
always @(posedge clk or negedge rst_n) begin
  if (!rst_n) q <= 1'b0; else q <= d;
end
endmodule
""",
    ),
    (
        "change_enable_condition",
        "sequential.enable_hold",
        "hold_or_enable_violation",
        "clocked_enable",
        """module m(input wire clk, input wire en, input wire d, output reg q);
always @(posedge clk) begin if (en) q <= d; end
endmodule
""",
    ),
    (
        "change_counter_terminal",
        "sequential.counter_index",
        "terminal_event_shifted",
        "counter_terminal_condition",
        """module m(input wire clk, output reg done);
reg [3:0] count;
always @(posedge clk) begin
  if (count == 7) done <= 1'b1;
  else begin count <= count + 1'b1; done <= 1'b0; end
end
endmodule
""",
    ),
    (
        "change_fsm_transition",
        "control.fsm_transition",
        "state_transition_diverges",
        "fsm_transition",
        """module m(input wire clk, output reg state);
localparam IDLE = 1'b0;
localparam RUN = 1'b1;
always @(posedge clk) begin
  case (state)
    IDLE: state <= RUN;
    RUN: state <= IDLE;
  endcase
end
endmodule
""",
    ),
]


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


def _plan(
    *,
    policy,
    registries,
    operator_id,
    family_id,
    effect_id,
    precondition,
    source,
):
    node = operator_nodes(
        source, module="m", operator_id=operator_id
    )[0]
    return build_mutation_plan(
        plan_id=f"MP_GRD2_{operator_id}",
        policy=policy,
        registries=registries,
        target_design="grd2_conformance",
        target_module="m",
        target_ast_node_hash=node.node_hash,
        family_id=family_id,
        operator_id=operator_id,
        expected_runtime_effect_id=effect_id,
        preconditions={precondition: True},
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


@pytest.mark.parametrize(
    (
        "operator_id",
        "family_id",
        "effect_id",
        "precondition",
        "source",
    ),
    OPERATOR_CASES,
)
def test_all_grd2_operators_dispatch_and_inverse_exactly(
    tmp_path,
    policy,
    registries,
    operator_id,
    family_id,
    effect_id,
    precondition,
    source,
):
    plan = _plan(
        policy=policy,
        registries=registries,
        operator_id=operator_id,
        family_id=family_id,
        effect_id=effect_id,
        precondition=precondition,
        source=source,
    )
    materialized = materialize_operator(plan, clean_source=source)
    receipt = verify_materialization_receipt(materialized.receipt)
    assert receipt["operator_id"] == operator_id
    assert receipt["ast_edit_count"] == 1
    assert materialized.semantic_diff_receipt["operator_id"] == operator_id
    assert (
        materialized.parser_provider_receipt["provider_kind"]
        == "semantic_parser"
    )
    restored = inverse_materialization(
        receipt, poison_source=materialized.poison_source
    )
    assert restored == source

    stale = deepcopy(plan)
    stale["target_ast_node_hash"] = hash_payload({"stale": operator_id})
    stale["plan_hash"] = hash_payload({
        key: value for key, value in stale.items() if key != "plan_hash"
    })
    with pytest.raises(AstMaterializationViolation, match="missing"):
        materialize_operator(stale, clean_source=source)

    tampered = deepcopy(receipt)
    if tampered["schema_version"].endswith("-v1"):
        tampered["new_operator"] = "=="
    else:
        tampered["edit"]["new_text"] += " "
    with pytest.raises(AstMaterializationViolation):
        verify_materialization_receipt(tampered)

    if HAS_ICARUS:
        workspace = tmp_path / "syntax"
        workspace.mkdir()
        clean_path = workspace / "clean.sv"
        poison_path = workspace / "poison.sv"
        clean_path.write_text(source, encoding="utf-8")
        poison_path.write_text(
            materialized.poison_source, encoding="utf-8"
        )
        iverilog = shutil.which("iverilog")
        assert iverilog
        runner = GroundedCommandRunner(
            allowed_root=workspace,
            artifact_root=workspace / "receipts",
            allowed_executables={"iverilog": iverilog},
            run_context_hash=hash_payload({"operator": operator_id}),
            toolchain_fingerprint_hash=hash_payload({
                "iverilog": hash_file(iverilog)
            }),
        )
        for label, path in (
            ("clean", clean_path),
            ("poison", poison_path),
        ):
            result = runner.run(
                receipt_id=f"{operator_id}:{label}",
                phase="compile",
                subject_hash=hash_file(path),
                executable="iverilog",
                arguments=["-g2012", "-tnull", str(path)],
                cwd=workspace,
                artifact_paths={"rtl": path},
            )
            assert result.receipt["result_kind"] == "completed"


@pytest.mark.skipif(not HAS_YOSYS, reason="Yosys is unavailable")
def test_yosys_formal_provider_builds_exact_proof_triplet(tmp_path):
    workspace = tmp_path / "formal"
    workspace.mkdir()
    clean = workspace / "clean.v"
    poison = workspace / "poison.v"
    revert = workspace / "revert.v"
    property_file = workspace / "property.v"
    clean_source = """module counter(
  input wire [3:0] count,
  output wire done
);
assign done = count < 4;
endmodule
"""
    clean.write_text(clean_source, encoding="utf-8")
    poison.write_text(
        clean_source.replace("count < 4", "count <= 4"),
        encoding="utf-8",
    )
    revert.write_text(clean_source, encoding="utf-8")
    property_file.write_text(
        """module formal_top;
  (* anyconst *) reg [3:0] count;
  wire done;
  counter dut(.count(count), .done(done));
  always @* assert(done == (count < 4));
endmodule
""",
        encoding="utf-8",
    )
    provider = YosysFormalProvider(
        workspace=workspace,
        run_context_hash=hash_payload({"formal": "triplet"}),
    )
    assessment = provider.execute_proof_assessment(
        receipt_prefix="MP_FORMAL_001",
        clean_rtl_path=clean,
        poison_rtl_path=poison,
        revert_rtl_path=revert,
        property_path=property_file,
        top_module="formal_top",
        depth=1,
        frozen_clean_rtl_hash=hash_file(clean),
        frozen_poison_rtl_hash=hash_file(poison),
        frozen_revert_rtl_hash=hash_file(revert),
        frozen_property_hash=hash_file(property_file),
    )
    assert assessment["proof_satisfied"]
    assert assessment["rejection_reasons"] == []
    assert verify_formal_proof_assessment(assessment) == assessment
    triplet = formal_triplet_from_assessment(assessment)
    verified = verify_formal_proof_triplet(triplet)
    assert verified["clean"]["verdict"] == "proved"
    assert verified["poison"]["verdict"] == "counterexample"
    assert verified["poison"]["counterexample"]
    assert verified["revert"]["verdict"] == "proved"

    tampered = deepcopy(triplet["poison"])
    tampered["verdict"] = "proved"
    with pytest.raises(YosysFormalProviderViolation):
        verify_formal_execution_receipt(tampered)


@pytest.mark.skipif(not HAS_YOSYS, reason="Yosys is unavailable")
def test_yosys_formal_provider_fails_closed(tmp_path):
    workspace = tmp_path / "formal"
    workspace.mkdir()
    rtl = workspace / "rtl.v"
    property_file = workspace / "property.v"
    rtl.write_text(
        "module counter(input wire a, output wire y); assign y=a; endmodule\n",
        encoding="utf-8",
    )
    property_file.write_text(
        "module formal_top; syntax is invalid; endmodule\n",
        encoding="utf-8",
    )
    provider = YosysFormalProvider(
        workspace=workspace,
        run_context_hash=hash_payload({"formal": "negative"}),
    )
    with pytest.raises(
        YosysFormalProviderViolation, match="frozen manifest"
    ):
        provider.execute(
            receipt_prefix="hash-mismatch",
            mode="clean_proof",
            rtl_path=rtl,
            property_path=property_file,
            top_module="formal_top",
            depth=1,
            frozen_rtl_hash=hash_file(rtl),
            frozen_property_hash=hash_payload({"wrong": "property"}),
        )
    receipt = provider.execute(
        receipt_prefix="malformed-property",
        mode="clean_proof",
        rtl_path=rtl,
        property_path=property_file,
        top_module="formal_top",
        depth=1,
        frozen_rtl_hash=hash_file(rtl),
        frozen_property_hash=hash_file(property_file),
    )
    assert receipt["verdict"] == "inconclusive"
    assert receipt["command_receipt"]["result_kind"] == "tool_error"
    assert not receipt["counterexample"]


@pytest.mark.skipif(not HAS_YOSYS, reason="Yosys is unavailable")
def test_yosys_formal_assessment_preserves_inconclusive_triplet(
    tmp_path,
):
    workspace = tmp_path / "formal-assessment"
    workspace.mkdir()
    clean = workspace / "clean.v"
    poison = workspace / "poison.v"
    revert = workspace / "revert.v"
    property_file = workspace / "property.v"
    clean.write_text(
        "module m(input wire a, output wire y); assign y=a; endmodule\n",
        encoding="utf-8",
    )
    poison.write_text(
        "module m(input wire a, output wire y); assign y=~a; endmodule\n",
        encoding="utf-8",
    )
    revert.write_text(clean.read_text(encoding="utf-8"), encoding="utf-8")
    property_file.write_text(
        "module formal_top; syntax is invalid; endmodule\n",
        encoding="utf-8",
    )
    provider = YosysFormalProvider(
        workspace=workspace,
        run_context_hash=hash_payload({"formal": "assessment"}),
    )
    assessment = provider.execute_proof_assessment(
        receipt_prefix="MP_FORMAL_REJECT_001",
        clean_rtl_path=clean,
        poison_rtl_path=poison,
        revert_rtl_path=revert,
        property_path=property_file,
        top_module="formal_top",
        depth=1,
        frozen_clean_rtl_hash=hash_file(clean),
        frozen_poison_rtl_hash=hash_file(poison),
        frozen_revert_rtl_hash=hash_file(revert),
        frozen_property_hash=hash_file(property_file),
    )
    assert not assessment["proof_satisfied"]
    assert assessment["rejection_reasons"] == [
        "F1_clean_not_proved",
        "F2_poison_counterexample_not_proved",
        "F3_revert_not_proved",
    ]
    assert all(
        assessment[key]["verdict"] == "inconclusive"
        for key in ("clean", "poison", "revert")
    )
    assert verify_formal_proof_assessment(assessment) == assessment
    with pytest.raises(
        YosysFormalProviderViolation,
        match="does not satisfy",
    ):
        formal_triplet_from_assessment(assessment)


def test_grd2_formal_milestone_is_reconstructable():
    milestone = read_json(
        ROOT
        / "configs/evolution/grounded_red_grd2_formal_v1.json"
    )
    parent = read_json(
        ROOT / "configs/evolution/grounded_red_execution_v1.json"
    )
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
