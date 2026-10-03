"""Paired test of the structural view: does structure as input change Blue's repairs?

For each bug of a public manifest (or the given ``--cases``), Blue runs per seed
twice, without and with ``structural_view``, with the same seeds, budget and
no memory. The view is in Blue's evidence from the first attempt, so the arms
differ from attempt 1 (a plain paired test; the fork design is for memory,
which enters at attempt 2). ``--offline`` prints the view each bug would get.

Usage:
  python -m experiments.loop_probes.structure_probe --manifest datasets/manifests/X.jsonl --out OUT \
      [--cases ID ...] [--offline | --llm-env FILE --model NAME --max-calls N]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from r3e.knowledge import BugTypeInference, KnowledgeMatcher
from r3e.knowledge.structural_view import build_structural_view
from r3e.loop.blue import BlueConfig, BlueRunner
from r3e.loop.budget import BudgetedClient
from r3e.loop.carriers import REPO_ROOT
from r3e.loop.corpus import load_public_manifest
from r3e.loop.sim import Simulator


def _args():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--cases", nargs="*", default=None)
    p.add_argument("--seeds", type=int, nargs="+", default=[7, 1016, 2025])
    p.add_argument("--offline", action="store_true")
    p.add_argument("--llm-env", type=Path)
    p.add_argument("--model", default=None)
    p.add_argument("--max-calls", type=int, default=0)
    return p.parse_args()


def main() -> int:
    args = _args()
    args.out.mkdir(parents=True, exist_ok=True)
    _, bugs = load_public_manifest(REPO_ROOT / args.manifest if not args.manifest.is_absolute() else args.manifest,
                                   REPO_ROOT)
    if args.cases:
        bugs = [b for b in bugs if b.challenge_id in set(args.cases)]
    sim = Simulator(args.out / "sim")
    if args.offline:
        for bug in bugs:
            verdict = sim.verdict(bug.buggy_rtl, bug.carrier)
            view = build_structural_view(bug.buggy_rtl, [d.signal for d in verdict.feedback.divergences])
            print(json.dumps({"case": bug.challenge_id, "structural_view": view})[:1500])
        return 0
    if not args.llm_env or args.max_calls <= 0:
        print("refusing: real calls need --llm-env and --max-calls > 0 (or use --offline)")
        return 2

    from r3e.loop.env import build_client, load_llm_env
    client = build_client(load_llm_env(args.llm_env), model_override=args.model, thinking="low",
                          maximum_output_tokens=16384, timeout_seconds=900, failure_dir=args.out / "unparseable")
    budget = BudgetedClient(client, max_calls=args.max_calls)
    rows = []
    with (args.out / "calls.jsonl").open("a") as log:
        budget.on_call = lambda e: (log.write(json.dumps(e) + "\n"), log.flush())
        runners = {arm: BlueRunner(json_client=budget, simulator=sim, project_root=REPO_ROOT,
                                   config=BlueConfig(structural_view=arm == "with_view"))
                   for arm in ("without_view", "with_view")}
        for seed in args.seeds:
            for bug in bugs:
                if args.max_calls - budget.total_calls < 6:  # a pair needs up to 2 x 3 attempts
                    break
                row = {"case": bug.challenge_id, "seed": seed}
                for arm, runner in runners.items():
                    enc = runner.run(bug, mode="none", pool=[], inference=BugTypeInference(),
                                     matcher=KnowledgeMatcher(), seed=seed, allow_escalation=False,
                                     phase="evaluation")
                    row[arm] = {"solved": enc.solved_within_budget, "inconclusive": enc.inconclusive,
                                "attempts": len(enc.repair_attempts)}
                rows.append(row)
                print(json.dumps(row))
    (args.out / "results.json").write_text(json.dumps(rows, indent=1))
    valid = [r for r in rows if not (r["without_view"]["inconclusive"] or r["with_view"]["inconclusive"])]
    helped = sum(r["with_view"]["solved"] and not r["without_view"]["solved"] for r in valid)
    harmed = sum(r["without_view"]["solved"] and not r["with_view"]["solved"] for r in valid)
    print(json.dumps({"pairs": len(valid), "helped": helped, "harmed": harmed, "calls": budget.total_calls}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
