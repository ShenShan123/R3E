"""Off-by-one-specific evidence matrix for raw vs hybrid evidence.

This is the narrow follow-up to the small hybrid_evidence_k_probe:
  - 6 recurrence-sensitive off_by_one cases
  - N=3 repetitions
  - primary arms fix n_candidates=1 to isolate evidence quality
  - two n_candidates=3 reference arms test interaction with best-of-N
"""
from __future__ import annotations

import json
import math
import statistics
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from functional_repair import repair_one

S = Path(".iso_semrepair")
N = 3
CASES = [
    "first_counter_overflow__buggy_counter",
    "first_counter_overflow__wadden_buggy1",
    "first_counter_overflow__wadden_buggy2",
    "first_counter_overflow__buggy_overflow",
    "lshift_reg__wadden_buggy1",
    "lshift_reg__kgoliya_buggy1",
]

# label: (evidence_k, structured_evidence, n_candidates)
ARMS = {
    "raw_k1_n1": (1, False, 1),
    "raw_k6_n1": (6, False, 1),
    "hybrid_k1_n1": (1, True, 1),
    "hybrid_k6_n1": (6, True, 1),
    "raw_k1_n3": (1, False, 3),
    "hybrid_k1_n3": (1, True, 3),
}


def _ci95(a: tuple[float, float, list[float]], b: tuple[float, float, list[float]]) -> float:
    return 1.96 * math.sqrt((a[1] / math.sqrt(N)) ** 2 + (b[1] / math.sqrt(N)) ** 2)


