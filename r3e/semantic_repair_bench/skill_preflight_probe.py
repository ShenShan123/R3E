"""No-LLM skill preflight probe.

Before frontend/backend LLM debugging, ask whether the current skill registry
already contains a transferable repair policy/strategy. This probe measures:
  - routed skill coverage
  - specific-vs-default routing
  - policy distribution
  - theoretical budget vs universal k6n3
  - optional actual budget from a prior repair run records file

It does not call the LLM and does not attempt new repairs.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from skill_registry import infer_bug_family, load_registry, make_strategy_recall, route

S = Path(".iso_semrepair")


def _load_cases(suite: str) -> list[dict]:
    if suite == "redfixed":
        path = S / "blue_abl/poison_pool.jsonl"
    elif suite == "cirfix":
        path = S / "survey/valid_functional.jsonl"
    else:
        raise ValueError(f"unknown suite {suite}")
    return [json.loads(l) for l in open(path) if l.strip()]


def _actual_budget_from_records(records_path: Path, arm: str) -> dict:
    if not records_path.exists():
        return {}
    data = json.load(open(records_path))
    arm_records = data.get("records", {}).get(arm, [])
    if not arm_records:
        return {}
    by_rep = defaultdict(list)
    for rec in arm_records:
        by_rep[rec["rep"]].append(rec)
    rep_rows = []
    for rep, rows in sorted(by_rep.items()):
        rep_rows.append({
            "rep": rep,
            "n_cases": len(rows),
            "success_rate": sum(bool(r.get("repaired")) for r in rows) / len(rows),
            "candidate_budget": sum(int(r.get("n_tried") or 0) for r in rows),
            "evidence_budget": sum(int(r.get("evidence_budget") or 0) for r in rows),
        })
    return {
        "source": str(records_path),
        "arm": arm,
        "reps": rep_rows,
        "mean_success_rate": sum(r["success_rate"] for r in rep_rows) / len(rep_rows),
        "mean_candidate_budget": sum(r["candidate_budget"] for r in rep_rows) / len(rep_rows),
        "mean_evidence_budget": sum(r["evidence_budget"] for r in rep_rows) / len(rep_rows),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["redfixed", "cirfix"], default="redfixed")
    ap.add_argument("--registry", default=str(S / "skills.json"))
    ap.add_argument("--actual-records", default=str(S / "k1n3_policy_probe.json"))
    ap.add_argument("--actual-arm", default="B3_prime_routed_strategy_k1_default")
    ap.add_argument("--out", default=str(S / "skill_preflight_probe.json"))
    args = ap.parse_args()

    cases = _load_cases(args.suite)
    registry = load_registry(args.registry)
    strategy_recall = make_strategy_recall(args.registry)

    rows = []
    policy_counts = Counter()
    skill_counts = Counter()
    family_counts = Counter()
    specific_hits = 0
    default_hits = 0
    strategy_hits = 0
    baseline_hits = 0
    for case in cases:
        family = infer_bug_family(case)
        policy, skill_id = route(case, registry)
        strategy = strategy_recall(case)
        is_specific = skill_id != "baseline" and skill_id != "r3e_enhanced_harness_default"
        is_default = skill_id == "r3e_enhanced_harness_default"
        if is_specific:
            specific_hits += 1
        if is_default:
            default_hits += 1
        if skill_id == "baseline":
            baseline_hits += 1
        if strategy:
            strategy_hits += 1
        family_counts[family] += 1
        skill_counts[skill_id] += 1
        policy_key = f"k{policy['evidence_k']}n{policy['n_candidates']}"
        policy_counts[policy_key] += 1
        rows.append({
            "design": case.get("design_name"),
            "mutation_type": case.get("mutation_type", ""),
            "family": family,
            "skill_id": skill_id,
            "policy": policy,
            "policy_key": policy_key,
            "has_strategy": bool(strategy),
            "route_type": "specific" if is_specific else ("default" if is_default else "baseline"),
        })

    max_candidate_budget = sum(int(r["policy"]["n_candidates"]) for r in rows)
    max_evidence_budget = sum(
        int(r["policy"]["evidence_k"]) * int(r["policy"]["n_candidates"])
        for r in rows
    )
    universal_k6n3_candidate_budget = len(rows) * 3
    universal_k6n3_evidence_budget = len(rows) * 6 * 3
    universal_k1n3_candidate_budget = len(rows) * 3
    universal_k1n3_evidence_budget = len(rows) * 3

    actual = _actual_budget_from_records(Path(args.actual_records), args.actual_arm)
    result = {
        "suite": args.suite,
        "n_cases": len(rows),
        "registry": args.registry,
        "coverage": {
            "specific_skill_hits": specific_hits,
            "default_skill_hits": default_hits,
            "baseline_hits": baseline_hits,
            "strategy_hits": strategy_hits,
            "specific_or_default_rate": (specific_hits + default_hits) / len(rows) if rows else 0.0,
            "strategy_rate": strategy_hits / len(rows) if rows else 0.0,
        },
        "family_counts": dict(family_counts),
        "skill_counts": dict(skill_counts),
        "policy_counts": dict(policy_counts),
        "theoretical_budget": {
            "preflight_policy_candidate_budget_max": max_candidate_budget,
            "preflight_policy_evidence_budget_max": max_evidence_budget,
            "universal_k6n3_candidate_budget_max": universal_k6n3_candidate_budget,
            "universal_k6n3_evidence_budget_max": universal_k6n3_evidence_budget,
            "universal_k1n3_candidate_budget_max": universal_k1n3_candidate_budget,
            "universal_k1n3_evidence_budget_max": universal_k1n3_evidence_budget,
            "evidence_budget_reduction_vs_universal_k6n3": (
                1.0 - max_evidence_budget / universal_k6n3_evidence_budget
            ) if universal_k6n3_evidence_budget else 0.0,
        },
        "actual_budget_from_records": actual,
        "rows": rows,
        "interpretation": (
            "Preflight can route before LLM debugging. It can save budget by selecting k1n3/k1n1 "
            "and by attaching a concise family strategy. It only skips LLM calls if a future exact "
            "template/deterministic patch succeeds; current generalized skills mainly reduce prompt "
            "evidence volume and avoid raw k6."
        ),
    }
    with open(args.out, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"skill preflight probe: suite={args.suite}, cases={len(rows)}")
    print(f"  coverage specific/default: {specific_hits + default_hits}/{len(rows)}")
    print(f"  strategy hits: {strategy_hits}/{len(rows)}")
    print(f"  policy counts: {dict(policy_counts)}")
    print(
        "  theoretical evidence budget: "
        f"{max_evidence_budget} vs universal k6n3 {universal_k6n3_evidence_budget} "
        f"({result['theoretical_budget']['evidence_budget_reduction_vs_universal_k6n3']*100:.1f}% reduction)"
    )
    if actual:
        print(
            "  actual from records: "
            f"success={actual['mean_success_rate']:.3f}, "
            f"candidate_budget={actual['mean_candidate_budget']:.1f}, "
            f"evidence_budget={actual['mean_evidence_budget']:.1f}"
        )
    print(f"  wrote {args.out}")


if __name__ == "__main__":
    main()
