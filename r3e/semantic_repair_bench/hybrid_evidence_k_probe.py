"""Small hybrid evidence-k probe.

Tests whether evidence_k still matters after structured evidence was upgraded
to hybrid evidence: structured diagnosis plus raw recurrence controlled by k.
This is intentionally small: 3 recurrence-sensitive off_by_one cases, N=2,
n_candidates=1 to isolate generation evidence from best-of-N selection.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from functional_repair import repair_one

S = Path(".iso_semrepair")
N = 2
CASES = [
    "first_counter_overflow__wadden_buggy2",
    "first_counter_overflow__buggy_overflow",
    "lshift_reg__kgoliya_buggy1",
]
ARMS = {"hybrid_k1": 1, "hybrid_k6": 6}


def _ci95(a: tuple[float, float, list[float]], b: tuple[float, float, list[float]]) -> float:
    return 1.96 * math.sqrt((a[1] / math.sqrt(N)) ** 2 + (b[1] / math.sqrt(N)) ** 2)


def main() -> None:
    allrows = {r["design_name"]: r for r in
               (json.loads(l) for l in open(S / "survey/valid_functional.jsonl") if l.strip())}
    cases = [allrows[n] for n in CASES]
    out: dict[str, tuple[float, float, list[float]]] = {}
    per_case = {a: {c["design_name"]: [] for c in cases} for a in ARMS}
    records = {a: [] for a in ARMS}
    print(f"hybrid evidence-k probe: {len(cases)} cases x {len(ARMS)} arms x N={N}, n=1", flush=True)
    for arm, k in ARMS.items():
        rates = []
        for rep in range(N):
            ok = 0
            for c in cases:
                r = repair_one(c, S / "hybrid_ev_k" / f"{arm}_r{rep}" / c["design_name"],
                               recall_fn=None, evidence_k=k, n_candidates=1,
                               structured_evidence=True)
                hit = bool(r.get("repaired"))
                ok += hit
                per_case[arm][c["design_name"]].append(hit)
                records[arm].append({
                    "rep": rep,
                    "design": c["design_name"],
                    "repaired": hit,
                    "evidence_mode": r.get("evidence_mode", ""),
                    "buggy_stage": r.get("buggy_stage", ""),
                    "n_tried": r.get("n_candidates_tried"),
                    "error": r.get("error", ""),
                    "apply_error": r.get("apply_error", ""),
                    "patched_evidence": r.get("patched_evidence", ""),
                    "llm_rationale": r.get("llm_rationale", ""),
                    "patch_range": r.get("patch_range"),
                })
            rate = ok / len(cases)
            rates.append(rate)
            print(f"  {arm} rep{rep}: {ok}/{len(cases)} = {rate:.3f}", flush=True)
        out[arm] = (statistics.mean(rates),
                    statistics.stdev(rates) if len(rates) > 1 else 0.0,
                    rates)
    delta = out["hybrid_k6"][0] - out["hybrid_k1"][0]
    ci = _ci95(out["hybrid_k1"], out["hybrid_k6"])
    result = {
        "suite": "hybrid evidence-k small probe",
        "n_cases": len(cases),
        "reps": N,
        "n_candidates": 1,
        "cases": CASES,
        "summary": {k: {"mean": v[0], "std": v[1], "rates": v[2]} for k, v in out.items()},
        "effect": {
            "hybrid_k1_to_k6_delta": delta,
            "ci95_half_width": ci,
            "ci95_delta": [delta - ci, delta + ci],
        },
        "per_case": per_case,
        "records": records,
    }
    with open(S / "hybrid_evidence_k_probe.json", "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"delta hybrid_k1->hybrid_k6: {delta*100:+.1f}pp CI+-{ci*100:.1f}pp", flush=True)
    print("\nper-case hits:")
    for c in CASES:
        print(f"  {c:38s} {sum(per_case['hybrid_k1'][c])}/{N} -> "
              f"{sum(per_case['hybrid_k6'][c])}/{N}", flush=True)


if __name__ == "__main__":
    main()
