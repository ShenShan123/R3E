"""Why does Blue fix so many bugs? Three paired arms, one attempt each, no memory.

- ``A`` normal evidence (visible-test cycle window with inputs);
- ``B`` no test evidence: only that the design fails its test;
- ``C`` normal evidence on the same bug with its internal signals renamed and
  its comments removed (ports, parameters and behaviour unchanged; checked by
  simulation before any call).

A vs B shows how much Blue relies on the test evidence (B high: it repairs
from the code or from prior knowledge). A vs C shows whether it relies on
remembering the public source text (a drop points to contamination).

Bugs come from public manifests, stratified by benchmark and bug type, with
designs whose renaming changes something preferred. Each bug's three arms run
back to back with the same seed, so the cap never leaves a bug half-run.

Usage:
  python -m experiments.loop_probes.evidence_probe --out OUT [--chipbench 40 --cirfix 20] \
      [--offline | --llm-env FILE --model NAME --max-calls 180]
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from r3e.knowledge import BugTypeInference, KnowledgeMatcher
from r3e.knowledge.rename import rename_internal_signals
from r3e.loop.blue import BlueConfig, BlueRunner
from r3e.loop.budget import BudgetedClient, CallBudgetExceeded
from r3e.loop.carriers import REPO_ROOT
from r3e.loop.corpus import Challenge, load_public_manifest
from r3e.loop.sim import Simulator
from r3e.protocol.hashing import hash_payload

ARMS = ("A_full", "B_no_evidence", "C_renamed")


def _args():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--chipbench", type=int, default=40)
    p.add_argument("--cirfix", type=int, default=20)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--offline", action="store_true")
    p.add_argument("--llm-env", type=Path)
    p.add_argument("--model", default=None)
    p.add_argument("--max-calls", type=int, default=0)
    return p.parse_args()


def _bug_type(row_types: dict[str, str], bug: Challenge) -> str:
    return row_types.get(bug.challenge_id) or "cirfix"


def select(sim: Simulator, n_chip: int, n_cir: int) -> list[tuple[Challenge, str, str]]:
    """(bug, renamed buggy RTL, bug type): stratified, renaming verified by simulation."""
    chosen = []
    for manifest, n in (("datasets/manifests/chipbench89.jsonl", n_chip), ("datasets/manifests/cirfix39.jsonl", n_cir)):
        rows = [json.loads(l) for l in (REPO_ROOT / manifest).read_text().splitlines() if l.strip()]
        types = {str(r.get("case_id")): r.get("bug_type", "cirfix") for r in rows}
        _, bugs = load_public_manifest(REPO_ROOT / manifest, REPO_ROOT)
        usable = collections.defaultdict(list)
        for bug in sorted(bugs, key=lambda b: hash_payload({"probe": "evidence", "c": b.challenge_id})):
            if bug.carrier.cluster_id.startswith("reed_solomon"):  # 10-20 s per simulation
                continue
            if sim.verdict(bug.buggy_rtl, bug.carrier).tier != "visible_fail":
                continue  # arm B needs a functional failure
            renamed, mapping = rename_internal_signals(bug.buggy_rtl)
            if sim.run(renamed, bug.carrier, bug.carrier.visible_tb)[0] != sim.run(bug.buggy_rtl, bug.carrier,
                                                                                   bug.carrier.visible_tb)[0]:
                continue  # renaming must not change behaviour
            usable[_bug_type(types, bug)].append((not mapping, bug, renamed))
        total = sum(len(v) for v in usable.values())
        quota = {t: max(1, round(n * len(v) / total)) for t, v in usable.items()}
        while sum(quota.values()) > n:
            quota[max(quota, key=quota.get)] -= 1
        for t, items in usable.items():
            items.sort(key=lambda x: x[0])  # designs where something is renamed first
            chosen += [(bug, renamed, t) for _, bug, renamed in items[: quota[t]]]
    return chosen


def main() -> int:
    args = _args()
    args.out.mkdir(parents=True, exist_ok=True)
    sim = Simulator(args.out / "sim")
    bugs = select(sim, args.chipbench, args.cirfix)
    print(json.dumps({"bugs": len(bugs), "by_type": dict(collections.Counter(t for _, _, t in bugs))}))
    if args.offline:
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
        cfg = dict(budget_k=1, escalation_k=0)
        runners = {"A_full": BlueRunner(json_client=budget, simulator=sim, project_root=REPO_ROOT, config=BlueConfig(**cfg)),
                   "B_no_evidence": BlueRunner(json_client=budget, simulator=sim, project_root=REPO_ROOT,
                                               config=BlueConfig(evidence="none", **cfg))}
        runners["C_renamed"] = runners["A_full"]
        for bug, renamed, bug_type in bugs:
            if args.max_calls - budget.total_calls < len(ARMS):
                break
            try:
                row = run_bug(bug, renamed, bug_type, runners, args.seed)
            except CallBudgetExceeded:  # provider retries reached the cap mid-bug: stop cleanly
                print("call cap reached; this bug is left out")
                break
            rows.append(row)
            (args.out / "results.json").write_text(json.dumps(rows, indent=1))
            print(json.dumps({k: (v["solved"] if isinstance(v, dict) else v) for k, v in row.items()}), flush=True)
    (args.out / "results.json").write_text(json.dumps(rows, indent=1))
    print(json.dumps({"calls": budget.total_calls, **summarise(rows)}, indent=1))
    return 0


def run_bug(bug, renamed, bug_type, runners, seed) -> dict:
    """The three arms of one bug, back to back with the same seed."""
    row = {"case": bug.challenge_id, "bug_type": bug_type, "design": bug.carrier.cluster_id}
    for arm in ARMS:
        challenge = bug if arm != "C_renamed" else Challenge(
            challenge_id=bug.challenge_id + ":renamed", carrier=bug.carrier, buggy_rtl=renamed,
            origin=bug.origin, provenance=dict(bug.provenance))
        enc = runners[arm].run(challenge, mode="none", pool=[], inference=BugTypeInference(),
                               matcher=KnowledgeMatcher(), seed=seed, allow_escalation=False, phase="evaluation")
        attempt = next((a for a in enc.attempts if not a.get("infra_failure")), {})
        row[arm] = {"solved": enc.solved_within_budget, "inconclusive": enc.inconclusive,
                    "tier": attempt.get("verdict_tier"), "edit": (attempt.get("edit") or "")[:200],
                    "accounting": enc.accounting()}
    return row


def summarise(rows: list[dict]) -> dict:
    """Solve rates per arm and paired discordant counts (A vs B, A vs C), overall and per group."""
    def stats(group):
        valid = [r for r in group if not any(r[a]["inconclusive"] for a in ARMS)]
        out = {"bugs": len(valid), **{a: sum(r[a]["solved"] for r in valid) for a in ARMS}}
        for other in ("B_no_evidence", "C_renamed"):
            out[f"A_only_vs_{other[0]}"] = sum(r["A_full"]["solved"] and not r[other]["solved"] for r in valid)
            out[f"{other[0]}_only_vs_A"] = sum(r[other]["solved"] and not r["A_full"]["solved"] for r in valid)
        return out
    groups = collections.defaultdict(list)
    for r in rows:
        groups["chipbench" if r["case"].startswith("chipbench") else "cirfix"].append(r)
        groups["type:" + r["bug_type"]].append(r)
    return {"overall": stats(rows), **{k: stats(v) for k, v in sorted(groups.items())}}


if __name__ == "__main__":
    raise SystemExit(main())
