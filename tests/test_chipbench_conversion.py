from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.convert_chipbench import (
    COMPILE_REPAIR_CASES, INELIGIBLE_CASES, STIMULATION_NAME, STIMULUS_NAME,
    extract_buggy, extract_spec, inject_trace, plan_conversion, rename_reference, run_native,
    stimulus_columns, trace_columns, validate, validate_spec, write_conversion,
)
from r3e.knowledge.feedback import compare_traces
from r3e.loop.corpus import Carrier, load_public_manifest
from r3e.loop.sim import Simulator


def test_extracts_after_marker_and_handles_plain_or_verilog_fences():
    for fence in ("```", "```verilog"):
        prompt = ("Example:\n```verilog\nmodule Decoy; endmodule\n```\n"
                  "The code below has bug, please fix that:\n" + fence
                  + "\nmodule TopModule; endmodule\n```\n")
        assert extract_buggy(prompt) == "module TopModule; endmodule\n"
    with pytest.raises(ValueError, match="exactly one module TopModule"):
        extract_buggy("The code below in TopModule has bug:\n```\n// module TopModule\n```\n")
    with pytest.raises(ValueError, match="one .*marker"):
        extract_buggy(prompt + prompt)


def test_reference_rename_does_not_change_comments_or_identifiers():
    source = ("// module RefModule should not be edited\n"
              "module RefModule(input RefModule_enable);\n"
              "// RefModule reference\nendmodule\nmodule helper; endmodule\n")
    assert rename_reference(source) == source.replace("module RefModule(input", "module TopModule(input")


def test_spec_preserves_target_pattern_and_fsm_prose_without_buggy_code():
    description = ("Implement `TopModule` with `a` and `match`.\n"
                   "Assert match for `8'b0111_0001`.\n"
                   "States are IDLE, PAID and DISPENSE; reset returns to IDLE.")
    prompt = description + "\n\nThe code below in TopModule has bug, please fix that:\n```verilog\nmodule TopModule; wire leaked_bug; endmodule\n```\n"
    spec, audit = extract_spec(prompt)
    assert spec == description + "\n" and audit == []
    assert "leaked_bug" not in spec and "has bug" not in spec


@pytest.mark.parametrize("fence", ["```", "```verilog", "~~~~systemverilog"])
def test_spec_removes_helper_implementations_preserving_surrounding_requirements(fence):
    closing = "~~~~" if fence.startswith("~") else "```"
    prefix = ("Implement a decoder with enable E.\n\n" + fence
              + "\nmodule helper(input E, output Y); assign Y=E; endmodule\n"
              + closing + "\n\nUse it to build a full subtractor with borrow output.\n")
    prompt = prefix + "The code below has bug:\n```\nmodule TopModule; endmodule\n```\n"
    spec, audit = extract_spec(prompt)
    assert "Implement a decoder with enable E." in spec
    assert "Use it to build a full subtractor with borrow output." in spec
    assert "module helper" not in spec and "assign Y" not in spec
    assert len(audit) == 1 and audit[0]["reason"] == "fenced_code_block"
    assert audit[0]["start_line"] == 3 and audit[0]["end_line"] == 5
    assert extract_buggy(prompt) == "module TopModule; endmodule\n"


def test_numeric_parameter_declarations_become_prose_with_values_and_comments():
    prompt = ("Divide by 8.7x.\nparameter M_N = 8'd87;\n"
              "parameter c89 = 8'd24; // 8/9 clock switching point\n"
              "The code below has bug:\n```\nmodule TopModule; endmodule\n```\n")
    spec, audit = extract_spec(prompt)
    assert spec == ("Divide by 8.7x.\n- Parameter `M_N`: `8'd87`.\n"
                    "- Parameter `c89`: `8'd24`. 8/9 clock switching point\n")
    assert [a["reason"] for a in audit] == ["numeric_parameter_to_prose"] * 2


@pytest.mark.parametrize("prefix", [
    "```verilog\nmodule helper; endmodule\n",  # unclosed fence
    "Implement TopModule.\nassign q = a;\n",  # unfenced implementation
    "Implement TopModule.\n    always @(posedge clk) q <= a;\n",
    "Use `q <= a;` for this implementation.\n",  # inline implementation
    "parameter W = $clog2(DEPTH);\n",  # no inferred rewriting of expressions
    "\n",  # absent task specification
])
def test_spec_rejects_unhandled_code_or_missing_prose(prefix):
    prompt = prefix + "The code below has bug:\n```\nmodule TopModule; endmodule\n```\n"
    with pytest.raises(ValueError):
        extract_spec(prompt)


