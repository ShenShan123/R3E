from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from r3e.knowledge.verilog_utils import safe_tokenize
from r3e.loop.carriers import build_carrier, generate_testbench, load_carrier_manifest, Port
from r3e.loop.operators import apply_edits, enumerate_sites
from r3e.loop.sim import Simulator


needs_tools = pytest.mark.skipif(
    not all(shutil.which(n) for n in ("iverilog", "vvp", "yosys")), reason="Icarus/Yosys unavailable"
)

COUNTER = """module RefModule(input clk, input reset, input en, output reg [3:0] q, output wrap);
  assign wrap = (q == 4'd9);
  always @(posedge clk) begin
    if (reset) q <= '0;
    else if (en) q <= (q == 4'd9) ? 4'd0 : q + 1;
  end
endmodule
"""


def test_tolerant_tokenizer_accepts_systemverilog_fill_literals():
    tokens = safe_tokenize(COUNTER)
    assert any(t.value == "'0" for t in tokens)


def test_generated_testbench_drives_wide_inputs_fully():
    tb = generate_testbench(top="T", ports=[Port("in", "input", 70), Port("out", "output", 3)],
                            clock=None, reset=None, seed=5, cycles=4, output_name="t.txt")
    assert "in = {$random(s), $random(s), $random(s)};" in tb
    assert '$fdisplay(f, "time,out[2],out[1],out[0]");' in tb


@needs_tools
def test_build_carrier_gates_and_hidden_tier(tmp_path):
    src = tmp_path / "ref.sv"
    src.write_text(COUNTER, encoding="utf-8")
    row, reason = build_carrier(rtl_path=src, top="RefModule", rename_top="TopModule",
                                dest=tmp_path / "c", cluster="test:counter", source={"dataset": "unit"})
    assert reason == "admitted"
    assert row["clock"] == "clk" and row["reset"] == "reset"
    manifest = tmp_path / "carriers.jsonl"
    manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")
    [carrier] = load_carrier_manifest(manifest)
    assert "module TopModule" in carrier.clean_rtl and carrier.hidden_tb
    sim = Simulator(tmp_path / "sim")
    assert sim.verdict(carrier.clean_rtl, carrier).tier == "hidden_pass"
    site = next(s for s in enumerate_sites(carrier) if s.operator == "compare_swap")
    assert sim.verdict(apply_edits(carrier, [(site, site.options[0])]), carrier).tier in {
        "visible_fail", "visible_pass", "hidden_pass"}

    constant = tmp_path / "const.sv"
    constant.write_text("module RefModule(input a, output y); assign y = 1'b0; endmodule\n", encoding="utf-8")
    row, reason = build_carrier(rtl_path=constant, top="RefModule", rename_top=None,
                                dest=tmp_path / "k", cluster="test:const", source={})
    assert row is None and reason in {"constant_outputs", "too_few_edit_sites"}
