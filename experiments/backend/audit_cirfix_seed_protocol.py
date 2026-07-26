#!/usr/bin/env python3
"""Read-only audit of CirFix-39 seed provenance and paired statistics."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ISO = ROOT / ".iso_semrepair"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def exact_mcnemar(r3e_only: int, baseline_only: int) -> float:
    n = r3e_only + baseline_only
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(min(r3e_only, baseline_only) + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def paired(values: dict[str, bool], baseline: dict[str, bool]) -> dict:
    names = sorted(set(values) & set(baseline))
    r3e_only = sum(values[n] and not baseline[n] for n in names)
    baseline_only = sum(baseline[n] and not values[n] for n in names)
    both = sum(values[n] and baseline[n] for n in names)
    neither = sum(not values[n] and not baseline[n] for n in names)
    return {
        "n": len(names),
        "r3e_repaired": sum(values[n] for n in names),
        "baseline_repaired": sum(baseline[n] for n in names),
        "r3e_only": r3e_only,
        "baseline_only": baseline_only,
        "both": both,
        "neither": neither,
        "mcnemar_exact_two_sided_p": exact_mcnemar(r3e_only, baseline_only),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ISO / "cirfix_seed_protocol_audit.json")
    args = ap.parse_args()

    headline_path = ISO / "blue_cirfix" / "p5" / "Blue-P5.json"
    headline = json.loads(headline_path.read_text())
    strict_path = ISO / "diag_percase.json"
    strict = json.loads(strict_path.read_text())["no-mem"]
    old_strict_path = ISO / "strict_memory_ablation.json"
    old_strict = json.loads(old_strict_path.read_text())["no-mem"]
    rr_path = ISO / "rr_full_result.jsonl"
    rr_rows = [json.loads(line) for line in rr_path.read_text().splitlines() if line.strip()]
    # oracle_gate is authoritative; the legacy `repaired` field includes three
    # rr_success=True, og_ok=False false positives.
    rr_oracle = {row["design"]: bool(row["og_ok"]) for row in rr_rows}

    strict_reps = []
    n_reps = len(next(iter(strict.values())))
    for rep in range(n_reps):
        values = {name: bool(outcomes[rep]) for name, outcomes in strict.items()}
        strict_reps.append({"rep": rep, **paired(values, rr_oracle)})

    headline_values = {
        row["design_name"]: bool(row["repaired"])
        for row in headline["per_case"]
    }
    headline_pair = paired(headline_values, rr_oracle)

    population = []
    population_root = ISO / "formal_s3_20260710_134125"
    for seed in range(3):
        path = population_root / f"cirfix_s{seed}" / "summary.json"
        summary = json.loads(path.read_text())
        row = summary["results"][0]
        population.append({
            "seed_label": seed,
            "repaired": row["repaired"],
            "total": row["total"],
            "rate": row["repair_rate"],
            "source": str(path.relative_to(ROOT)),
            "source_sha256": sha(path),
        })

    claimed = {"r3e_only": 12, "baseline_only": 3, "p": exact_mcnemar(12, 3)}
    claimed["implied_r3e_minus_baseline"] = claimed["r3e_only"] - claimed["baseline_only"]
    claimed["observed_aggregate_difference_28_minus_22"] = 6
    claimed["arithmetically_consistent_with_28_vs_22"] = (
        claimed["implied_r3e_minus_baseline"] == 6
    )

    evidence = {
        "schema": "r3e_cirfix_seed_protocol_audit_v1",
        "headline_28_of_39": {
            "source": str(headline_path.relative_to(ROOT)),
            "source_sha256": sha(headline_path),
            "repaired": headline["repaired"],
            "total": headline["total_cases"],
            "protocol": "Blue-P5 lens-diverse population, five agents per case, k=3/n=1 per agent",
            "seed_field_present": False,
            "is_single_agent": False,
            "is_preregistered_primary_seed": False,
            "selection_status": "single unlabeled run; no preselection record found",
            "paired_vs_rtl_repair_oracle": headline_pair,
        },
        "single_agent_k6n3_three_rep": {
            "source": str(strict_path.relative_to(ROOT)),
            "source_sha256": sha(strict_path),
            "protocol": "no-memory, one repair agent with best-of-3 candidates, k=6/n=3",
            "provider_seed_control_recorded": False,
            "rep_counts": [sum(bool(v[i]) for v in strict.values()) for i in range(n_reps)],
            "paired_vs_rtl_repair_oracle": strict_reps,
        },
        "older_single_agent_aggregate_only": {
            "source": str(old_strict_path.relative_to(ROOT)),
            "source_sha256": sha(old_strict_path),
            "rep_counts": [round(rate * 39) for rate in old_strict],
            "per_case_pairing_available": False,
        },
        "population_three_seed": {
            "protocol": "Blue-P5 lens-diverse population, five agents per case, k=3/n=1 per agent",
            "runs": population,
        },
        "rtl_repair_baseline": {
            "source": str(rr_path.relative_to(ROOT)),
            "source_sha256": sha(rr_path),
            "oracle_repaired": sum(rr_oracle.values()),
            "legacy_repaired_field": sum(bool(row.get("repaired")) for row in rr_rows),
            "false_positive_legacy_rows": sum(bool(row.get("repaired")) and not row.get("og_ok") for row in rr_rows),
        },
        "published_mcnemar_claim": claimed,
        "mcnemar_source_audit": {
            "script": "experiments/backend/kdd_statistical_tests.py",
            "input_mode": "hard-coded discordant counts, not loaded per-case outcomes",
            "matches_any_reconstructed_run": False,
            "verdict": "INVALID_UNTIL_RECOMPUTED_FROM_A_DECLARED_RUN_AND_PER_CASE_VECTORS",
        },
        "headline_protocol_verdict": (
            "INVALID_LABELING: 28/39 is neither a declared single-agent primary seed "
            "nor the three-seed population mean"
        ),
    }
    args.out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "out": str(args.out),
        "headline_verdict": evidence["headline_protocol_verdict"],
        "mcnemar_verdict": evidence["mcnemar_source_audit"]["verdict"],
        "single_agent_counts": evidence["single_agent_k6n3_three_rep"]["rep_counts"],
        "population_counts": [row["repaired"] for row in population],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
