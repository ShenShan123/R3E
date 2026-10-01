"""Post-chain evaluation on the frozen holdout split.

For each saved snapshot (round), each knowledge mode is run on every holdout
challenge with the same executor, budget and seeds, with no escalation and no
learning. Results are written to the ``evaluation`` ledger and never fed back
into the loop.

Modes (C2-A):
- ``none``: no knowledge;
- ``static``: the same top items by support for every case;
- ``matched``: the knowledge matched to each case;
- ``shuffled``: the same count of non-matching items.

Negative transfer counts holdout cases solved under ``none`` but not under
the evaluated mode.
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
) -> dict[str, Any]:
    snapshots = state.read("rounds")
    if rounds is not None:
        snapshots = [s for s in snapshots if s["round"] in set(rounds)]
    episode_rows = state.read("episodes")
    results: dict[str, Any] = {"cases": [c.challenge_id for c in holdout], "snapshots": []}

    def run_mode(mode, pool, inference, static_items):
        solved = {}
        for ch in holdout:
            enc = runner.run(ch, mode=mode, pool=pool, inference=inference, matcher=state.matcher,
                             seed=seed, static_items=static_items, allow_escalation=False,
                             phase="evaluation")
            solved[ch.challenge_id] = {
                "solved": enc.solved_within_budget,
                "attempts": len(enc.attempts),
                "shown": enc.record()["shown_items"],
            }
        return solved

    baseline = run_mode("none", [], BugTypeInference(), ()) if "none" in modes else {}
    for snap in snapshots:
        pool = state.items_by_key((a[0], a[1]) for a in snap["active"])
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
            negative = sum(
                baseline.get(cid, {}).get("solved", False) and not v["solved"]
                for cid, v in solved.items()
            ) if baseline else None
            summary[mode] = {"solve_rate": rate, "negative_transfer": negative,
                             "mean_attempts": sum(v["attempts"] for v in solved.values()) / max(1, len(solved))}
        entry = {"round": snap["round"], "active_items": len(pool), "summary": summary, "per_case": per_mode}
        state.append("evaluation", entry)
        results["snapshots"].append(entry)
    results["cost"] = runner.budget.report()
    return results