TB = '''`timescale 1ps/1ps
`default_nettype none
module tb;
  reg clk = 0;
  reg rst_n = 1;
  logic [1:0] data_in = 0;
  always #5 clk = ~clk;
  always @(negedge clk) data_in <= data_in + 1;
  logic [2:0] q_dut;
  logic [2:0] q_ref;
  RefModule good1(.clk(clk), .rst_n(rst_n), .data_in(data_in), .q(q_ref));
  TopModule top_module1(.clk, .rst_n(rst_n), .data_in(data_in), .q(q_dut));
  integer errors = 0;
  always @(posedge clk, negedge clk) begin
    if (q_ref !== q_dut) errors = errors + 1;
  end
  initial begin
    repeat (6) @(negedge clk);
    #1 $finish;
  end
  final $display("Mismatches: %0d in 12 samples", errors);
endmodule
'''


def test_output_bus_keeps_bit_order_and_rejects_unhandled_declarations():
    assert trace_columns(TB) == [("q[2]", "q_dut[2]"), ("q[1]", "q_dut[1]"), ("q[0]", "q_dut[0]")]
    ascending = TB.replace("[2:0] q_dut", "[0:2] q_dut")
    assert [c for c, _ in trace_columns(ascending)] == ["q[0]", "q[1]", "q[2]"]
    with pytest.raises(ValueError, match="unsupported/missing"):
        trace_columns(TB.replace("[2:0] q_dut", "[WIDTH-1:0] q_dut"))
    with pytest.raises(ValueError, match="already contains"):
        inject_trace(inject_trace(TB))


@pytest.mark.skipif(not all(shutil.which(t) for t in ("iverilog", "vvp")), reason="Icarus unavailable")
def test_trace_observes_nba_updates_and_r3e_detects_bus_mismatch(tmp_path):
    ref = "module RefModule(input clk, input rst_n, input [1:0] data_in, output reg [2:0] q=0); always @(posedge clk) q <= q+1; endmodule\n"
    (tmp_path / "ref.sv").write_text(ref)
    (tmp_path / "golden.sv").write_text(rename_reference(ref))
    (tmp_path / "buggy.sv").write_text(rename_reference(ref).replace("q+1", "q+2"))
    (tmp_path / "tb_visible.sv").write_text(inject_trace(TB))
    row = {"golden_rtl": "golden.sv", "buggy_rtl": "buggy.sv", "tb_sources": ["ref.sv", "tb_visible.sv"]}
    golden = run_native(row, tmp_path, "golden_rtl", tmp_path / "golden_run", 10, repeat=2)
    buggy = run_native(row, tmp_path, "buggy_rtl", tmp_path / "buggy_run", 10)
    assert golden["ok"] and golden["deterministic"] and golden["mismatches"] == 0
    assert buggy["ok"] and buggy["mismatches"] > 0
    assert golden["trace"].splitlines()[1] == "5,0,0,1"  # same edge, after q <= 1
    divergence = compare_traces(golden["trace"], buggy["trace"]).divergences[0]
    assert divergence.signal == "q" and divergence.width == 3
    assert golden["stimulus_rows"] == golden["trace_rows"]
    assert golden["stimulus"] == buggy["stimulus"]
    stimulus = (tmp_path / "golden_run" / STIMULUS_NAME).read_text()
    assert stimulus == (tmp_path / "golden_run" / STIMULATION_NAME).read_text()
    lines = [line for line in stimulus.splitlines() if not line.startswith("#")]
    assert lines[:3] == ["time,clk,rst_n,data_in", "5,1,1,00", "10,0,1,01"]
    carrier = Carrier(carrier_id="test", cluster_id="test", top_module="TopModule",
                      clean_rtl=(tmp_path / "golden.sv").read_text(),
                      visible_tb=(tmp_path / "ref.sv", tmp_path / "tb_visible.sv"))
    simulator = Simulator(tmp_path / "runner")
    verdict = simulator.verdict((tmp_path / "buggy.sv").read_text(), carrier)
    assert verdict.tier == "visible_fail" and verdict.window["inputs_logged"]
    for row in verdict.window["rows"]:
        # The timing-aware evidence API may represent the clock as an edge
        # label rather than an ordinary input. The CSV must still log it.
        if "edge" in row:
            assert row["edge"] in {"rising", "falling"}
            assert set(row["inputs"]) == {"rst_n", "data_in"}
        else:
            assert set(row["inputs"]) == {"clk", "rst_n", "data_in"}


