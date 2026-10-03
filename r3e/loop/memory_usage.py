"""What happened each time memory was offered, written back to the cases.

Blue tries without memory first (``BlueConfig.memory_from_attempt``). Every
learning encounter is classified from its own record:

- ``solved_alone``: the first attempt fixed it; memory was never offered.
  The bug was too simple, and its fix still becomes a case.
- ``fixed_after_memory``: memory was offered after a failed attempt and a
  later attempt within budget fixed it. This is observational: the paired
  gate and mastery tests decide whether the memory caused it.
- ``escalation_after_memory``: memory was offered, all budgeted attempts
  failed, escalation fixed it. Each offered case is marked by whether the
  verified fix has the same change type as the case (``agrees``: the memory
  pointed the right way but was not applied; otherwise it did not transfer to
  this bug).
- ``unresolved_with_memory``: memory was offered and nothing fixed it.
- ``novel_fixed``: nothing was retrieved and escalation fixed it; a new kind
  of bug, and the new case updates the memory.
- ``novel_unresolved``: nothing was retrieved and nothing fixed it; a weak
  point for Red.
- ``first_failed_fixed_without_memory``: memory was retrieved but never
  offered (possible only if ``memory_from_attempt`` is later than the fix).

One ``memory_usage`` row per offered case records the outcome. ``usage_stats``
folds them into a per-case track record, which is shown to Blue next to the
case as data. Measurement runs (gate, mastery, evaluation) are never recorded
here.
"""
from __future__ import annotations

from typing import Any, Mapping

from .state import RunState


PASS = {"visible_pass", "hidden_pass", "formal_pass"}


def classify(enc) -> str:
    record = enc.record()
    tries = [a for a in enc.attempts if not a.get("infra_failure")]
    offered = bool(record["shown_items"])
    if enc.solved_within_budget and tries and tries[0].get("verdict_tier") in PASS:
        return "solved_alone"
    if offered:
        if enc.solved_within_budget:
            return "fixed_after_memory"
        return "escalation_after_memory" if enc.solved_by_escalation else "unresolved_with_memory"
    if record.get("retrieved_items"):
        return "first_failed_fixed_without_memory" if (enc.solved_within_budget or enc.solved_by_escalation) \
            else "unresolved_with_memory"
    return "novel_fixed" if (enc.solved_within_budget or enc.solved_by_escalation) else "novel_unresolved"


def record_memory_usage(state: RunState, enc, *, round_index: int, pool) -> dict[str, Any]:
    """Classify a learning encounter and append one row per offered case."""
    outcome = classify(enc)
    record = enc.record()
    fix_type = enc.episode.verified_bug_type
    by_key = {f"{i.item_id}@v{i.version}": i for i in pool}
    rows = []
    for key in record["shown_items"]:
        item = by_key.get(key)
        rows.append(state.append("memory_usage", {
            "round": round_index, "item": key, "challenge_id": enc.challenge_id, "outcome": outcome,
            "fix_type": fix_type, "case_type": item.bug_type if item else None,
            "fix_agrees_with_case": (fix_type == item.bug_type) if (item and fix_type) else None,
        }))
    return {"challenge_id": enc.challenge_id, "outcome": outcome, "offered": len(rows)}


def usage_stats(state: RunState) -> dict[str, dict[str, int]]:
    """Per offered case: how often it was offered and what followed."""
    stats: dict[str, dict[str, int]] = {}
    for row in state.read("memory_usage"):
        s = stats.setdefault(row["item"], {"offered": 0, "fixed_after": 0, "escalation_fix_agreed": 0,
                                           "escalation_fix_differed": 0, "unresolved": 0})
        s["offered"] += 1
        if row["outcome"] == "fixed_after_memory":
            s["fixed_after"] += 1
        elif row["outcome"] == "escalation_after_memory":
            s["escalation_fix_agreed" if row.get("fix_agrees_with_case") else "escalation_fix_differed"] += 1
        else:
            s["unresolved"] += 1
    return stats


def track_record_view(stats: Mapping[str, int] | None) -> dict[str, int]:
    """What Blue sees next to a case (counts only, no verdict)."""
    if not stats:
        return {"offered_before": 0}
    return {"offered_before": stats["offered"], "followed_by_a_fix": stats["fixed_after"],
            "fix_needed_more_attempts_same_change_type": stats["escalation_fix_agreed"],
            "fix_needed_more_attempts_different_change_type": stats["escalation_fix_differed"],
            "no_fix": stats["unresolved"]}
