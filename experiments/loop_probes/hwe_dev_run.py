"""HWE development run: Blue (memory-free) on repository tasks, a fixed schedule under one call budget.

Scope: development measurement of Blue's baseline difficulty within each task's visible-build scope; no memory,
no evaluation set, no transfer claim.

- **Setup (no calls):** each task's baseline build gives its closure and locked route (``repo_blue.setup``); the
  route and RTL size are part of the frozen configuration.
- **Frozen configuration** (``run_config.json``): task inputs (hash of everything Blue may see, plus per-file
  baseline hashes), routes, runner settings, model settings, tokenizer identity, schedule, the call cap, the
  evaluation materials (oracle patch hashes, verification boundaries) and verification settings, and code hashes.
  A resume with any difference, including a different cap, is refused; ``verify`` and ``report`` recompute and
  compare all of it first, so nothing is judged on inputs or criteria changed after data collection.
- **Schedule:** repeat-major; within a repeat, a seeded task order. Fixed: no stopping or extension based on results.
- **Budget:** one ledger (``calls.jsonl``) for every call. Before an encounter, its worst case
  (``RepoBlueConfig.worst_case_calls``) must fit what is left, else the run stops before it. Unused calls create
  no extra encounters or retries.
- **Journal** (``events.jsonl``): completed encounters are never re-run; an encounter started without completion
  refuses automatic replay.
- **Archive** (``archive/``): every applied candidate, mandatory.

Steps:
  run     the schedule (model calls, cap ``--max-calls``)
  verify  offline supplementary verification of every visible pass (``repo_verify.py``)
  report  per encounter and summary; the evaluator-side file selection coverage for ``select`` tasks
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from r3e.loop import repo_blue as rb
from r3e.loop.budget import BudgetedClient
from r3e.protocol.hashing import hash_payload

CODE = ["r3e/loop/repo_blue.py", "r3e/loop/token_count.py", "r3e/loop/budget.py",
        "experiments/loop_probes/hwe_dev_run.py", "experiments/loop_probes/repo_verify.py",
        "experiments/loop_probes/repair_verify.py"]
VERIFICATION = {"seeds": [11, 23, 37], "cycles_per_seed": 1000, "default_depth": 12}


class ResumeRefused(RuntimeError):
    pass


def task_inputs_hash(task: rb.RepoTask, setup: rb.Setup) -> str:
    return hash_payload({"task": task.task, "spec": task.spec, "build": task.build, "harness": task.harness,
                         "closure": setup.baseline_hashes})


def schedule(task_ids: list[str], repeats: int, seed: int) -> list[dict[str, Any]]:
    out = []
    for rep in range(repeats):
        for tid in sorted(task_ids, key=lambda t: hash_payload({"order": seed, "repeat": rep, "task": t})):
            out.append({"key": f"{rep}:{tid}", "repeat": rep, "task_id": tid,
                        "seed": int(hash_payload({"seed": seed, "repeat": rep, "task": tid})[7:15], 16)})
    return out


def evaluation_materials(task: rb.RepoTask) -> dict[str, Any]:
    """What verification and coverage will judge against: frozen before any call."""
    from experiments.loop_probes.repo_verify import VERSION
    boundaries = json.loads((task.task_dir.parent / "verification_boundaries.json").read_text())
    return {"oracle_fix_patch_hash": hash_payload((task.task_dir / "oracle" / "fix.patch").read_text()),
            "boundary": boundaries[task.task["task_id"]], "verifier": VERSION, **VERIFICATION}


def frozen_config(tasks: dict[str, rb.RepoTask], setups: dict[str, rb.Setup], cfg: rb.RepoBlueConfig, *,
                  repeats: int, seed: int, model: dict[str, Any], tokenizer: dict[str, Any], root: Path,
                  max_calls: int) -> dict:
    return {"scope": "development run, memory-free Blue, visible-build scope",
            "tasks": {t: {"inputs_hash": task_inputs_hash(tasks[t], setups[t]), "route": setups[t].route,
                          "rtl_tokens": setups[t].rtl_tokens, "baseline_hashes": setups[t].baseline_hashes,
                          "evaluation": evaluation_materials(tasks[t])} for t in sorted(tasks)},
            "runner": cfg.__dict__, "model": model, "tokenizer": tokenizer, "repeats": repeats, "seed": seed,
            "max_calls": max_calls, "code": {p: hash_payload((root / p).read_text()) for p in CODE}}


def check_frozen(out: Path, task_dirs: dict[str, Path], counter, cfg: rb.RepoBlueConfig, root: Path) -> dict:
    """Recompute task inputs, routes, evaluation materials and code; refuse on any difference."""
    frozen = json.loads((out / "run_config.json").read_text())
    tasks, setups = load([task_dirs[t] for t in frozen["tasks"]], counter, cfg)
    problems = []
    for t, want in frozen["tasks"].items():
        have = {"inputs_hash": task_inputs_hash(tasks[t], setups[t]), "route": setups[t].route,
                "rtl_tokens": setups[t].rtl_tokens, "baseline_hashes": setups[t].baseline_hashes,
                "evaluation": json.loads(json.dumps(evaluation_materials(tasks[t])))}
        problems += [f"{t}: {k} changed" for k in want if want[k] != have[k]]
    problems += [f"code changed: {p}" for p, h in frozen["code"].items() if hash_payload((root / p).read_text()) != h]
    if problems:
        raise ResumeRefused("inputs or criteria differ from the frozen run: " + "; ".join(problems))
    return frozen


def execute(*, out: Path, tasks: dict[str, rb.RepoTask], setups: dict[str, rb.Setup], cfg: rb.RepoBlueConfig,
            client_factory: Callable[[], Any], counter, max_calls: int, repeats: int, seed: int,
            frozen: dict[str, Any]) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    if frozen.get("max_calls") != max_calls:
        raise ResumeRefused("the call cap must be the frozen one")
    cfg_path = out / "run_config.json"
    if cfg_path.exists():
        if json.loads(cfg_path.read_text()).get("max_calls") != max_calls:
            raise ResumeRefused("the call cap must be the frozen one")
        if json.loads(cfg_path.read_text()) != json.loads(json.dumps(frozen)):
            raise ResumeRefused("run configuration differs from the frozen one; start a new directory")
    else:
        cfg_path.write_text(json.dumps(frozen, indent=1, sort_keys=True))
    events = [json.loads(l) for l in (out / "events.jsonl").read_text().splitlines()] if \
        (out / "events.jsonl").exists() else []
    started = {e["encounter"] for e in events if e["event"] == "start"}
    done = {e["encounter"] for e in events if e["event"] == "complete"}
    if started - done:
        raise ResumeRefused(f"encounter(s) {sorted(started - done)} started without completion; review first")
    used = len((out / "calls.jsonl").read_text().splitlines()) if (out / "calls.jsonl").exists() else 0
    budget = BudgetedClient(client_factory(), max_calls=max(max_calls - used, 0))
    calls_log = (out / "calls.jsonl").open("a")
    events_log = (out / "events.jsonl").open("a")
    budget.on_call = lambda e: (calls_log.write(json.dumps(e) + "\n"), calls_log.flush())
    runner = rb.RepoBlueRunner(client=budget, counter=counter, cfg=cfg, archive=out / "archive")
    stopped = None
    try:
        for enc in schedule(sorted(tasks), repeats, seed):
            if enc["key"] in done:
                continue
            need = cfg.worst_case_calls(setups[enc["task_id"]].route)
            if budget.max_calls - budget.total_calls < need:
                stopped = {"reason": "budget_reservation", "next_encounter": enc["key"], "reserve": need,
                           "calls_used": used + budget.total_calls}
                break
            events_log.write(json.dumps({"event": "start", "encounter": enc["key"],
                                         "calls_before": used + budget.total_calls}) + "\n")
            events_log.flush()
            result = runner.run(tasks[enc["task_id"]], setups[enc["task_id"]], seed=enc["seed"])
            with (out / "encounters.jsonl").open("a") as f:
                f.write(json.dumps({**enc, "result": result}) + "\n")
            events_log.write(json.dumps({"event": "complete", "encounter": enc["key"],
                                         "calls_after": used + budget.total_calls}) + "\n")
            events_log.flush()
    finally:
        calls_log.close()
        events_log.close()
    status = {"stopped": stopped, "calls_used": used + budget.total_calls, "max_calls": max_calls}
    (out / "status.json").write_text(json.dumps(status, indent=1))
    return status


def _encounters(out: Path) -> list[dict]:
    path = out / "encounters.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def verify(out: Path, task_dirs: dict[str, Path], counter, cfg: rb.RepoBlueConfig, root: Path) -> list[dict]:
    """Offline supplementary verification of every candidate that passed the visible test (after the freeze check)."""
    from experiments.loop_probes.repo_verify import verify as repo_verify
    frozen = check_frozen(out, task_dirs, counter, cfg, root)
    # keyed by task and candidate: identical content can occur in two tasks
    archive = {(r["task_id"], r["candidate_hash"]): r
               for r in (json.loads(f.read_text()) for f in (out / "archive").glob("*.json"))}
    rows = []
    for enc in _encounters(out):
        for a in enc["result"]["attempts"]:
            if a.get("tier") == "visible_pass":
                rec = archive.get((enc["task_id"], a["candidate_hash"]))
                base = frozen["tasks"][enc["task_id"]]["baseline_hashes"]
                why = ("candidate missing from archive" if rec is None else
                       "archived files do not match the candidate hash" if hash_payload(rec["files"]) != a["candidate_hash"]
                       else "archived baseline hashes differ from the frozen baseline"
                       if any(base.get(f) != h for f, h in rec["baseline_hashes"].items()) else None)
                if why:
                    rows.append({"encounter": enc["key"], "candidate_hash": a["candidate_hash"],
                                 "status": "incomplete_or_not_applicable", "reason": why})
                    continue
                ev = frozen["tasks"][enc["task_id"]]["evaluation"]
                rows.append({"encounter": enc["key"], **repo_verify(
                    task_dirs[enc["task_id"]], rec["files"], a["candidate_hash"], depth=ev["default_depth"],
                    seeds=tuple(ev["seeds"]), cycles=ev["cycles_per_seed"])})
    (out / "verification.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return rows


def _selection_coverage(task_dir: Path, closure: list[str], attempts: list[dict]) -> dict[str, Any]:
    """Evaluator-side share of the oracle patch's closure files Blue selected (never a diagnosis), twice:
    over every requested selection (rejected ones included), and over selections actually submitted to a repair."""
    import re
    patch = (task_dir / "oracle" / "fix.patch").read_text()
    target = {f for f in re.findall(r"(?m)^\+\+\+ b/(\S+)", patch) if f in closure}
    requested = {c["path"] for a in attempts for c in a.get("selection") or []}
    submitted = {c["path"] for a in attempts if a.get("tier") != "invalid_selection" for c in a.get("selection") or []}
    share = lambda chosen: round(len(chosen & target) / len(target), 3) if target else None
    return {"oracle_closure_files": sorted(target),
            "requested_selection_coverage": share(requested), "submitted_selection_coverage": share(submitted),
            "submitted_files": sorted(submitted & target),
            "budget_rejected_selections": sum("over the budget" in str(a.get("feedback") or "") for a in attempts)}


def report(out: Path, task_dirs: dict[str, Path], counter, runner_cfg: rb.RepoBlueConfig, root: Path) -> dict[str, Any]:
    cfg = check_frozen(out, task_dirs, counter, runner_cfg, root)
    encs = _encounters(out)
    verif = {}
    if (out / "verification.jsonl").exists():
        for r in map(json.loads, (out / "verification.jsonl").read_text().splitlines()):
            verif.setdefault(r["encounter"], []).append(r.get("status"))
    planned = len(cfg["tasks"]) * cfg["repeats"]
    calls = [json.loads(l) for l in (out / "calls.jsonl").read_text().splitlines()] if \
        (out / "calls.jsonl").exists() else []
    rows = []
    for e in encs:
        res = e["result"]
        tries = [a for a in res["attempts"] if not a.get("infra_failure")]
        per_call = [x for a in tries for x in (a.get("selection_tokens"), a.get("repair_tokens")) if x]
        row = {"encounter": e["key"], "task": e["task_id"], "route": res["route"], "outcome": res["outcome"],
               "attempts": [a.get("tier") for a in tries], "calls": res["calls"],
               "provider_failures": sum(1 for a in res["attempts"] if a.get("infra_failure")),
               "input_tokens": sum(x.get("input", 0) for x in per_call),
               "output_tokens": sum(x.get("output", 0) for x in per_call),
               "counted_vs_provider_input": [(x.get("counted_request"), x.get("input")) for x in per_call],
               "model_seconds": round(sum(x.get("model_seconds") or 0 for x in per_call), 1),
               "build_sim_seconds": round(sum((a.get("build_seconds") or 0) + (a.get("sim_seconds") or 0)
                                              for a in tries), 1),
               "supplementary_verification": verif.get(e["key"], [])}
        if res["route"] == "select":
            row["file_selection"] = _selection_coverage(task_dirs[e["task_id"]], res["closure"], tries)
        rows.append(row)
    summary = {"encounters_completed": f"{len(encs)} of {planned}", "calls_ledger": len(calls),
               "calls_from_results": sum(r["calls"] for r in rows),
               "outcomes": {o: sum(r["outcome"] == o for r in rows) for o in sorted({r["outcome"] for r in rows})},
               "status": json.loads((out / "status.json").read_text()) if (out / "status.json").exists() else None}
    rep = {"scope": cfg["scope"], "summary": summary, "encounters": rows}
    (out / "report.json").write_text(json.dumps(rep, indent=1))
    return rep


def load(task_dirs: list[Path], counter, cfg: rb.RepoBlueConfig):
    tasks = {}
    setups = {}
    for d in task_dirs:
        t = rb.RepoTask.load(d)
        tasks[t.task["task_id"]] = t
        setups[t.task["task_id"]] = rb.setup(t, counter, cfg)
    return tasks, setups


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("run", "verify", "report"):
        s = sub.add_parser(name)
        s.add_argument("--tasks", nargs="+", type=Path, required=True)
        s.add_argument("--out", type=Path, required=True)
        if name == "run":
            s.add_argument("--llm-env", type=Path, required=True)
            s.add_argument("--model", required=True)
            s.add_argument("--max-calls", type=int, required=True)
            s.add_argument("--repeats", type=int, default=3)
            s.add_argument("--seed", type=int, default=2026)
    args = p.parse_args()
    dirs = {rb.RepoTask.load(d).task["task_id"]: d for d in args.tasks}
    from r3e.loop.token_count import TokenCounter
    counter, cfg = TokenCounter(), rb.RepoBlueConfig()
    root = Path(__file__).resolve().parents[2]
    if args.cmd == "verify":
        print(json.dumps({"verified": len(verify(args.out, dirs, counter, cfg, root))}))
        return 0
    if args.cmd == "report":
        print(json.dumps(report(args.out, dirs, counter, cfg, root)["summary"], indent=1))
        return 0
    from r3e.loop.env import build_client, load_llm_env
    tasks, setups = load(args.tasks, counter, cfg)
    env = load_llm_env(args.llm_env)
    model = {"model": args.model, "thinking": "low", "maximum_output_tokens": cfg.output_reserve,
             "timeout_seconds": 1500}
    factory = lambda: build_client(env, model_override=args.model, thinking="low",
                                   maximum_output_tokens=cfg.output_reserve, timeout_seconds=1500,
                                   failure_dir=args.out / "unparseable")
    frozen = frozen_config(tasks, setups, cfg, repeats=args.repeats, seed=args.seed, model=model,
                           tokenizer=counter.identity, root=root, max_calls=args.max_calls)
    print(json.dumps(execute(out=args.out, tasks=tasks, setups=setups, cfg=cfg, client_factory=factory,
                             counter=counter, max_calls=args.max_calls, repeats=args.repeats, seed=args.seed,
                             frozen=frozen)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