def _write_result(summary, effects, per_case, records, n_cases):
    result = {
        "suite": "off_by_one hybrid evidence matrix",
        "reps": N,
        "n_cases": n_cases,
        "cases": CASES,
        "summary": summary,
        "effects": effects,
        "per_case": per_case,
        "records": records,
    }
    with open(S / "off_by_one_hybrid_evidence_matrix.json", "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--arms",
        default=",".join(ARMS.keys()),
        help="Comma-separated arm labels to run; existing JSON summaries for other arms are preserved.",
    )
    args = ap.parse_args()
    selected_arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    bad = [a for a in selected_arms if a not in ARMS]
    if bad:
        raise SystemExit(f"Unknown arms: {bad}")

    allrows = {
        r["design_name"]: r
        for r in (json.loads(l) for l in open(S / "survey/valid_functional.jsonl") if l.strip())
    }
    missing = [name for name in CASES if name not in allrows]
    if missing:
        raise SystemExit(f"Missing cases: {missing}")
    cases = [allrows[name] for name in CASES]

    out = {}
    existing_summary = {}
    existing_per_case = {}
    existing_records = {}
    out_path = S / "off_by_one_hybrid_evidence_matrix.json"
    if out_path.exists():
        existing = json.load(open(out_path))
        existing_summary = existing.get("summary", {})
        existing_per_case = existing.get("per_case", {})
        existing_records = existing.get("records", {})

    per_case = {
        arm: existing_per_case.get(arm, {case["design_name"]: [] for case in cases})
        for arm in ARMS
    }
    records = {arm: existing_records.get(arm, []) for arm in ARMS}
    # Selected arms are fresh reruns; reset their per-case and record payload.
    for arm in selected_arms:
        per_case[arm] = {case["design_name"]: [] for case in cases}
        records[arm] = []

    print(
        f"off_by_one hybrid evidence matrix: {len(cases)} cases x {len(selected_arms)} selected arms x N={N}",
        flush=True,
    )
    for arm in selected_arms:
        k, structured, n_candidates = ARMS[arm]
        rates = []
        cand_budgets = []
        evidence_budgets = []
        for rep in range(N):
            ok = 0
            cand_budget = 0
            evidence_budget = 0
            for case in cases:
                r = repair_one(
                    case,
                    S / "off_by_one_hybrid_matrix" / f"{arm}_r{rep}" / case["design_name"],
                    recall_fn=None,
                    evidence_k=k,
                    n_candidates=n_candidates,
                    structured_evidence=structured,
                )
                hit = bool(r.get("repaired"))
                tried = int(r.get("n_candidates_tried") or n_candidates)
                ok += hit
                cand_budget += tried
                evidence_budget += k * tried
                per_case[arm][case["design_name"]].append(hit)
                records[arm].append({
                    "rep": rep,
                    "design": case["design_name"],
                    "evidence_k": k,
                    "n_candidates": n_candidates,
                    "structured_evidence": structured,
                    "evidence_mode": r.get("evidence_mode", ""),
                    "n_tried": tried,
                    "evidence_budget": k * tried,
                    "repaired": hit,
                    "error": r.get("error", ""),
                    "apply_error": r.get("apply_error", ""),
                    "patch_range": r.get("patch_range"),
                    "llm_rationale": r.get("llm_rationale", ""),
                    "patched_evidence": r.get("patched_evidence", ""),
                })
            rates.append(ok / len(cases))
            cand_budgets.append(cand_budget)
            evidence_budgets.append(evidence_budget)
            print(
                f"  {arm} rep{rep}: {ok}/{len(cases)} rate={rates[-1]:.3f} "
                f"cand_budget={cand_budget} evidence_budget={evidence_budget}",
                flush=True,
            )
            partial_summary = {}
            partial_summary.update(existing_summary)
            for done_arm, done in out.items():
                mean, std, done_rates, done_cand, done_evidence = done
                partial_summary[done_arm] = {
                    "evidence_mode": "hybrid" if ARMS[done_arm][1] else "raw",
                    "evidence_k": ARMS[done_arm][0],
                    "n_candidates": ARMS[done_arm][2],
                    "success_mean": mean,
                    "success_std": std,
                    "success_rates": done_rates,
                    "candidate_budget_mean": statistics.mean(done_cand),
                    "candidate_budgets": done_cand,
                    "evidence_budget_mean": statistics.mean(done_evidence),
                    "evidence_budgets": done_evidence,
                }
            partial_std = statistics.stdev(rates) if len(rates) > 1 else 0.0
            partial_summary[arm] = {
                "evidence_mode": "hybrid" if structured else "raw",
                "evidence_k": k,
                "n_candidates": n_candidates,
                "success_mean": statistics.mean(rates),
                "success_std": partial_std,
                "success_rates": list(rates),
                "candidate_budget_mean": statistics.mean(cand_budgets),
                "candidate_budgets": list(cand_budgets),
                "evidence_budget_mean": statistics.mean(evidence_budgets),
                "evidence_budgets": list(evidence_budgets),
            }
            _write_result(partial_summary, {}, per_case, records, len(cases))
        out[arm] = (
            statistics.mean(rates),
            statistics.stdev(rates) if len(rates) > 1 else 0.0,
            rates,
            cand_budgets,
            evidence_budgets,
        )

    summary = {}
    effects = {}
    summary.update(existing_summary)
    for arm, (mean, std, rates, cand_budgets, evidence_budgets) in out.items():
        k, structured, n_candidates = ARMS[arm]
        summary[arm] = {
            "evidence_mode": "hybrid" if structured else "raw",
            "evidence_k": k,
            "n_candidates": n_candidates,
            "success_mean": mean,
            "success_std": std,
            "success_rates": rates,
            "candidate_budget_mean": statistics.mean(cand_budgets),
            "candidate_budgets": cand_budgets,
            "evidence_budget_mean": statistics.mean(evidence_budgets),
            "evidence_budgets": evidence_budgets,
        }

    baseline_summary = summary.get("raw_k1_n1")
    if baseline_summary:
        baseline = (
            baseline_summary["success_mean"],
            baseline_summary["success_std"],
            baseline_summary["success_rates"],
        )
        for arm, item in summary.items():
            cur = (item["success_mean"], item["success_std"], item["success_rates"])
            delta = item["success_mean"] - baseline[0]
            ci = _ci95(baseline, cur)
            item["delta_vs_raw_k1_n1"] = delta
            item["ci95_vs_raw_k1_n1_half_width"] = ci
            item["ci95_vs_raw_k1_n1_delta"] = [delta - ci, delta + ci]
            item["verdict_vs_raw_k1_n1"] = "significant" if abs(delta) > ci else "noise"

    for label, a, b in [
        ("raw_k1_to_raw_k6_at_n1", "raw_k1_n1", "raw_k6_n1"),
        ("raw_k1_to_hybrid_k1_at_n1", "raw_k1_n1", "hybrid_k1_n1"),
        ("raw_k6_to_hybrid_k6_at_n1", "raw_k6_n1", "hybrid_k6_n1"),
        ("hybrid_k1_to_hybrid_k6_at_n1", "hybrid_k1_n1", "hybrid_k6_n1"),
        ("raw_k1_to_hybrid_k1_at_n3", "raw_k1_n3", "hybrid_k1_n3"),
        ("best_of_n_raw_k1_n1_to_n3", "raw_k1_n1", "raw_k1_n3"),
        ("best_of_n_hybrid_k1_n1_to_n3", "hybrid_k1_n1", "hybrid_k1_n3"),
    ]:
        if a not in summary or b not in summary:
            continue
        aa = summary[a]
        bb = summary[b]
        at = (aa["success_mean"], aa["success_std"], aa["success_rates"])
        bt = (bb["success_mean"], bb["success_std"], bb["success_rates"])
        delta = bb["success_mean"] - aa["success_mean"]
        ci = _ci95(at, bt)
        effects[label] = {
            "from": a,
            "to": b,
            "delta_success": delta,
            "ci95_half_width": ci,
            "ci95_delta": [delta - ci, delta + ci],
            "verdict": "significant" if abs(delta) > ci else "noise",
        }

    _write_result(summary, effects, per_case, records, len(cases))

    print("\n=== summary vs raw_k1_n1 ===", flush=True)
    for arm, s in summary.items():
        print(
            f"  {arm:16s} {s['success_mean']:.3f}±{s['success_std']:.3f} "
            f"Δ={s['delta_vs_raw_k1_n1']*100:+.1f}pp "
            f"CI±{s['ci95_vs_raw_k1_n1_half_width']*100:.1f}pp "
            f"{s['verdict_vs_raw_k1_n1']}",
            flush=True,
        )
    print("\n=== selected effects ===", flush=True)
    for label, e in effects.items():
        print(
            f"  {label:32s} Δ={e['delta_success']*100:+.1f}pp "
            f"CI±{e['ci95_half_width']*100:.1f}pp {e['verdict']}",
            flush=True,
        )


if __name__ == "__main__":
    main()
