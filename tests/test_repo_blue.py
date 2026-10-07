from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from r3e.loop import repo_blue as rb
from r3e.loop.budget import BudgetedClient
from r3e.loop.env import build_client
from r3e.loop.fakes import _reply
from r3e.providers.openai_compatible import OpenAICompatibleProviderViolation

needs_verilator = pytest.mark.skipif(not shutil.which("verilator"), reason="Verilator is unavailable")

TOP = """`include "defs.svh"
`include "check.svh"
module top(input logic clk, input logic rst, output logic [3:0] q);
  always_ff @(posedge clk) if (rst) q <= 4'd0; else q <= q + `STEP;
endmodule
"""
MAIN = r"""#include "Vtop.h"
#include "verilated.h"
#include <cstdio>
int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  Vtop* t = new Vtop;
  t->rst = 1; t->clk = 0;
  for (int i = 0; i < 4; i++) { t->clk = !t->clk; t->eval(); }
  t->rst = 0;
  for (int i = 0; i < 10; i++) { t->clk = !t->clk; t->eval(); }
  if (t->q != 5) { printf("%s %d\n", "FAIL_TEXT", t->q); return 1; }
  printf("SIM PASS\n"); delete t; return 0;
}
"""


def make_task(root: Path, *, fail_text="ERROR: expected 5 got", tool="verilator") -> Path:
    pub = root / "public"
    (pub / "repo" / "rtl").mkdir(parents=True)
    (pub / "repo" / "dv").mkdir()
    (pub / "repo" / "rtl" / "top.sv").write_text(TOP)
    (pub / "repo" / "rtl" / "defs.svh").write_text("`define STEP 4'd2\n")   # the bug: the step should be 1
    (pub / "repo" / "rtl" / "unused.sv").write_text("module unused(); endmodule\n")
    (pub / "repo" / "dv" / "check.svh").write_text("`define CHECKS_ON 1\n")  # test support, read-only
    (pub / "visible").mkdir()
    (pub / "visible" / "sim_main.cpp").write_text(MAIN.replace("FAIL_TEXT", fail_text))
    (pub / "visible" / "build.json").write_text(json.dumps({
        "tool": tool, "binary": "Vtop", "pass_marker": "SIM PASS", "fail_marker": "ERROR: expected",
        "source_files": ["sim_main.cpp"],
        "argv": ["-sv", "--cc", "--exe", "--build", "--top-module", "top", "--Mdir", "{harness}/obj_dir",
                 "-DSYNTHESIS", "-Irtl", "-Idv", "-Wno-fatal", "rtl/top.sv", "{harness}/sim_main.cpp"]}))
    (pub / "spec.txt").write_text("After reset q counts up by one on every rising clock edge.")
    (pub / "task.json").write_text(json.dumps({"task_id": "synthetic", "source_root": "repo", "spec": "spec.txt",
                                               "visible_test": "visible/build.json"}))
    (root / "oracle").mkdir()
    (root / "oracle" / "fix.patch").write_text("ORACLE_SECRET_123 -`define STEP 4'd2 +`define STEP 4'd1")
    (root / "validation").mkdir()
    (root / "validation" / "reference.sim.log").write_text("REFLOG_SECRET_456")
    (root / "manifest.json").write_text('{"note": "MANIFEST_SECRET_789"}')
    return root


class WordCounter:  # deterministic stand-in for the pinned tokenizer
    identity = {"tokenizer": "words"}

    def count(self, text):
        return len(text.split())

    def request(self, messages):
        return sum(self.count(m["content"]) for m in messages) + 52


def scripted(replies):
    sent = []

    def transport(**request):
        sent.append({"system": request["messages"][0]["content"], "user": json.loads(request["messages"][-1]["content"])})
        r = replies[len(sent) - 1]
        if isinstance(r, Exception):
            raise r
        if r == "NO_ANSWER":
            return {"content": "", "finish_reason": "length", "input_tokens": 10, "output_tokens": 32768}
        return _reply(r, len(sent))
    return transport, sent


