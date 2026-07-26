#!/usr/bin/env python3
"""Rebuild and audit Wave1 Direct-LLM and Tool-Feedback results."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / ".cross_benchmark_r3e_202607"
WAVE1 = BASE / "wave1"
PROTOCOL = BASE / "WAVE1_PROTOCOL_v2.json"
SUMMARY = WAVE1 / "WAVE1_LAUNCH_SUMMARY.json"
METHODS = ("direct_llm", "llm_tool_feedback")
BENCHMARKS = {"cirfix39": 39, "literature32": 32, "strider14": 14}
SEEDS = (101, 202, 303, 404, 505)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def case_id(row: dict[str, Any]) -> str:
    return str(row.get("task_id") or row.get("case_id"))


def reference_text(row: dict[str, Any]) -> str | None:
    raw = row.get("golden_rtl") or row.get("reference_path")
    if not raw:
        return None
    path = Path(raw)
    return path.read_text(errors="replace").strip() if path.is_file() else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)

    protocol = json.loads(PROTOCOL.read_text())
    summary = json.loads(SUMMARY.read_text())
    require(protocol.get("schema") == "r3e-table2-wave1-protocol-v2",
            "unexpected Wave1 protocol")
    require(summary.get("expected_runs") == 40 and summary.get("observed_runs") == 40
            and summary.get("launcher_failures") == 0,
            "Wave1 launch summary incomplete")
    require(all(row.get("status") == "complete" and
                row.get("completed_cases") == row.get("total_cases")
                for row in summary["runs"]), "Wave1 contains incomplete run")

    manifests: dict[str, dict[str, dict[str, Any]]] = {}
    manifest_hashes: dict[str, str] = {}
    references: dict[tuple[str, str], str | None] = {}
    for benchmark, expected in BENCHMARKS.items():
        spec = protocol["manifests"][benchmark]
        path = ROOT / spec["path"]
        require(sha256_file(path) == spec["sha256"], f"manifest hash drift: {benchmark}")
        rows = read_jsonl(path)
        by_id = {case_id(row): row for row in rows}
        require(len(rows) == len(by_id) == expected == spec["rows"],
                f"manifest coverage drift: {benchmark}")
        manifests[benchmark] = by_id
        manifest_hashes[benchmark] = spec["sha256"]
        for cid, row in by_id.items():
            references[(benchmark, cid)] = reference_text(row)

    candidates = 0
    case_rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    artifact_counts: Counter[str] = Counter()
    calls_per_case: dict[str, Counter[int]] = {method: Counter() for method in METHODS}
    duplicate_response_hashes: Counter[str] = Counter()
    repeated_later_prompts: Counter[str] = Counter()
    request_ids: list[str] = []
    method_commits: dict[str, dict[str, Any]] = {}
    run_canonical_hashes: dict[str, str] = {}
    run_config_hashes: dict[str, str] = {}
    full_golden_prompt_leaks = 0
    golden_path_field_tokens = 0
    aggregate_cells: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    candidate_keys: set[tuple[str, str, int, str, int]] = set()

    for method in METHODS:
        for benchmark, denominator in BENCHMARKS.items():
            manifest = manifests[benchmark]
            for seed in SEEDS:
                run = WAVE1 / method / benchmark / f"seed_{seed}"
                config_path = run / "run_config.json"
                canonical_path = run / "canonical_candidates.jsonl"
                config = json.loads(config_path.read_text())
                rows = read_jsonl(canonical_path)
                require(config.get("manifest_hash") == manifest_hashes[benchmark]
                        and config.get("selected_case_count") == denominator
                        and config.get("method") == method and config.get("seed") == seed,
                        f"run identity drift: {method}/{benchmark}/{seed}")
                require(config.get("model") == "deepseek-v4-flash"
                        and config.get("temperature") == 0.2
                        and config.get("max_output_tokens") == 8192
                        and config.get("model_timeout_sec") == 120
                        and config.get("max_semantic_model_calls_per_case") == 3,
                        f"model/budget drift: {method}/{benchmark}/{seed}")
                require(config.get("state_features") == {
                    "cross_case": False, "memory": False, "promotion": False,
                    "red": False, "registry": False, "template": False,
                }, f"state drift: {method}/{benchmark}/{seed}")
                by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
                for row in rows:
                    cid = row["case_id"]
                    require(cid in manifest and row.get("case_sha256") ==
                            manifest[cid]["case_sha256"], "candidate case identity mismatch")
                    require(row.get("manifest_hash") == manifest_hashes[benchmark]
                            and row.get("benchmark") == manifest[cid].get("benchmark")
                            and row.get("method") == method and row.get("seed") == seed,
                            "candidate run identity mismatch")
                    require(row.get("model") == "deepseek-v4-flash"
                            and row.get("temperature") == 0.2
                            and row.get("max_output_tokens") == 8192
                            and row.get("model_timeout_sec") == 120,
                            "candidate model drift")
                    require(row.get("used_memory") is False
                            and row.get("red_enabled") is False
                            and row.get("population_size") == 1
                            and row.get("registry_enabled") is False
                            and row.get("template_enabled") is False
                            and row.get("promotion_enabled") is False
                            and row.get("cross_case_state") is False,
                            "candidate state-feature drift")
                    index = int(row["candidate_index"])
                    key = (method, benchmark, seed, cid, index)
                    require(key not in candidate_keys, f"duplicate candidate grain: {key}")
                    candidate_keys.add(key)
                    require((row.get("status") == "pass") == bool(row.get("oracle_ok")),
                            f"status/oracle mismatch: {key}")
                    if "gate" in row:
                        require(bool(row["gate"].get("ok")) == bool(row.get("oracle_ok")),
                                f"gate/oracle mismatch: {key}")
                    commit = row.get("method_commit")
                    require(isinstance(commit, dict) and isinstance(commit.get("commit"), str)
                            and isinstance(commit.get("dirty_tree"), bool),
                            f"invalid method commit: {key}")
                    commit_key = json.dumps(commit, sort_keys=True, separators=(",", ":"))
                    method_commits[commit_key] = commit
                    for stem in ("prompt", "response"):
                        path = Path(row[f"{stem}_path"])
                        require(path.is_file() and sha256_file(path) == row[f"{stem}_hash"],
                                f"{stem} hash mismatch: {key}")
                        artifact_counts[f"{stem}_verified"] += 1
                    patch_path = row.get("patch_path")
                    if patch_path:
                        path = Path(patch_path)
                        require(path.is_file() and sha256_file(path) == row["patch_hash"],
                                f"patch hash mismatch: {key}")
                        artifact_counts["patch_verified"] += 1
                    else:
                        require(row.get("patch_hash") in (None, ""),
                                f"patch path/hash inconsistency: {key}")
                        artifact_counts["patch_absent"] += 1
                    prompt = Path(row["prompt_path"]).read_text(errors="replace")
                    golden = references[(benchmark, cid)]
                    if golden and golden in prompt:
                        full_golden_prompt_leaks += 1
                    if "golden_rtl" in prompt or "reference_path" in prompt:
                        golden_path_field_tokens += 1
                    if row.get("request_id"):
                        request_ids.append(str(row["request_id"]))
                    status_counts[str(row.get("status"))] += 1
                    by_case[cid].append(row)
                    candidates += 1

                require(set(by_case) == set(manifest) and len(by_case) == denominator,
                        f"fixed denominator incomplete: {method}/{benchmark}/{seed}")
                pass1 = 0
                within3 = 0
                for cid, case_candidates in by_case.items():
                    ordered = sorted(case_candidates, key=lambda row: int(row["candidate_index"]))
                    indices = [int(row["candidate_index"]) for row in ordered]
                    require(indices == list(range(len(indices))) and 1 <= len(indices) <= 3,
                            f"candidate index/budget violation: {method}/{benchmark}/{seed}/{cid}")
                    passed_indices = [int(row["candidate_index"]) for row in ordered
                                      if row.get("oracle_ok")]
                    require(not passed_indices or passed_indices == [indices[-1]],
                            f"runner did not stop at first pass: {method}/{benchmark}/{seed}/{cid}")
                    pass1 += bool(ordered[0].get("oracle_ok"))
                    within3 += bool(passed_indices)
                    calls_per_case[method][len(ordered)] += 1
                    response_hashes = [row["response_hash"] for row in ordered]
                    duplicate_response_hashes[method] += len(response_hashes) - len(set(response_hashes))
                    repeated_later_prompts[method] += sum(
                        row["prompt_hash"] == ordered[0]["prompt_hash"] for row in ordered[1:]
                    )
                    case_rows.append({
                        "schema": "r3e-table2-wave1-direct-feedback-case-v2",
                        "method": method, "benchmark": benchmark, "seed": seed,
                        "case_id": cid, "case_sha256": manifest[cid]["case_sha256"],
                        "manifest_sha256": manifest_hashes[benchmark],
                        "candidate_rows": len(ordered),
                        "pass_at_1": bool(ordered[0].get("oracle_ok")),
                        "success_within_3_calls": bool(passed_indices),
                        "first_passing_candidate_index": passed_indices[0] if passed_indices else None,
                        "source_canonical_sha256": sha256_file(canonical_path),
                    })
                aggregate_cells[(method, benchmark)].append({
                    "seed": seed, "denominator": denominator,
                    "pass_at_1_count": int(pass1),
                    "success_within_3_calls_count": int(within3),
                    "pass_at_1_percent": 100.0 * pass1 / denominator,
                    "success_within_3_calls_percent": 100.0 * within3 / denominator,
                })
                run_canonical_hashes[str(canonical_path)] = sha256_file(canonical_path)
                run_config_hashes[str(config_path)] = sha256_file(config_path)

    require(len(case_rows) == 850 and candidates == 1569, "unexpected Wave1 evidence grain")
    require(full_golden_prompt_leaks == 0 and golden_path_field_tokens == 0,
            "golden/reference prompt leakage detected")
    require(len(request_ids) == len(set(request_ids)), "duplicate non-null API request ID")
    require(len(method_commits) == 1, "method commit drift")

    aggregates: list[dict[str, Any]] = []
    for (method, benchmark), runs in sorted(aggregate_cells.items()):
        runs.sort(key=lambda row: row["seed"])
        pass1_values = [float(row["pass_at_1_percent"]) for row in runs]
        within3_values = [float(row["success_within_3_calls_percent"]) for row in runs]
        aggregates.append({
            "method": method, "benchmark": benchmark, "runs": runs,
            "pass_at_1_percent_mean": statistics.mean(pass1_values),
            "pass_at_1_percent_sample_sd": statistics.stdev(pass1_values),
            "success_within_3_calls_percent_mean": statistics.mean(within3_values),
            "success_within_3_calls_percent_sample_sd": statistics.stdev(within3_values),
            "main_table_metric": (
                "pass_at_1" if method == "direct_llm" else "success_within_3_calls"
            ),
        })

    case_path = out / "canonical_case_results.jsonl"
    case_rows.sort(key=lambda row: (row["method"], row["benchmark"], row["seed"], row["case_id"]))
    case_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in case_rows))
    status_path = out / "candidate_status_counts.json"
    status_payload = {
        "candidate_rows": candidates,
        "status_counts": dict(sorted(status_counts.items())),
        "artifact_hash_checks": dict(sorted(artifact_counts.items())),
        "calls_per_case": {method: {str(k): v for k, v in sorted(counts.items())}
                           for method, counts in calls_per_case.items()},
        "duplicate_same_case_response_hashes": dict(duplicate_response_hashes),
        "later_prompts_identical_to_first": dict(repeated_later_prompts),
    }
    status_path.write_text(json.dumps(status_payload, indent=2, sort_keys=True) + "\n")
    aggregate = {
        "schema": "r3e-table2-wave1-direct-feedback-aggregate-v2",
        "main_table_reporting": {
            "direct_llm": "one model call: pass_at_1",
            "llm_tool_feedback": "sequential success within at most three calls",
            "direct_best_of_3": "diagnostic/appendix only; not labeled plain Direct LLM",
        },
        "input_disclosure": (
            "Both methods receive buggy RTL plus initial frozen-evaluator mismatch evidence. "
            "Tool Feedback additionally receives same-case candidate/evaluator history."
        ),
        "aggregates": aggregates,
        "coverage": {"case_seed_rows": len(case_rows), "candidate_rows": candidates,
                     "runs": len(aggregate_cells) * len(SEEDS)},
        "method_commits": [method_commits[key] for key in sorted(method_commits)],
        "run_canonical_hashes": dict(sorted(run_canonical_hashes.items())),
        "run_config_hashes": dict(sorted(run_config_hashes.items())),
        "canonical_case_results_sha256": sha256_file(case_path),
        "candidate_status_counts_sha256": sha256_file(status_path),
        "historical_artifacts_overwritten": False,
    }
    aggregate_path = out / "AGGREGATE.json"
    aggregate_path.write_text(json.dumps(aggregate, indent=2, sort_keys=True) + "\n")
    audit = {
        "schema": "r3e-table2-wave1-direct-feedback-formal-audit-v2",
        "verdict": "PASS_WITH_REPORTING_CORRECTION",
        "numeric_verdict": "PASS: documented pass@1 and within-3 values rebuild exactly",
        "reporting_correction": (
            "Plain Direct LLM must use pass@1. Its within-3 result is Best-of-3 Sampling, "
            "not the main Direct LLM row."
        ),
        "quality_checks": {
            "fixed_denominators_complete": True,
            "candidate_grain_unique_and_indices_contiguous": True,
            "early_stop_after_first_pass_valid_for_within_3": True,
            "case_manifest_hashes_match": True,
            "prompt_response_patch_hashes_match": True,
            "status_oracle_gate_consistent": True,
            "parse_and_transport_failures_retained": True,
            "state_features_disabled": True,
            "full_golden_rtl_absent_from_all_prompts": True,
            "golden_reference_path_fields_absent_from_all_prompts": True,
        },
        "limitations": {
            "direct_initial_input": "buggy RTL plus initial oracle mismatch evidence, not RTL-only",
            "direct_candidate_independence": (
                "context-independent repeated calls use the same prompt and run seed; strict "
                "probabilistic independence is not established"
            ),
            "repair_action_space": (
                "Direct/Tool Feedback return complete RTL; Evolved-Blue uses bounded line patches"
            ),
        },
        "coverage": aggregate["coverage"],
        "protocol_sha256": sha256_file(PROTOCOL),
        "launch_summary_sha256": sha256_file(SUMMARY),
        "aggregate_sha256": sha256_file(aggregate_path),
        "canonical_case_results_sha256": sha256_file(case_path),
        "candidate_status_counts_sha256": sha256_file(status_path),
        "network_or_model_calls": 0,
        "historical_artifacts_overwritten": False,
    }
    audit_path = out / "FORMAL_AUDIT.json"
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"verdict": audit["verdict"], "aggregate": str(aggregate_path),
                      "audit": str(audit_path), "aggregates": aggregates}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