def test_stimulus_connection_widths_and_unsupported_input_are_checked():
    assert stimulus_columns(TB) == [("clk", "clk", 1), ("rst_n", "rst_n", 1), ("data_in", "data_in", 2)]
    with pytest.raises(ValueError, match="unsupported DUT connection"):
        stimulus_columns(TB.replace(".data_in(data_in), .q(q_dut)", ".data_in(data_in[0]), .q(q_dut)"))
    with pytest.raises(ValueError, match="unsupported/missing TB input"):
        stimulus_columns(TB.replace("logic [1:0] data_in = 0;", "logic [WIDTH-1:0] data_in;"))


SOURCE = Path(__file__).resolve().parents[1] / "datasets/external/chipbench"


@pytest.mark.skipif(not SOURCE.is_dir(), reason="raw ChipBench checkout unavailable")
def test_full_manifest_keeps_exact_exclusions_and_compile_repair(tmp_path):
    import json

    rows, files = plan_conversion(SOURCE, tmp_path)
    assert len(rows) == 89
    assert {r["top_module"] for r in rows} == {"TopModule"}
    excluded = {r["case_id"] for r in rows if not r["eligible"]}
    assert excluded == set(INELIGIBLE_CASES) and len(excluded) == 21
    assert sum(r["eligible"] for r in rows) == 68
    conflict_designs = {"Prob007_data_accumulation_output", "Prob033_traffic_lights",
                        "Prob006_data_serial-to-parallel_circuit"}
    conflicts = [r for r in rows if r["case_id"].split(":")[-1] in conflict_designs]
    assert len(conflicts) == 10
    assert all(not r["eligible"] and r["ineligible_reason"].startswith("reference_spec_conflict:")
               for r in conflicts)
    assert all(r.get("ineligible_reason") for r in rows if not r["eligible"])
    assert all(r["eligible"] for r in rows if r["case_id"] in COMPILE_REPAIR_CASES)
    for row in rows:
        assert row["spec"].endswith("/spec.txt")
        spec = files[tmp_path / row["spec"]].decode()
        validate_spec(spec)
        assert "has bug, please fix" not in spec
    manifest = tmp_path / "datasets/manifests/chipbench89.jsonl"
    files[manifest] = "".join(json.dumps(r) + "\n" for r in rows).encode()
    write_conversion(files, overwrite=False)
    carriers, challenges = load_public_manifest(manifest, tmp_path)
    assert len(carriers) == len(challenges) == 68
    assert not excluded.intersection(c.challenge_id for c in challenges)


@pytest.mark.skipif(not SOURCE.is_dir() or not all(shutil.which(t) for t in ("iverilog", "vvp")),
                    reason="raw ChipBench/Icarus unavailable")
def test_compile_repair_qualifies_with_golden_inputs_and_compile_failure(tmp_path):
    import json

    rows, files = plan_conversion(SOURCE, tmp_path)
    row = next(r for r in rows if r["case_id"] in COMPILE_REPAIR_CASES)
    paths = {tmp_path / p for p in [row["golden_rtl"], row["buggy_rtl"], row["spec"], *row["tb_sources"]]}
    files = {p: data for p, data in files.items() if p in paths}
    manifest = tmp_path / "test.jsonl"
    files[manifest] = (json.dumps(row) + "\n").encode()
    write_conversion(files, overwrite=False)
    report = validate([row], tmp_path, manifest, 10)
    assert report["passed"] == report["eligible"] == report["input_logs_verified"] == 1
    case = report["cases"][0]
    assert case["task_type"] == "compile_repair"
    assert case["runner_verdicts"] == {"golden": "visible_pass", "buggy": "compile_fail"}
    assert case["buggy"]["expected_compile_failure"]
    assert not case["evidence_inputs_logged"]  # compile failure has no functional window