def runner(transport, cfg=rb.RepoBlueConfig(), max_calls=20, archive=None):
    import tempfile
    client = BudgetedClient(build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=transport), max_calls=max_calls)
    archive = archive or Path(tempfile.mkdtemp(prefix="r3e_archive_"))
    return rb.RepoBlueRunner(client=client, counter=WordCounter(), cfg=cfg, archive=archive), client


FIX = {"edits": [{"file": "rtl/defs.svh", "find": "`define STEP 4'd2", "replace": "`define STEP 4'd1"}],
       "edit": "step of one"}
WRONG = {"edits": [{"file": "rtl/top.sv", "find": "q + `STEP", "replace": "q + `STEP + 4'd0"}], "edit": "no-op"}


def test_load_reads_only_public_files(tmp_path):
    task = rb.RepoTask.load(make_task(tmp_path))
    assert task.read_files and all(f.startswith("public/") for f in task.read_files)
    bad = tmp_path / "bad"
    make_task(bad)
    (bad / "public" / "task.json").write_text(json.dumps({"task_id": "x", "source_root": "repo", "spec": "spec.txt",
                                                           "visible_test": "../oracle/fix.patch"}))
    with pytest.raises(rb.RepoTaskError, match="outside public"):
        rb.RepoTask.load(bad)


@needs_verilator
def test_closure_comes_from_what_the_build_reads_and_the_route_is_locked(tmp_path):
    task = rb.RepoTask.load(make_task(tmp_path))
    s = rb.setup(task, WordCounter(), rb.RepoBlueConfig())
    assert s.baseline.tier == "visible_fail" and s.baseline.evidence["fail_marker_found"]
    # headers reached only through -I are in; files the build never reads are not
    assert {"rtl/top.sv", "rtl/defs.svh", "dv/check.svh"} <= set(s.repo_files) and "rtl/unused.sv" not in s.repo_files
    assert rb.editable("rtl/defs.svh") and not rb.editable("dv/check.svh") and not rb.editable("harness:sim_main.cpp")
    assert s.route == "direct" and s.rtl_tokens == sum(s.file_tokens.values())
    assert rb.setup(task, WordCounter(), rb.RepoBlueConfig(rtl_budget=5)).route == "select"


@needs_verilator
def test_results_are_classified_and_tool_failures_are_not_blue_failures(tmp_path):
    task = rb.RepoTask.load(make_task(tmp_path / "a"))
    fixed = {"rtl/defs.svh": "`define STEP 4'd1\n"}
    assert rb.execute(task, fixed).tier == "visible_pass"
    assert rb.execute(task, {"rtl/defs.svh": "`define STEP 4'd1\nmodule broken(\n"}).tier == "compile_fail"
    assert rb.execute(task, {}, sim_timeout=1e-6).tier == "timeout"  # the runner killed it
    tb_timeout = rb.RepoTask.load(make_task(tmp_path / "b", fail_text="TB_FAIL timeout"))
    run = rb.execute(tb_timeout, {})  # the test itself reports a timeout: a functional failure
    assert run.tier == "visible_fail" and not run.evidence["pass_marker_found"]
    assert rb.execute(rb.RepoTask.load(make_task(tmp_path / "c", tool="no_such_tool")), {}).tier == "infra_error"


