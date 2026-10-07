"""Part A report: does the adaptive Red find Blue's weaknesses better than chance?

Reads curriculum runs (one state directory per Red arm) and reports, per arm:
- M1 hit rate per admitted bug, overall and by source (VerilogEval / RTLLM /
  benchmark), in two forms: candidate hits (reproducible failures, ``screens``
  ledger) and valid hits (candidate hits reviewed as PASS in
  ``STATE_DIR/hit_decisions.json``, see ``hit_review.py``). Excluded and
  pending candidates are counted; an unreviewed candidate counts as pending.
  M1b-M3 use valid hits;
- M1b efficiency: model calls (all phases) per reproducible failure found;
- M2 recurrence: of follow-up variants (``same`` / ``harder``) on new designs,
  the share that Blue also fails reproducibly, next to the exploration rate;
- M3 diversity: distinct weakness classes among the hits (operator set) and
  the design families they sit on;
- observations: how Blue's first run on each admitted bug went, kept apart
  because they mean different things: fixed on the first attempt, fixed after
  a wrong repair, fixed after running out of output budget (no answer), not
  fixed with at least one wrong repair, not fixed with only no-answer attempts;
- transfer: follow-up hits counted by design problem (a ChipBench problem's bug
  types share one design family), never by carrier.
For a pilot with branches, report each branch's directory separately
(``seed/``, ``cold/``, ``warm/``); the root ``calls.jsonl`` is the shared budget.
The first two arms are compared on M1 with Fisher's exact test (two-sided),
for valid hits and for candidate hits.

Usage: python -m experiments.loop_probes.red_hits LABEL=STATE_DIR [LABEL=STATE_DIR ...]
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

from r3e.loop.state import RunState

from .hit_review import cut_screens, decisions_path


def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """p for the 2x2 table [[a, b], [c, d]] (rows: arms; columns: hit, miss)."""
    n, row, col = a + b + c + d, a + b, a + c

    def p(x: int) -> float:
        return math.comb(col, x) * math.comb(n - col, row - x) / math.comb(n, row)
    observed = p(a)
    lo, hi = max(0, row + col - n), min(row, col)
    return min(1.0, sum(p(x) for x in range(lo, hi + 1) if p(x) <= observed * (1 + 1e-9)))


def _source(screen: dict) -> str:
    """ve / rtllm (generated designs, ``ve:Prob..``) or the benchmark (``chipbench_Prob..``)."""
    return screen["cluster_id"].split(":")[0].split("_")[0]


def _observation(encounter: dict) -> str:
    tiers = [a.get("verdict_tier") for a in encounter.get("attempts", []) if not a.get("infra_failure")]
    wrong = any(t in ("visible_fail", "compile_fail") for t in tiers)
    if encounter.get("inconclusive"):
        return "inconclusive"
    if encounter.get("solved_within_budget"):
        if len(tiers) <= 1:
            return "fixed_first_attempt"
        return "fixed_after_wrong_repair" if wrong else "fixed_after_no_answer"
    return "not_fixed_wrong_repair" if wrong else "not_fixed_no_answer_only"


def arm_report(state_dir: Path) -> dict:
    state = RunState(state_dir)
    # screens cut short by a call cap but already meeting the rule count too (marked)
    screens = [*state.read("screens"), *cut_screens(state)]
    calls = state.read("calls")
    screened = {s["challenge_id"] for s in screens}
    primary = [row["encounter"] for row in state.read("encounters")
               if not row.get("confirmation") and row["encounter"]["challenge_id"] in screened]
    path = decisions_path(state_dir)
    review = json.loads(path.read_text()) if path.exists() else {}

    def verdict(s):
        return (review.get(s["challenge_id"]) or {}).get("decision") or "PENDING"
    hits = [s for s in screens if s["reproducible"] and verdict(s) == "PASS"]

    def rate(rows):
        cand = [s for s in rows if s["reproducible"]]
        k = sum(verdict(s) == "PASS" for s in cand)
        return {"bugs": len(rows), "candidate_hits": len(cand), "hits": k,
                "excluded": sum(verdict(s) == "EXCLUDED" for s in cand),
                "pending": sum(verdict(s) == "PENDING" for s in cand),
                "rate": round(k / len(rows), 3) if rows else None,
                "candidate_rate": round(len(cand) / len(rows), 3) if rows else None}
    follow = [s for s in screens if s["direction"] in ("same", "harder", "simpler")]
    explore = [s for s in screens if s["direction"] == "explore"]
    by_phase = Counter(c.get("phase") for c in calls)
    return {
        "red_proposals": len(state.read("red")),
        "M1_hit_rate": rate(screens),
        "M1_by_source": {src: rate([s for s in screens if _source(s) == src])
                         for src in sorted({_source(s) for s in screens})},
        "M1b_calls": {"total": len(calls), "by_phase": dict(by_phase),
                      "per_hit": round(len(calls) / len(hits), 1) if hits else None},
        "M2_recurrence": {"follow_ups": rate(follow), "explorations": rate(explore)},
        "M3_diversity": {"operator_sets": len({tuple(sorted(s["edit_kinds"])) for s in hits}),
                         "design_families": len({s["cluster_id"] for s in hits}),
                         "hits_by_operator_set": dict(Counter("+".join(sorted(s["edit_kinds"])) for s in hits))},
        "fails_out_of_runs": dict(Counter(f"{s['fails']}/{s['runs']}" for s in screens)),
        "observations": dict(Counter(_observation(e) for e in primary)),
        "transfer": {"follow_up_valid_hits": sum(s["reproducible"] and verdict(s) == "PASS" for s in follow),
                     "problems": sorted({s["cluster_id"] for s in follow
                                         if s["reproducible"] and verdict(s) == "PASS"})},
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("arms", nargs="+", help="LABEL=STATE_DIR")
    args = p.parse_args()
    reports = {}
    for arm in args.arms:
        label, _, path = arm.partition("=")
        reports[label] = arm_report(Path(path))
    out = {"arms": reports}
    if len(reports) >= 2:
        (l1, r1), (l2, r2) = list(reports.items())[:2]
        a, b = r1["M1_hit_rate"]["hits"], r1["M1_hit_rate"]["bugs"] - r1["M1_hit_rate"]["hits"]
        c, d = r2["M1_hit_rate"]["hits"], r2["M1_hit_rate"]["bugs"] - r2["M1_hit_rate"]["hits"]
        m1, m2 = r1["M1_hit_rate"], r2["M1_hit_rate"]
        out["M1_fisher"] = {"arms": [l1, l2], "p_two_sided": round(fisher_two_sided(a, b, c, d), 4),
                            "candidate_p_two_sided": round(fisher_two_sided(
                                m1["candidate_hits"], m1["bugs"] - m1["candidate_hits"],
                                m2["candidate_hits"], m2["bugs"] - m2["candidate_hits"]), 4)}
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
