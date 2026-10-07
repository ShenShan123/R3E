"""Record what Blue's reasoning does on bugs where it runs away (diagnosis only).

One attempt per bug, normal evidence, no memory, no register trace. The
provider's reasoning text is appended to ``OUT/reasoning.jsonl`` (never shown
to any agent). Bugs come from a structure-probe run: those whose no-view arm
used the most output tokens per attempt.

Usage:
  python -m experiments.loop_probes.reasoning_probe --from-structure-run RUN --bugs 12 --out OUT \
      --llm-env FILE --model NAME --max-calls 14
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


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--from-structure-run", type=Path, required=True)
    p.add_argument("--manifest", type=Path, default=Path("datasets/manifests/chipbench89.jsonl"))
    p.add_argument("--bugs", type=int, default=12)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--llm-env", type=Path, required=True)
    p.add_argument("--model", default=None)
    p.add_argument("--max-calls", type=int, required=True)
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    rows = json.loads((args.from_structure_run / "results.json").read_text())
    per_attempt = {}
    for r in rows:
        acc = r["without_view"]["accounting"]
        if acc["llm_calls"]:
            per_attempt.setdefault(r["case"], []).append(acc["output_tokens"] / acc["llm_calls"])
    ranked = sorted(per_attempt, key=lambda c: -max(per_attempt[c]))[: args.bugs]
    _, bugs = load_public_manifest(REPO_ROOT / args.manifest, REPO_ROOT)
    bugs = [b for b in bugs if b.challenge_id in set(ranked)]

    from r3e.loop.env import build_client, load_llm_env
    client = build_client(load_llm_env(args.llm_env), model_override=args.model, thinking="low",
                          maximum_output_tokens=16384, timeout_seconds=900,
                          failure_dir=args.out / "unparseable", reasoning_log=args.out / "reasoning.jsonl")
    budget = BudgetedClient(client, max_calls=args.max_calls)
    blue = BlueRunner(json_client=budget, simulator=Simulator(args.out / "sim"), project_root=REPO_ROOT,
                      config=BlueConfig(budget_k=1, escalation_k=0))
    results = []
    with (args.out / "calls.jsonl").open("a") as log:
        budget.on_call = lambda e: (log.write(json.dumps(e) + "\n"), log.flush())
        for bug in bugs:
            try:
                enc = blue.run(bug, mode="none", pool=[], inference=BugTypeInference(), matcher=KnowledgeMatcher(),
                               seed=args.seed, allow_escalation=False, phase="evaluation")
            except CallBudgetExceeded:
                break
            results.append({"case": bug.challenge_id, "solved": enc.solved_within_budget,
                            "tiers": [a.get("verdict_tier") for a in enc.attempts], "accounting": enc.accounting()})
            print(json.dumps({"case": bug.challenge_id, "solved": enc.solved_within_budget,
                              "tiers": results[-1]["tiers"]}), flush=True)
    (args.out / "results.json").write_text(json.dumps(results, indent=1))
    print("calls used:", budget.total_calls)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