def test_edit_batches_are_atomic_and_confined_to_editable_closure_rtl():
    state = {"rtl/top.sv": "a b c\n", "rtl/defs.svh": "x y\n", "dv/check.svh": "z\n"}
    allowed = {"rtl/top.sv", "rtl/defs.svh"}
    new, why = rb.apply_batch(state, [{"file": "rtl/top.sv", "find": "b", "replace": "B"},
                                      {"file": "rtl/defs.svh", "find": "nope", "replace": ""}], allowed)
    assert new is None and "edit 1 (rtl/defs.svh): find text not found" in why  # nothing applied
    for path in ("../oracle/fix.patch", "./rtl/top.sv", "/abs/top.sv", "rtl/../rtl/top.sv", "dv/check.svh",
                 "harness:sim_main.cpp", "rtl/unused.sv"):
        new, why = rb.apply_batch(state, [{"file": path, "content": "x"}], allowed)
        assert new is None, path
    new, why = rb.apply_batch(state, [{"file": "rtl/top.sv", "find": "b", "replace": "B"},
                                      {"file": "rtl/defs.svh", "content": "w\n"}], allowed)
    assert why is None and new["rtl/top.sv"] == "a B c\n" and new["rtl/defs.svh"] == "w\n"


@needs_verilator
def test_direct_route_carries_the_candidate_across_attempts_and_never_leaks_evaluator_files(tmp_path):
    root = make_task(tmp_path)
    task = rb.RepoTask.load(root)
    s = rb.setup(task, WordCounter(), rb.RepoBlueConfig())
    transport, sent = scripted([WRONG, FIX])
    r, client = runner(transport)
    out = r.run(task, s, seed=1)
    assert out["outcome"] == "fixed" and [a["tier"] for a in out["attempts"]] == ["visible_fail", "visible_pass"]
    assert out["calls"] == client.total_calls == 2
    # attempt 2 saw its own earlier edit in the file contents, and the result of attempt 1
    second = sent[1]["user"]
    assert "q + `STEP + 4'd0" in second["files"]["rtl/top.sv"]["content"]
    assert second["previous_attempts"][0]["result"] == "visible_fail"
    assert second["files"]["dv/check.svh"]["editable"] is False
    blob = json.dumps(sent)
    for secret in ("ORACLE_SECRET_123", "REFLOG_SECRET_456", "MANIFEST_SECRET_789"):
        assert secret not in blob
    assert all(f.startswith("public/") for f in out["inputs_read"])
    assert all("json" in m["system"].lower() for m in sent)


@needs_verilator
def test_select_route_checks_selections_and_an_invalid_one_uses_up_its_attempt(tmp_path):
    task = rb.RepoTask.load(make_task(tmp_path))
    cfg = rb.RepoBlueConfig(rtl_budget=20)
    s = rb.setup(task, WordCounter(), cfg)
    assert s.route == "select" and s.rtl_tokens > 20
    pick = lambda *paths: {"files": [{"path": p, "reason": "needed"} for p in paths]}
    transport, sent = scripted([pick("rtl/nowhere.sv"),                   # attempt 0: not in the closure
                                pick("rtl/defs.svh", "rtl/defs.svh"),     # attempt 1: duplicate
                                pick("rtl/defs.svh"), FIX])               # attempt 2: valid, then the repair
    r, client = runner(transport, cfg)
    out = r.run(task, s, seed=3)
    tiers = [a["tier"] for a in out["attempts"]]
    assert tiers == ["invalid_selection", "invalid_selection", "visible_pass"] and out["outcome"] == "fixed"
    assert client.total_calls == 4 == out["calls"]  # two spent selections, then select + repair
    assert "repository_map" in sent[0]["user"] and "files" not in sent[0]["user"]
    assert list(sent[3]["user"]["files"]) == ["rtl/defs.svh"]  # only what was selected is shown
    checker = rb.RepoBlueRunner(client=None, counter=WordCounter(), cfg=rb.RepoBlueConfig(rtl_budget=20),
                                archive=tmp_path / "chk")
    grown = {"rtl/top.sv": "w " * 1000, "rtl/defs.svh": "x"}  # budgets use the current content, not the baseline
    assert "over the budget" in checker._check_selection({"result": pick("rtl/top.sv")}, grown)[1]
    assert checker._check_selection({"result": pick("rtl/top.sv")}, {"rtl/top.sv": "w " * 5})[1] is None
    assert checker._check_selection({"result": ["not", "an", "object"]}, grown)[1]  # malformed answer: invalid
    assert cfg.worst_case_calls("select") == 8 and cfg.worst_case_calls("direct") == 5


