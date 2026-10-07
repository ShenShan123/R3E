from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from experiments.loop_probes import hwe_dev_run as hd
from r3e.loop import repo_blue as rb
from r3e.loop.env import build_client
from r3e.loop.fakes import _reply
from tests.test_repo_blue import FIX, WordCounter, make_task
from tests.test_repo_verify import FIX_PATCH

needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in ("verilator", "yosys", "iverilog", "patch")),
                                 reason="Verilator, Yosys, Icarus or patch unavailable")
ROOT = Path(__file__).resolve().parents[1]
CFG = rb.RepoBlueConfig()


def two_tasks(tmp_path):
    dirs = {}
    for tid in ("task_a", "task_b"):
        root = make_task(tmp_path / "tasks" / tid)
        meta = json.loads((root / "public" / "task.json").read_text())
        (root / "public" / "task.json").write_text(json.dumps({**meta, "task_id": tid}))
        (root / "oracle" / "fix.patch").write_text(FIX_PATCH)
        (root / "public" / "repo" / "doc").mkdir()
        (root / "public" / "repo" / "doc" / "notes.md").write_text("old\n")
        dirs[tid] = root
    (tmp_path / "tasks" / "verification_boundaries.json").write_text(json.dumps(
        {t: {"top": "top", "clock": "clk", "reset": "rst", "reset_active": 1, "depth": 6} for t in dirs}))
    tasks, setups = hd.load(list(dirs.values()), WordCounter(), CFG)
    return dirs, tasks, setups


def go(tmp_path, tasks, setups, transport, *, max_calls, repeats=2, frozen_extra=None):
    frozen = hd.frozen_config(tasks, setups, CFG, repeats=repeats, seed=1, model={"model": "fake"},
                              tokenizer={"tokenizer": "words"}, root=ROOT, max_calls=max_calls)
    frozen.update(frozen_extra or {})
    return hd.execute(out=tmp_path / "out", tasks=tasks, setups=setups, cfg=CFG, counter=WordCounter(),
                      client_factory=lambda: build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=transport),
                      max_calls=max_calls, repeats=repeats, seed=1, frozen=frozen)


def fixing(**request):
    return _reply(FIX, 1)


@needs_tools
def test_a_full_run_matches_its_ledger_and_every_pass_is_verified(tmp_path):
    dirs, tasks, setups = two_tasks(tmp_path)
    status = go(tmp_path, tasks, setups, fixing, max_calls=60)
    assert status["stopped"] is None and status["calls_used"] == 4
    rows = hd.verify(tmp_path / "out", dirs, WordCounter(), CFG, ROOT)
    assert len(rows) == 4 and {r["status"] for r in rows} == {"no_mismatch_found"}
    rep = hd.report(tmp_path / "out", dirs, WordCounter(), CFG, ROOT)
    assert rep["summary"]["encounters_completed"] == "4 of 4"
    assert rep["summary"]["calls_ledger"] == rep["summary"]["calls_from_results"] == 4
    assert all(r["outcome"] == "fixed" and r["supplementary_verification"] == ["no_mismatch_found"]
               for r in rep["encounters"])
    assert all(r["counted_vs_provider_input"] and r["build_sim_seconds"] > 0 for r in rep["encounters"])


@needs_tools
def test_the_reservation_stops_before_an_encounter_and_the_cap_is_frozen(tmp_path):
    dirs, tasks, setups = two_tasks(tmp_path)
    assert CFG.worst_case_calls("direct") == 5
    status = go(tmp_path, tasks, setups, fixing, max_calls=6)  # 6 -> 5 -> 4 left: two encounters, then stop
    assert status["stopped"]["reason"] == "budget_reservation" and status["calls_used"] == 2
    assert hd.report(tmp_path / "out", dirs, WordCounter(), CFG, ROOT)["summary"]["encounters_completed"] == "2 of 4"
    with pytest.raises(hd.ResumeRefused, match="call cap"):
        go(tmp_path, tasks, setups, fixing, max_calls=60)  # a resume cannot raise the frozen cap
    status = go(tmp_path, tasks, setups, fixing, max_calls=6)  # the same cap: nothing new fits, nothing reruns
    assert status["calls_used"] == 2 and status["stopped"]["reason"] == "budget_reservation"
    with pytest.raises(hd.ResumeRefused, match="configuration differs"):
        go(tmp_path, tasks, setups, fixing, max_calls=6, frozen_extra={"seed": 2})


