"""Strict correctness-gate ablation.

Re-evaluates fixed memories produced by build_gate_ablation.py:
  no-memory vs strong oracle-gated memory vs weak compile-only memory.

The memory-build step itself is intentionally not repeated here; the old build
already captured the mechanism (weak memory contains compile-only false repairs).
This script supplies the missing strict N=3 evaluation and writes results
incrementally. Default suite is Red-Fixed-12 to match the P2-P6 strict matrix;
use --suite cirfix for the larger CirFix-39 appendix rerun.
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
from memory_store import make_recall_fn

S = Path(".iso_semrepair")
N = 3
CONFIGS = [
    ("no_memory", None),
    ("strong_oracle_gate_memory", S / "mem_strong.jsonl"),
    ("weak_compile_only_memory", S / "mem_weak.jsonl"),
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


def _count_memory(path: Path | None) -> dict:
    if path is None:
        return {"skills": 0}
    rows = [json.loads(l) for l in open(path) if l.strip()]
    return {"skills": len(rows), "path": str(path)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["redfixed", "cirfix"], default="redfixed")
    args = ap.parse_args()
    manifest = S / ("blue_abl/poison_pool.jsonl" if args.suite == "redfixed"
                    else "survey/valid_functional.jsonl")
    suite_name = "Red-Fixed-12" if args.suite == "redfixed" else "CirFix-39"
    out_path = S / f"strict_correctness_gate_ablation_{args.suite}.json"
    partial_path = S / f"strict_correctness_gate_ablation_{args.suite}.partial.json"
    cases = [json.loads(l) for l in open(manifest) if l.strip()]
    out = {
        "suite": suite_name,
        "n_cases": len(cases),
        "reps": N,
        "configs": {},
        "effects": {},
        "status": "running",
        "source": "r3e/semantic_repair_bench/strict_correctness_gate_ablation.py",
        "note": "Fixed strong/weak memories from build_gate_ablation.py; strict N=3 evaluation only.",
    }
    print(f"=== strict correctness-gate ablation: {suite_name}, {len(cases)} cases, N={N}, k6n3 ===", flush=True)
    for label, mem_path in CONFIGS:
        recall = make_recall_fn(str(mem_path)) if mem_path else None
        rates = []
        rep_records = []
        for rep in range(N):
            ok = 0
            records = []
            for c in cases:
                r = repair_one(
                    c,
                    S / "strict_gate" / f"{label}_r{rep}" / c["design_name"],
                    recall_fn=recall,
                    evidence_k=6,
                    n_candidates=3,
                )
                rec = {
                    "design": c["design_name"],
                    "repaired": bool(r.get("repaired")),
                    "used_memory": bool(r.get("used_memory")),
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
                **_count_memory(mem_path),
                **_stats(rates),
                "rep_records": rep_records,
            }
            with open(partial_path, "w") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
            print(f"  {label} rep{rep}: {ok}/{len(cases)} = {rate:.3f}", flush=True)

    c = out["configs"]
    out["effects"] = {
        "strong_vs_weak": _ci_delta(c["weak_compile_only_memory"], c["strong_oracle_gate_memory"]),
        "strong_vs_no_memory": _ci_delta(c["no_memory"], c["strong_oracle_gate_memory"]),
        "weak_vs_no_memory": _ci_delta(c["no_memory"], c["weak_compile_only_memory"]),
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
