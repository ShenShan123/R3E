#!/usr/bin/env python3
"""Aggregate and formally audit the V2+V3 ordinary-only Evolved-Blue run."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from table2_common import read_jsonl, sha256_file


ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / ".cross_benchmark_r3e_202607/r3e_evolved_blue_v2_control"
V2_ROOT = ROOT / ".cross_benchmark_r3e_202607/r3e_evolved_blue_rerun_20260716_v2"
V3_ROOT = ROOT / ".cross_benchmark_r3e_202607/r3e_evolved_blue_recovery_20260716_v3"
PARENT_PROTOCOL = ROOT / ".cross_benchmark_r3e_202607/R3E_EVOLVED_BLUE_PROTOCOL_20260716_V2.json"
RECOVERY_PROTOCOL = ROOT / ".cross_benchmark_r3e_202607/R3E_EVOLVED_BLUE_RECOVERY_PROTOCOL_20260716_V3.json"
EXPECTED_CASES = {"cirfix39": 39, "literature32": 32, "strider14": 14}
OBSERVED_CASES = {"cirfix39": 39, "literature32": 28, "strider14": 14}
SEEDS = (101, 202, 303, 404, 505)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mean_sd(values: list[float]) -> tuple[float, float]:
    return statistics.mean(values), statistics.stdev(values)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)

    formal_path = CONTROL / "FORMAL_JOBS.jsonl"
    jobs = read_jsonl(formal_path)
    reused = read_jsonl(V3_ROOT / "REUSED_V2_RESULTS.jsonl")
    pending = read_jsonl(V3_ROOT / "JOBS.jsonl")
    excluded = read_jsonl(V3_ROOT / "EXCLUDED_SHA3.jsonl")
    require(len(jobs) == 425, "parent formal inventory must contain 425 jobs")
    require(len({row["job_id"] for row in jobs}) == 425, "duplicate parent job_id")
    job_by_id = {row["job_id"]: row for row in jobs}
    reused_by_id = {row["job_id"]: row for row in reused}
    pending_by_id = {row["job_id"]: row for row in pending}
    excluded_by_id = {row["job_id"]: row for row in excluded}
    require((set(reused_by_id) | set(pending_by_id) | set(excluded_by_id)) == set(job_by_id),
            "reused/pending/excluded partition does not cover the parent inventory")
    require(not (set(reused_by_id) & set(pending_by_id)), "reused/pending overlap")
    require(not (set(reused_by_id) & set(excluded_by_id)), "reused/excluded overlap")
    require(not (set(pending_by_id) & set(excluded_by_id)), "pending/excluded overlap")
    require((len(reused), len(pending), len(excluded)) == (8, 397, 20),
            "unexpected recovery partition cardinality")
    require(all(row["resource_class"] == "heavy_serial" and row["case_id"].startswith("sha3_")
                for row in excluded), "non-SHA3 job entered exclusion list")

    cases: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    artifact_counts: Counter[str] = Counter()
    policy_hashes: set[str] = set()
    inference_hashes: set[str] = set()
    method_commits: dict[str, dict[str, Any]] = {}
    candidate_key_seen: set[tuple[str, int]] = set()

    for job in jobs:
        if job["job_id"] in excluded_by_id:
            continue
        source = "v2_reused" if job["job_id"] in reused_by_id else "v3_recovery"
        root = V2_ROOT if source == "v2_reused" else V3_ROOT
        case_dir = root / job["artifact_rel"]
        meta_path = case_dir / "SUPERVISOR.json"
        artifact = case_dir / "artifact"
        canonical_path = artifact / "canonical_candidates.jsonl"
        config_path = artifact / "run_config.json"
        require(meta_path.is_file() and canonical_path.is_file() and config_path.is_file(),
                f"missing completed artifact: {job['job_id']}")
        meta = json.loads(meta_path.read_text())
        config = json.loads(config_path.read_text())
        rows = read_jsonl(canonical_path)
        require(meta.get("status") == "complete" and meta.get("return_code") == 0,
                f"supervisor not complete: {job['job_id']}")
        require(not meta.get("timed_out") and not meta.get("memory_limit_exceeded")
                and not meta.get("process_limit_exceeded"),
                f"resource failure in selected artifact: {job['job_id']}")
        require(config.get("manifest_hash") == job["manifest_sha256"], "config manifest mismatch")
        require(config.get("seed") == job["seed"] and config.get("selected_case_count") == 1,
                "config identity mismatch")
        require(config.get("model") == "deepseek-v4-flash" and config.get("temperature") == 0.2
                and config.get("max_output_tokens") == 8192
                and config.get("model_timeout_sec") == 120, "model config drift")
        require(config.get("requested_policy") == {"P": 1, "evidence_k": 6, "n_candidates": 3},
                "requested policy drift")
        state = config.get("state_features", {})
        require(state == {"cross_case": False, "naive_memory": False, "population": False,
                          "promotion": False, "red": False, "registry_read_only": True,
                          "registry_write": False, "template_write": False},
                "state-feature drift")
        require(1 <= len(rows) <= 3, f"candidate count outside [1,3]: {job['job_id']}")
        indices = [int(row.get("candidate_index", -1)) for row in rows]
        require(len(indices) == len(set(indices)) and min(indices) >= 0 and max(indices) <= 2,
                f"candidate-index violation: {job['job_id']}")
        pass1 = False
        pass3 = False
        for row in rows:
            idx = int(row["candidate_index"])
            key = (job["job_id"], idx)
            require(key not in candidate_key_seen, f"duplicate candidate grain: {key}")
            candidate_key_seen.add(key)
            require(row.get("case_id") == job["case_id"] and
                    row.get("case_sha256") == job["case_sha256"] and
                    row.get("seed") == job["seed"] and
                    row.get("manifest_hash") == job["manifest_sha256"],
                    f"candidate identity mismatch: {key}")
            require(row.get("model") == "deepseek-v4-flash" and row.get("temperature") == 0.2
                    and row.get("max_output_tokens") == 8192, f"candidate model drift: {key}")
            require(row.get("red_enabled") is False and row.get("population_size") == 1
                    and row.get("used_memory") is False and row.get("cross_case_state") is False
                    and row.get("registry_read_only") is True
                    and row.get("registry_write_count") == 0
                    and row.get("template_write_count") == 0
                    and row.get("promotion_write_count") == 0,
                    f"candidate state drift: {key}")
            require(row.get("used_strategy") is True, f"strategy not active: {key}")
            policy_hashes.add(str(row.get("policy_hash")))
            inference_hashes.add(str(row.get("inference_config_hash")))
            method_commit = row.get("method_commit")
            require(isinstance(method_commit, dict), f"unstructured method commit: {key}")
            require(isinstance(method_commit.get("commit"), str)
                    and len(method_commit["commit"]) == 40
                    and isinstance(method_commit.get("dirty_tree"), bool),
                    f"invalid method commit: {key}")
            commit_key = json.dumps(method_commit, sort_keys=True, separators=(",", ":"))
            method_commits[commit_key] = method_commit
            ok = bool(row.get("oracle_ok"))
            require(not ok or (row.get("compile_ok") is True and row.get("simulation_ok") is True
                               and row.get("status") == "pass"), f"oracle consistency failure: {key}")
            require((row.get("status") == "pass") == ok, f"status/oracle mismatch: {key}")
            pass1 = pass1 or (idx == 0 and ok)
            pass3 = pass3 or ok
            status_counts[str(row.get("status"))] += 1
            for stem in ("prompt", "response", "patch"):
                raw_path = row.get(f"{stem}_path")
                raw_hash = row.get(f"{stem}_hash")
                if raw_path is None:
                    if raw_hash in (None, ""):
                        artifact_counts[f"{stem}_absent"] += 1
                    else:
                        require(len(str(raw_hash)) == 64 and
                                all(c in "0123456789abcdef" for c in str(raw_hash)),
                                f"invalid digest-only {stem} hash: {key}")
                        artifact_counts[f"{stem}_digest_only"] += 1
                    continue
                path = Path(str(raw_path))
                require(path.is_file(), f"missing {stem} artifact: {path}")
                require(raw_hash == file_hash(path), f"{stem} hash mismatch: {path}")
                artifact_counts[f"{stem}_verified"] += 1
            candidates.append({**row, "job_id": job["job_id"], "source_run": source,
                               "canonical_file_sha256": sha256_file(canonical_path)})
        cases.append({
            "schema": "r3e-table2-evolved-blue-case-result-v3",
            "job_id": job["job_id"], "job_key_sha256": job["job_key_sha256"],
            "benchmark": job["benchmark"], "seed": job["seed"],
            "case_id": job["case_id"], "case_sha256": job["case_sha256"],
            "pass_at_1": pass1, "pass_at_3": pass3, "candidate_rows": len(rows),
            "source_run": source, "supervisor_sha256": sha256_file(meta_path),
            "canonical_candidates_sha256": sha256_file(canonical_path),
            "run_config_sha256": sha256_file(config_path),
        })
        source_counts[source] += 1

    require(source_counts == {"v2_reused": 8, "v3_recovery": 397},
            f"unexpected selected source counts: {source_counts}")
    require(policy_hashes == {"32b9484c647cbad50a0914e45435c728728ed66a269edb7daf954061e95a40cf"},
            f"policy hash drift: {policy_hashes}")
    require(inference_hashes == {"4e9358092bd921cc097cf115067c72adc817f1dfc74c362089676d90a75e6f6d"},
            f"inference hash drift: {inference_hashes}")
    require(len(method_commits) == 1, f"method commit drift: {sorted(method_commits)}")

    by_cell: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in cases:
        by_cell[(row["benchmark"], row["seed"])].append(row)
    per_seed: list[dict[str, Any]] = []
    for benchmark in EXPECTED_CASES:
        for seed in SEEDS:
            rows = by_cell[(benchmark, seed)]
            require(len(rows) == OBSERVED_CASES[benchmark],
                    f"coverage mismatch {benchmark}/{seed}: {len(rows)}")
            ids = {row["case_id"] for row in rows}
            require(len(ids) == len(rows), f"duplicate case {benchmark}/{seed}")
            p1 = sum(bool(row["pass_at_1"]) for row in rows)
            p3 = sum(bool(row["pass_at_3"]) for row in rows)
            observed_den = OBSERVED_CASES[benchmark]
            fixed_den = EXPECTED_CASES[benchmark]
            per_seed.append({
                "benchmark": benchmark, "seed": seed,
                "completed_cases": observed_den, "fixed_manifest_denominator": fixed_den,
                "excluded_cases": fixed_den - observed_den,
                "pass_at_1_count": p1, "pass_at_3_count": p3,
                "ordinary_pass_at_1_percent": 100.0 * p1 / observed_den,
                "ordinary_pass_at_3_percent": 100.0 * p3 / observed_den,
                "conservative_fixed_denominator_pass_at_1_percent": 100.0 * p1 / fixed_den,
                "conservative_fixed_denominator_pass_at_3_percent": 100.0 * p3 / fixed_den,
            })

    aggregates: dict[str, Any] = {}
    for benchmark in EXPECTED_CASES:
        cells = [row for row in per_seed if row["benchmark"] == benchmark]
        entry: dict[str, Any] = {"seed_results": cells}
        for metric in ("ordinary_pass_at_1_percent", "ordinary_pass_at_3_percent",
                       "conservative_fixed_denominator_pass_at_1_percent",
                       "conservative_fixed_denominator_pass_at_3_percent"):
            mean, sd = mean_sd([float(row[metric]) for row in cells])
            entry[f"{metric}_mean"] = mean
            entry[f"{metric}_sample_sd"] = sd
        aggregates[benchmark] = entry

    status_path = out / "candidate_status_counts.json"
    status_payload = {
        "candidate_rows": len(candidates), "status_counts": dict(sorted(status_counts.items())),
        "artifact_hash_checks": dict(sorted(artifact_counts.items())),
    }
    status_path.write_text(json.dumps(status_payload, indent=2, sort_keys=True) + "\n")
    case_path = out / "canonical_case_results.jsonl"
    candidate_path = out / "canonical_candidates_merged.jsonl"
    case_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in cases))
    candidate_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                                      for row in candidates))
    aggregate = {
        "schema": "r3e-table2-evolved-blue-aggregate-v3",
        "metric": "case repaired when candidate 1 (pass@1) or any of candidates 1-3 (pass@3) has oracle_ok=true",
        "expected_parent_jobs": 425, "completed_ordinary_jobs": len(cases),
        "excluded_sha3_jobs": len(excluded), "source_counts": dict(source_counts),
        "aggregates": aggregates, "candidate_status_counts": status_payload,
        "policy_hash": next(iter(policy_hashes)),
        "inference_config_hash": next(iter(inference_hashes)),
        "method_commits": [method_commits[key] for key in sorted(method_commits)],
        "canonical_case_results_sha256": sha256_file(case_path),
        "canonical_candidates_merged_sha256": sha256_file(candidate_path),
        "historical_artifacts_overwritten": False,
    }
    aggregate_path = out / "AGGREGATE.json"
    aggregate_path.write_text(json.dumps(aggregate, indent=2, sort_keys=True) + "\n")
    audit = {
        "schema": "r3e-table2-evolved-blue-formal-audit-v3",
        "verdict": "PASS",
        "grain": "benchmark x seed x case; candidate_index unique within job",
        "coverage": {
            "parent_jobs": 425, "selected_complete_jobs": len(cases),
            "v2_reused": source_counts["v2_reused"], "v3_recovery": source_counts["v3_recovery"],
            "excluded_sha3": len(excluded), "candidate_rows": len(candidates),
        },
        "quality_checks": {
            "partition_complete_and_disjoint": True, "supervisors_complete_return_zero": True,
            "case_seed_unique_and_complete_at_reported_denominator": True,
            "candidate_indices_unique_and_within_budget": True,
            "manifest_case_seed_hashes_match": True, "model_and_budget_match": True,
            "prompt_response_patch_files_verified_when_paths_present": True,
            "digest_only_artifacts_preserved_and_counted": True,
            "oracle_compile_simulation_status_consistent": True,
            "state_write_counts_zero": True, "single_policy_hash": True,
            "method_commit_structured_and_consistent": True,
        },
        "headline_eligibility": {
            "cirfix39": "eligible: complete 39-case x five-seed functional result",
            "strider14": "eligible: complete 14-case x five-seed functional result",
            "literature32": (
                "not eligible as a complete Literature-32 rerun: four SHA3 cases per seed were not run; "
                "report Literature-28 ordinary-only and/or a clearly labeled conservative 32-denominator sensitivity"
            ),
        },
        "sha3_reporting_rule": (
            "excluded jobs are not native/model failures; never describe them as executed. "
            "They may be counted unrepaired only in the explicitly labeled conservative fixed-denominator sensitivity."
        ),
        "parent_protocol_sha256": sha256_file(PARENT_PROTOCOL),
        "recovery_protocol_sha256": sha256_file(RECOVERY_PROTOCOL),
        "formal_jobs_sha256": sha256_file(formal_path),
        "aggregate_sha256": sha256_file(aggregate_path),
        "canonical_case_results_sha256": sha256_file(case_path),
        "canonical_candidates_merged_sha256": sha256_file(candidate_path),
        "status_counts_sha256": sha256_file(status_path),
        "network_or_model_calls": 0,
        "historical_artifacts_overwritten": False,
    }
    audit_path = out / "FORMAL_AUDIT.json"
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"verdict": "PASS", "aggregate": str(aggregate_path),
                      "audit": str(audit_path), "aggregates": aggregates}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
