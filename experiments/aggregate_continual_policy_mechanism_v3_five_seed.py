#!/usr/bin/env python3
"""Combine the completed seed-101 audit with the frozen four-seed replication."""
from __future__ import annotations

import hashlib
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "r3e"), str(ROOT)]
from semantic_repair_bench.formal_protocol import atomic_write_json, hash_payload  # noqa: E402

OUT = ROOT / ".formal_r3e/launch_logs/continual_policy_mechanism_20260720_v3_five_seed_aggregate.json"
AUDIT_101 = ROOT / ".formal_r3e/launch_logs/continual_policy_mechanism_20260720_v3_seed101/final_audit.json"
AUDIT_REPL = ROOT / ".formal_r3e/launch_logs/continual_policy_mechanism_20260720_v3_seeds202_505/final_audit.json"
PROTOCOL_101 = ROOT / ".formal_r3e/protocols/continual_policy_mechanism_v3_seed101.json"
PROTOCOL_REPL = ROOT / ".formal_r3e/protocols/continual_policy_mechanism_v3_seeds202_505.json"
RUN_101 = ROOT / ".formal_r3e/runs/batches/continual_policy_mechanism_20260720_v3_seed101"
RUN_REPL = ROOT / ".formal_r3e/runs/batches/continual_policy_mechanism_20260720_v3_seeds202_505"
SEEDS = [101, 202, 303, 404, 505]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if OUT.exists():
        raise SystemExit("refusing to overwrite five-seed aggregate")
    audit_101 = json.loads(AUDIT_101.read_text())
    audit_repl = json.loads(AUDIT_REPL.read_text())
    bad = []
    if audit_101.get("verdict") != "PASS" or audit_101.get("bad"):
        bad.append("seed101_audit")
    if audit_repl.get("verdict") != "PASS":
        bad.append("replication_audit")

    seed_rows = []
    ir_hashes = set()
    for seed in SEEDS:
        root = RUN_101 if seed == 101 else RUN_REPL
        run = root / "Auto-Promote" / f"seed_{seed}"
        result_path = run / "result.json"
        registry_path = run / "formal_registry.json"
        if not result_path.is_file() or not registry_path.is_file():
            bad.append(f"seed{seed}_missing")
            continue
        result = json.loads(result_path.read_text())
        registry = json.loads(registry_path.read_text())
        active = [row for row in registry.get("artifacts", []) if row.get("runtime_status") == "promoted"]
        if len(active) != 1:
            bad.append(f"seed{seed}_active_count")
            continue
        artifact = active[0]
        ir_hash = hash_payload(artifact.get("strategy_reasoning_ir"))
        ir_hashes.add(ir_hash)
        g1, g2 = result.get("opportunities", [{}, {}])[:2]
        exact = (
            result.get("policy_transition_count") == 1
            and result.get("anchor_M0") == {"repaired": 4, "total": 8}
            and result.get("anchor_final") == {"repaired": 8, "total": 8}
            and g1.get("promoted") is True
            and g1.get("metrics", {}).get("target_gain") == 1.0
            and g1.get("metrics", {}).get("non_target_delta") == 0.0
            and g2.get("fresh_red_failures") == 0
        )
        if not exact:
            bad.append(f"seed{seed}_mechanism_metrics")
        seed_rows.append({
            "seed": seed,
            "result_path": str(result_path.relative_to(ROOT)),
            "result_sha256": sha(result_path),
            "registry_path": str(registry_path.relative_to(ROOT)),
            "registry_sha256": sha(registry_path),
            "M0_policy_hash": result.get("policy_hash_M0"),
            "M1_policy_hash": result.get("policy_hash_final"),
            "candidate_hash": g1.get("candidate_hash"),
            "strategy_reasoning_ir_hash": ir_hash,
            "target_M0": "0/4",
            "target_M1": "4/4",
            "target_gain": g1.get("metrics", {}).get("target_gain"),
            "non_target_M0": "4/4",
            "non_target_M1": "4/4",
            "non_target_delta": g1.get("metrics", {}).get("non_target_delta"),
            "G2_repaired": 4 - int(g2.get("fresh_red_failures", 4)),
            "G2_total": 4,
            "promoted": bool(g1.get("promoted")),
        })
    if len(ir_hashes) != 1:
        bad.append("strategy_ir_not_semantically_identical")

    gains = [row["target_gain"] for row in seed_rows]
    deltas = [row["non_target_delta"] for row in seed_rows]
    g2_rates = [row["G2_repaired"] / row["G2_total"] for row in seed_rows]
    payload = {
        "schema": "r3e-continual-policy-mechanism-v3-five-seed-aggregate-v1",
        "verdict": "PASS" if not bad and len(seed_rows) == 5 else "FAIL",
        "bad": sorted(set(bad)),
        "seeds": SEEDS,
        "inputs": {
            "seed101_protocol_sha256": sha(PROTOCOL_101),
            "replication_protocol_sha256": sha(PROTOCOL_REPL),
            "seed101_audit_sha256": sha(AUDIT_101),
            "replication_audit_sha256": sha(AUDIT_REPL),
            "generator_sha256": sha(Path(__file__)),
        },
        "seed_results": seed_rows,
        "summary": {
            "promotion_successes": sum(row["promoted"] for row in seed_rows),
            "promotion_total": 5,
            "promotion_rate": sum(row["promoted"] for row in seed_rows) / 5,
            "target_gain_mean": statistics.mean(gains),
            "target_gain_sample_sd": statistics.stdev(gains),
            "non_target_delta_mean": statistics.mean(deltas),
            "non_target_delta_sample_sd": statistics.stdev(deltas),
            "non_target_no_regression_seeds": sum(delta >= 0 for delta in deltas),
            "G2_repaired_pooled": sum(row["G2_repaired"] for row in seed_rows),
            "G2_total_pooled": sum(row["G2_total"] for row in seed_rows),
            "G2_seed_rate_mean": statistics.mean(g2_rates),
            "G2_seed_rate_sample_sd": statistics.stdev(g2_rates),
            "unique_candidate_hashes": len({row["candidate_hash"] for row in seed_rows}),
            "unique_M1_policy_hashes": len({row["M1_policy_hash"] for row in seed_rows}),
            "unique_strategy_reasoning_ir_hashes": len(ir_hashes),
        },
        "claim_boundary": (
            "Five-seed reproducibility of one audited M0-to-M1 policy-revision cycle; "
            "not evidence of repeated M0-to-M1-to-M2 or long-horizon continual improvement."
        ),
    }
    atomic_write_json(OUT, payload)
    print(json.dumps({
        "verdict": payload["verdict"],
        "output": str(OUT.relative_to(ROOT)),
        "output_sha256": sha(OUT),
        "summary": payload["summary"],
    }, ensure_ascii=False, indent=2))
    raise SystemExit(payload["verdict"] != "PASS")


if __name__ == "__main__":
    main()
