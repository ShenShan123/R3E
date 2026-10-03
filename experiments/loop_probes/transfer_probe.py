"""Targeted transfer probe: do memory cases from other designs help Blue on given bugs?

1. Teacher bugs: for each test bug, catalog bugs on *other* designs using the same
   Red operators, admitted by Red's checks; the ones with the closest failure
   profiles are used. Blue repairs them (escalation allowed); verified repairs
   become causal-chain cases.
2. Paired runs: each test bug, per seed, without memory and with normal retrieval
   (memory is offered from Blue's second attempt).

Test bugs are Red bugs recorded in an earlier run (``--source-run``).
Real model calls need ``--llm-env`` and ``--max-calls``; ``--offline`` only
selects and prints the teacher bugs.

Usage:
  python -m experiments.loop_probes.transfer_probe --source-run RUN --tests RC_a RC_b --out OUT \
      [--offline | --llm-env FILE --model NAME --max-calls 50]
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from r3e.knowledge import BugTypeInference, CaseMemoryAuthor, KnowledgeMatcher, analyze_rtl, build_profile
from r3e.loop.blue import BlueConfig, BlueRunner
from r3e.loop.budget import BudgetedClient
from r3e.loop.carriers import REPO_ROOT
from r3e.loop.corpus import Challenge
from r3e.loop.operators import apply_edits, enumerate_sites
from r3e.loop.red import RedAgent
from r3e.loop.sim import Simulator
from r3e.protocol.hashing import hash_payload

from .common import all_carriers, rebuild, red_rows


def _args():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--source-run", type=Path, required=True)
    p.add_argument("--tests", nargs="+", required=True, help="challenge ids from the source run")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--teachers-per-test", type=int, default=3)
    p.add_argument("--seeds", type=int, nargs="+", default=[7, 1016, 2025])
    p.add_argument("--offline", action="store_true")
    p.add_argument("--llm-env", type=Path)
    p.add_argument("--model", default=None)
    p.add_argument("--max-calls", type=int, default=0)
    p.add_argument("--teacher-calls", type=int, default=16)
    return p.parse_args()


def main() -> int:
    args = _args()
    args.out.mkdir(parents=True, exist_ok=True)
    cars, rows = all_carriers(), red_rows(args.source_run)
    tests = [rebuild(rows[cid], cars) for cid in args.tests]
    sim = Simulator(args.out / "sim")
    admit = RedAgent(mode="random", client=None, simulator=sim, seed=0)

    def profile_of(ch):
        return build_profile(sim.verdict(ch.buggy_rtl, ch.carrier).feedback, analyze_rtl(ch.buggy_rtl))

    def closeness(a, b):
        return (sum(a.causal.get(k) == v for k, v in b.causal.items())
                + sum(a.status.get(k) == v for k, v in b.status.items()))

    teachers, test_clusters = [], {t.carrier.cluster_id for t in tests}
    for test in tests:
        ops = list(dict.fromkeys(test.provenance["ops"]))
        target, found = profile_of(test), []
        for car in sorted(cars.values(), key=lambda c: hash_payload({"p": 2, "c": c.carrier_id})):
            if car.cluster_id in test_clusters:
                continue
            by_op = {op: [s for s in enumerate_sites(car) if s.operator == op] for op in ops}
            if not all(by_op.values()):
                continue
            rng = random.Random(hash_payload(car.carrier_id))
            for _ in range(4):
                picks = [rng.choice(by_op[op]) for op in ops]
                if len({p.site_id for p in picks}) != len(picks):
                    continue
                try:
                    rtl = apply_edits(car, [(p, rng.choice(p.options)) for p in picks])
                except ValueError:
                    continue
                if admit._admit(car, rtl)[0] == "admitted":
                    ch = Challenge(challenge_id="T_" + hash_payload(rtl).split(":", 1)[1][:12], carrier=car,
                                   buggy_rtl=rtl, origin="teacher", provenance={"for": test.challenge_id})
                    found.append((closeness(profile_of(ch), target), ch))
                    break
        found.sort(key=lambda t: (-t[0], t[1].challenge_id))
        teachers += [ch for _, ch in found[: args.teachers_per_test]]
        print(f"test {test.challenge_id} ({test.carrier.cluster_id}) ops={ops}: {len(found)} admitted teacher "
              f"bugs; using {[(ch.carrier.cluster_id, s) for s, ch in found[: args.teachers_per_test]]}")
    if args.offline:
        return 0
    if not args.llm_env or args.max_calls <= 0:
        print("refusing: real calls need --llm-env and --max-calls > 0 (or use --offline)")
        return 2

    from r3e.loop.env import build_client, load_llm_env
    client = build_client(load_llm_env(args.llm_env), model_override=args.model, thinking="low",
                          maximum_output_tokens=32768, timeout_seconds=900, failure_dir=args.out / "unparseable")
    budget = BudgetedClient(client, max_calls=args.max_calls)
    budget.output_caps = {"blue_inference": 16384, "escalation": 32768, "evaluation": 16384}
    with (args.out / "calls.jsonl").open("a") as log:
        budget.on_call = lambda e: (log.write(json.dumps(e) + "\n"), log.flush())
        blue = BlueRunner(json_client=budget, simulator=sim, project_root=REPO_ROOT,
                          config=BlueConfig(budget_k=3, escalation_k=3))
        results, episodes = {"teachers": [], "pairs": []}, []
        for ch in teachers:
            if budget.total_calls >= args.teacher_calls:
                break
            enc = blue.run(ch, mode="none", pool=[], inference=BugTypeInference(), matcher=KnowledgeMatcher(), seed=5)
            results["teachers"].append({"id": ch.challenge_id, "design": ch.carrier.cluster_id,
                                        "within": enc.solved_within_budget, "escalation": enc.solved_by_escalation})
            if enc.episode.verified_bug_type is not None:
                episodes.append(enc.episode)
        items = CaseMemoryAuthor().author(episodes)
        (args.out / "items.json").write_text(json.dumps([i.to_dict() for i in items], indent=1))
        inference, matcher = BugTypeInference().fit(episodes), KnowledgeMatcher()
        for seed in args.seeds:
            for test in tests:
                if args.max_calls - budget.total_calls < 6:  # a pair needs up to 2 x 3 attempts
                    break
                row = {"test": test.challenge_id, "seed": seed}
                for arm, mode, pool in (("without", "none", []), ("with", "matched", items)):
                    enc = blue.run(test, mode=mode, pool=pool, inference=inference, matcher=matcher, seed=seed,
                                   allow_escalation=False, phase="evaluation")
                    rec = enc.record()
                    row[arm] = {"solved": enc.solved_within_budget, "inconclusive": enc.inconclusive,
                                "attempts": [(a.get("verdict_tier"), (a.get("edit") or "")[:160],
                                              a.get("memory_offered")) for a in enc.attempts],
                                "retrieved": rec.get("retrieved_items"), "delivered": rec.get("shown_items")}
                results["pairs"].append(row)
                print(json.dumps({k: row[k] if k in ("test", "seed") else
                                  {"solved": row[k]["solved"], "delivered": bool(row[k]["delivered"])} for k in row}))
        (args.out / "results.json").write_text(json.dumps(results, indent=1))
    print("calls used:", budget.total_calls)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