@needs_tools
def test_a_completed_run_resumes_without_rerunning_anything(tmp_path):
    dirs, tasks, setups = two_tasks(tmp_path)
    go(tmp_path, tasks, setups, fixing, max_calls=60)
    go(tmp_path, tasks, setups, fixing, max_calls=60)
    keys = [json.loads(l)["key"] for l in (tmp_path / "out" / "encounters.jsonl").read_text().splitlines()]
    assert len(keys) == len(set(keys)) == 4
    assert len((tmp_path / "out" / "calls.jsonl").read_text().splitlines()) == 4


@needs_tools
def test_verify_and_report_refuse_changed_inputs_or_criteria(tmp_path):
    dirs, tasks, setups = two_tasks(tmp_path)
    go(tmp_path, tasks, setups, fixing, max_calls=60, repeats=1)
    bounds = tmp_path / "tasks" / "verification_boundaries.json"
    original = bounds.read_text()
    bounds.write_text(original.replace('"depth": 6', '"depth": 2'))  # a criterion changed after collection
    with pytest.raises(hd.ResumeRefused, match="evaluation changed"):
        hd.verify(tmp_path / "out", dirs, WordCounter(), CFG, ROOT)
    with pytest.raises(hd.ResumeRefused, match="evaluation changed"):
        hd.report(tmp_path / "out", dirs, WordCounter(), CFG, ROOT)
    bounds.write_text(original)
    patch = dirs["task_a"] / "oracle" / "fix.patch"
    patch.write_text(patch.read_text() + "\n")  # the reference changed
    with pytest.raises(hd.ResumeRefused, match="task_a: evaluation changed"):
        hd.verify(tmp_path / "out", dirs, WordCounter(), CFG, ROOT)
    patch.write_text(FIX_PATCH)
    # an archived candidate whose baseline hashes differ from the frozen baseline is not verified
    arch = next((tmp_path / "out" / "archive").glob("*.json"))
    rec = json.loads(arch.read_text())
    rec["baseline_hashes"] = {f: "sha256:" + "0" * 64 for f in rec["baseline_hashes"]}
    arch.write_text(json.dumps(rec))
    rows = hd.verify(tmp_path / "out", dirs, WordCounter(), CFG, ROOT)
    assert any(r.get("reason") == "archived baseline hashes differ from the frozen baseline" for r in rows)


@needs_tools
def test_an_interrupted_encounter_refuses_automatic_replay(tmp_path):
    dirs, tasks, setups = two_tasks(tmp_path)

    def crash(**request):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        go(tmp_path, tasks, setups, crash, max_calls=60)
    with pytest.raises(hd.ResumeRefused, match="started without completion"):
        go(tmp_path, tasks, setups, fixing, max_calls=60)


def test_file_selection_coverage_is_computed_against_the_oracle_closure_files(tmp_path):
    root = make_task(tmp_path / "t")
    (root / "oracle" / "fix.patch").write_text(FIX_PATCH)
    rejected = {"tier": "invalid_selection", "selection": [{"path": "rtl/defs.svh"}],
                "feedback": "selected files hold 99 tokens, over the budget"}
    used = {"tier": "visible_fail", "selection": [{"path": "rtl/top.sv"}]}
    cov = hd._selection_coverage(root, ["rtl/top.sv", "rtl/defs.svh"], [rejected, used])
    assert cov["oracle_closure_files"] == ["rtl/defs.svh"]
    assert cov["requested_selection_coverage"] == 1.0   # asked for, but rejected: never read
    assert cov["submitted_selection_coverage"] == 0.0   # what a repair actually saw
    assert cov["budget_rejected_selections"] == 1
