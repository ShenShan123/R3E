"""Paired test of one Blue setting: does it change Blue's repairs and their cost?

``--compare registers``: without vs with the register trace (the design's
register values in the evidence). ``--compare answer_format``: Blue answers
with the complete RTL vs with edits or the complete RTL, as it chooses.

For each bug of a public manifest (or the given ``--cases``), Blue runs per seed
once per arm, with the same seeds, budget and no memory. The setting acts from
the first attempt, so this is a plain paired test (the fork design is for
memory, which enters at attempt 2). ``--offline`` prints the evidence window
each bug would get. ``--register-trace`` turns the trace on in both arms of an
answer-format comparison.

(The register trace replaced the structural view, which restated code Blue
already had: no effect on fixes at +39% input tokens, structure_probe_20261003.)

``--attempts`` repairs per run (default 3); ``--traced-only`` keeps the bugs
whose evidence actually gets register values (on the others both arms are the
same). All bugs run on the first seed before any runs on the next, so a cap
reached early still leaves complete pairs. The provider's reasoning text is
kept in ``OUT/reasoning.jsonl`` (diagnosis only).

Usage:
  python -m experiments.loop_probes.paired_probe --compare registers|answer_format \
      --manifest datasets/manifests/X.jsonl --out OUT \
      [--cases ID ...] [--offline | --llm-env FILE --model NAME --max-calls N]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from r3e.knowledge import BugTypeInference, KnowledgeMatcher
from r3e.loop.blue import BlueConfig, BlueRunner
from r3e.loop.budget import BudgetedClient, CallBudgetExceeded
from r3e.loop.carriers import REPO_ROOT
from r3e.loop.corpus import load_public_manifest
from r3e.loop.sim import Simulator


# arm name -> BlueConfig settings; the first arm is the baseline
ARMS = {
    "registers": lambda a: {"without_registers": {"register_trace": False},
                            "with_registers": {"register_trace": True}},
    "answer_format": lambda a: {"full": {"answer_format": "full", "register_trace": a.register_trace},
                                "edits": {"answer_format": "edits", "register_trace": a.register_trace}},
}


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
    p.add_argument("--attempts", type=int, default=3)
    p.add_argument("--traced-only", action="store_true")
    p.add_argument("--compare", choices=["registers", "answer_format"], default="registers")
    p.add_argument("--register-trace", action="store_true", help="answer_format comparison: trace on in both arms")
    p.add_argument("--max-output-tokens", type=int, default=16384)
    p.add_argument("--timeout", type=float, default=900)
    return p.parse_args()


def main() -> int:
    args = _args()
    args.out.mkdir(parents=True, exist_ok=True)
    _, bugs = load_public_manifest(REPO_ROOT / args.manifest if not args.manifest.is_absolute() else args.manifest,
                                   REPO_ROOT)
    if args.cases:
        bugs = [b for b in bugs if b.challenge_id in set(args.cases)]
    sim = Simulator(args.out / "sim")
    if args.traced_only:
        bugs = [b for b in bugs if "registers_note" in sim.verdict(b.buggy_rtl, b.carrier, registers=True).window]
    print(json.dumps({"bugs": len(bugs), "seeds": args.seeds, "attempts": args.attempts}), flush=True)
    if args.offline:
        for bug in bugs:
            window = sim.verdict(bug.buggy_rtl, bug.carrier, registers=True).window
            print(json.dumps({"case": bug.challenge_id, "rows": window.get("rows", [])[:4]})[:1500])
        return 0
    if not args.llm_env or args.max_calls <= 0:
        print("refusing: real calls need --llm-env and --max-calls > 0 (or use --offline)")
        return 2

    from r3e.loop.env import build_client, load_llm_env
    client = build_client(load_llm_env(args.llm_env), model_override=args.model, thinking="low",
                          maximum_output_tokens=args.max_output_tokens, timeout_seconds=args.timeout,
                          failure_dir=args.out / "unparseable", reasoning_log=args.out / "reasoning.jsonl")
    budget = BudgetedClient(client, max_calls=args.max_calls)
    rows = []
    with (args.out / "calls.jsonl").open("a") as log:
        budget.on_call = lambda e: (log.write(json.dumps(e) + "\n"), log.flush())
        runners = {arm: BlueRunner(json_client=budget, simulator=sim, project_root=REPO_ROOT,
                                   config=BlueConfig(budget_k=args.attempts, escalation_k=0, **setting))
                   for arm, setting in ARMS[args.compare](args).items()}
        for seed in args.seeds:
            for bug in bugs:
                if args.max_calls - budget.total_calls < 2 * args.attempts:  # room for one pair
                    break
                row = {"case": bug.challenge_id, "seed": seed}
                try:
                    for arm, runner in runners.items():
                        enc = runner.run(bug, mode="none", pool=[], inference=BugTypeInference(),
                                         matcher=KnowledgeMatcher(), seed=seed, allow_escalation=False,
                                         phase="evaluation")
                        row[arm] = {"solved": enc.solved_within_budget, "inconclusive": enc.inconclusive,
                                    "attempts": len(enc.repair_attempts), "accounting": enc.accounting()}
                except CallBudgetExceeded:  # provider retries reached the cap mid-pair: stop cleanly
                    print("call cap reached; this pair is left out")
                    break
                rows.append(row)
                (args.out / "results.json").write_text(json.dumps(rows, indent=1))
                print(json.dumps({k: (v["solved"] if isinstance(v, dict) else v) for k, v in row.items()}), flush=True)
            else:
                continue
            break
    (args.out / "results.json").write_text(json.dumps(rows, indent=1))
    base, new = ARMS[args.compare](args)
    valid = [r for r in rows if not (r[base]["inconclusive"] or r[new]["inconclusive"])]
    helped = sum(r[new]["solved"] and not r[base]["solved"] for r in valid)
    harmed = sum(r[base]["solved"] and not r[new]["solved"] for r in valid)
    print(json.dumps({"pairs": len(valid), "helped": helped, "harmed": harmed, "calls": budget.total_calls}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
