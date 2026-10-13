import json
import shutil

import pytest

from experiments.loop_probes import witness_matrix as wm

needs_tools = pytest.mark.skipif(not (shutil.which("iverilog") and shutil.which("verilator")),
                                 reason="iverilog and verilator are required")

# Toy carrier: the suites only exercise the matrix logic (W_bug reads y, W_preserve reads z).
CLEAN = "module toy(input [3:0] a, output [3:0] y, output [3:0] z); assign y = a + 1; assign z = a; endmodule\n"
ROW_Y = "module toy(input [3:0] a, output [3:0] y, output [3:0] z); assign y = a; assign z = a; endmodule\n"
ROW_Z = "module toy(input [3:0] a, output [3:0] y, output [3:0] z); assign y = a + 1; assign z = 0; endmodule\n"


def tb(check: str) -> str:
    return f"""module tb; reg [3:0] a; wire [3:0] y, z; toy dut(.a(a), .y(y), .z(z));
initial begin a = 5; #1 if ({check}) begin $display("CHECK_FAIL"); $finish; end $display("TESTBENCH PASS"); $finish; end
endmodule
"""


def package(tmp_path, tool="icarus", variants=None, editable=("rtl/toy.v",)):
    pkg = tmp_path / "pkg"
    (pkg / "src" / "rtl").mkdir(parents=True)
    (pkg / "src" / "rtl" / "toy.v").write_text(CLEAN)
    for suite, check in (("W_bug", "y != 6"), ("W_preserve", "z != 5")):
        (pkg / "suites" / suite).mkdir(parents=True)
        (pkg / "suites" / suite / "tb.sv").write_text(tb(check))
    variants = variants or {"clean": ("clean", None), "defect": ("defect", ROW_Y), "shortcut": ("shortcut", ROW_Z)}
    for name, (_, text) in variants.items():
        if text is not None:
            (pkg / "variants" / name / "rtl").mkdir(parents=True)
            (pkg / "variants" / name / "rtl" / "toy.v").write_text(text)
    (pkg / "spec.md").write_text("toy\n")
    (pkg / "carrier.json").write_text(json.dumps({
        "carrier_id": "toy", "tool": tool, "rtl": ["rtl/toy.v"], "editable": list(editable),
        "suites": {s: {"files": ["tb.sv"], "top": "tb"} for s in ("W_bug", "W_preserve")},
        "pass_marker": "TESTBENCH PASS", "fail_marker": "CHECK_FAIL",
        "variants": {n: {"role": r} for n, (r, _) in variants.items()}}))
    return pkg


@needs_tools
@pytest.mark.parametrize("tool", ["icarus", "verilator"])
def test_the_expected_matrix_qualifies(tmp_path, tool):
    rec = wm.qualify(package(tmp_path, tool))
    assert rec["qualified"] and rec["single_file"] and not rec["problems"]
    assert {k: c["tier"] for k, c in rec["matrix"].items()} == {
        "clean/W_bug": "pass", "clean/W_preserve": "pass", "defect/W_bug": "fail", "defect/W_preserve": "pass",
        "shortcut/W_bug": "pass", "shortcut/W_preserve": "fail"}
    assert all(c["deterministic"] for c in rec["matrix"].values())
    assert "variants/defect/rtl/toy.v" in rec["package_files"]


@needs_tools
def test_a_wrong_row_or_a_compile_failure_does_not_qualify(tmp_path):
    rec = wm.qualify(package(tmp_path, variants={
        "clean": ("clean", None), "defect": ("defect", ROW_Z), "shortcut": ("shortcut", "module toy(;\n")}))
    assert not rec["qualified"]
    assert "defect (defect) W_bug: expected fail, got pass" in rec["problems"]
    assert rec["matrix"]["shortcut/W_bug"]["tier"] == "compile_fail"


def test_packages_outside_the_contract_are_refused(tmp_path):
    with pytest.raises(ValueError, match="no overlay"):
        wm.load(package(tmp_path / "a", variants={"clean": ("clean", ROW_Y)}))
    with pytest.raises(ValueError, match="editable scope must be listed"):
        wm.load(package(tmp_path / "b", editable=("rtl/other.v",)))
    rec_missing = wm.qualify(package(tmp_path / "c", variants={"clean": ("clean", None)})) if shutil.which("iverilog") else None
    if rec_missing:
        assert {"no defect variant", "no shortcut variant"} <= set(rec_missing["problems"])
