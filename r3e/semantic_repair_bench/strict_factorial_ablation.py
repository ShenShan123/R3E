"""Strict 2x2 ablation for evidence_k x n_candidates.

This replaces the old single-run k/n grid with an N=3 same-suite rerun:
  k1n1, k6n1, k1n3, k6n3, no memory.
Default suite is Red-Fixed-12 to match the P2-P6 strict skill-promotion matrix;
use --suite cirfix for the larger CirFix-39 appendix rerun.
Results are written incrementally so long runs can be monitored/resumed manually.
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
CONFIGS = [
    ("k1n1", 1, 1),
    ("k6n1", 6, 1),
    ("k1n3", 1, 3),
    ("k6n3", 6, 3),
]


def _stats(rates: list[float]) -> dict:
    return {
        "mean": statistics.mean(rates),
        "std": statistics.stdev(rates) if len(rates) > 1 else 0.0,
        "rates": rates,
    }


def _ci_delta(a: dict, b: dict, n: int = N) -> dict:
    delta = b["mean"] - a["mean"]
    sem = math.sqrt((a["std"] / math.sqrt(n)) ** 2 + (b["std"] / math.sqrt(n)) ** 2)
    ci = 1.96 * sem
    return {"delta": delta, "ci95_half_width": ci, "ci95_delta": [delta - ci, delta + ci]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["redfixed", "cirfix"], default="redfixed")
    args = ap.parse_args()
    manifest = S / ("blue_abl/poison_pool.jsonl" if args.suite == "redfixed"
                    else "survey/valid_functional.jsonl")
    suite_name = "Red-Fixed-12" if args.suite == "redfixed" else "CirFix-39"
    out_path = S / f"strict_factorial_ablation_{args.suite}.json"
    partial_path = S / f"strict_factorial_ablation_{args.suite}.partial.json"
    cases = [json.loads(l) for l in open(manifest) if l.strip()]
    out = {
        "suite": suite_name,
        "n_cases": len(cases),
        "reps": N,
        "configs": {},
        "effects": {},
        "status": "running",
        "source": "r3e/semantic_repair_bench/strict_factorial_ablation.py",
    }
    print(f"=== strict factorial ablation: {suite_name}, {len(cases)} cases, N={N}, no-memory ===", flush=True)
    for label, evidence_k, n_candidates in CONFIGS:
        rates = []
        rep_records = []
        for rep in range(N):
            ok = 0
            records = []
            for c in cases:
                r = repair_one(
                    c,
                    S / "strict_factorial" / f"{label}_r{rep}" / c["design_name"],
                    recall_fn=None,
                    evidence_k=evidence_k,
                    n_candidates=n_candidates,
                )
                rec = {
                    "design": c["design_name"],
                    "repaired": bool(r.get("repaired")),
                    "n_tried": r.get("n_candidates_tried"),
                    "note": r.get("note", ""),
                    "buggy_stage": r.get("buggy_stage", ""),
                    "error": r.get("error", ""),
                    "apply_error": r.get("apply_error", ""),
                }
                records.append(rec)
                ok += rec["repaired"]
            rate = ok / len(cases)
            rates.append(rate)
            rep_records.append({"rep": rep, "rate": rate, "records": records})
            out["configs"][label] = {
                "evidence_k": evidence_k,
                "n_candidates": n_candidates,
                **_stats(rates),
                "rep_records": rep_records,
            }
            with open(partial_path, "w") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
            print(f"  {label} rep{rep}: {ok}/{len(cases)} = {rate:.3f}", flush=True)

    c = out["configs"]
    out["effects"] = {
        "multi_candidate_at_k1": _ci_delta(c["k1n1"], c["k1n3"]),
        "multi_candidate_at_k6": _ci_delta(c["k6n1"], c["k6n3"]),
        "evidence_k_at_n1": _ci_delta(c["k1n1"], c["k6n1"]),
        "evidence_k_at_n3": _ci_delta(c["k1n3"], c["k6n3"]),
        "full_harness_vs_baseline": _ci_delta(c["k1n1"], c["k6n3"]),
    }
    out["status"] = "complete"
    with open(out_path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    if partial_path.exists():
        partial_path.unlink()
    print("\n=== effects ===", flush=True)
    for k, v in out["effects"].items():
        print(f"  {k}: Δ{v['delta']*100:+.1f}pp 95%CI[{v['ci95_delta'][0]*100:+.1f},{v['ci95_delta'][1]*100:+.1f}]", flush=True)


if __name__ == "__main__":
    main()
