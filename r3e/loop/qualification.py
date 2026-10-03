"""Paired qualification of candidate knowledge, and usage monitoring.

**Qualification.** For each case in the frozen qualification set, the knowledge
pool shown to Blue *with* the candidate is compared with the pool *without*
it. Only cases where the two bundles differ can be affected by the candidate.
Those cases are re-run under both conditions with the same seed, budget and
executor, and without escalation. Cases whose bundle is identical receive the
identical prompt, so they are preserved by construction and are not re-run.

Each affected case is run ``repeats`` times, with different seeds, because
run-to-run noise is large. Each run is a **fork**: Blue's first attempt (no
memory, since memory is offered from attempt 2) is made once. If it fixes the
bug, the case is simple and both arms would be identical, so it counts as a
tie with no second run. Otherwise the same failed attempt continues twice:
without the candidate and with it. The difference is exactly what the
candidate added after a failure, with first-attempt noise removed.

Promotion requires all of:
- ``coverage >= min_coverage``: the candidate actually reached Blue;
- discordant run pairs ``helped - harmed >= min_net_gain``, with an exact
  one-sided sign test ``p <= max_p_value``;
- at most ``max_harmed`` cases that each lose ``regression_runs`` or more runs
  (displacement or targeted regressions).

Cost control: the without-candidate arm is shared through ``baseline_cache``
by every candidate qualified against the same active pool in a round, and a
first-attempt fix costs one run instead of two.

``objective`` selects what counts as help. Under ``solve_rate``, only newly
solved cases count. Under ``cost``, fewer attempts on a case solved in both
arms also counts, matching the spec's alternative primary objective.

A candidate that never reached Blue (every first attempt succeeded) has no
evidence either way: with ``pending_if_undelivered`` it stays a candidate
(``pending``/``not_delivered``); otherwise it is ``rejected`` (``no_coverage``).

**Usage monitoring.** After activation, every encounter that showed an item is
tallied. An item is suspended when its within-budget solve rate while shown
falls ``margin`` below the chain's no-knowledge baseline, given enough uses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from r3e.knowledge import KnowledgeItem, build_bundle
from r3e.protocol.hashing import hash_payload

from .blue import BlueRunner, first_attempt
from .corpus import Challenge
from .state import RunState


@dataclass
class QualificationConfig:
    min_coverage: int = 1
    # Paired runs per affected case. Run-to-run noise is large: in the
    # 2026-10-01 smoke pilot, 3 of 7 identical-prompt cases flipped outcome.
    repeats: int = 3
    min_net_gain: int = 2          # discordant pairs helped - harmed
    max_p_value: float = 0.2       # exact one-sided sign test on discordant pairs
    regression_runs: int = 2       # a case regresses if it loses >= this many runs
    max_harmed: int = 0            # cases allowed to regress
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


def _sign_test(helped: int, harmed: int) -> float:
    """Exact one-sided binomial P(X >= helped | n = helped + harmed, p = 0.5)."""
    n = helped + harmed
    if n == 0:
        return 1.0
    from math import comb
    return sum(comb(n, k) for k in range(helped, n + 1)) / 2 ** n


def _shown(bundle) -> list[str]:
    return list(bundle.selection_detail.get("shown_item_ids", []))


def affected_cases(candidate: KnowledgeItem, *, state: RunState, qset: QualificationSet,
                   seed: int) -> list[Challenge]:
    """Cases whose delivered knowledge changes when the candidate joins the pool (no calls)."""
    active = state.active_items()
    inference = state.inference()
    out = []
    for ch in qset.challenges:
        profile = qset.profiles[ch.challenge_id]
        posterior = inference.posterior(profile)
        without = build_bundle("matched", profile=profile, type_posterior=posterior, pool=active,
                               matcher=state.matcher, case_id=ch.challenge_id, seed=seed)
        with_c = build_bundle("matched", profile=profile, type_posterior=posterior, pool=[*active, candidate],
                              matcher=state.matcher, case_id=ch.challenge_id, seed=seed)
        if _shown(without) != _shown(with_c):
            out.append(ch)
    return out


def qualify_candidate(
    candidate: KnowledgeItem,
    *,
    runner: BlueRunner,
    state: RunState,
    qset: QualificationSet,
    config: QualificationConfig,
    round_index: int,
    baseline_cache: dict | None = None,
    pending_if_undelivered: bool = False,
) -> dict[str, Any]:
    """Paired fork test of one candidate; see the module docstring.

    With ``pending_if_undelivered``, a candidate that was retrieved but never
    reached Blue (every first attempt succeeded) is left pending rather than
    rejected: there was no evidence about it either way.
    """
    active = state.active_items()
    inference = state.inference()
    cases = affected_cases(candidate, state=state, qset=qset, seed=config.seed)[: config.max_cases]
    if config.objective not in {"solve_rate", "cost"}:
        raise ValueError("qualification objective must be solve_rate or cost")
    outcomes = []
    key = f"{candidate.item_id}@v{candidate.version}"
    helped = harmed = 0  # discordant run pairs
    pool_key = hash_payload(sorted(i.item_hash for i in active))
    cache = baseline_cache if baseline_cache is not None else {}
    saved_runs = 0

    def without_run(ch, seed):
        # The without-candidate arm depends only on (case, seed, active pool),
        # so it is shared by every candidate qualified against this pool.
        cache_key = (ch.challenge_id, seed, pool_key)
        if cache_key not in cache:
            cache[cache_key] = runner.run(ch, mode="matched", pool=active, inference=inference,
                                          matcher=state.matcher, seed=seed, allow_escalation=False,
                                          phase="qualification")
        return cache[cache_key]

    for ch in cases:
        per_case = {"challenge_id": ch.challenge_id, "candidate_shown": False, "runs": []}
        seeds = [config.seed + 1009 * rep for rep in range(config.repeats)]
        for seed in seeds:
            # Fork: Blue's first attempt (no memory) is shared by both arms. If it
            # fixes the bug, the case is simple and the arms are identical (a tie,
            # no second run). Otherwise the same failed attempt continues twice:
            # without the candidate, and with it offered from attempt 2.
            without = without_run(ch, seed)
            first = first_attempt(without)
            if without.inconclusive or first is None:
                per_case["runs"].append({"seed": seed, "inconclusive": True})
                continue
            if first.get("verdict_tier") in {"visible_pass", "hidden_pass", "formal_pass"}:
                per_case["runs"].append({"seed": seed, "solved_first_attempt": True})
                saved_runs += 1
                continue
            with_run = runner.run(ch, mode="matched", pool=[*active, candidate], inference=inference,
                                  matcher=state.matcher, seed=seed, allow_escalation=False,
                                  phase="qualification", reuse_first=first)
            per_case["candidate_shown"] |= key in with_run.record()["shown_items"]
            if with_run.inconclusive:
                per_case["runs"].append({"seed": seed, "inconclusive": True})
                continue
            sw, so = with_run.solved_within_budget, without.solved_within_budget
            aw, ao = len(with_run.repair_attempts), len(without.repair_attempts)
            better = (sw and not so) or (config.objective == "cost" and sw and so and aw < ao)
            worse = (so and not sw) or (config.objective == "cost" and sw and so and aw > ao)
            helped += better
            harmed += worse
            per_case["runs"].append({"seed": seed, "solved_with": sw, "solved_without": so,
                                     "attempts_with": aw, "attempts_without": ao})
        per_case["baseline_saturated"] = all(r.get("solved_first_attempt") for r in per_case["runs"])
        valid = [r for r in per_case["runs"] if "solved_with" in r]
        per_case["net"] = sum(r["solved_with"] - r["solved_without"] for r in valid)
        per_case["inconclusive_pairs"] = sum(1 for r in per_case["runs"] if r.get("inconclusive"))
        outcomes.append(per_case)
    coverage = sum(o["candidate_shown"] for o in outcomes)
    regressions = sum(o["net"] <= -config.regression_runs for o in outcomes)
    p_value = _sign_test(helped, harmed)
    if coverage < config.min_coverage and pending_if_undelivered:
        decision, reason = "pending", "not_delivered"
    elif coverage < config.min_coverage:
        decision, reason = "rejected", "no_coverage"
    elif regressions > config.max_harmed:
        decision, reason = "rejected", "regression"
    elif helped - harmed < config.min_net_gain:
        decision, reason = "rejected", "no_net_gain"
    elif p_value > config.max_p_value:
        decision, reason = "rejected", "not_significant"
    else:
        decision, reason = "active", "net_gain_without_regression"
    valid_runs = [r for o in outcomes for r in o["runs"] if "solved_with" in r]
    fewer_attempts = sum(r["solved_with"] and r["solved_without"] and r["attempts_with"] < r["attempts_without"]
                         for r in valid_runs)
    more_attempts = sum(r["solved_with"] and r["solved_without"] and r["attempts_with"] > r["attempts_without"]
                        for r in valid_runs)
    report = {
        "round": round_index,
        "item_id": candidate.item_id,
        "version": candidate.version,
        "item_hash": candidate.item_hash,
        "affected_cases": len(cases),
        "coverage": coverage,
        "helped": helped,
        "harmed": harmed,
        "regressing_cases": regressions,
        "sign_test_p": p_value,
        "repeats": config.repeats,
        "with_runs_skipped_saturated": saved_runs,
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
    elif decision == "rejected":
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
    """Suspend active items whose use correlates with failure.

    Memory reaches Blue only after a failed first attempt, so an encounter
    that received an item is conditioned on that failure. The comparison is
    therefore made among encounters whose first attempt failed: those that
    received the item vs. those that received no memory.
    """
    encounters = [row["encounter"] for row in state.read("encounters")
                  if row["encounter"]["mode"] == "matched" and not row["encounter"].get("inconclusive")]

    def first_failed(e) -> bool:
        tries = [a for a in e.get("attempts", []) if not a.get("infra_failure")]
        return bool(tries) and tries[0].get("verdict_tier") not in {"visible_pass", "hidden_pass"}

    encounters = [e for e in encounters if first_failed(e)]
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
