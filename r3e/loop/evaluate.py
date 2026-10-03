"""Post-chain evaluation on the frozen holdout split.

For each saved snapshot (round), each knowledge mode is run on every holdout
challenge with the same executor, budget and seeds, with no escalation and no
learning. Results are written to the ``evaluation`` ledger and never fed back
into the loop. A snapshot with no active memory is recorded as skipped: every
knowledge mode would equal ``none``.

Modes (C2-A):
- ``none``: no knowledge;
- ``static``: the same top items by support for every case;
- ``matched``: the knowledge matched to each case;
- ``shuffled``: the same count of non-matching items.

Every case runs ``repeats`` times per mode. Per-case solve fractions are
compared with ``none``: the summary reports how many cases got worse (negative
transfer) or better, and the mean difference on cases where knowledge was
actually shown. Cases without shown knowledge differ only by sampling noise.
"""
from __future__ import annotations

from typing import Any, Sequence

from r3e.knowledge import BugTypeInference, RepairEpisode

from .blue import BlueRunner
from .corpus import Challenge
from .state import RunState


def evaluate_holdout(
    state: RunState,
    runner: BlueRunner,
    holdout: Sequence[Challenge],
    *,
    modes: Sequence[str] = ("none", "static", "matched", "shuffled"),
    rounds: Sequence[int] | None = None,
    seed: int = 11,
    static_top: int = 3,
    repeats: int = 3,
    blue_model: str = "primary",
) -> dict[str, Any]:
    snapshots = state.read("rounds")
    if rounds is not None:
        snapshots = [s for s in snapshots if s["round"] in set(rounds)]
    episode_rows = state.read("episodes")
    results: dict[str, Any] = {"cases": [c.challenge_id for c in holdout], "snapshots": []}

    def run_mode(mode, pool, inference, static_items):
        solved = {}
        for ch in holdout:
            runs = [runner.run(ch, mode=mode, pool=pool, inference=inference, matcher=state.matcher,
                               seed=seed + 1009 * rep, static_items=static_items, allow_escalation=False,
                               phase="evaluation") for rep in range(repeats)]
            valid = [r for r in runs if not r.inconclusive]
            if not valid:  # provider failures only: excluded, not counted as unsolved
                continue
            solved[ch.challenge_id] = {
                "solved": sum(r.solved_within_budget for r in valid) / len(valid),
                "attempts": sum(len(r.repair_attempts) for r in valid) / len(valid),
                "valid_runs": len(valid),
                "shown": sorted({s for r in valid for s in r.record()["shown_items"]}),
            }
        return solved

    baseline = run_mode("none", [], BugTypeInference(), ()) if "none" in modes else {}
    for snap in snapshots:
        pool = state.items_by_key((a[0], a[1]) for a in snap["active"])
        if not pool and any(m != "none" for m in modes):
            # no active memory: every knowledge mode would equal "none"; skip the calls
            entry = {"round": snap["round"], "blue_model": blue_model, "active_items": 0,
                     "skipped": "no active memory in this snapshot", "summary": {}, "per_case": {}}
            state.append("evaluation", entry)
            results["snapshots"].append(entry)
            continue
        episodes = [RepairEpisode(r["episode"]) for r in episode_rows if r["round"] <= snap["round"]]
        inference = BugTypeInference().fit(episodes)
        static_items = sorted(pool, key=lambda i: (-i.payload["evidence"]["support"], i.item_id))[:static_top]
        per_mode = {"none": baseline} if baseline else {}
        for mode in modes:
            if mode == "none":
                continue
            per_mode[mode] = run_mode(mode, pool, inference, static_items)
        summary = {}
        for mode, solved in per_mode.items():
            rate = sum(v["solved"] for v in solved.values()) / max(1, len(solved))
            if baseline:
                common = [cid for cid in solved if cid in baseline]
                diffs = [solved[cid]["solved"] - baseline[cid]["solved"] for cid in common]
                negative = sum(d < 0 for d in diffs)
                positive = sum(d > 0 for d in diffs)
                shown_diffs = [solved[cid]["solved"] - baseline[cid]["solved"] for cid in common
                               if solved[cid]["shown"]]
            else:
                negative = positive = None
                shown_diffs = []
            summary[mode] = {
                "solve_rate": rate,
                "cases_worse_than_none": negative,
                "cases_better_than_none": positive,
                "mean_diff_on_cases_with_knowledge_shown": (
                    sum(shown_diffs) / len(shown_diffs) if shown_diffs else None),
                "cases_with_knowledge_shown": len(shown_diffs),
                "mean_attempts": sum(v["attempts"] for v in solved.values()) / max(1, len(solved)),
            }
        entry = {"round": snap["round"], "blue_model": blue_model, "active_items": len(pool),
                 "summary": summary, "per_case": per_mode}
        state.append("evaluation", entry)
        results["snapshots"].append(entry)
    results["cost"] = runner.budget.report()
    return results
