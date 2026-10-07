"""Part B: does frozen, ungated case memory help Blue on frozen, unseen designs? (paired fork runner)

Unit = one (bug, repeat). A shared first attempt is made without memory; if it passes,
the unit passes in both arms. If it fails, two branches continue from that same attempt
(``reuse_first``), without and with memory, each with up to ``budget_k - 1`` more
attempts. Memory is offered from attempt 2, as in the loop.

Freeze and restore (nothing is trusted from the files' own declarations):
- the evaluation set's bug, reference and specification hashes are recomputed from the
  manifest, and the manifest's own hash is checked;
- every memory item's hash is recomputed from its content (``KnowledgeItem.from_dict``),
  and the snapshot hash must equal ``--snapshot-hash`` (fixed in the protocol);
- the run settings, inputs and code hashes are frozen in ``run_config.json``; a resume
  with any difference is refused.

Order: repeat-major; a seeded bug order per repeat; each fork's branch order alternates
by a seeded hash of (bug, repeat). One call ledger (``calls.jsonl``) covers the shared
attempt, both branches and every provider retry. Before each unit, the runner reserves
the unit's worst case (shared ``1 + R`` plus two branches of ``budget_k - 1 + R``, where
``R`` = provider failures tolerated per encounter); a unit is never started without that
room. ``events.jsonl`` journals unit start and completion: completed units are never
re-run, and a unit started but not completed refuses automatic replay.

Usage:
  python -m experiments.loop_probes.memory_eval run --plan-dir P --out OUT --snapshot-hash H
      --llm-env FILE --model M --max-calls 200 [--repeats 5]
  python -m experiments.loop_probes.memory_eval verify --plan-dir P --out OUT --snapshot-hash H
  python -m experiments.loop_probes.memory_eval report --out OUT

``verify`` checks every candidate that passed the visible test (shared first attempts and
both branches) against the reference with ``repair_verify.py``'s methods, offline, and
writes ``verification.jsonl``; ``report`` then lists the supplementary results per arm,
separately from the visible-test results. A mismatch needs review before it is attributed
to Blue.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from r3e.knowledge import BugTypeInference, KnowledgeMatcher
from r3e.knowledge.schema import KnowledgeItem
from r3e.loop.blue import BlueConfig, BlueRunner, first_attempt
from r3e.loop.budget import BudgetedClient
from r3e.loop.carriers import REPO_ROOT
from r3e.loop.corpus import load_public_manifest
from r3e.loop.red_repair_feedback import PublicCandidateRecorder
from r3e.loop.sim import Simulator
from r3e.protocol.hashing import hash_payload

PASSING = {"visible_pass", "hidden_pass", "formal_pass"}
MATCHER = {"threshold": 0.6, "top_k": 3}
ARMS = ("without_memory", "with_memory")


class ResumeRefused(RuntimeError):
    pass


def load_frozen(plan_dir: Path, snapshot_hash: str):
    """(bugs, items): recomputed and checked, never taken on the files' word."""
    plan = json.loads((plan_dir / "eval_set.json").read_text())
    manifest = REPO_ROOT / plan["manifest"]
    if hash_payload(manifest.read_text()) != plan["manifest_sha256"]:
        raise ResumeRefused("evaluation manifest changed since the set was frozen")
    _, challenges = load_public_manifest(manifest, REPO_ROOT)
    by_id = {c.challenge_id: c for c in challenges}
    bugs = []
    for row in plan["bugs"]:
        ch = by_id.get(row["challenge_id"])
        if ch is None or (ch.buggy_hash, hash_payload(ch.carrier.clean_rtl), hash_payload(ch.carrier.spec)) != \
                (row["buggy_rtl_hash"], row["reference_hash"], row["spec_hash"]):
            raise ResumeRefused(f"evaluation bug {row['challenge_id']} does not match its frozen hashes")
        bugs.append(ch)
    snap = json.loads((plan_dir / "memory_primary.json").read_text())
    items = [KnowledgeItem.from_dict(i["item"]) for i in snap["items"]]  # recomputes each item hash
    if hash_payload([i.item_hash for i in items]) != snapshot_hash:
        raise ResumeRefused("memory snapshot hash differs from the protocol's")
    if snap.get("matcher") != {"weights": KnowledgeMatcher().config()["weights"], **MATCHER}:
        raise ResumeRefused("snapshot matcher configuration differs from the protocol's")
    return bugs, items


