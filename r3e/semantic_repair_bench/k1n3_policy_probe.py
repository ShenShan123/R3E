"""Minimal B1'/B3' probe: replace k6n3 policy with k1n3.

Goal: test whether removing raw evidence-k preserves repair rate while reducing
prompt evidence budget. This intentionally leaves the main frozen_probe_curve
result untouched and writes a separate JSON result.
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
from skill_registry import load_registry, make_strategy_recall, route

S = Path(".iso_semrepair")
N = 3


def _ci95(a: tuple[float, float, list[float]], b: tuple[float, float, list[float]]) -> float:
    return 1.96 * math.sqrt((a[1] / math.sqrt(N)) ** 2 + (b[1] / math.sqrt(N)) ** 2)


def _downgrade_k6_to_k1(policy: dict) -> dict:
    pol = dict(policy)
    if pol.get("evidence_k") == 6 and pol.get("n_candidates") == 3:
        pol["evidence_k"] = 1
    return pol


def _write_result(summary, effects, records, n_cases):
    result = {
        "suite": "Red-Fixed-12 B1'/B3' k1n3 policy probe",
        "reps": N,
        "n_cases": n_cases,
        "budget_definitions": {
            "candidate_budget": "sum of n_candidates_tried, matching prior early-stop budget",
            "evidence_budget": "sum of evidence_k * n_candidates_tried, proxy for prompt evidence volume",
        },
        "summary": summary,
        "effects": effects,
        "records": records,
    }
    with open(S / "k1n3_policy_probe.json", "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["all", "prime-only"], default="all")
    args = ap.parse_args()

    registry = load_registry(str(S / "skills.json"))
    strategy_recall = make_strategy_recall()
    probe = [json.loads(l) for l in open(S / "blue_abl/poison_pool.jsonl") if l.strip()]

    all_arms = {
        "B1_old_k6n3": {
            "old_policy": "k6n3",
            "new_policy": None,
            "strategy": False,
        },
        "B1_prime_k1n3": {
            "old_policy": "k6n3",
            "new_policy": "k1n3",
            "strategy": False,
        },
        "B3_old_routed_strategy": {
            "old_policy": "routed+strategy, k6 default",
            "new_policy": None,
            "strategy": True,
        },
        "B3_prime_routed_strategy_k1_default": {
            "old_policy": "routed+strategy, k6 default",
            "new_policy": "routed+strategy, k1 default",
            "strategy": True,
        },
    }
    arms = all_arms
    if args.mode == "prime-only":
        arms = {
            k: all_arms[k]
            for k in ["B1_prime_k1n3", "B3_prime_routed_strategy_k1_default"]
        }

    def policy_for(arm: str, case: dict) -> tuple[int, int, object | None, str]:
        if arm == "B1_old_k6n3":
            return 6, 3, None, "universal_k6n3"
        if arm == "B1_prime_k1n3":
            return 1, 3, None, "universal_k1n3"
        pol, skill_id = route(case, registry)
        if arm == "B3_prime_routed_strategy_k1_default":
            pol = _downgrade_k6_to_k1(pol)
        return pol["evidence_k"], pol["n_candidates"], strategy_recall, skill_id

    print(f"k1n3 policy probe: Red-Fixed-12={len(probe)}, N={N}", flush=True)
    summary = {}
    records = {}
    for arm in arms:
        rates = []
        cand_budgets = []
        evidence_budgets = []
        records[arm] = []
        for rep in range(N):
            ok = 0
            cand_budget = 0
            evidence_budget = 0
            for case in probe:
                k, n, recall_fn, skill_id = policy_for(arm, case)
                r = repair_one(
                    case,
                    S / "k1n3_policy_probe" / f"{arm}_r{rep}" / case["design_name"],
                    recall_fn=recall_fn,
                    evidence_k=k,
                    n_candidates=n,
                )
                hit = bool(r.get("repaired"))
                tried = int(r.get("n_candidates_tried") or n)
                ok += hit
                cand_budget += tried
                evidence_budget += k * tried
                records[arm].append({
                    "rep": rep,
                    "design": case["design_name"],
                    "mutation_type": case.get("mutation_type", ""),
                    "skill_id": skill_id,
                    "evidence_k": k,
                    "n_candidates": n,
                    "n_tried": tried,
                    "evidence_budget": k * tried,
                    "repaired": hit,
                    "error": r.get("error", ""),
                    "apply_error": r.get("apply_error", ""),
                })
            rates.append(ok / len(probe))
            cand_budgets.append(cand_budget)
            evidence_budgets.append(evidence_budget)
            print(
                f"  {arm} rep{rep}: {ok}/{len(probe)} rate={rates[-1]:.3f} "
                f"cand_budget={cand_budget} evidence_budget={evidence_budget}",
                flush=True,
            )
            partial_std = statistics.stdev(rates) if len(rates) > 1 else 0.0
            summary[arm] = {
                **arms[arm],
                "success_mean": statistics.mean(rates),
                "success_std": partial_std,
                "success_rates": list(rates),
                "candidate_budget_mean": statistics.mean(cand_budgets),
                "candidate_budget_std": (
                    statistics.stdev(cand_budgets) if len(cand_budgets) > 1 else 0.0
                ),
                "candidate_budgets": list(cand_budgets),
                "evidence_budget_mean": statistics.mean(evidence_budgets),
                "evidence_budget_std": (
                    statistics.stdev(evidence_budgets) if len(evidence_budgets) > 1 else 0.0
                ),
                "evidence_budgets": list(evidence_budgets),
            }
            _write_result(summary, {}, records, len(probe))
        summary[arm] = {
            **arms[arm],
            "success_mean": statistics.mean(rates),
            "success_std": statistics.stdev(rates) if len(rates) > 1 else 0.0,
            "success_rates": rates,
            "candidate_budget_mean": statistics.mean(cand_budgets),
            "candidate_budget_std": statistics.stdev(cand_budgets) if len(cand_budgets) > 1 else 0.0,
            "candidate_budgets": cand_budgets,
            "evidence_budget_mean": statistics.mean(evidence_budgets),
            "evidence_budget_std": statistics.stdev(evidence_budgets) if len(evidence_budgets) > 1 else 0.0,
            "evidence_budgets": evidence_budgets,
        }

    effects = {}
    for name, old, new in [
        ("B1_old_to_prime", "B1_old_k6n3", "B1_prime_k1n3"),
        ("B3_old_to_prime", "B3_old_routed_strategy", "B3_prime_routed_strategy_k1_default"),
    ]:
        if old not in summary or new not in summary:
            continue
        old_tuple = (
            summary[old]["success_mean"],
            summary[old]["success_std"],
            summary[old]["success_rates"],
        )
        new_tuple = (
            summary[new]["success_mean"],
            summary[new]["success_std"],
            summary[new]["success_rates"],
        )
        delta = summary[new]["success_mean"] - summary[old]["success_mean"]
        ci = _ci95(old_tuple, new_tuple)
        effects[name] = {
            "delta_success": delta,
            "ci95_half_width": ci,
            "ci95_delta": [delta - ci, delta + ci],
            "delta_candidate_budget": (
                summary[new]["candidate_budget_mean"] - summary[old]["candidate_budget_mean"]
            ),
            "delta_evidence_budget": (
                summary[new]["evidence_budget_mean"] - summary[old]["evidence_budget_mean"]
            ),
            "evidence_budget_reduction_pct": (
                1.0 - summary[new]["evidence_budget_mean"] / summary[old]["evidence_budget_mean"]
            ) if summary[old]["evidence_budget_mean"] else 0.0,
        }

    _write_result(summary, effects, records, len(probe))

    print("\n=== summary ===", flush=True)
    for arm, s in summary.items():
        print(
            f"  {arm:36s} {s['success_mean']:.3f}±{s['success_std']:.3f} "
            f"cand_budget={s['candidate_budget_mean']:.1f} "
            f"evidence_budget={s['evidence_budget_mean']:.1f}",
            flush=True,
        )
    for name, e in effects.items():
        print(
            f"  {name}: Δsuccess={e['delta_success']*100:+.1f}pp "
            f"CI±{e['ci95_half_width']*100:.1f}pp, "
            f"Δevidence_budget={e['delta_evidence_budget']:+.1f} "
            f"({e['evidence_budget_reduction_pct']*100:.1f}% reduction)",
            flush=True,
        )


if __name__ == "__main__":
    main()