@needs_verilator
def test_no_answer_retries_and_oversized_requests(tmp_path):
    task = rb.RepoTask.load(make_task(tmp_path))
    s = rb.setup(task, WordCounter(), rb.RepoBlueConfig())
    transport, _ = scripted(["NO_ANSWER", WRONG, OpenAICompatibleProviderViolation("down"), FIX])
    r, client = runner(transport)
    out = r.run(task, s, seed=5)
    assert [a.get("tier") or "infra" for a in out["attempts"]] == ["no_answer", "visible_fail", "infra", "visible_pass"]
    assert out["outcome"] == "fixed" and client.total_calls == 4
    down = OpenAICompatibleProviderViolation("down")
    transport, _ = scripted([down, down, down])
    out = runner(transport)[0].run(task, s, seed=6)
    assert out["outcome"] == "inconclusive"
    transport, sent = scripted([FIX])
    out = runner(transport, rb.RepoBlueConfig(context_tokens=100))[0].run(task, s, seed=7)
    assert out["outcome"] == "request_too_large" and not sent  # never sent


def test_pinned_tokenizer_matches_its_hash():
    from r3e.loop.token_count import REQUEST_OVERHEAD, TokenCounter, default_tokenizer_path
    if not default_tokenizer_path().exists():
        pytest.skip("pinned tokenizer file not present")
    pytest.importorskip("tokenizers")
    c = TokenCounter()
    assert c.request([{"content": "hello"}]) == c.count("hello") + REQUEST_OVERHEAD



@needs_verilator
def test_configuration_errors_stop_the_run_instead_of_becoming_failed_repairs(tmp_path):
    task = rb.RepoTask.load(make_task(tmp_path))
    s = rb.setup(task, WordCounter(), rb.RepoBlueConfig())
    transport, _ = scripted([ValueError("bad configuration")] * 3)
    with pytest.raises(ValueError, match="bad configuration"):
        runner(transport)[0].run(task, s, seed=1)


@needs_verilator
def test_every_applied_candidate_is_archived_before_its_build(tmp_path):
    task = rb.RepoTask.load(make_task(tmp_path / "t"))
    s = rb.setup(task, WordCounter(), rb.RepoBlueConfig())
    transport, _ = scripted([WRONG, FIX])
    client = BudgetedClient(build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=transport), max_calls=10)
    out = rb.RepoBlueRunner(client=client, counter=WordCounter(), archive=tmp_path / "arch").run(task, s, seed=1)
    saved = {json.loads(f.read_text())["candidate_hash"]: json.loads(f.read_text())
             for f in (tmp_path / "arch").glob("*.json")}
    for a in out["attempts"]:
        rec = saved[a["candidate_hash"]]
        assert set(rec["files"]) == set(a["changed_files"]) and rec["patch"].startswith("--- a/")
        assert rec["baseline_hashes"] == {f: s.baseline_hashes[f] for f in rec["files"]}
    assert out["final_candidate_hash"] == out["attempts"][-1]["candidate_hash"]
    # an encounter that ends early still leaves its current candidate archived
    transport, _ = scripted([WRONG, OpenAICompatibleProviderViolation("x")] + [OpenAICompatibleProviderViolation("x")] * 3)
    client = BudgetedClient(build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=transport), max_calls=10)
    out = rb.RepoBlueRunner(client=client, counter=WordCounter(), archive=tmp_path / "arch2").run(task, s, seed=2)
    assert out["outcome"] == "inconclusive" and out["final_candidate_hash"]
    assert any(json.loads(f.read_text())["candidate_hash"] == out["final_candidate_hash"]
               for f in (tmp_path / "arch2").glob("*.json"))


