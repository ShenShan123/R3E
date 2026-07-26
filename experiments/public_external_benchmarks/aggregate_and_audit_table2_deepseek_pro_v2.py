#!/usr/bin/env python3
"""Canonical aggregation and fail-closed audit for Table-2 DeepSeek-v4-Pro V2.

Offline only: this script makes no model or network calls.  It merges 729 new
isolated jobs, 81 frozen Direct-LLM seed-101 reuse rows, and 40 pre-frozen SHA3
missing-as-failure rows into the exact 850-row method x seed x case panel.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from table2_common import (
    read_jsonl,
    sha256_file,
    sha256_payload,
    write_jsonl,
)


ROOT = Path(__file__).resolve().parents[2]
RUN_ROOT = ROOT / ".cross_benchmark_r3e_202607/table2_deepseek_v4_pro_20260720_v2"
PROTOCOL = ROOT / ".cross_benchmark_r3e_202607/TABLE2_DEEPSEEK_V4_PRO_PROTOCOL_20260720_V2.json"
RECOVERY_ROOT = ROOT / ".cross_benchmark_r3e_202607/table2_direct_deepseek_v4_pro_seed101_transport_recovery_20260720_v1"
RECOVERY_AUDIT = RECOVERY_ROOT / "RECOVERY_AUDIT.json"
DIRECT_SEED101_ORIGINAL = ROOT / ".cross_benchmark_r3e_202607/table2_direct_deepseek_v4_pro_seed101_20260720_v1"
EXPECTED_CASES = {"cirfix39": 39, "literature32": 32, "strider14": 14}
EXECUTED_CASES = {"cirfix39": 39, "literature32": 28, "strider14": 14}
SEEDS = (101, 202, 303, 404, 505)
METHODS = ("direct_llm_deepseek_v4_pro", "r3e_deepseek_v4_pro")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def mean_sd(values: list[float]) -> tuple[float, float]:
    return statistics.mean(values), statistics.stdev(values)


def verify_file_field(row: dict[str, Any], stem: str, allowed_roots: tuple[Path, ...],
                      counts: Counter[str], key: tuple[str, int]) -> None:
    raw_path = row.get(f"{stem}_path")
    raw_hash = row.get(f"{stem}_hash")
    if raw_path in (None, ""):
        require(raw_hash in (None, ""), f"{stem} hash without path: {key}")
        counts[f"{stem}_absent"] += 1
        return
    path = Path(str(raw_path)).resolve()
    require(path.is_file(), f"missing {stem}: {path}")
    require(any(path.is_relative_to(root.resolve()) for root in allowed_roots),
            f"{stem} path escaped frozen roots: {path}")
    require(raw_hash == sha256_file(path), f"{stem} hash mismatch: {path}")
    counts[f"{stem}_verified"] += 1


def validate_candidate(row: dict[str, Any], job: dict[str, Any], idx: int,
                       protocol: dict[str, Any], counts: Counter[str]) -> None:
    key = (job["job_id"], idx)
    expected_candidate_benchmark = (
        "strider" if job["benchmark"] == "strider14" else job["benchmark"]
    )
    require(row.get("case_id") == job["case_id"]
            and row.get("case_sha256") == job["case_sha256"]
            and row.get("benchmark") == expected_candidate_benchmark
            and row.get("seed") == job["seed"]
            and row.get("manifest_hash") == job["manifest_sha256"],
            f"candidate identity mismatch: {key}")
    require(row.get("candidate_index") == idx, f"candidate index mismatch: {key}")
    require(row.get("model") == "deepseek-v4-pro"
            and row.get("temperature") == 0.2
            and row.get("max_output_tokens") == 8192
            and row.get("model_timeout_sec") == 120,
            f"candidate model configuration drift: {key}")
    require(row.get("used_memory") is False
            and row.get("red_enabled") is False
            and row.get("population_size") == 1
            and row.get("cross_case_state") is False,
            f"candidate state drift: {key}")
    require((row.get("status") == "pass") == bool(row.get("oracle_ok")),
            f"status/oracle mismatch: {key}")
    require(not row.get("oracle_ok") or (
        row.get("compile_ok") is True and row.get("simulation_ok") is True
    ), f"passing row lacks compile/simulation pass: {key}")
    if job["method"] == "direct_llm_deepseek_v4_pro":
        require(row.get("schema") == "r3e-table2-candidate-v1"
                and row.get("method") == "direct_llm"
                and row.get("registry_enabled") is False
                and row.get("template_enabled") is False
                and row.get("promotion_enabled") is False,
                f"Direct candidate protocol drift: {key}")
    else:
        require(row.get("schema") == "r3e-table2-evolved-blue-feedback-candidate-v1"
                and row.get("method") == "r3e_evolved_blue_feedback_deepseek_v4_pro",
                f"R3E candidate schema/method drift: {key}")
        require(row.get("policy_hash") == protocol["r3e_executable_policy_sha256"]
                and row.get("inference_config_hash") == protocol["r3e_feedback_inference_config_sha256"]
                and row.get("registry_file_sha256") == protocol["files"]["configs/skills.json"],
                f"R3E policy/config provenance drift: {key}")
        require(row.get("used_strategy") is True
                and row.get("registry_read_only") is True
                and row.get("registry_write_count") == 0
                and row.get("template_write_count") == 0
                and row.get("promotion_write_count") == 0,
                f"R3E state/write drift: {key}")
        require(row.get("feedback_mode") == "sequential_same_case_frozen_evaluator_feedback"
                and row.get("feedback_round") == idx
                and row.get("prior_attempt_count") == idx
                and row.get("conditioned_on_prior_residual") is (idx > 0),
                f"R3E feedback-chain metadata drift: {key}")
    for stem in ("prompt", "response", "patch"):
        verify_file_field(
            row, stem, (RUN_ROOT, RECOVERY_ROOT, DIRECT_SEED101_ORIGINAL), counts, key
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    require(not out.exists() or not any(out.iterdir()),
            f"refusing to overwrite non-empty aggregate directory: {out}")
    out.mkdir(parents=True, exist_ok=True)

    jobs_path = RUN_ROOT / "JOBS.jsonl"
    reuse_path = RUN_ROOT / "REUSED_DIRECT_SEED101.jsonl"
    excluded_path = RUN_ROOT / "EXCLUDED_SHA3.jsonl"
    inventory_path = RUN_ROOT / "INVENTORY.json"
    launch_summary_path = RUN_ROOT / "LAUNCH_SUMMARY.json"
    protocol = json.loads(PROTOCOL.read_text())
    inventory = json.loads(inventory_path.read_text())
    summary = json.loads(launch_summary_path.read_text())
    recovery_audit = json.loads(RECOVERY_AUDIT.read_text())
    jobs = read_jsonl(jobs_path)
    reused = read_jsonl(reuse_path)
    excluded = read_jsonl(excluded_path)

    require(protocol.get("schema") == "r3e-table2-deepseek-v4-pro-protocol-v2",
            "protocol schema mismatch")
    require(protocol.get("model", {}).get("model") == "deepseek-v4-pro"
            and protocol.get("model", {}).get("seeds") == list(SEEDS),
            "protocol model/seeds mismatch")
    for name, path in {
        "INVENTORY.json": inventory_path,
        "JOBS.jsonl": jobs_path,
        "REUSED_DIRECT_SEED101.jsonl": reuse_path,
        "EXCLUDED_SHA3.jsonl": excluded_path,
    }.items():
        require(protocol["inventory_hashes"][name] == sha256_file(path),
                f"frozen inventory hash drift: {name}")
    require(inventory["new_jobs"] == 729
            and inventory["reused_direct_seed101"] == 81
            and inventory["excluded_sha3_jobs"] == 40,
            "inventory cardinality drift")
    require(len(jobs) == 729 and len(reused) == 81 and len(excluded) == 40,
            "partition file cardinality drift")
    require(summary == {
        **summary,
        "expected_new_jobs": 729,
        "observed_supervisors": 729,
        "complete_supervisors": 729,
        "failed_supervisors": 0,
        "launcher_failures": 0,
        "reused_direct_seed101_jobs": 81,
        "sha3_jobs_launched": 0,
    }, "launch summary does not certify a clean complete run")
    require(recovery_audit.get("verdict") == "PASS" and not recovery_audit.get("problems"),
            "Direct seed-101 recovery audit is not PASS")

    partitions = [jobs, reused, excluded]
    id_sets = [{row["job_id"] for row in part} for part in partitions]
    require([len(values) for values in id_sets] == [729, 81, 40], "duplicate job IDs")
    require(not (id_sets[0] & id_sets[1] or id_sets[0] & id_sets[2] or id_sets[1] & id_sets[2]),
            "new/reuse/excluded partitions overlap")
    all_ids = set().union(*id_sets)
    require(len(all_ids) == 850, "full job union is not 850")

    manifests: dict[str, dict[str, dict[str, Any]]] = {}
    for benchmark in EXPECTED_CASES:
        spec = next(row for row in jobs + reused + excluded if row["benchmark"] == benchmark)
        manifest_path = ROOT / spec["manifest"]
        require(sha256_file(manifest_path) == spec["manifest_sha256"],
                f"manifest hash drift: {benchmark}")
        manifest_rows = read_jsonl(manifest_path)
        require(len(manifest_rows) == EXPECTED_CASES[benchmark],
                f"manifest denominator drift: {benchmark}")
        manifest_map = {
            str(row.get("case_id") or row.get("task_id")): row
            for row in manifest_rows
        }
        require("None" not in manifest_map and len(manifest_map) == len(manifest_rows),
                f"manifest case identity drift: {benchmark}")
        manifests[benchmark] = manifest_map
    expected_ids = {
        f"{method}|{benchmark}|{seed}|{case_id}"
        for method in METHODS for benchmark, cases in manifests.items()
        for seed in SEEDS for case_id in cases
    }
    require(all_ids == expected_ids, "partition union does not equal exact manifests x methods x seeds")
    require(all(row.get("execution") == "reuse"
                and row.get("method") == "direct_llm_deepseek_v4_pro"
                and row.get("seed") == 101 for row in reused),
            "reuse classification drift")
    require(all(row.get("execution") == "not_run"
                and row.get("classification") == "operational_sha3_exclusion"
                and row.get("resource_class") == "heavy_serial"
                and row.get("reporting_rule") == "count unrepaired on exact Literature-32 denominator"
                and row.get("case_id", "").startswith("sha3_") for row in excluded),
            "SHA3 exclusion classification drift")
    require(all(not (RUN_ROOT / row["artifact_rel"]).exists() for row in reused + excluded),
            "reuse/excluded row unexpectedly has a new-run artifact")

    candidate_keys: set[tuple[str, int]] = set()
    artifact_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    method_commits: dict[str, dict[str, Any]] = {}
    cases: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    for job in jobs:
        case_dir = RUN_ROOT / job["artifact_rel"]
        meta_path = case_dir / "SUPERVISOR.json"
        canonical_path = case_dir / "artifact/canonical_candidates.jsonl"
        config_path = case_dir / "artifact/run_config.json"
        require(meta_path.is_file() and canonical_path.is_file() and config_path.is_file(),
                f"missing new-run artifact: {job['job_id']}")
        meta = json.loads(meta_path.read_text())
        config = json.loads(config_path.read_text())
        rows = read_jsonl(canonical_path)
        require(meta.get("status") == "complete" and meta.get("return_code") == 0
                and not meta.get("timed_out")
                and not meta.get("memory_limit_exceeded")
                and not meta.get("process_limit_exceeded"),
                f"supervisor/resource failure: {job['job_id']}")
        require(config.get("manifest_hash") == job["manifest_sha256"]
                and config.get("seed") == job["seed"]
                and config.get("selected_case_count") == 1
                and config.get("model") == "deepseek-v4-pro"
                and config.get("temperature") == 0.2
                and config.get("max_output_tokens") == 8192
                and config.get("model_timeout_sec") == 120,
                f"run config drift: {job['job_id']}")
        if job["method"] == "direct_llm_deepseek_v4_pro":
            require(config.get("schema") == "r3e-table2-run-v1"
                    and config.get("method") == "direct_llm"
                    and config.get("max_semantic_model_calls_per_case") == 1
                    and len(rows) == 1,
                    f"Direct run budget drift: {job['job_id']}")
        else:
            frozen = config.get("frozen_evolved_policy", {})
            require(config.get("schema") == "r3e-table2-evolved-blue-feedback-run-v1"
                    and config.get("method") == "r3e_evolved_blue_feedback_deepseek_v4_pro"
                    and config.get("requested_policy") == {"P": 1, "evidence_k": 6, "n_candidates": 3}
                    and 1 <= len(rows) <= 3,
                    f"R3E run budget drift: {job['job_id']}")
            require(frozen.get("executable_policy_hash") == protocol["r3e_executable_policy_sha256"]
                    and frozen.get("inference_config_hash") == protocol["r3e_feedback_inference_config_sha256"]
                    and frozen.get("registry_file_sha256") == protocol["files"]["configs/skills.json"],
                    f"R3E frozen policy drift: {job['job_id']}")
        indices = [int(row.get("candidate_index", -99)) for row in rows]
        require(indices == list(range(len(rows))), f"non-contiguous candidates: {job['job_id']}")
        pass_indices = []
        for idx, row in enumerate(rows):
            key = (job["job_id"], idx)
            require(key not in candidate_keys, f"duplicate candidate grain: {key}")
            candidate_keys.add(key)
            validate_candidate(row, job, idx, protocol, artifact_counts)
            if row.get("oracle_ok"):
                pass_indices.append(idx)
            commit = row.get("method_commit")
            require(isinstance(commit, dict) and len(str(commit.get("commit", ""))) == 40
                    and isinstance(commit.get("dirty_tree"), bool),
                    f"invalid method commit: {key}")
            method_commits[json.dumps(commit, sort_keys=True)] = commit
            status_counts[str(row.get("status"))] += 1
            candidates.append({**row, "formal_method": job["method"],
                               "job_id": job["job_id"],
                               "canonical_file_sha256": sha256_file(canonical_path)})
        require(len(pass_indices) <= 1 and (not pass_indices or pass_indices[0] == len(rows) - 1),
                f"early-stop violation: {job['job_id']}")
        cases.append({
            "schema": "r3e-table2-deepseek-pro-case-result-v2",
            "method": job["method"], "job_id": job["job_id"],
            "job_key_sha256": job["job_key_sha256"], "benchmark": job["benchmark"],
            "seed": job["seed"], "case_id": job["case_id"],
            "case_sha256": job["case_sha256"], "execution": "new_run",
            "pass_at_1": bool(rows[0].get("oracle_ok")),
            "pass_within_budget": any(bool(row.get("oracle_ok")) for row in rows),
            "candidate_rows": len(rows), "failure_reason": None,
            "supervisor_sha256": sha256_file(meta_path),
            "run_config_sha256": sha256_file(config_path),
            "canonical_candidates_sha256": sha256_file(canonical_path),
        })

    source_cache: dict[Path, dict[str, dict[str, Any]]] = {}
    for job in reused:
        source = (ROOT / job["source_canonical"]).resolve()
        require(source.is_file() and sha256_file(source) == job["source_canonical_sha256"],
                f"reuse source hash drift: {job['job_id']}")
        if source not in source_cache:
            source_rows = read_jsonl(source)
            source_cache[source] = {row["case_id"]: row for row in source_rows}
        row = source_cache[source].get(job["case_id"])
        require(row is not None and sha256_payload(row) == job["source_row_sha256"],
                f"reuse row hash drift: {job['job_id']}")
        require(row.get("seed") == 101 and row.get("candidate_index") == 0
                and row.get("case_sha256") == job["case_sha256"]
                and row.get("status") == job["source_status"]
                and bool(row.get("oracle_ok")) is bool(job["source_oracle_ok"]),
                f"reuse row identity/outcome drift: {job['job_id']}")
        validate_candidate(row, job, 0, protocol, artifact_counts)
        key = (job["job_id"], 0)
        require(key not in candidate_keys, f"duplicate reused candidate: {key}")
        candidate_keys.add(key)
        status_counts[str(row.get("status"))] += 1
        candidates.append({**row, "formal_method": job["method"],
                           "job_id": job["job_id"], "reuse_source_sha256": sha256_file(source)})
        cases.append({
            "schema": "r3e-table2-deepseek-pro-case-result-v2",
            "method": job["method"], "job_id": job["job_id"],
            "job_key_sha256": job["job_key_sha256"], "benchmark": job["benchmark"],
            "seed": 101, "case_id": job["case_id"], "case_sha256": job["case_sha256"],
            "execution": "audited_seed101_reuse", "pass_at_1": bool(row.get("oracle_ok")),
            "pass_within_budget": bool(row.get("oracle_ok")), "candidate_rows": 1,
            "failure_reason": row.get("failure_reason"),
            "source_canonical_sha256": sha256_file(source),
            "source_row_sha256": job["source_row_sha256"],
        })

    for job in excluded:
        cases.append({
            "schema": "r3e-table2-deepseek-pro-case-result-v2",
            "method": job["method"], "job_id": job["job_id"],
            "job_key_sha256": job["job_key_sha256"], "benchmark": job["benchmark"],
            "seed": job["seed"], "case_id": job["case_id"],
            "case_sha256": job["case_sha256"], "execution": "not_run",
            "pass_at_1": False, "pass_within_budget": False, "candidate_rows": 0,
            "failure_reason": "operational_sha3_exclusion_counted_unrepaired",
            "reporting_rule": job["reporting_rule"],
        })

    require(len(cases) == 850 and len({row["job_id"] for row in cases}) == 850,
            "canonical case coverage is not exactly 850 unique jobs")
    require(len(candidates) == 1146 and len(candidate_keys) == 1146,
            "canonical candidate coverage drift")
    require(len(method_commits) == 1, "method commit drift across new runs")

    cases.sort(key=lambda row: (row["method"], row["benchmark"], row["seed"], row["case_id"]))
    candidates.sort(key=lambda row: (
        row["formal_method"], row["benchmark"], row["seed"], row["case_id"], row["candidate_index"]
    ))
    by_cell: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in cases:
        by_cell[(row["method"], row["benchmark"], row["seed"])].append(row)

    per_seed: list[dict[str, Any]] = []
    for method in METHODS:
        for benchmark, denominator in EXPECTED_CASES.items():
            for seed in SEEDS:
                rows = by_cell[(method, benchmark, seed)]
                require(len(rows) == denominator and len({row["case_id"] for row in rows}) == denominator,
                        f"fixed denominator coverage mismatch: {method}/{benchmark}/{seed}")
                p1 = sum(bool(row["pass_at_1"]) for row in rows)
                within = sum(bool(row["pass_within_budget"]) for row in rows)
                executed = [row for row in rows if row["execution"] != "not_run"]
                headline = p1 if method.startswith("direct_") else within
                per_seed.append({
                    "method": method, "benchmark": benchmark, "seed": seed,
                    "fixed_denominator": denominator, "executed_cases": len(executed),
                    "excluded_cases_counted_unrepaired": denominator - len(executed),
                    "pass_at_1_count": p1, "pass_within_budget_count": within,
                    "headline_count": headline,
                    "pass_at_1_percent": 100.0 * p1 / denominator,
                    "pass_within_budget_percent": 100.0 * within / denominator,
                    "headline_percent": 100.0 * headline / denominator,
                    "executed_only_headline_percent": 100.0 * headline / len(executed),
                })

    aggregates: dict[str, dict[str, Any]] = {}
    table_rows: list[dict[str, Any]] = []
    for method in METHODS:
        aggregates[method] = {}
        display = "Direct LLM (DeepSeek-v4-Pro)" if method.startswith("direct_") else "R³E (DeepSeek-v4-Pro)"
        protocol_label = "1 model call; pass@1" if method.startswith("direct_") else "≤3 verified revisions; within-3"
        table_row = {"method": display, "search_protocol": protocol_label, "cells": {}}
        for benchmark in EXPECTED_CASES:
            cells = [row for row in per_seed
                     if row["method"] == method and row["benchmark"] == benchmark]
            rates = [float(row["headline_percent"]) for row in cells]
            mean, sd = mean_sd(rates)
            entry = {
                "headline_metric": "pass_at_1" if method.startswith("direct_") else "pass_within_3_verified_revisions",
                "seed_results": cells,
                "seed_repaired_counts": [row["headline_count"] for row in cells],
                "mean_percent": mean, "sample_sd_percent": sd,
                "formatted_mean_sample_sd": f"{mean:.2f}±{sd:.2f}",
            }
            aggregates[method][benchmark] = entry
            table_row["cells"][benchmark] = entry["formatted_mean_sample_sd"] + (
                "†" if benchmark == "literature32" else ""
            )
        table_rows.append(table_row)

    case_path = out / "canonical_case_results.jsonl"
    candidate_path = out / "canonical_candidates_merged.jsonl"
    status_path = out / "candidate_status_counts.json"
    table_path = out / "TABLE2_ROWS.json"
    write_jsonl(case_path, cases)
    write_jsonl(candidate_path, candidates)
    status_payload = {
        "candidate_rows": len(candidates),
        "status_counts": dict(sorted(status_counts.items())),
        "artifact_hash_checks": dict(sorted(artifact_counts.items())),
    }
    status_path.write_text(json.dumps(status_payload, indent=2, sort_keys=True) + "\n")
    table_path.write_text(json.dumps(table_rows, indent=2, ensure_ascii=False, sort_keys=True) + "\n")

    aggregate = {
        "schema": "r3e-table2-deepseek-v4-pro-aggregate-v2",
        "methods": {
            "direct_llm_deepseek_v4_pro": "one model call; candidate-0 pass@1",
            "r3e_deepseek_v4_pro": "at most three sequential candidate-evaluator-residual revisions; within-3",
        },
        "coverage": {
            "canonical_case_rows": len(cases), "new_jobs": len(jobs),
            "audited_reuse_jobs": len(reused), "excluded_sha3_jobs": len(excluded),
            "candidate_rows": len(candidates),
        },
        "aggregates": aggregates, "table_rows": table_rows,
        "candidate_status_counts": status_payload,
        "method_commits": [method_commits[key] for key in sorted(method_commits)],
        "canonical_case_results_sha256": sha256_file(case_path),
        "canonical_candidates_merged_sha256": sha256_file(candidate_path),
        "candidate_status_counts_sha256": sha256_file(status_path),
        "table2_rows_sha256": sha256_file(table_path),
        "historical_artifacts_overwritten": False,
    }
    aggregate_path = out / "AGGREGATE.json"
    aggregate_path.write_text(json.dumps(aggregate, indent=2, ensure_ascii=False, sort_keys=True) + "\n")

    audit = {
        "schema": "r3e-table2-deepseek-v4-pro-formal-audit-v2",
        "verdict": "PASS",
        "quality_checks": {
            "protocol_and_inventory_hashes_verified": True,
            "new_reuse_excluded_partition_complete_disjoint_850": True,
            "all_729_new_supervisors_complete_return_zero_without_resource_guard": True,
            "all_81_reuse_rows_bound_to_recovery_audit_and_source_row_hash": True,
            "all_40_sha3_rows_not_run_and_counted_unrepaired": True,
            "exact_manifest_case_seed_method_coverage": True,
            "candidate_grain_unique_contiguous_and_within_budget": True,
            "direct_exactly_one_model_call_per_case": True,
            "r3e_feedback_metadata_and_early_stop_verified": True,
            "model_seed_manifest_case_policy_and_state_provenance_verified": True,
            "prompt_response_patch_hashes_verified_when_present": True,
            "oracle_compile_simulation_status_consistent": True,
        },
        "headline_eligibility": {
            "cirfix39": "eligible: exact 39-case x five-seed result",
            "strider14": "eligible: exact 14-case x five-seed result",
            "literature32": (
                "eligible only with dagger: exact 32-case denominator; four SHA3 cases per "
                "seed and method were not run and are counted unrepaired"
            ),
        },
        "protocol_sha256": sha256_file(PROTOCOL),
        "inventory_sha256": sha256_file(inventory_path),
        "jobs_sha256": sha256_file(jobs_path),
        "reuse_sha256": sha256_file(reuse_path),
        "excluded_sha3_sha256": sha256_file(excluded_path),
        "launch_summary_sha256": sha256_file(launch_summary_path),
        "recovery_audit_sha256": sha256_file(RECOVERY_AUDIT),
        "aggregator_sha256": sha256_file(Path(__file__)),
        "aggregate_sha256": sha256_file(aggregate_path),
        "canonical_case_results_sha256": sha256_file(case_path),
        "canonical_candidates_merged_sha256": sha256_file(candidate_path),
        "candidate_status_counts_sha256": sha256_file(status_path),
        "table2_rows_sha256": sha256_file(table_path),
        "network_or_model_calls": 0,
        "historical_artifacts_overwritten": False,
    }
    audit_path = out / "FORMAL_AUDIT.json"
    audit_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps({
        "verdict": "PASS", "aggregate": str(aggregate_path), "audit": str(audit_path),
        "table_rows": table_rows,
    }, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
