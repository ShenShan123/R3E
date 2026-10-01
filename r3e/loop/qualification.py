"""Paired qualification of candidate knowledge, and usage monitoring.

**Qualification.** For each case in the frozen qualification set, the knowledge
pool shown to Blue *with* the candidate is compared with the pool *without*
it. Only cases where the two bundles differ can be affected by the candidate.
Those cases are re-run under both conditions with the same seed, budget and
executor, and without escalation. Cases whose bundle is identical receive the
identical prompt, so they are preserved by construction and are not re-run.

Promotion requires all three conditions:
- ``coverage >= min_coverage``: the candidate was actually shown;
- ``helped - harmed >= min_net_gain``;
- ``harmed <= max_harmed``: harmed covers both displacement regressions and
  regressions on cases the candidate targets.

``objective`` selects what counts as help. Under ``solve_rate``, only newly
solved cases count. Under ``cost``, fewer attempts on a case solved in both
arms also counts, matching the spec's alternative primary objective.

A gate that activates nothing has demonstrated no utility, so a candidate with
zero coverage is ``rejected`` with reason ``no_coverage``.

**Usage monitoring.** After activation, every encounter that showed an item is
tallied. An item is suspended when its within-budget solve rate while shown
falls ``margin`` below the chain's no-knowledge baseline, given enough uses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from r3e.knowledge import KnowledgeItem, build_bundle

from .blue import BlueRunner
from .corpus import Challenge
from .state import RunState


@dataclass
class QualificationConfig:
    min_coverage: int = 1
    min_net_gain: int = 1
    max_harmed: int = 0
    max_cases: int = 8
    seed: int = 7
    # Primary objective, frozen before formal runs:
    # "solve_rate": gain = cases newly solved within budget;
    # "cost": cases solved in both arms with fewer attempts also count as
    #         gains, and cases needing more attempts count as harm.
    objective: str = "solve_rate"


@dataclass
class QualificationSet:
    """Frozen cases plus their precomputed profiles (no model calls)."""

    challenges: list[Challenge]
    profiles: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build(cls, runner: BlueRunner, challenges: Sequence[Challenge]) -> "QualificationSet":
        out = cls(list(challenges))
        for ch in out.challenges:
            out.profiles[ch.challenge_id] = runner.initial(ch)[1]
        return out


def _shown(bundle) -> list[str]:
    return list(bundle.selection_detail.get("shown_item_ids", []))


def qualify_candidate(
    candidate: KnowledgeItem,
    *,
    runner: BlueRunner,
    state: RunState,
    qset: QualificationSet,
    config: QualificationConfig,
    round_index: int,
) -> dict[str, Any]:
    active = state.active_items()
    inference = state.inference()
    cases = []
    for ch in qset.challenges:
        profile = qset.profiles[ch.challenge_id]
        posterior = inference.posterior(profile)
        without = build_bundle("matched", profile=profile, type_posterior=posterior, pool=active,
                               matcher=state.matcher, case_id=ch.challenge_id, seed=config.seed)
        with_c = build_bundle("matched", profile=profile, type_posterior=posterior, pool=[*active, candidate],
                              matcher=state.matcher, case_id=ch.challenge_id, seed=config.seed)
        if _shown(without) != _shown(with_c):
            cases.append(ch)
    cases = cases[: config.max_cases]
    outcomes = []
    key = f"{candidate.item_id}@v{candidate.version}"
    for ch in cases:
        runs = {}
        for label, pool in (("without", active), ("with", [*active, candidate])):
            enc = runner.run(ch, mode="matched", pool=pool, inference=inference, matcher=state.matcher,
                             seed=config.seed, allow_escalation=False, phase="qualification")
            runs[label] = enc
        shown = key in runs["with"].record()["shown_items"]
        outcomes.append({
            "challenge_id": ch.challenge_id,
            "candidate_shown": shown,
            "solved_without": runs["without"].solved_within_budget,
            "solved_with": runs["with"].solved_within_budget,
            "attempts_without": len(runs["without"].attempts),
            "attempts_with": len(runs["with"].attempts),
        })
    coverage = sum(o["candidate_shown"] for o in outcomes)
    helped = sum(o["solved_with"] and not o["solved_without"] for o in outcomes)
    harmed = sum(o["solved_without"] and not o["solved_with"] for o in outcomes)
    fewer_attempts = sum(
        o["solved_with"] and o["solved_without"] and o["attempts_with"] < o["attempts_without"]
        for o in outcomes
    )
    more_attempts = sum(
        o["solved_with"] and o["solved_without"] and o["attempts_with"] > o["attempts_without"]
        for o in outcomes
    )
    if config.objective not in {"solve_rate", "cost"}:
        raise ValueError("qualification objective must be solve_rate or cost")
    if config.objective == "cost":
        helped += fewer_attempts
        harmed += more_attempts
    if coverage < config.min_coverage:
        decision, reason = "rejected", "no_coverage"
    elif harmed > config.max_harmed:
        decision, reason = "rejected", "regression"
    elif helped - harmed < config.min_net_gain:
        decision, reason = "rejected", "no_net_gain"
    else:
        decision, reason = "active", "net_gain_without_regression"
    report = {
        "round": round_index,
        "item_id": candidate.item_id,
        "version": candidate.version,
        "item_hash": candidate.item_hash,
        "affected_cases": len(cases),
        "coverage": coverage,
        "helped": helped,
        "harmed": harmed,
        "fewer_attempts_when_both_solved": fewer_attempts,
        "more_attempts_when_both_solved": more_attempts,
        "objective": config.objective,
        "decision": decision,
        "reason": reason,
        "outcomes": outcomes,
        "config": config.__dict__,
    }
    if decision == "active":
        state.store.transition(candidate.item_id, candidate.version, "qualified",
                               reason="paired replay passed", evidence={"helped": helped, "harmed": harmed})
        state.store.transition(candidate.item_id, candidate.version, "active", reason=reason)
    else:
        state.store.transition(candidate.item_id, candidate.version, "rejected", reason=reason,
                               evidence={"coverage": coverage, "helped": helped, "harmed": harmed})
    state.append("qualification", report)
    return report


@dataclass
class MonitorConfig:
    min_uses: int = 4
    min_baseline: int = 4
    margin: float = 0.25


def monitor_usage(state: RunState, config: MonitorConfig, *, round_index: int) -> list[dict[str, Any]]:
    """Suspend active items whose use correlates with failure."""
    encounters = [row["encounter"] for row in state.read("encounters") if row["encounter"]["mode"] == "matched"]
    baseline = [e["solved_within_budget"] for e in encounters if not e["shown_items"]]
    actions = []
    if len(baseline) < config.min_baseline:
        return actions
    base_rate = sum(baseline) / len(baseline)
    for item in state.active_items():
        key = f"{item.item_id}@v{item.version}"
        shown = [e["solved_within_budget"] for e in encounters if key in e["shown_items"]]
        if len(shown) < config.min_uses:
            continue
        rate = sum(shown) / len(shown)
        if rate < base_rate - config.margin:
            state.store.transition(item.item_id, item.version, "suspended",
                                   reason="usage monitor: solve rate below baseline",
                                   evidence={"uses": len(shown), "rate": rate, "baseline": base_rate})
            actions.append({"item": key, "uses": len(shown), "rate": rate, "baseline": base_rate})
    state.append("monitor", {"round": round_index, "baseline_n": len(baseline),
                             "baseline_rate": base_rate, "suspended": actions})
    return actions