def worst_case(cfg: BlueConfig) -> int:
    r = cfg.max_infra_retries
    return (1 + r) + 2 * ((cfg.budget_k - 1) + r)


def schedule(bugs, repeats: int, seed: int):
    units = []
    for rep in range(repeats):
        order = sorted(bugs, key=lambda b: hash_payload({"order": seed, "repeat": rep, "bug": b.challenge_id}))
        for b in order:
            flip = int(hash_payload({"arms": seed, "repeat": rep, "bug": b.challenge_id})[-1], 16) % 2
            units.append({"key": f"{rep}:{b.challenge_id}", "repeat": rep, "bug": b,
                          "arm_order": ARMS[::-1] if flip else ARMS,
                          "seed": int(hash_payload({"seed": seed, "repeat": rep, "bug": b.challenge_id})[7:15], 16)})
    return units


def _arm(enc) -> dict[str, Any]:
    tries = [a for a in enc.attempts if not a.get("infra_failure")]
    return {"solved": enc.solved_within_budget, "inconclusive": enc.inconclusive,
            "tiers": [a.get("verdict_tier") for a in tries],
            "no_answer": sum(a.get("verdict_tier") == "no_answer" for a in tries if not a.get("reused")),
            "wrong_repair": sum(a.get("verdict_tier") in ("visible_fail", "compile_fail")
                                for a in tries if not a.get("reused")),
            "passing_candidates": [a["candidate_hash"] for a in tries if a.get("verdict_tier") in PASSING
                                   and a.get("candidate_hash") and not a.get("reused")],
            # actual delivery: memory injected into a request, not merely offered or retrieved
            "memory_injected": any(d.get("injected") for d in enc.deliveries),
            "accounting": enc.accounting()}


def run_unit(unit, *, first_runner, branch_runner, items) -> dict[str, Any]:
    bug, seed = unit["bug"], unit["seed"]
    kw = dict(inference=BugTypeInference(), matcher=KnowledgeMatcher(**MATCHER), seed=seed,
              allow_escalation=False, phase="evaluation")
    shared = first_runner.run(bug, mode="none", pool=[], **kw)
    out = {"unit": unit["key"], "repeat": unit["repeat"], "challenge_id": bug.challenge_id,
           "problem": bug.carrier.cluster_id, "seed": seed, "arm_order": list(unit["arm_order"]),
           "shared_first": _arm(shared)}
    first = first_attempt(shared)
    if shared.inconclusive or first is None:
        out["outcome"] = "inconclusive_first_attempt"
        return out
    if first.get("verdict_tier") in PASSING:
        out["outcome"] = "first_attempt_passed"
        return out
    out["outcome"] = "forked"
    out["first_attempt_tier"] = first.get("verdict_tier")
    for arm in unit["arm_order"]:
        enc = branch_runner.run(bug, mode="matched" if arm == "with_memory" else "none",
                                pool=list(items) if arm == "with_memory" else [], reuse_first=first, **kw)
        out[arm] = _arm(enc)
    return out


