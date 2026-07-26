from __future__ import annotations

from types import SimpleNamespace

from semantic_repair_bench import csv2tb, oracle_gate


def test_compare_rejects_missing_and_extra_columns():
    golden = "time,a,b\n0,0,1\n"
    ok, reason = oracle_gate._compare(golden, "time,a,b\n0,0\n")
    assert not ok and "candidate_column_count" in reason
    ok, reason = oracle_gate._compare(golden, "time,a,b\n0,0,1,1\n")
    assert not ok and "candidate_column_count" in reason


def test_compare_rejects_extra_candidate_cycle():
    golden = "time,a\n0,0\n"
    candidate = "time,a\n0,0\n1,0\n"
    ok, reason = oracle_gate._compare(golden, candidate)
    assert not ok and "output_extra_cycles" in reason


def test_simulate_rejects_nonzero_vvp_and_stale_output(monkeypatch, tmp_path):
    dut = tmp_path / "dut.v"
    tb = tmp_path / "tb.v"
    dut.write_text("module dut; endmodule\n")
    tb.write_text("module tb; endmodule\n")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "iverilog":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        (kwargs["cwd"] / "out.txt").write_text("time,y\n0,0\n")
        return SimpleNamespace(returncode=2, stdout="", stderr="fatal")

    monkeypatch.setattr(oracle_gate.subprocess, "run", fake_run)
    text, error = oracle_gate._simulate([dut], [tb], "out.txt", tmp_path / "work", 1)
    assert text is None
    assert "sim_err(2)" in error


def test_simulate_rejects_basename_collision(tmp_path):
    first = tmp_path / "a" / "same.v"
    second = tmp_path / "b" / "same.v"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("module a; endmodule\n")
    second.write_text("module b; endmodule\n")
    text, error = oracle_gate._simulate(
        [first, second], [], "out.txt", tmp_path / "work", 1
    )
    assert text is None
    assert "basename_collision" in error


def test_csv_to_tb_rejects_ragged_rows(tmp_path):
    rtl = tmp_path / "top.v"
    csv = tmp_path / "stim.csv"
    rtl.write_text("module top(input a, output y); assign y=a; endmodule\n")
    csv.write_text("a,y\n0\n")
    try:
        csv2tb.gen_tb(rtl, csv)
    except ValueError as exc:
        assert "columns" in str(exc)
    else:
        raise AssertionError("ragged CSV row was accepted")