@needs_verilator
def test_calls_of_an_attempt_that_ends_early_are_counted_like_the_ledger(tmp_path):
    task = rb.RepoTask.load(make_task(tmp_path))
    cfg = rb.RepoBlueConfig(rtl_budget=20, context_tokens=33_000)  # selection fits; the repair request will not
    s = rb.setup(task, WordCounter(), cfg)
    big = {"files": [{"path": "rtl/defs.svh", "reason": "r"}]}
    transport, sent = scripted([big])
    r, client = runner(transport, cfg)
    r.counter = type("C", (WordCounter,), {"request": lambda self, m: 40_000 if '"repair"' in m[-1]["content"] else 10})()
    out = r.run(task, s, seed=1)
    assert out["outcome"] == "request_too_large" and len(sent) == 1
    assert out["calls"] == client.total_calls == 1
    assert out["attempts"][0]["selection_tokens"]["counted_request"] == 10
    # provider retries exhausted after a completed selection call: all four calls are counted
    down = OpenAICompatibleProviderViolation("down")
    transport, _ = scripted([big, down, down, down])
    r, client = runner(transport, rb.RepoBlueConfig(rtl_budget=20))
    out = r.run(task, s, seed=2)
    assert out["outcome"] == "inconclusive" and out["calls"] == client.total_calls == 4


def test_a_runner_timeout_kills_the_whole_process_group(tmp_path):
    task_root = make_task(tmp_path / "t")
    tool = tmp_path / "slow_tool.sh"
    pidfile = tmp_path / "child.pid"
    tool.write_text(f"#!/bin/sh\nsleep 60 &\necho $! > {pidfile}\nwait\n")
    tool.chmod(0o755)
    build = json.loads((task_root / "public/visible/build.json").read_text())
    (task_root / "public/visible/build.json").write_text(json.dumps({**build, "tool": str(tool)}))
    run = rb.execute(rb.RepoTask.load(task_root), {}, build_timeout=1)
    assert run.tier == "timeout" and "process group" in run.evidence["message"]
    import os
    import time
    child = int(pidfile.read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)  # the background child of the build tool is gone too


def test_a_simulator_that_cannot_start_is_an_infrastructure_error(tmp_path):
    task_root = make_task(tmp_path / "t")
    tool = tmp_path / "fake_build.sh"  # "builds" a binary that is not executable
    tool.write_text("#!/bin/sh\nfor a in \"$@\"; do case $a in */obj_dir) d=$a;; esac; done\n"
                    "mkdir -p $d && echo junk > $d/Vtop && chmod 644 $d/Vtop\n")
    tool.chmod(0o755)
    build = json.loads((task_root / "public/visible/build.json").read_text())
    (task_root / "public/visible/build.json").write_text(json.dumps({**build, "tool": str(tool)}))
    run = rb.execute(rb.RepoTask.load(task_root), {})
    assert run.tier == "infra_error" and run.stage == "simulation"


@needs_verilator
def test_a_direct_repair_is_not_sent_once_the_current_rtl_exceeds_the_budget(tmp_path):
    task = rb.RepoTask.load(make_task(tmp_path))
    s = rb.setup(task, WordCounter(), rb.RepoBlueConfig(rtl_budget=60))
    assert s.route == "direct"
    grow = {"edits": [{"file": "rtl/defs.svh", "find": "", "replace": "// pad " * 40}], "edit": "grows the file"}
    transport, sent = scripted([grow, FIX])
    r, client = runner(transport, rb.RepoBlueConfig(rtl_budget=60))
    out = r.run(task, s, seed=1)
    assert out["outcome"] == "rtl_context_limit" and len(sent) == 1 == client.total_calls == out["calls"]
    assert out["attempts"][-1]["rtl_tokens_shown"] > 60 and out["attempts"][0]["tier"] == "visible_fail"


def test_the_archive_is_mandatory_and_must_be_writable(tmp_path):
    with pytest.raises(TypeError):
        rb.RepoBlueRunner(client=None, counter=WordCounter())
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(PermissionError):
            rb.RepoBlueRunner(client=None, counter=WordCounter(), archive=locked)
    finally:
        locked.chmod(0o700)
