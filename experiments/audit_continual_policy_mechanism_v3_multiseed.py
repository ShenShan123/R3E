#!/usr/bin/env python3
"""Fail-closed per-seed and aggregate audit for v3 mechanism replications."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "r3e"), str(ROOT)]
from semantic_repair_bench.formal_protocol import hash_payload  # noqa: E402
from semantic_repair_bench.strategy_reasoning_ir import SCHEMA  # noqa: E402


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def audit_seed(run_root: Path, protocol_path: Path, cache_root: Path, seed: int) -> dict:
    cfg = json.loads(protocol_path.read_text())
    run = run_root / "Auto-Promote" / f"seed_{seed}"
    result_path = run / "result.json"
    manifest_path = run / "run_manifest.json"
    bad: list[str] = []
    if not result_path.is_file() or not manifest_path.is_file():
        return {"seed": seed, "verdict": "FAIL", "bad": ["missing_result_or_run_manifest"]}
    result = json.loads(result_path.read_text())
    run_manifest = json.loads(manifest_path.read_text())
    if run_manifest.get("result_sha256") != sha(result_path): bad.append("result_hash")
    if result.get("protocol_sha256") != sha(protocol_path): bad.append("protocol_hash")
    if result.get("seed") != seed: bad.append("seed_binding")
    if result.get("policy_transition_count") != 1: bad.append("transition_count")
    if result.get("policy_hash_M0") == result.get("policy_hash_final"): bad.append("policy_unchanged")
    if result.get("anchor_M0") != {"repaired": 4, "total": 8}: bad.append("M0_anchor_not_4of8")
    if result.get("anchor_final") != {"repaired": 8, "total": 8}: bad.append("M1_anchor_not_8of8")
    opportunities = result.get("opportunities", [])
    if len(opportunities) != 2:
        bad.append("opportunity_count")
        opportunities = [{}, {}]
    g1, g2 = opportunities[:2]
    if not g1.get("promoted") or not g1.get("eligible"): bad.append("G1_not_promoted")
    expected_metrics = {
        "validation_hits": 4, "covered_designs": 4,
        "baseline_target_failures": 4, "target_recovery_ratio": 1.0,
        "target_gain": 1.0, "non_target_delta": 0.0,
        "target_total": 4, "non_target_total": 4,
    }
    if g1.get("metrics") != expected_metrics: bad.append("G1_metrics")
    if not g1.get("checks") or not all(g1.get("checks", {}).values()): bad.append("G1_checks")
    if g2.get("parent_policy_hash") != g1.get("policy_hash_after"): bad.append("G2_parent_lineage")
    if g2.get("fresh_red_failures") != 0: bad.append("G2_not_absorbed")
    if g2.get("policy_hash_after") != g1.get("policy_hash_after"): bad.append("G2_policy_changed")

    manifest = json.loads((ROOT / cfg["manifest"]).read_text())
    target_ids = set(manifest["selected_target_ids"])
    non_ids = set(manifest["selected_non_target_ids"])
    m0 = {}
    for path in sorted((run / "work/anchor/M0").glob("case_*/result.json")):
        row = json.loads(path.read_text())
        m0[row["case_id"]] = bool(row["result"]["repaired"])
    if {key for key, value in m0.items() if not value} != target_ids: bad.append("M0_target_boundary")
    if not all(m0.get(case_id) for case_id in non_ids): bad.append("M0_non_target_capability")
    candidate = {}
    for path in sorted((run / "work/candidate_replay/G1").glob("case_*/result.json")):
        row = json.loads(path.read_text())
        candidate[row["case_id"]] = bool(row["result"]["repaired"])
        if row["result"].get("n_llm_revisions") != 0: bad.append("runtime_llm_revision")
        if row["result"].get("llm_patch_generation_allowed") is not False: bad.append("llm_patch_authority")
    if not candidate or not all(candidate.values()): bad.append("candidate_replay_not_8of8")
    g2_results = [json.loads(path.read_text()) for path in sorted(
        (run / "work/discovery/G2").glob("case_*/result.json")
    )]
    if len(g2_results) != 4 or not all(row["result"]["repaired"] for row in g2_results):
        bad.append("G2_results")
    if any(row["policy_hash"] != result.get("policy_hash_final") for row in g2_results):
        bad.append("G2_result_policy_hash")

    registry_path = run / "formal_registry.json"
    if not registry_path.is_file():
        bad.append("registry_missing")
        registry = {}
    else:
        registry = json.loads(registry_path.read_text())
    if hash_payload(registry.get("base_policy")) != cfg.get("base_policy_hash"):
        bad.append("base_policy_binding")
    active = [row for row in registry.get("artifacts", []) if row.get("runtime_status") == "promoted"]
    if len(active) != 1: bad.append("active_artifact_count")
    artifact = active[0] if active else {}
    ir = artifact.get("strategy_reasoning_ir", {})
    if ir.get("schema_version") != SCHEMA or set(ir) != {
        "schema_version", "intent_type", "bug_family", "primitive", "search_order", "stop_condition"
    }: bad.append("reasoning_ir")
    if artifact.get("action_policy", {}).get("llm_patch_generation_allowed") is not False:
        bad.append("artifact_llm_patch_authority")
    if artifact.get("parent_policy_hash") != result.get("policy_hash_M0"):
        bad.append("candidate_parent_policy")

    ledger_path = run / "work/a1/decision_ledger.jsonl"
    ledger = rows(ledger_path) if ledger_path.is_file() else []
    previous = "0" * 64
    for sequence, row in enumerate(ledger, 1):
        raw = {key: value for key, value in row.items() if key != "ledger_entry_hash"}
        if row.get("sequence") != sequence or row.get("previous_ledger_entry_hash") != previous:
            bad.append("ledger_chain")
        if row.get("ledger_entry_hash") != hash_payload(raw): bad.append("ledger_hash")
        previous = row.get("ledger_entry_hash")
    if sum(row.get("event") == "promoted" for row in ledger) != 1: bad.append("ledger_promotion")
    uses_path = run / "work/a1/runtime_policy_uses.jsonl"
    uses = rows(uses_path) if uses_path.is_file() else []
    g2_uses = [row for row in uses if str(row.get("use_id", "")).startswith("fresh-red:G2:")]
    if len(g2_uses) != 4 or any(
        row.get("policy_hash") != result.get("policy_hash_final") for row in g2_uses
    ): bad.append("G2_runtime_policy_use")
    if active and any(artifact["artifact_id"] not in row.get("activated_artifact_ids", []) for row in g2_uses):
        bad.append("G2_active_artifact_use")

    cache_files = sorted((cache_root / f"seed_{seed}").glob("*.json"))
    if len(cache_files) != 1: bad.append("reasoning_call_count")
    if cache_files:
        cache = json.loads(cache_files[0].read_text())
        if cache.get("request", {}).get("seed") != str(seed): bad.append("cache_seed_binding")
        if cache.get("response_hash") != hash_payload(cache.get("response")): bad.append("cache_response_hash")
        if cache.get("response") != artifact.get("candidate_raw_output"): bad.append("cache_candidate_output")
        if cache.get("credentials_recorded") is not False: bad.append("credentials_recorded")

    return {
        "seed": seed,
        "verdict": "PASS" if not bad else "FAIL",
        "bad": sorted(set(bad)),
        "result_path": str(result_path.relative_to(ROOT)),
        "result_sha256": sha(result_path),
        "registry_sha256": sha(registry_path) if registry_path.is_file() else None,
        "six_step": {
            "M0_policy_hash": result.get("policy_hash_M0"),
            "G1_fresh_red_failures": g1.get("fresh_red_failures"),
            "candidate_hash": g1.get("candidate_hash"),
            "target_gain": g1.get("metrics", {}).get("target_gain"),
            "non_target_delta": g1.get("metrics", {}).get("non_target_delta"),
            "M1_policy_hash": result.get("policy_hash_final"),
            "G2_fresh_red_failures": g2.get("fresh_red_failures"),
            "G2_runtime_uses": len(g2_uses),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    args = parser.parse_args()
    protocol_path = args.protocol if args.protocol.is_absolute() else ROOT / args.protocol
    cfg = json.loads(protocol_path.read_text())
    audits = [audit_seed(args.run_root, protocol_path, args.cache_root, seed) for seed in cfg["seeds"]]
    passed = [row for row in audits if row["verdict"] == "PASS"]
    gains = [row["six_step"]["target_gain"] for row in passed]
    deltas = [row["six_step"]["non_target_delta"] for row in passed]
    aggregate = {
        "schema": "r3e-continual-policy-mechanism-v3-multiseed-audit-v1",
        "verdict": "PASS" if len(passed) == len(audits) else "FAIL",
        "protocol_path": str(protocol_path.relative_to(ROOT)),
        "protocol_sha256": sha(protocol_path),
        "seeds": cfg["seeds"],
        "seed_audits": audits,
        "summary": {
            "audited_seeds": len(audits),
            "passing_seeds": len(passed),
            "promotion_rate": len(passed) / len(audits),
            "target_gain_mean": statistics.mean(gains) if gains else None,
            "target_gain_sample_sd": statistics.stdev(gains) if len(gains) > 1 else None,
            "non_target_delta_mean": statistics.mean(deltas) if deltas else None,
            "non_target_no_regression_rate": (
                sum(delta >= 0 for delta in deltas) / len(audits) if deltas else 0.0
            ),
            "G2_absorption_rate": (
                sum(row["six_step"]["G2_fresh_red_failures"] == 0 for row in passed) / len(audits)
                if audits else 0.0
            ),
        },
    }
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))
    raise SystemExit(aggregate["verdict"] != "PASS")


if __name__ == "__main__":
    main()