def execute(*, out: Path, bugs, items, client_factory: Callable[[], Any], max_calls: int, repeats: int,
            seed: int, cfg: BlueConfig, frozen: dict[str, Any]) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    cfg_path = out / "run_config.json"
    if cfg_path.exists():
        if json.loads(cfg_path.read_text()) != frozen:
            raise ResumeRefused("run configuration differs from the frozen one; start a new directory")
    else:
        cfg_path.write_text(json.dumps(frozen, indent=1, sort_keys=True))
    events = [json.loads(l) for l in (out / "events.jsonl").read_text().splitlines()] if \
        (out / "events.jsonl").exists() else []
    started = {e["unit"] for e in events if e["event"] == "start"}
    done = {e["unit"] for e in events if e["event"] == "complete"}
    if started - done:
        raise ResumeRefused(f"unit(s) {sorted(started - done)} started without completion; review before resuming")
    used = len((out / "calls.jsonl").read_text().splitlines()) if (out / "calls.jsonl").exists() else 0
    budget = BudgetedClient(client_factory(), max_calls=max(max_calls - used, 0))
    calls_log = (out / "calls.jsonl").open("a")
    events_log = (out / "events.jsonl").open("a")
    budget.on_call = lambda e: (calls_log.write(json.dumps(e) + "\n"), calls_log.flush())
    sim = PublicCandidateRecorder(Simulator(out / "sim"), out)  # keeps every candidate for verification
    first_runner = BlueRunner(json_client=budget, simulator=sim, project_root=REPO_ROOT,
                              config=BlueConfig(**{**cfg.__dict__, "budget_k": 1}))
    branch_runner = BlueRunner(json_client=budget, simulator=sim, project_root=REPO_ROOT, config=cfg)
    reserve, stopped = worst_case(cfg), None
    try:
        for unit in schedule(bugs, repeats, seed):
            if unit["key"] in done:
                continue
            if budget.max_calls - budget.total_calls < reserve:
                stopped = {"reason": "budget_reservation", "next_unit": unit["key"],
                           "calls_used": used + budget.total_calls, "reserve": reserve}
                break
            events_log.write(json.dumps({"event": "start", "unit": unit["key"],
                                         "calls_before": used + budget.total_calls}) + "\n")
            events_log.flush()
            result = run_unit(unit, first_runner=first_runner, branch_runner=branch_runner, items=items)
            with (out / "units.jsonl").open("a") as f:
                f.write(json.dumps(result) + "\n")
            events_log.write(json.dumps({"event": "complete", "unit": unit["key"],
                                         "calls_after": used + budget.total_calls}) + "\n")
            events_log.flush()
    finally:
        calls_log.close()
        events_log.close()
    status = {"stopped": stopped, "calls_used": used + budget.total_calls, "max_calls": max_calls}
    (out / "status.json").write_text(json.dumps(status, indent=1))
    return status


