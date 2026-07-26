#!/usr/bin/env python3
"""Merge and audit the seed-101 Kimi-3 Direct Table-2 recovery."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from table2_common import read_jsonl, sha256_file


ROOT = Path(__file__).resolve().parents[2]
PARENT = ROOT / ".cross_benchmark_r3e_202607/table2_kimi3_20260718_v3"
RECOVERY = ROOT / ".cross_benchmark_r3e_202607/table2_kimi3_direct_recovery_20260718_v1"
PROTOCOL = ROOT / ".cross_benchmark_r3e_202607/TABLE2_KIMI3_DIRECT_RECOVERY_PROTOCOL_20260718_V1.json"
PARENT_PROTOCOL = ROOT / ".cross_benchmark_r3e_202607/TABLE2_KIMI3_PROTOCOL_20260718_V3.json"
EXPECTED = {"cirfix39": 39, "literature32": 32, "strider14": 14}
EXECUTED = {"cirfix39": 39, "literature32": 28, "strider14": 14}
MANIFEST_BENCHMARK = {"cirfix39": "cirfix39", "literature32": "literature32", "strider14": "strider"}
SEED = 101
METHOD = "direct_llm_kimi3_v3"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def canonical(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)


def verify_artifact(path: Any, digest: Any, label: str) -> str:
    require(isinstance(path, str) and path, f"missing {label} path")
    artifact = Path(path)
    require(artifact.is_file(), f"missing {label} artifact: {artifact}")
    actual = sha256_file(artifact)
    require(digest == actual, f"{label} hash mismatch: {artifact}")
    return actual


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=RECOVERY / "formal_aggregate")
    args = parser.parse_args()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)

    inventory = json.loads((RECOVERY / "INVENTORY.json").read_text())
    protocol = json.loads(PROTOCOL.read_text())
    launch = json.loads((RECOVERY / "LAUNCH_SUMMARY.json").read_text())
    parent_jobs_path = ROOT / inventory["parent_jobs"]
    parent_jobs = [
        row for row in read_jsonl(parent_jobs_path)
        if row.get("method") == METHOD and row.get("seed") == SEED
    ]
    reused = read_jsonl(RECOVERY / "REUSED_PARENT_JOBS.jsonl")
    pending = read_jsonl(RECOVERY / "PENDING_JOBS.jsonl")
    excluded = read_jsonl(RECOVERY / "EXCLUDED_SHA3.jsonl")

    require(sha256_file(parent_jobs_path) == inventory["parent_jobs_sha256"], "parent job inventory drift")
    for name, digest in inventory["hashes"].items():
        require(sha256_file(RECOVERY / name) == digest, f"frozen recovery inventory drift: {name}")
    require(protocol["inventory"] == inventory, "protocol/inventory mismatch")
    require(protocol["parent_protocol_sha256"] == sha256_file(PARENT_PROTOCOL), "parent protocol drift")
    require((len(parent_jobs), len(reused), len(pending), len(excluded)) == (81, 37, 44, 4),
            "unexpected parent/reused/pending/excluded cardinality")
    require(launch.get("complete_supervisors") == 44 and launch.get("failed_supervisors") == 0
            and launch.get("launcher_failures") == 0, "recovery launcher is not cleanly complete")
    require(launch.get("r3e_jobs_launched") == 0 and launch.get("sha3_jobs_launched") == 0,
            "out-of-scope jobs were launched")

    parent_by_id = {row["job_id"]: row for row in parent_jobs}
    reused_by_id = {row["job_id"]: row for row in reused}
    pending_by_id = {row["job_id"]: row for row in pending}
    excluded_by_id = {row["job_id"]: row for row in excluded}
    require(len(parent_by_id) == 81 and len(reused_by_id) == 37 and len(pending_by_id) == 44
            and len(excluded_by_id) == 4, "duplicate job_id in an inventory")
    require(not (set(reused_by_id) & set(pending_by_id)), "reused/pending overlap")
    require((set(reused_by_id) | set(pending_by_id)) == set(parent_by_id),
            "reused/pending partition does not cover the parent Direct seed")
    require(not (set(parent_by_id) & set(excluded_by_id)), "executed/excluded overlap")
    require(all(row.get("benchmark") == "literature32" and row.get("resource_class") == "heavy_serial"
                and str(row.get("case_id", "")).startswith("sha3_") for row in excluded),
            "non-SHA3 case entered the exclusion inventory")

    # Check that selected plus excluded cases reproduce each frozen manifest exactly.
    selected_by_benchmark: dict[str, set[tuple[str, str]]] = defaultdict(set)
    excluded_by_benchmark: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in parent_jobs:
        selected_by_benchmark[row["benchmark"]].add((row["case_id"], row["case_sha256"]))
    for row in excluded:
        excluded_by_benchmark[row["benchmark"]].add((row["case_id"], row["case_sha256"]))
    manifest_hashes: dict[str, str] = {}
    for benchmark in EXPECTED:
        manifest_path = ROOT / next(row["manifest"] for row in parent_jobs + excluded
                                    if row["benchmark"] == benchmark)
        manifest_rows = read_jsonl(manifest_path)
        manifest_cases = {(row.get("case_id") or row.get("task_id"), row["case_sha256"])
                          for row in manifest_rows}
        require(len(manifest_cases) == EXPECTED[benchmark], f"manifest grain mismatch: {benchmark}")
        require(selected_by_benchmark[benchmark] | excluded_by_benchmark[benchmark] == manifest_cases,
                f"selected/excluded coverage mismatch: {benchmark}")
        require(not (selected_by_benchmark[benchmark] & excluded_by_benchmark[benchmark]),
                f"selected/excluded case overlap: {benchmark}")
        require(len(selected_by_benchmark[benchmark]) == EXECUTED[benchmark],
                f"executed denominator mismatch: {benchmark}")
        manifest_hashes[benchmark] = sha256_file(manifest_path)

    cases: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    status_by_benchmark: dict[str, Counter[str]] = defaultdict(Counter)
    method_commits: dict[str, dict[str, Any]] = {}
    candidate_keys: set[tuple[str, int]] = set()
    artifact_checks: Counter[str] = Counter()

    for job in parent_jobs:
        if job["job_id"] in reused_by_id:
            source = "parent_v3_reused"
            selected = reused_by_id[job["job_id"]]
            case_dir = ROOT / selected["source_output"]
        else:
            source = "direct_recovery_v1"
            selected = pending_by_id[job["job_id"]]
            case_dir = RECOVERY / selected["artifact_rel"]
        meta_path = case_dir / "SUPERVISOR.json"
        artifact = case_dir / "artifact"
        heartbeat_path = artifact / "heartbeat.json"
        config_path = artifact / "run_config.json"
        canonical_path = artifact / "canonical_candidates.jsonl"
        require(all(path.is_file() for path in (meta_path, heartbeat_path, config_path, canonical_path)),
                f"missing selected artifact: {job['job_id']}")
        meta = json.loads(meta_path.read_text())
        heartbeat = json.loads(heartbeat_path.read_text())
        config = json.loads(config_path.read_text())
        rows = read_jsonl(canonical_path)
        require(meta.get("status") == "complete" and meta.get("return_code") == 0,
                f"supervisor not complete: {job['job_id']}")
        require(not meta.get("timed_out") and not meta.get("memory_limit_exceeded")
                and not meta.get("process_limit_exceeded"), f"resource failure: {job['job_id']}")
        require(heartbeat.get("status") == "complete" and heartbeat.get("completed_cases") == 1
                and heartbeat.get("total_cases") == 1, f"heartbeat mismatch: {job['job_id']}")
        require(config.get("manifest_hash") == job["manifest_sha256"]
                and config.get("seed") == SEED and config.get("selected_case_count") == 1,
                f"run identity mismatch: {job['job_id']}")
        require(config.get("method") == "direct_llm" and config.get("model") == "kimi-k3"
                and float(config.get("temperature")) == 1.0
                and config.get("max_output_tokens") == 8192
                and config.get("model_timeout_sec") == 120
                and config.get("max_semantic_model_calls_per_case") == 1
                and config.get("transport_retries_per_semantic_call") == 1,
                f"Direct model/call budget drift: {job['job_id']}")
        require(config.get("state_features") == {
                    "cross_case": False, "memory": False, "promotion": False, "red": False,
                    "registry": False, "template": False,
                }, f"state boundary drift: {job['job_id']}")
        require(len(rows) == 1 and rows[0].get("candidate_index") == 0,
                f"Direct job is not exactly one candidate: {job['job_id']}")
        row = rows[0]
        key = (job["job_id"], int(row["candidate_index"]))
        require(key not in candidate_keys, f"duplicate candidate grain: {key}")
        candidate_keys.add(key)
        require(row.get("case_id") == job["case_id"] and row.get("case_sha256") == job["case_sha256"]
                and row.get("seed") == SEED and row.get("manifest_hash") == job["manifest_sha256"],
                f"candidate identity mismatch: {job['job_id']}")
        require(row.get("benchmark") == MANIFEST_BENCHMARK[job["benchmark"]],
                f"candidate benchmark mismatch: {job['job_id']}")
        require(row.get("method") == "direct_llm" and row.get("model") == "kimi-k3"
                and float(row.get("temperature")) == 1.0 and row.get("max_output_tokens") == 8192
                and row.get("model_timeout_sec") == 120 and row.get("population_size") == 1,
                f"candidate method/model drift: {job['job_id']}")
        require(row.get("used_memory") is False and row.get("registry_enabled") is False
                and row.get("template_enabled") is False and row.get("promotion_enabled") is False
                and row.get("red_enabled") is False and row.get("cross_case_state") is False,
                f"candidate state drift: {job['job_id']}")
        require(row.get("status") in {"pass", "functional_failure", "api_timeout", "api_http_error"},
                f"unexpected candidate status: {job['job_id']}")
        repaired = bool(row.get("oracle_ok"))
        require((row.get("status") == "pass") == repaired, f"status/oracle mismatch: {job['job_id']}")
        require(not repaired or (row.get("compile_ok") is True and row.get("simulation_ok") is True),
                f"passing candidate lacks compile/simulation success: {job['job_id']}")
        commit = row.get("method_commit")
        require(isinstance(commit, dict) and isinstance(commit.get("commit"), str)
                and len(commit["commit"]) == 40 and isinstance(commit.get("dirty_tree"), bool),
                f"invalid method commit: {job['job_id']}")
        method_commits[json.dumps(commit, sort_keys=True)] = commit
        for stem in ("prompt", "response", "patch"):
            path = row.get(f"{stem}_path")
            digest = row.get(f"{stem}_hash")
            if path is None:
                require(digest in (None, ""), f"digest without {stem} path: {job['job_id']}")
                artifact_checks[f"{stem}_absent"] += 1
            else:
                verify_artifact(path, digest, stem)
                artifact_checks[f"{stem}_verified"] += 1
        if source == "parent_v3_reused":
            require(selected["source_supervisor_sha256"] == sha256_file(meta_path)
                    and selected["source_heartbeat_sha256"] == sha256_file(heartbeat_path)
                    and selected["source_canonical_candidates_sha256"] == sha256_file(canonical_path)
                    and selected["candidate_status"] == row["status"],
                    f"reused evidence drift: {job['job_id']}")
        cases.append({
            "schema": "r3e-table2-kimi3-direct-case-result-v1", "job_id": job["job_id"],
            "benchmark": job["benchmark"], "seed": SEED, "case_id": job["case_id"],
            "case_sha256": job["case_sha256"], "executed": True, "repaired": repaired,
            "candidate_status": row["status"], "source_run": source,
            "supervisor_sha256": sha256_file(meta_path), "heartbeat_sha256": sha256_file(heartbeat_path),
            "run_config_sha256": sha256_file(config_path),
            "canonical_candidates_sha256": sha256_file(canonical_path),
        })
        candidates.append({**row, "job_id": job["job_id"], "table2_benchmark": job["benchmark"],
                           "source_run": source, "canonical_file_sha256": sha256_file(canonical_path)})
        source_counts[source] += 1
        status_counts[str(row["status"])] += 1
        status_by_benchmark[job["benchmark"]][str(row["status"])] += 1

    require(source_counts == {"parent_v3_reused": 37, "direct_recovery_v1": 44},
            f"source count mismatch: {source_counts}")
    require(len(method_commits) == 1, f"method commit drift: {method_commits}")

    results: dict[str, Any] = {}
    for benchmark in EXPECTED:
        rows = [row for row in cases if row["benchmark"] == benchmark]
        require(len(rows) == EXECUTED[benchmark], f"case coverage mismatch: {benchmark}")
        require(len({row["case_id"] for row in rows}) == len(rows), f"duplicate case: {benchmark}")
        repaired = sum(bool(row["repaired"]) for row in rows)
        exact_den = EXPECTED[benchmark]
        executed_den = EXECUTED[benchmark]
        results[benchmark] = {
            "seed": SEED, "repaired_cases": repaired, "executed_cases": executed_den,
            "excluded_unexecuted_cases": exact_den - executed_den,
            "exact_manifest_denominator": exact_den,
            "table2_fixed_denominator_percent": 100.0 * repaired / exact_den,
            "executed_only_diagnostic_percent": 100.0 * repaired / executed_den,
            "candidate_status_counts": dict(sorted(status_by_benchmark[benchmark].items())),
        }

    status_path = out / "CANDIDATE_STATUS_COUNTS.json"
    case_path = out / "canonical_case_results.jsonl"
    candidate_path = out / "canonical_candidates_merged.jsonl"
    status_payload = {
        "candidate_rows": len(candidates), "status_counts": dict(sorted(status_counts.items())),
        "status_counts_by_benchmark": {
            benchmark: dict(sorted(status_by_benchmark[benchmark].items())) for benchmark in EXPECTED
        },
        "artifact_hash_checks": dict(sorted(artifact_checks.items())),
    }
    status_path.write_text(json.dumps(status_payload, indent=2, sort_keys=True) + "\n")
    case_path.write_text(canonical(cases))
    candidate_path.write_text(canonical(candidates))
    aggregate = {
        "schema": "r3e-table2-kimi3-direct-seed101-aggregate-v1",
        "metric": "one-call pass@1; a case is repaired iff candidate_index=0 has oracle_ok=true",
        "reporting_rule": "unexecuted SHA3 cases count unrepaired on the exact Literature-32 denominator",
        "seed": SEED, "model": "kimi-k3", "temperature": 1.0,
        "source_counts": dict(sorted(source_counts.items())), "completed_ordinary_jobs": len(cases),
        "excluded_sha3_jobs": len(excluded), "results": results,
        "candidate_status_counts": status_payload, "manifest_hashes": manifest_hashes,
        "method_commits": [method_commits[key] for key in sorted(method_commits)],
        "recovery_protocol_sha256": sha256_file(PROTOCOL),
        "parent_protocol_sha256": sha256_file(PARENT_PROTOCOL),
        "canonical_case_results_sha256": sha256_file(case_path),
        "canonical_candidates_merged_sha256": sha256_file(candidate_path),
        "candidate_status_counts_sha256": sha256_file(status_path),
        "network_or_model_calls": 0, "historical_artifacts_overwritten": False,
    }
    aggregate_path = out / "AGGREGATE.json"
    aggregate_path.write_text(json.dumps(aggregate, indent=2, sort_keys=True) + "\n")
    audit = {
        "schema": "r3e-table2-kimi3-direct-seed101-formal-audit-v1", "verdict": "PASS",
        "grain": "benchmark x seed x case; exactly one candidate_index=0 per executed case",
        "coverage": {"selected_complete_jobs": len(cases), "parent_v3_reused": 37,
                     "direct_recovery_v1": 44, "excluded_sha3": 4, "candidate_rows": len(candidates)},
        "quality_checks": {
            "reused_pending_partition_complete_and_disjoint": True,
            "selected_excluded_manifest_coverage_complete_and_disjoint": True,
            "supervisors_complete_return_zero_without_resource_failure": True,
            "heartbeats_complete": True, "case_seed_unique_at_reported_grain": True,
            "exactly_one_candidate_index_zero_per_executed_case": True,
            "manifest_case_seed_hashes_match": True, "model_and_call_budget_match": True,
            "state_boundary_matches": True, "prompt_response_patch_hashes_verified_when_present": True,
            "oracle_compile_simulation_status_consistent": True,
            "single_structured_method_commit": True,
        },
        "paper_reporting": {
            "cirfix39": "eligible as a seed-101 pilot result on the complete 39-case denominator",
            "literature32": "report only with a dagger: four SHA3 cases were not executed and count unrepaired",
            "strider14": "eligible as a seed-101 pilot result on the complete 14-case denominator",
            "uncertainty": "single seed only; do not attach a standard deviation",
            "provider_failures": "API timeout/HTTP errors are retained as provider-inclusive unrepaired outcomes",
        },
        "aggregate_sha256": sha256_file(aggregate_path),
        "canonical_case_results_sha256": sha256_file(case_path),
        "canonical_candidates_merged_sha256": sha256_file(candidate_path),
        "candidate_status_counts_sha256": sha256_file(status_path),
        "network_or_model_calls": 0, "historical_artifacts_overwritten": False,
    }
    audit_path = out / "FORMAL_AUDIT.json"
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")

    markdown = [
        "# Kimi-3 Direct Table 2 — seed 101\n",
        "| Benchmark | Repaired / executed | Exact denominator | Table 2 fixed-denominator result | Executed-only diagnostic |",
        "|---|---:|---:|---:|---:|",
    ]
    labels = {"cirfix39": "CirFix-39", "literature32": "Literature-32†", "strider14": "Strider-14"}
    for benchmark in EXPECTED:
        row = results[benchmark]
        markdown.append(
            f"| {labels[benchmark]} | {row['repaired_cases']} / {row['executed_cases']} | "
            f"{row['exact_manifest_denominator']} | {row['table2_fixed_denominator_percent']:.2f}% | "
            f"{row['executed_only_diagnostic_percent']:.2f}% |"
        )
    markdown.extend([
        "",
        "† Four SHA3 cases were not executed because of the frozen resource exclusion and are counted unrepaired on the exact Literature-32 denominator.",
        "",
        "This is a single-seed result; no standard deviation is reported. API transport failures remain unrepaired provider-inclusive outcomes.",
        "",
    ])
    result_path = out / "TABLE2_RESULT.md"
    result_path.write_text("\n".join(markdown))
    print(json.dumps({"verdict": "PASS", "results": results, "aggregate": str(aggregate_path),
                      "audit": str(audit_path), "table2_result": str(result_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
