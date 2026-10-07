from __future__ import annotations

import json
import shutil

import pytest

from experiments.loop_probes import repo_verify as rv
from tests.test_repo_blue import make_task

needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in ("verilator", "yosys", "iverilog", "patch")),
                                 reason="Verilator, Yosys, Icarus or patch unavailable")

FIX_PATCH = """diff --git a/rtl/defs.svh b/rtl/defs.svh
--- a/rtl/defs.svh
+++ b/rtl/defs.svh
@@ -1 +1 @@
-`define STEP 4'd2
+`define STEP 4'd1
diff --git a/doc/notes.md b/doc/notes.md
--- a/doc/notes.md
+++ b/doc/notes.md
@@ -1 +1 @@
-old
+new
"""


def _task(tmp_path):
    root = make_task(tmp_path / "tasks" / "synthetic")
    (root / "oracle" / "fix.patch").write_text(FIX_PATCH)
    (root / "public" / "repo" / "doc").mkdir()
    (root / "public" / "repo" / "doc" / "notes.md").write_text("old\n")
    (tmp_path / "tasks" / "verification_boundaries.json").write_text(json.dumps(
        {"synthetic": {"top": "top", "clock": "clk", "reset": "rst", "reset_active": 1}}))
    return root


@needs_tools
@pytest.mark.parametrize("defs,expected", [
    ("`define STEP 4'd1\n", "no_mismatch_found"),          # the reference fix
    ("`define STEP (4'd3 - 4'd2)\n", "no_mismatch_found"),  # a different but equivalent repair
    ("`define STEP 4'd2\n", "mismatch_found"),              # unchanged: the bug remains
])
def test_candidates_are_compared_with_the_reference_at_the_declared_boundary(tmp_path, defs, expected):
    root = _task(tmp_path)
    rec = rv.verify(root, {"rtl/defs.svh": defs}, "sha256:test", depth=8, seeds=(1,), cycles=50)
    assert rec["status"] == expected, rec["methods"]
    assert rec["reference"]["oracle_patch_files_outside_closure"] == ["doc/notes.md"]
    assert any("doc/notes.md" in u for u in rec["uncovered"]) and any("SVA" in u for u in rec["uncovered"])
    eq = next(m for m in rec["methods"] if m["method"] == "bounded_equivalence")
    assert eq["scope"]["steps"] == 8 and eq["scope"]["boundary"] == "top" and eq["scope"]["defines"] == ["SYNTHESIS"]
    if expected == "mismatch_found":
        assert rec["review"].startswith("needed")
        assert next(m for m in rec["methods"] if m["method"] == "random_simulation")["counterexample"]["seed"] == 1


@pytest.mark.parametrize("rc,out,expected", [
    (1, "simulator error", "incomplete"),            # crashed: not a clean run
    (0, "", "incomplete"),                           # ran but never said it finished
    (0, "NO_MISMATCH\n", "no_mismatch"),
    (0, "MISMATCH 7\n", "mismatch"),
    (None, "", "timeout"),
])
def test_a_random_seed_counts_only_when_it_finishes_and_says_so(tmp_path, monkeypatch, rc, out, expected):
    def fake_run(cmd, cwd, timeout):
        if cmd[0] == "yosys":
            (tmp_path / "miter.json").write_text(json.dumps({"modules": {"miter": {"ports": {
                "in_clk": {"direction": "input", "bits": [2]}, "in_rst": {"direction": "input", "bits": [3]},
                "trigger": {"direction": "output", "bits": [4]}}}}}))
            return 0, ""
        if cmd[0] == "iverilog":
            return 0, ""
        return rc, out
    monkeypatch.setattr(rv.rb, "_run", fake_run)
    got = rv.random_sim("script", {"clock": "clk", "reset": "rst", "reset_active": 1}, (1,), 10, tmp_path)
    assert got["outcome"] == expected


def test_no_method_failure_is_masked_by_another_methods_pass():
    ok, gone = {"outcome": "no_mismatch"}, {"outcome": "incomplete"}
    assert rv.summarise([ok, {"outcome": "no_mismatch_within_bound"}]) == "no_mismatch_found"
    assert rv.summarise([ok, gone]) == "no_mismatch_found_partial"
    assert rv.summarise([ok, {"outcome": "not_applicable"}]) == "no_mismatch_found_partial"
    assert rv.summarise([gone, {"outcome": "mismatch"}]) == "mismatch_found"
    assert rv.summarise([gone, {"outcome": "timeout"}]) == "incomplete_or_not_applicable"