def test_collision_is_checked_before_writing_other_outputs(tmp_path):
    existing = tmp_path / "existing.sv"
    existing.write_bytes(b"user modification")
    new = tmp_path / "new.sv"
    with pytest.raises(ValueError, match="refusing to replace"):
        write_conversion({new: b"new", existing: b"different"}, overwrite=False)
    assert not new.exists()
    assert existing.read_bytes() == b"user modification"


def test_incomplete_source_fails_before_generating_outputs(tmp_path):
    with pytest.raises(ValueError, match="expected 30 prompts"):
        plan_conversion(tmp_path / "missing_source", tmp_path / "output")
    assert not (tmp_path / "output").exists()


# These witnesses demonstrate why matching a reference against itself is not
# specification validation. A changed reference must trigger a fresh review.
SPEC_CONFLICT_WITNESSES = {
    'Prob007_data_accumulation_output': '''
reg clk=0,rst_n=0,valid_a=0,ready_b=1; reg [7:0] data_in=0;
wire ready_a,valid_b; wire [9:0] data_out;
TopModule dut(.*); always #5 clk=~clk;
task send(input [7:0] v); begin
  @(negedge clk); data_in=v; valid_a=1; @(posedge clk); #1;
end endtask
initial begin
  #12; rst_n=1;
  send(1); send(2); send(3); send(4);
  if(data_out !== 10 || valid_b !== 1) $fatal(1,"first group failed");
  @(negedge clk); valid_a=0; @(posedge clk); #1;
  @(negedge clk); ready_b=0;
  send(5); send(6); send(7); send(8);
  if(data_out !== 36 || valid_b !== 1) $fatal(1,"reference changed; review exclusion");
  $display("SPEC_CONFLICT: second group is 36, expected 26"); $finish;
end
''',
    'Prob033_traffic_lights': '''
reg clk=0,rst_n=0,pass_request=0;
wire [7:0] clock; wire red,yellow,green;
TopModule dut(.*); always #5 clk=~clk;
initial begin
  #6;
  if(red !== 0 || clock !== 10) $fatal(1,"reference changed; review exclusion");
  $display("SPEC_CONFLICT: reset red is 0, expected 1"); $finish;
end
''',
    'Prob006_data_serial-to-parallel_circuit': '''
reg clk=0,rst_n=0,valid_a=0,data_a=1;
wire ready_a,valid_b; wire [5:0] data_b;
TopModule dut(.*); always #5 clk=~clk;
initial begin
  #12; rst_n=1;
  @(negedge clk); valid_a=1;
  repeat(5) begin @(posedge clk); #1; end
  @(negedge clk); valid_a=0;
  @(posedge clk); #1;
  if(valid_b !== 1 || data_b !== 0) $fatal(1,"reference changed; review exclusion");
  $display("SPEC_CONFLICT: valid after only five bits, with stale data");
  @(negedge clk); valid_a=1;
  @(posedge clk); #1;
  if(valid_b !== 1 || data_b !== 6'b111111) $fatal(1,"sixth input failed");
  $finish;
end
''',
}


@pytest.mark.skipif(not SOURCE.is_dir() or not all(shutil.which(t) for t in ('iverilog', 'vvp')),
                    reason='raw ChipBench/Icarus unavailable')
@pytest.mark.parametrize('case_id', [cid for cid, reason in INELIGIBLE_CASES.items()
                                    if reason.startswith('reference_spec_conflict:')])
def test_reference_spec_conflicts_have_behavioral_witnesses(tmp_path, case_id):
    _, kind, problem = case_id.split(':')
    source = SOURCE / 'Verilog Debugging' / f'dataset_debug_zero_shot_{kind}' / f'{problem}_ref.sv'
    (tmp_path / 'golden.sv').write_text(rename_reference(source.read_text()))
    (tmp_path / 'tb.sv').write_text('`timescale 1ns/1ps\nmodule tb;\n'
                                   + SPEC_CONFLICT_WITNESSES[problem] + '\nendmodule\n')
    subprocess.run(['iverilog', '-g2012', '-s', 'tb', '-o', 'a.out', 'golden.sv', 'tb.sv'],
                   cwd=tmp_path, check=True, capture_output=True, text=True, timeout=10)
    run = subprocess.run(['vvp', 'a.out'], cwd=tmp_path, check=True,
                         capture_output=True, text=True, timeout=10)
    assert 'SPEC_CONFLICT:' in run.stdout
