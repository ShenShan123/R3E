#!/usr/bin/env python3
"""Reconstruct memory and red-challenge funnels from frozen formal artifacts.

This analysis is deliberately read-only.  It keeps evaluation counts separate
from unique frozen cases and refuses to infer a discovery-case repair result
from replay on a different case.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEED_RUNS = {
    101: ROOT / ".formal_r3e/runs/batches/continual_policy_mechanism_20260720_v3_seed101/Auto-Promote/seed_101",
    202: ROOT / ".formal_r3e/runs/batches/continual_policy_mechanism_20260720_v3_seeds202_505/Auto-Promote/seed_202",
    303: ROOT / ".formal_r3e/runs/batches/continual_policy_mechanism_20260720_v3_seeds202_505/Auto-Promote/seed_303",
    404: ROOT / ".formal_r3e/runs/batches/continual_policy_mechanism_20260720_v3_seeds202_505/Auto-Promote/seed_404",
    505: ROOT / ".formal_r3e/runs/batches/continual_policy_mechanism_20260720_v3_seeds202_505/Auto-Promote/seed_505",
}
MANIFEST = ROOT / ".formal_r3e/manifests/continual_policy_pilot_v2h_seed101.json"
R2_ROOT = ROOT / ".formal_r3e/runs/batches/auto_evolution_formal_20260713_r2/auto_evolution/Auto-Promote"
DEFAULT_OUT = ROOT / ".formal_r3e/launch_logs/continual_policy_mechanism_20260720_v3_funnel_audit.json"


def load_json(path: Path):
    return json.loads(path.read_text())


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def seed_row(seed: int, run: Path) -> dict:
    result = load_json(run / "result.json")
    shadow = load_jsonl(run / "work/a1/shadow_trajectories.jsonl")
    candidates = load_jsonl(run / "work/a1/candidate_strategies.jsonl")
    ledger = load_jsonl(run / "work/a1/decision_ledger.jsonl")
    uses = load_jsonl(run / "work/a1/runtime_policy_uses.jsonl")
    g1, g2 = result["opportunities"]

    active_g2_uses = [
        row for row in uses
        if row["use_id"].startswith("fresh-red:G2:") and row["activated_artifact_ids"]
    ]
    g2_results = [
        load_json(path)
        for path in sorted((run / "work/discovery/G2").glob("case_*/result.json"))
    ]
    successful_g2_uses = [
        row for row in g2_results
        if row["result"]["repaired"] and row.get("activated_artifact_ids")
    ]
    eligible = [row for row in ledger if row["event"] == "promotion-eligible"]
    promoted = [row for row in ledger if row["event"] == "promoted"]

    checks = {
        "seed_matches": result["seed"] == seed,
        "one_candidate": len(candidates) == 1,
        "four_shadow_rows": len(shadow) == 4,
        "one_eligible": len(eligible) == 1,
        "one_promoted": len(promoted) == 1,
        "g1_four_of_four_fail": g1["fresh_red_cases"] == g1["fresh_red_failures"] == 4,
        "g2_zero_of_four_fail": g2["fresh_red_cases"] == 4 and g2["fresh_red_failures"] == 0,
        "four_active_g2_uses": len(active_g2_uses) == 4,
        "four_successful_g2_uses": len(successful_g2_uses) == 4,
        "paired_target_gain_four": (
            g1["metrics"]["baseline_target_failures"] == 4
            and g1["metrics"]["target_gain"] == 1
        ),
    }
    if not all(checks.values()):
        raise RuntimeError(f"seed {seed} funnel invariant failed: {checks}")

    return {
        "seed": seed,
        "memory": {
            "shadow_trajectory_rows": len(shadow),
            "distilled_candidate_rows": len(candidates),
            "replay_qualified_candidates": len(eligible),
            "promoted_candidates": len(promoted),
            "active_strategy_G2_uses": len(active_g2_uses),
            "successful_active_strategy_G2_uses": len(successful_g2_uses),
            "paired_target_repair_gain_cases": int(
                g1["metrics"]["baseline_target_failures"] * g1["metrics"]["target_gain"]
            ),
        },
        "challenge": {
            "G1": {
                "red_probe_evaluations": g1["fresh_red_cases"],
                "valid_hard_bug_evaluations": g1["fresh_red_failures"],
                "new_family_case_evaluations": g1["fresh_red_failures"],
                "distinct_new_families": len({row["bug_type"] for row in shadow}),
                "current_policy_uncovered_evaluations": g1["fresh_red_failures"],
                "primitive_repairable_discovery_cases": None,
                "primitive_repairable_supporting_target_replay": g1["metrics"]["validation_hits"],
                "source_trajectories_bound_to_eligible_candidate": len(
                    candidates[0]["source_trajectory_ids"]
                ),
                "eligible_candidate_count": len(eligible),
            },
            "G2": {
                "red_probe_evaluations": g2["fresh_red_cases"],
                "valid_hard_bug_evaluations": g2["fresh_red_failures"],
                "new_family_case_evaluations": 0,
                "distinct_new_families": 0,
                "current_policy_uncovered_evaluations": g2["fresh_red_failures"],
                "primitive_repairable_uncovered_cases": 0,
                "active_strategy_absorbed_probe_evaluations": len(successful_g2_uses),
                "promotion_supporting_trajectory_count": 0,
                "eligible_candidate_count": 0,
            },
        },
        "semantic": {
            "bug_families": sorted({row["bug_type"] for row in shadow}),
            "designs": sorted({row["design_id"] for row in shadow}),
            "candidate_hash": candidates[0]["candidate_hash"],
            "strategy_reasoning_ir_hash": candidates[0]["candidate_output_hash"],
            "M0_policy_hash": result["policy_hash_M0"],
            "M1_policy_hash": result["policy_hash_final"],
        },
        "checks": checks,
        "sources": {
            "result": rel(run / "result.json"),
            "shadow": rel(run / "work/a1/shadow_trajectories.jsonl"),
            "candidates": rel(run / "work/a1/candidate_strategies.jsonl"),
            "ledger": rel(run / "work/a1/decision_ledger.jsonl"),
            "runtime_uses": rel(run / "work/a1/runtime_policy_uses.jsonl"),
        },
    }


def r2_context() -> dict:
    rows = []
    for seed in (101, 202, 303):
        run = R2_ROOT / f"seed_{seed}"
        records = load_jsonl(run / "work/correctness_gated_records.jsonl")
        distilled = load_json(run / "work/distilled_pattern_templates.json")
        reports = sorted((run / "work/promotion_validation").glob("R*/promotion_validation_report.json"))
        decisions = [decision for path in reports for decision in load_json(path)["decisions"]]
        promoted = load_json(run / "work/promoted_pattern_templates.json")
        top_level_decisions = load_json(run / "formal_decisions.json")
        shadow_registry = load_jsonl(run / "candidate_shadow_registry.jsonl")
        rows.append({
            "seed": seed,
            "correctness_gated_record_rows": len(records),
            "terminal_distilled_shadow_templates": len(distilled),
            "nested_promotion_decisions": len(decisions),
            "promoted_templates": len(promoted),
            "top_level_formal_decision_rows": len(top_level_decisions),
            "candidate_shadow_registry_rows": len(shadow_registry),
        })
    return {
        "seed_rows": rows,
        "pooled": {
            key: sum(row[key] for row in rows)
            for key in (
                "correctness_gated_record_rows",
                "terminal_distilled_shadow_templates",
                "nested_promotion_decisions",
                "promoted_templates",
                "top_level_formal_decision_rows",
                "candidate_shadow_registry_rows",
            )
        },
        "quality_warning": (
            "The 11 gate decisions are reconstructable from nested per-round promotion reports, "
            "but the top-level formal_decisions.json and candidate_shadow_registry.jsonl files "
            "are empty in the retained artifact. Do not cite a canonical 11-row ledger."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    manifest = load_json(MANIFEST)
    seed_rows = [seed_row(seed, path) for seed, path in SEED_RUNS.items()]
    memory_keys = tuple(seed_rows[0]["memory"])
    memory_pooled = {
        key: sum(row["memory"][key] for row in seed_rows)
        for key in memory_keys
    }
    challenge_pooled = {}
    for generation in ("G1", "G2"):
        keys = tuple(seed_rows[0]["challenge"][generation])
        challenge_pooled[generation] = {
            key: (
                None if any(row["challenge"][generation][key] is None for row in seed_rows)
                else sum(row["challenge"][generation][key] for row in seed_rows)
            )
            for key in keys
        }

    discovery = manifest["discovery"]
    unique_cases = {
        generation: [row for row in discovery if row["generation"] == generation]
        for generation in (1, 2)
    }
    # Distinct categories must be deduplicated across the shared manifest, not
    # summed across stochastic seed evaluations.
    challenge_pooled["G1"]["distinct_new_families"] = len(
        {row["family"] for row in unique_cases[1]}
    )
    challenge_pooled["G2"]["distinct_new_families"] = 0
    candidate_hashes = {
        row["semantic"]["candidate_hash"] for row in seed_rows
    }
    reasoning_ir_hashes = {
        row["semantic"]["strategy_reasoning_ir_hash"] for row in seed_rows
    }
    payload = {
        "schema": "r3e-policy-memory-challenge-funnel-audit-v1",
        "verdict": "PASS",
        "claim_boundary": (
            "Five-seed reproducibility of one M0-to-M1 revision and same-family G2 absorption; "
            "not multi-generation continual improvement or open-world bug-family discovery."
        ),
        "grain": {
            "seed_evaluations": len(seed_rows),
            "shared_manifest_across_seeds": True,
            "unique_G1_cases": len(unique_cases[1]),
            "unique_G2_cases": len(unique_cases[2]),
            "unique_G1_designs": len({row["design"] for row in unique_cases[1]}),
            "unique_G2_designs": len({row["design"] for row in unique_cases[2]}),
            "G1_G2_same_design_set": (
                {row["design"] for row in unique_cases[1]}
                == {row["design"] for row in unique_cases[2]}
            ),
            "unique_discovery_families": sorted({row["family"] for row in discovery}),
        },
        "memory_funnel_pooled_seed_evaluations": memory_pooled,
        "memory_funnel_unique_entities": {
            "unique_G1_buggy_artifacts": len({row["buggy_sha256"] for row in unique_cases[1]}),
            "unique_distilled_candidate_hashes": len(candidate_hashes),
            "unique_strategy_reasoning_IR_hashes": len(reasoning_ir_hashes),
            "unique_G2_buggy_artifacts_with_active_use": len(
                {row["buggy_sha256"] for row in unique_cases[2]}
            ),
        },
        "challenge_funnel_pooled_seed_evaluations": challenge_pooled,
        "challenge_funnel_unique_entities": {
            "G1_red_probe_cases": len(unique_cases[1]),
            "G1_hard_bug_cases": len(unique_cases[1]),
            "G1_new_family_cases": len(unique_cases[1]),
            "G1_distinct_new_families": len({row["family"] for row in unique_cases[1]}),
            "G2_red_probe_cases": len(unique_cases[2]),
            "G2_hard_bug_cases": 0,
            "G2_distinct_new_families": 0,
        },
        "seed_rows": seed_rows,
        "historical_r2_context": r2_context(),
        "measurement_gaps": [
            "G1 discovery failures were not replayed directly under the candidate strategy; "
            "primitive repairability is measured on four separate frozen target-replay cases.",
            "G2 contains the same off_by_one family and the same four designs as G1, with new "
            "buggy hashes. It measures boundary absorption, not discovery of a new bug family.",
            "Seeds 202/303/404/505 were frozen after observing seed 101 and are replication seeds, "
            "not a jointly preregistered five-seed protocol.",
        ],
        "input_hashes": {
            "manifest": sha256(MANIFEST),
            **{
                f"seed_{seed}_result": sha256(run / "result.json")
                for seed, run in SEED_RUNS.items()
            },
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "verdict": payload["verdict"],
        "out": rel(args.out.resolve()),
        "memory": memory_pooled,
        "challenge": challenge_pooled,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
