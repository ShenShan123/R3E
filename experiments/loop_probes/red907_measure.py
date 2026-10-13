"""Measurement side of the 907 cross-design experiment: Blue encounters and endpoint classification.

``run_blue`` runs the independent memory-free Blue encounters on one admitted, exported task (``blue_task`` of a
slot), with the shared experiment client. Before any call it checks the export's file hashes and that the
baseline still shows the admission row (W_bug fails, W_preserve passes), and it refuses to start unless the
client can still pay for every encounter's worst case. The output directory must be new; an event journal
marks each encounter's start and completion, so an interrupted encounter is visible and never rerun.

``classify`` runs offline after collection. Per encounter it takes the endpoint candidate (the first public
pass, or the last materialised candidate when attempts run out), replays both public suites and runs the frozen
evaluator suite on it, and assigns exactly one category:

    public_pass_hidden_pass          passes both public suites and the evaluator
    public_pass_hidden_fail          passes both public suites; the evaluator reports a checker violation
                                     (its fail marker) -- review required
    public_pass_hidden_inconclusive  passes both public suites; the evaluator did not reach a verdict
                                     (compile failure, timeout, nonzero exit without a marker, no pass marker)
    public_unrepaired_at_budget      no public pass within the attempts; per-suite results of the final candidate
    replay_mismatch                  the endpoint's public replay disagrees with the tier recorded in collection
    no_candidate                     no candidate was ever materialised (only no-answers / invalid edits)
    infrastructure                   provider or tool failure ended the encounter
    context_limit                    request or shown RTL over the limit (runner limit, not Blue)

Only checker violations count as functional failures. An unrepaired endpoint is *eligible* for target review
when it compiles, passes W_bug and fails W_preserve *with the W_preserve fail marker*; a
``public_pass_hidden_fail`` is eligible too. Inconclusive and replay-mismatch endpoints never are.
Intermediate candidates with the target pattern are reported separately.
Mechanism attribution is human: ``review_items.json`` carries code, logs and violated checks with no arm and
no Red narrative; decisions come back in ``review_decisions.json``. The primary 2-of-3 confirmed target
challenge counts only ``public_unrepaired_at_budget`` endpoints reviewed as the target mechanism.

The separately frozen screening protocol uses one direct attempt without retries.
It reports ``public_unrepaired_after_one_attempt`` and primary signal candidates
requiring blind review; ``confirmed_target_challenge`` is null, never a 2-of-3 claim.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from r3e.loop import repo_blue as rb
from r3e.loop.budget import BudgetedClient
from r3e.protocol.hashing import hash_payload

ENCOUNTERS = 3
CATEGORY = {"fixed": "public_pass", "not_fixed": "public_unrepaired_at_budget",
            "inconclusive": "infrastructure", "inconclusive_infrastructure": "infrastructure",
            "request_too_large": "context_limit", "rtl_context_limit": "context_limit"}


class MeasurementRefused(RuntimeError):
    pass


class DirectContextLimit(MeasurementRefused):
    """A direct-only screening task would require a forbidden selection call."""


def _check_export(task_dir: Path) -> dict[str, Any]:
    exported = json.loads((task_dir / "export.json").read_text())
    changed = [rel for rel, h in exported["public_files"].items() if hash_payload((task_dir / rel).read_text()) != h]
    if changed:
        raise MeasurementRefused(f"exported task changed since export: {changed}")
    return exported


def run_blue(task_dir: Path, out: Path, *, client: BudgetedClient, counter, cfg: rb.RepoBlueConfig = rb.RepoBlueConfig(),
             seed: int, encounters: int = ENCOUNTERS, direct_only: bool = False,
             endpoint_protocol: str = "confirmation", experience: dict | None = None,
             experience_journal: Path | None = None, retrieval_policy_hash: str | None = None) -> list[dict[str, Any]]:
    if experience is not None and (not direct_only or experience.get("retrieval_policy_hash") != retrieval_policy_hash):
        raise MeasurementRefused("experience needs direct-only Blue and the plan's retrieval policy binding")
    if endpoint_protocol not in {"confirmation", "screening"}:
        raise MeasurementRefused("unknown endpoint protocol")
    if endpoint_protocol == "screening" and (encounters != 1 or cfg.budget_k != 1
                                             or cfg.max_infra_retries != 0 or not direct_only):
        raise MeasurementRefused("screening requires one direct attempt and no retries")
    exported = _check_export(task_dir)
    task = rb.RepoTask.load(task_dir)
    s = rb.setup(task, counter, cfg)
    row = {k: v["result"] for k, v in s.baseline.evidence.get("suites", {}).items()}
    if row != {"W_bug": "visible_fail", "W_preserve": "visible_pass"}:
        raise MeasurementRefused(f"baseline no longer shows the admission row: {row}")
    if direct_only and s.route != "direct":
        raise DirectContextLimit(f"direct context limit: {s.rtl_tokens} RTL tokens exceed {cfg.rtl_budget}")
    if experience is not None:
        rb.check_delivery(experience, task, counter)  # refused before any directory or call exists
    need = encounters * cfg.worst_case_calls(s.route)
    if client.max_calls - client.total_calls < need:
        raise MeasurementRefused(f"needs {need} calls reserved, {client.max_calls - client.total_calls} left")
    out.mkdir(parents=True, exist_ok=False)
    (out / "blue_config.json").write_text(json.dumps({
        "task_id": task.task["task_id"], "export": exported, "route": s.route, "rtl_tokens": s.rtl_tokens,
        "baseline_hashes": s.baseline_hashes, "runner": cfg.__dict__, "encounters": encounters, "seed": seed,
        "reserved_calls": need, "direct_only": direct_only, "endpoint_protocol": endpoint_protocol,
        **({"experience_delivery_hash": hash_payload(experience), "experience_snapshot":
            {k: experience[k] for k in ("snapshot_id", "snapshot_hash", "state_hash")}} if experience is not None else {})},
        indent=1))
    runner = rb.RepoBlueRunner(client=client, counter=counter, cfg=cfg, archive=out / "archive")
    results = []
    with (out / "events.jsonl").open("a") as events, (out / "encounters.jsonl").open("a") as log:
        for i in range(encounters):
            events.write(json.dumps({"event": "start", "encounter": i, "calls_before": client.total_calls}) + "\n")
            events.flush()
            result = runner.run(task, s, seed=seed * 10 + i, experience=experience, experience_journal=experience_journal)
            log.write(json.dumps({"encounter": i, "seed": seed * 10 + i, "result": result}) + "\n")
            log.flush()
            events.write(json.dumps({"event": "complete", "encounter": i, "calls_after": client.total_calls}) + "\n")
            events.flush()
            results.append(result)
    return results


# ----------------------------------------------------------------------- offline classification


def _evaluate(task: rb.RepoTask, overlay: dict[str, str], evaluator: dict[str, Any], eval_dir: Path) -> dict[str, Any]:
    run = evaluator["run"]
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        shutil.copytree(task.repo, work / "repo")
        for rel, text in overlay.items():
            (work / "repo" / rel).write_text(text)
        tb = [str(f) for f in sorted(eval_dir.glob("*.sv"))]
        rc, log = rb._run(["iverilog", "-g2012", "-s", run["top"], "-o", str(work / "e.vvp"), *run["rtl"], *tb],
                          work / "repo", 300)
        if rc != 0:
            return {"result": "compile_fail" if rc is not None else "timeout", "log": log[-1500:]}
        rc, log = rb._run(["vvp", "-n", str(work / "e.vvp")], work / "repo", 600)
    failures = [l for l in log.splitlines() if run["fail_marker"] in l]
    result = ("timeout" if rc is None else "fail" if failures else
              "pass" if rc == 0 and run["pass_marker"] in log else
              "unmarked_nonzero_exit" if rc != 0 else "no_pass_marker")
    return {"result": result, "returncode": rc, "failures": failures[:12], "log_hash": hash_payload(log)}


def _public(task: rb.RepoTask, overlay: dict[str, str]) -> dict[str, Any]:
    run = rb.execute(task, overlay)
    marker = task.build["fail_marker"]
    return {"tier": run.tier, "suites": {k: {"result": v["result"], "checker_violation": bool(v.get("fail_marker_found")),
                                             "failures": [l for l in v.get("log", "").splitlines() if marker in l][:6]}
                                         for k, v in run.evidence.get("suites", {}).items()}}


def _target_pattern(public: dict[str, Any]) -> bool:
    """Compiles, W_bug passes, W_preserve fails through its checker (not an unmarked exit or timeout)."""
    s = public["suites"]
    w_preserve = s.get("W_preserve", {})
    return public["tier"] != "compile_fail" and s.get("W_bug", {}).get("result") == "visible_pass" \
        and w_preserve.get("result") == "visible_fail" and w_preserve.get("checker_violation", False)


def _recorded_tier(attempts: list[dict[str, Any]], candidate: str) -> str | None:
    """The tier collection recorded for this candidate (its last build)."""
    return next((a.get("tier") for a in reversed(attempts) if a.get("candidate_hash") == candidate), None)


def classify(blue_dir: Path, task_dir: Path, evaluator_freeze: dict[str, Any], carriers_root: Path) -> dict[str, Any]:
    """Endpoint categories for every encounter of one admitted mutant (no model calls)."""
    changed = [r for r, h in evaluator_freeze["files"].items() if hash_payload((carriers_root / r).read_text()) != h]
    if changed:
        raise MeasurementRefused(f"evaluator changed since its freeze: {changed}")
    eval_dir = carriers_root / Path(next(iter(evaluator_freeze["files"]))).parent
    _check_export(task_dir)
    config = json.loads((blue_dir / "blue_config.json").read_text())
    protocol = config.get("endpoint_protocol", "confirmation")
    screening = protocol == "screening"
    if protocol not in {"confirmation", "screening"}:
        raise MeasurementRefused("unknown endpoint protocol")
    if screening and (config["encounters"] != 1 or config["runner"]["budget_k"] != 1
                      or config["runner"]["max_infra_retries"] != 0 or not config.get("direct_only")):
        raise MeasurementRefused("screening configuration mismatch")
    task = rb.RepoTask.load(task_dir)
    archive = {r["candidate_hash"]: r for r in (json.loads(f.read_text()) for f in (blue_dir / "archive").glob("*.json"))}
    events = [json.loads(l) for l in (blue_dir / "events.jsonl").read_text().splitlines()]
    interrupted = sorted({e["encounter"] for e in events if e["event"] == "start"} -
                         {e["encounter"] for e in events if e["event"] == "complete"})
    rows, items = [], []
    for line in (blue_dir / "encounters.jsonl").read_text().splitlines():
        enc = json.loads(line)
        res = enc["result"]
        endpoint = res["final_candidate_hash"]
        category = CATEGORY[res["outcome"]]
        row: dict[str, Any] = {"encounter": enc["encounter"], "outcome": res["outcome"], "calls": res["calls"],
                               "attempt_tiers": [a.get("tier", "infra") for a in res["attempts"]],
                               "endpoint_candidate": endpoint}
        if category == "public_unrepaired_at_budget" and endpoint is None:
            category = "no_candidate"
        if endpoint is not None and category in ("public_pass", "public_unrepaired_at_budget"):
            files = archive[endpoint]["files"]
            row["endpoint_public"] = _public(task, files)
            row["endpoint_evaluator"] = _evaluate(task, files, evaluator_freeze, eval_dir)
            row["recorded_tier"] = _recorded_tier(res["attempts"], endpoint)
            evaluated = row["endpoint_evaluator"]["result"]
            if row["endpoint_public"]["tier"] != row["recorded_tier"]:
                category = "replay_mismatch"
            elif category == "public_pass":
                category = {"pass": "public_pass_hidden_pass", "fail": "public_pass_hidden_fail"}.get(
                    evaluated, "public_pass_hidden_inconclusive")
            row["target_review_eligible"] = (category == "public_pass_hidden_fail" or
                                             (category == "public_unrepaired_at_budget" and _target_pattern(row["endpoint_public"])))
        if screening and category == "public_unrepaired_at_budget":
            category = "public_unrepaired_after_one_attempt"
        row["category"] = category
        if screening:
            row["primary_signal_candidate"] = (category == "public_unrepaired_after_one_attempt"
                                               and bool(row.get("target_review_eligible")))
        row["intermediate_target_pattern"] = []
        for a in res["attempts"]:
            h = a.get("candidate_hash")
            if h and h != endpoint and h in archive and _target_pattern(_public(task, archive[h]["files"])):
                row["intermediate_target_pattern"].append({"attempt": a["attempt"], "candidate": h})
        if row.get("target_review_eligible"):
            items.append({"item": hash_payload([task.task["task_id"], enc["encounter"], endpoint])[7:23],
                          "task": task.task["task_id"], "encounter": enc["encounter"], "category": category,
                          "patch_vs_task": archive[endpoint]["patch"], "public": row["endpoint_public"],
                          "evaluator": row["endpoint_evaluator"]})
        rows.append(row)
    decisions_path = blue_dir / "review_decisions.json"
    decisions = json.loads(decisions_path.read_text()) if decisions_path.exists() else {}
    for row, item in ((r, i) for r in rows for i in items if i["encounter"] == r["encounter"]):
        row["review"] = decisions.get(item["item"], {"decision": "PENDING"})
    confirmed = sum(r["category"] == "public_unrepaired_at_budget" and r.get("review", {}).get("decision") == "TARGET"
                    for r in rows)
    summary = {"task_id": task.task["task_id"], "encounters": len(rows), "interrupted": interrupted,
               "endpoint_protocol": protocol,
               "categories": {c: sum(r["category"] == c for r in rows) for c in sorted({r["category"] for r in rows})},
               "target_review_eligible": sum(bool(r.get("target_review_eligible")) for r in rows),
               "pending_review": sum(r.get("review", {}).get("decision") == "PENDING" for r in rows),
               "confirmed_public_target_endpoints": confirmed,
               "confirmed_target_challenge": confirmed >= 2,
               "intermediate_target_patterns": sum(len(r["intermediate_target_pattern"]) for r in rows)}
    if screening:
        if len(rows) > 1:
            raise MeasurementRefused("screening contains more than one encounter")
        summary.pop("confirmed_public_target_endpoints")
        summary["confirmed_target_challenge"] = None  # 2-of-3 is not defined for this protocol
        summary["primary_signal_candidates"] = sum(r["primary_signal_candidate"] for r in rows)
        summary["reviewed_primary_signals"] = sum(r["primary_signal_candidate"]
            and r.get("review", {}).get("decision") == "TARGET" for r in rows)
    (blue_dir / "review_items.json").write_text(json.dumps(items, indent=1))  # masked: no arm, no Red material
    out = {"summary": summary, "encounters": rows}
    (blue_dir / "classification.json").write_text(json.dumps(out, indent=1))
    return out