def verify(out: Path, bugs) -> list[dict[str, Any]]:
    """Supplementary verification of every passing candidate (offline; see repair_verify.py)."""
    import tempfile
    from experiments.loop_probes.repair_verify import bounded_equivalence, random_simulation, summarise
    from r3e.loop.red_repair_feedback import read_public_candidate
    carrier = {b.challenge_id: b.carrier for b in bugs}
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        sim = Simulator(Path(tmp) / "sim")
        for u in [json.loads(l) for l in (out / "units.jsonl").read_text().splitlines()]:
            c = carrier[u["challenge_id"]]
            for arm in ("shared_first", *ARMS):
                for h in (u.get(arm) or {}).get("passing_candidates", []):
                    rtl = read_public_candidate(out, c.carrier_id, h)
                    row = {"unit": u["unit"], "arm": arm, "candidate_hash": h,
                           "reference_hash": hash_payload(c.clean_rtl)}
                    if rtl is None:
                        row["supplementary"] = {"status": "incomplete_or_not_applicable",
                                                "reason": "candidate not stored"}
                    else:
                        work = Path(tmp) / f"{len(rows)}"
                        work.mkdir()
                        methods = [random_simulation(c.clean_rtl, rtl, c, sim, seeds=[11, 23, 37], cycles=1000,
                                                     work=work),
                                   bounded_equivalence(c.clean_rtl, rtl, c, depth=20, work=work)]
                        status = summarise(methods)
                        row["supplementary"] = {"status": status, "methods": methods,
                                                **({"review": "needed"} if status == "mismatch_found" else {})}
                    rows.append(row)
    (out / "verification.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return rows


def report(out: Path) -> dict[str, Any]:
    cfg = json.loads((out / "run_config.json").read_text())
    units = [json.loads(l) for l in (out / "units.jsonl").read_text().splitlines()] if \
        (out / "units.jsonl").exists() else []
    n_bugs, repeats = cfg["eval_bugs"], cfg["repeats"]
    per_rep: dict[int, list] = {}
    for u in units:
        per_rep.setdefault(u["repeat"], []).append(u)
    complete = sorted(r for r, us in per_rep.items() if len(us) == n_bugs)
    unfinished = {r: len(us) for r, us in per_rep.items() if len(us) < n_bugs}

    def passed(u, arm):
        if u["outcome"] == "first_attempt_passed":
            return True
        return bool(u.get(arm, {}).get("solved"))

    def summary(us):
        valid = [u for u in us if u["outcome"] != "inconclusive_first_attempt"
                 and not any(u.get(a, {}).get("inconclusive") for a in ARMS)]
        forks = [u for u in valid if u["outcome"] == "forked"]
        helped = sum(passed(u, "with_memory") and not passed(u, "without_memory") for u in forks)
        harmed = sum(passed(u, "without_memory") and not passed(u, "with_memory") for u in forks)
        delivered = [u["with_memory"]["memory_injected"] for u in forks]
        rate = lambda xs: round(sum(xs) / len(xs), 3) if xs else "not applicable"
        return {
            "units": len(us), "valid_units": len(valid),
            "inconclusive_units": len(us) - len(valid),
            "all_units_pass_rate": {a: rate([passed(u, a) for u in valid]) for a in ARMS},
            "forks": len(forks),
            "fork_recovery_rate": {a: rate([passed(u, a) for u in forks]) for a in ARMS},
            "helped": helped, "harmed": harmed,
            "memory_delivery_rate": rate(delivered),
            # the units where memory could have changed anything: forks with memory actually injected
            "memory_intervention_units": sum(delivered),
            "no_answer_attempts": {a: sum(u.get(a, {}).get("no_answer", 0) for u in forks) for a in ARMS},
            "wrong_repair_attempts": {a: sum(u.get(a, {}).get("wrong_repair", 0) for u in forks) for a in ARMS},
            "by_problem": {p: {"units": sum(u["problem"] == p for u in valid),
                               "forks": sum(u["problem"] == p for u in forks),
                               "helped": sum(u["problem"] == p and passed(u, "with_memory")
                                             and not passed(u, "without_memory") for u in forks),
                               "harmed": sum(u["problem"] == p and passed(u, "without_memory")
                                             and not passed(u, "with_memory") for u in forks)}
                           for p in sorted({u["problem"] for u in valid})},
        }
    calls = len((out / "calls.jsonl").read_text().splitlines()) if (out / "calls.jsonl").exists() else 0
    rep = {"scope": "feasibility: frozen, ungated case memory; Red-generated sources -> real bugs",
           "repeats_completed": f"{len(complete)} of {repeats}",
           "primary_complete_repeats": summary([u for u in units if u["repeat"] in complete]),
           "unfinished_repeats": {r: {"units": n, "of": n_bugs,
                                      **summary([u for u in units if u["repeat"] == r])}
                                  for r, n in unfinished.items()},
           "all_completed_units_disclosed": len(units), "calls_used": calls,
           "supplementary_verification": _verification_summary(out),
           "status": json.loads((out / "status.json").read_text()) if (out / "status.json").exists() else None}
    (out / "report.json").write_text(json.dumps(rep, indent=1))
    return rep


def _verification_summary(out: Path):
    path = out / "verification.jsonl"
    if not path.exists():
        return "not run yet"
    rows = [json.loads(l) for l in path.read_text().splitlines()]
    # independent check records: one per shared first-attempt pass (it covers both arms,
    # but is one repair and one check) and one per branch pass
    records: dict[str, dict[str, int]] = {}
    for r in rows:
        cell = records.setdefault(r["arm"], {})
        cell[r["supplementary"]["status"]] = cell.get(r["supplementary"]["status"], 0) + 1
    covered = {a: {} for a in ARMS}
    for source, cell in records.items():
        for arm in (ARMS if source == "shared_first" else (source,)):
            for status, n in cell.items():
                covered[arm][status] = covered[arm].get(status, 0) + n
    return {"independent_check_records": {"total": len(rows), **records},
            "covered_per_arm (shared records counted in both; not independent)": covered}


def frozen_config(*, plan_dir: Path, snapshot_hash: str, cfg: BlueConfig, repeats: int, seed: int, bugs,
                  client_settings: dict[str, Any]) -> dict[str, Any]:
    code = ["r3e/loop/blue.py", "r3e/loop/blue_provider.py", "r3e/loop/sim.py", "r3e/knowledge/delivery.py",
            "r3e/knowledge/matcher.py", "experiments/loop_probes/memory_eval.py"]
    return {"plan_dir": str(plan_dir), "eval_set_hash": hash_payload((plan_dir / "eval_set.json").read_text()),
            "eval_bugs": len(bugs), "snapshot_hash": snapshot_hash, "matcher": MATCHER,
            "inference": "BugTypeInference() default", "blue": cfg.__dict__, "repeats": repeats, "seed": seed,
            "client": client_settings,
            "code": {p: hash_payload((REPO_ROOT / p).read_text()) for p in code}}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--plan-dir", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--snapshot-hash", required=True)
    r.add_argument("--llm-env", type=Path, required=True)
    r.add_argument("--model", required=True)
    r.add_argument("--max-calls", type=int, required=True)
    r.add_argument("--repeats", type=int, default=5)
    r.add_argument("--seed", type=int, default=2026)
    q = sub.add_parser("report")
    q.add_argument("--out", type=Path, required=True)
    v = sub.add_parser("verify")
    v.add_argument("--plan-dir", type=Path, required=True)
    v.add_argument("--out", type=Path, required=True)
    v.add_argument("--snapshot-hash", required=True)
    args = p.parse_args()
    if args.cmd == "verify":
        rows = verify(args.out, load_frozen(args.plan_dir, args.snapshot_hash)[0])
        print(json.dumps({"verified_candidates": len(rows), "summary": _verification_summary(args.out)}))
        return 0
    if args.cmd == "report":
        print(json.dumps(report(args.out), indent=1))
        return 0
    from r3e.loop.env import build_client, load_llm_env
    bugs, items = load_frozen(args.plan_dir, args.snapshot_hash)
    cfg = BlueConfig(budget_k=3, escalation_k=0, register_trace=True, answer_format="full")
    settings = {"model": args.model, "thinking": "low", "maximum_output_tokens": 32768, "timeout_seconds": 1500}
    env = load_llm_env(args.llm_env)
    factory = lambda: build_client(env, model_override=args.model, thinking="low", maximum_output_tokens=32768,
                                   timeout_seconds=1500, failure_dir=args.out / "unparseable")
    frozen = frozen_config(plan_dir=args.plan_dir, snapshot_hash=args.snapshot_hash, cfg=cfg, repeats=args.repeats,
                           seed=args.seed, bugs=bugs, client_settings=settings)
    print(json.dumps(execute(out=args.out, bugs=bugs, items=items, client_factory=factory, max_calls=args.max_calls,
                             repeats=args.repeats, seed=args.seed, cfg=cfg, frozen=frozen)))
    print(json.dumps(report(args.out), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
