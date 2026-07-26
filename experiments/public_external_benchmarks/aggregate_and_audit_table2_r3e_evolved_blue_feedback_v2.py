#!/usr/bin/env python3
"""Rebuild and formally audit the Table-2 Evolved-Blue feedback V2 run.

This is an offline, fail-closed aggregation pass.  It makes no model or
network calls and writes only to the explicitly selected aggregate directory.
"""
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
RUN_ROOT = ROOT / ".cross_benchmark_r3e_202607/r3e_evolved_blue_feedback_20260717_v2"
CONTROL = ROOT / ".cross_benchmark_r3e_202607/r3e_evolved_blue_v2_control"
V1_PROTOCOL = ROOT / ".cross_benchmark_r3e_202607/R3E_EVOLVED_BLUE_FEEDBACK_PROTOCOL_20260717_V1.json"
V2_PROTOCOL = ROOT / ".cross_benchmark_r3e_202607/R3E_EVOLVED_BLUE_FEEDBACK_PROTOCOL_20260717_V2.json"
PARENT_V2_PROTOCOL = ROOT / ".cross_benchmark_r3e_202607/R3E_EVOLVED_BLUE_PROTOCOL_20260716_V2.json"
EXPECTED_CASES = {"cirfix39": 39, "literature32": 32, "strider14": 14}
EXECUTED_CASES = {"cirfix39": 39, "literature32": 28, "strider14": 14}
SEEDS = (101, 202, 303, 404, 505)
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
FEEDBACK_START = "Same-case sequential repair history and real frozen-evaluator residuals:\n"
FEEDBACK_END = "\n\nUse the latest residual to refine the next bounded line patch."


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def mean_sd(values: list[float]) -> tuple[float, float]:
    return statistics.mean(values), statistics.stdev(values)


def verify_artifact(row: dict[str, Any], stem: str, key: tuple[str, int],
                    counts: Counter[str]) -> None:
    raw_path = row.get(f"{stem}_path")
    raw_hash = row.get(f"{stem}_hash")
    if raw_path is None:
        require(raw_hash in (None, ""), f"digest without {stem} path: {key}")
        counts[f"{stem}_absent"] += 1
        return
    path = Path(str(raw_path)).resolve()
    require(path.is_file(), f"missing {stem} artifact: {path}")
    require(path.is_relative_to(RUN_ROOT.resolve()), f"{stem} escaped run root: {path}")
    require(raw_hash == sha256_file(path), f"{stem} hash mismatch: {path}")
    counts[f"{stem}_verified"] += 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    require(not out.exists() or not any(out.iterdir()),
            f"refusing to overwrite non-empty aggregate directory: {out}")
    out.mkdir(parents=True, exist_ok=True)

    inventory_path = RUN_ROOT / "INVENTORY.json"
    jobs_path = RUN_ROOT / "JOBS.jsonl"
    excluded_path = RUN_ROOT / "EXCLUDED_SHA3.jsonl"
    parent_jobs_path = CONTROL / "FORMAL_JOBS.jsonl"
    inventory = json.loads(inventory_path.read_text())
    protocol_v1 = json.loads(V1_PROTOCOL.read_text())
    protocol_v2 = json.loads(V2_PROTOCOL.read_text())
    parent_v2_protocol = json.loads(PARENT_V2_PROTOCOL.read_text())
    jobs = read_jsonl(jobs_path)
    excluded = read_jsonl(excluded_path)
    parent_jobs = read_jsonl(parent_jobs_path)

    require(protocol_v2["parent_inference_protocol_sha256"] == sha256_file(V1_PROTOCOL),
            "V2 protocol no longer binds the frozen V1 inference protocol")
    require(protocol_v1["parent_v2_protocol_sha256"] == sha256_file(PARENT_V2_PROTOCOL),
            "V1 protocol no longer binds the parent Evolved-Blue protocol")
    require(protocol_v2["inference_config_sha256"] == protocol_v1["inference_config_sha256"],
            "V1/V2 inference-config mismatch")
    require(protocol_v2["executable_policy_sha256"] == protocol_v1["executable_policy_sha256"],
            "V1/V2 executable-policy mismatch")
    require(protocol_v2["inventory_hashes"]["INVENTORY.json"] == sha256_file(inventory_path),
            "inventory hash drift")
    require(protocol_v2["inventory_hashes"]["JOBS.jsonl"] == sha256_file(jobs_path),
            "job inventory hash drift")
    require(protocol_v2["inventory_hashes"]["EXCLUDED_SHA3.jsonl"] == sha256_file(excluded_path),
            "SHA3 exclusion hash drift")
    require(inventory["ordinary_jobs"] == 405 and inventory["excluded_sha3_jobs"] == 20,
            "unexpected frozen inventory cardinality")
    require(len(jobs) == 405 and len(excluded) == 20 and len(parent_jobs) == 425,
            "job files do not match the frozen 405+20 partition")

    job_ids = {row["job_id"] for row in jobs}
    excluded_ids = {row["job_id"] for row in excluded}
    parent_ids = {row["job_id"] for row in parent_jobs}
    require(len(job_ids) == 405 and len(excluded_ids) == 20 and len(parent_ids) == 425,
            "duplicate job IDs in inventory")
    require(not (job_ids & excluded_ids) and (job_ids | excluded_ids) == parent_ids,
            "ordinary/excluded partition is not complete and disjoint")
    require(all(row.get("resource_class") == "ordinary" for row in jobs),
            "non-ordinary job entered the executed inventory")
    require(all(row.get("resource_class") == "heavy_serial"
                and str(row.get("case_id", "")).startswith("sha3_")
                and row.get("retry_policy") == "do_not_run"
                and row.get("success") is False for row in excluded),
            "SHA3 exclusion classification drift")
    require(all(not (RUN_ROOT / row["artifact_rel"] / "artifact").exists()
                for row in excluded), "an excluded SHA3 job has an execution artifact")

    expected_policy_hash = protocol_v2["executable_policy_sha256"]
    expected_inference_hash = protocol_v2["inference_config_sha256"]
    expected_base_b3_policy_hash = protocol_v1["base_b3_policy_sha256"]
    expected_registry_hash = parent_v2_protocol["frozen_policy"]["registry_file_sha256"]
    candidate_keys: set[tuple[str, int]] = set()
    method_commits: dict[str, dict[str, Any]] = {}
    status_counts: Counter[str] = Counter()
    artifact_counts: Counter[str] = Counter()
    feedback_counts: Counter[str] = Counter()
    cases: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    for job in jobs:
        case_dir = RUN_ROOT / job["artifact_rel"]
        meta_path = case_dir / "SUPERVISOR.json"
        artifact_dir = case_dir / "artifact"
        canonical_path = artifact_dir / "canonical_candidates.jsonl"
        config_path = artifact_dir / "run_config.json"
        require(meta_path.is_file() and canonical_path.is_file() and config_path.is_file(),
                f"missing formal artifact: {job['job_id']}")
        meta = json.loads(meta_path.read_text())
        config = json.loads(config_path.read_text())
        rows = read_jsonl(canonical_path)

        require(meta.get("status") == "complete" and meta.get("return_code") == 0,
                f"supervisor incomplete: {job['job_id']}")
        require(not meta.get("timed_out") and not meta.get("memory_limit_exceeded")
                and not meta.get("process_limit_exceeded"),
                f"resource guard triggered: {job['job_id']}")
        require(config.get("schema") == "r3e-table2-evolved-blue-feedback-run-v1"
                and config.get("method") == "r3e_evolved_blue_feedback",
                f"run schema/method drift: {job['job_id']}")
        require(config.get("manifest_hash") == job["manifest_sha256"]
                and config.get("seed") == job["seed"]
                and config.get("selected_case_count") == 1,
                f"run identity mismatch: {job['job_id']}")
        require(config.get("model") == "deepseek-v4-flash"
                and config.get("temperature") == 0.2
                and config.get("max_output_tokens") == 8192
                and config.get("model_timeout_sec") == 120,
                f"model configuration drift: {job['job_id']}")
        require(config.get("requested_policy") ==
                {"P": 1, "evidence_k": 6, "n_candidates": 3},
                f"requested-policy drift: {job['job_id']}")
        require(config.get("state_features") == {
            "cross_case": False, "naive_memory": False, "population": False,
            "promotion": False, "red": False, "registry_read_only": True,
            "registry_write": False, "template_write": False,
        }, f"state-feature drift: {job['job_id']}")
        frozen = config.get("frozen_evolved_policy", {})
        require(frozen.get("executable_policy_hash") == expected_policy_hash
                and frozen.get("inference_config_hash") == expected_inference_hash
                and frozen.get("base_b3_executable_policy_hash") == expected_base_b3_policy_hash
                and frozen.get("registry_file_sha256") == expected_registry_hash,
                f"frozen policy/config drift: {job['job_id']}")
        origin = frozen.get("overlay_origin", {})
        require(origin.get("automatic_distillation_claimed") is False
                and origin.get("automatic_promotion_claimed") is False,
                f"unsupported automatic-evolution claim: {job['job_id']}")
        require(1 <= len(rows) <= 3, f"candidate count outside [1,3]: {job['job_id']}")
        indices = [int(row.get("candidate_index", -99)) for row in rows]
        require(indices == list(range(len(rows))),
                f"candidate indices are not contiguous from zero: {job['job_id']}")

        pass_indices: list[int] = []
        previous_evaluated = -1
        for row in rows:
            idx = int(row["candidate_index"])
            key = (job["job_id"], idx)
            require(key not in candidate_keys, f"duplicate candidate grain: {key}")
            candidate_keys.add(key)
            require(row.get("schema") == "r3e-table2-evolved-blue-feedback-candidate-v1"
                    and row.get("method") == "r3e_evolved_blue_feedback",
                    f"candidate schema/method drift: {key}")
            require(row.get("case_id") == job["case_id"]
                    and row.get("case_sha256") == job["case_sha256"]
                    and row.get("seed") == job["seed"]
                    and row.get("manifest_hash") == job["manifest_sha256"],
                    f"candidate identity mismatch: {key}")
            require(row.get("model") == "deepseek-v4-flash"
                    and row.get("temperature") == 0.2
                    and row.get("max_output_tokens") == 8192
                    and row.get("model_timeout_sec") == 120,
                    f"candidate model drift: {key}")
            require(row.get("policy_hash") == expected_policy_hash
                    and row.get("inference_config_hash") == expected_inference_hash,
                    f"candidate policy/config hash drift: {key}")
            require(row.get("registry_file_sha256") == expected_registry_hash,
                    f"candidate registry hash drift: {key}")
            require(row.get("used_strategy") is True
                    and row.get("used_memory") is False
                    and row.get("red_enabled") is False
                    and row.get("population_size") == 1
                    and row.get("registry_read_only") is True
                    and row.get("registry_write_count") == 0
                    and row.get("template_write_count") == 0
                    and row.get("promotion_write_count") == 0
                    and row.get("cross_case_state") is False,
                    f"candidate state drift: {key}")
            require(row.get("feedback_mode") ==
                    "sequential_same_case_frozen_evaluator_feedback"
                    and row.get("feedback_round") == idx
                    and row.get("prior_attempt_count") == idx
                    and row.get("conditioned_on_prior_residual") is (idx > 0),
                    f"feedback metadata drift: {key}")
            prior_evaluated = int(row.get("prior_evaluated_candidate_count", -1))
            require(previous_evaluated <= prior_evaluated <= idx,
                    f"invalid prior evaluated-candidate count: {key}")
            previous_evaluated = prior_evaluated

            for stem in ("prompt", "response", "patch"):
                verify_artifact(row, stem, key, artifact_counts)
            prompt_text = Path(str(row["prompt_path"])).read_text()
            if idx == 0:
                require(FEEDBACK_START not in prompt_text
                        and row.get("feedback_history_sha256") == EMPTY_SHA256,
                        f"candidate zero is not the frozen no-history prompt: {key}")
                feedback_counts["candidate_zero_unconditioned"] += 1
            else:
                require(FEEDBACK_START in prompt_text and FEEDBACK_END in prompt_text,
                        f"feedback history missing from prompt: {key}")
                history = prompt_text.split(FEEDBACK_START, 1)[1].split(FEEDBACK_END, 1)[0]
                require(hashlib.sha256(history.encode()).hexdigest() ==
                        row.get("feedback_history_sha256"),
                        f"feedback-history hash mismatch: {key}")
                require(f"Round {idx} candidate patch JSON:" in history
                        and f"Round {idx} residual:" in history,
                        f"latest prior candidate/residual missing: {key}")
                feedback_counts["feedback_conditioned"] += 1

            method_commit = row.get("method_commit")
            require(isinstance(method_commit, dict)
                    and isinstance(method_commit.get("commit"), str)
                    and len(method_commit["commit"]) == 40
                    and isinstance(method_commit.get("dirty_tree"), bool),
                    f"invalid method provenance: {key}")
            method_commits[json.dumps(method_commit, sort_keys=True)] = method_commit
            ok = bool(row.get("oracle_ok"))
            require((row.get("status") == "pass") == ok,
                    f"status/oracle mismatch: {key}")
            require(not ok or (row.get("compile_ok") is True
                               and row.get("simulation_ok") is True),
                    f"passing candidate lacks compile/simulation pass: {key}")
            if ok:
                pass_indices.append(idx)
            status_counts[str(row.get("status"))] += 1
            candidates.append({**row, "job_id": job["job_id"],
                               "canonical_file_sha256": sha256_file(canonical_path)})

        require(len(pass_indices) <= 1
                and (not pass_indices or pass_indices[0] == len(rows) - 1),
                f"early-stop-after-pass violation: {job['job_id']}")
        cases.append({
            "schema": "r3e-table2-evolved-blue-feedback-case-result-v2",
            "job_id": job["job_id"], "job_key_sha256": job["job_key_sha256"],
            "benchmark": job["benchmark"], "seed": job["seed"],
            "case_id": job["case_id"], "case_sha256": job["case_sha256"],
            "pass_at_1": bool(rows[0].get("oracle_ok")),
            "pass_within_3_feedback_rounds": any(bool(row.get("oracle_ok")) for row in rows),
            "candidate_rows": len(rows),
            "supervisor_sha256": sha256_file(meta_path),
            "canonical_candidates_sha256": sha256_file(canonical_path),
            "run_config_sha256": sha256_file(config_path),
        })

    require(len(cases) == 405 and len(method_commits) == 1,
            "coverage or method-commit drift")
    by_cell: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in cases:
        by_cell[(row["benchmark"], row["seed"])].append(row)

    per_seed: list[dict[str, Any]] = []
    for benchmark, fixed_den in EXPECTED_CASES.items():
        for seed in SEEDS:
            rows = by_cell[(benchmark, seed)]
            observed_den = EXECUTED_CASES[benchmark]
            require(len(rows) == observed_den
                    and len({row["case_id"] for row in rows}) == observed_den,
                    f"coverage mismatch: {benchmark}/{seed}")
            p1 = sum(bool(row["pass_at_1"]) for row in rows)
            p3 = sum(bool(row["pass_within_3_feedback_rounds"]) for row in rows)
            per_seed.append({
                "benchmark": benchmark, "seed": seed,
                "completed_cases": observed_den,
                "fixed_manifest_denominator": fixed_den,
                "excluded_cases_counted_unrepaired": fixed_den - observed_den,
                "pass_at_1_count": p1,
                "pass_within_3_feedback_rounds_count": p3,
                "executed_only_pass_at_1_percent": 100.0 * p1 / observed_den,
                "executed_only_pass_within_3_feedback_rounds_percent": 100.0 * p3 / observed_den,
                "fixed_denominator_pass_at_1_percent": 100.0 * p1 / fixed_den,
                "fixed_denominator_pass_within_3_feedback_rounds_percent": 100.0 * p3 / fixed_den,
            })

    aggregates: dict[str, Any] = {}
    for benchmark in EXPECTED_CASES:
        cells = [row for row in per_seed if row["benchmark"] == benchmark]
        entry: dict[str, Any] = {"seed_results": cells}
        for metric in (
            "executed_only_pass_at_1_percent",
            "executed_only_pass_within_3_feedback_rounds_percent",
            "fixed_denominator_pass_at_1_percent",
            "fixed_denominator_pass_within_3_feedback_rounds_percent",
        ):
            mean, sd = mean_sd([float(row[metric]) for row in cells])
            entry[f"{metric}_mean"] = mean
            entry[f"{metric}_sample_sd"] = sd
        aggregates[benchmark] = entry

    case_path = out / "canonical_case_results.jsonl"
    candidate_path = out / "canonical_candidates_merged.jsonl"
    status_path = out / "candidate_status_counts.json"
    case_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in cases))
    candidate_path.write_text("".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in candidates
    ))
    status_payload = {
        "candidate_rows": len(candidates),
        "status_counts": dict(sorted(status_counts.items())),
        "artifact_hash_checks": dict(sorted(artifact_counts.items())),
        "feedback_chain_checks": dict(sorted(feedback_counts.items())),
    }
    status_path.write_text(json.dumps(status_payload, indent=2, sort_keys=True) + "\n")

    aggregate = {
        "schema": "r3e-table2-evolved-blue-feedback-aggregate-v2",
        "metric": (
            "pass@1 is candidate zero; headline within-3 is success by any of at most "
            "three sequential candidate->evaluator->residual rounds with early stop"
        ),
        "parent_jobs": 425, "completed_ordinary_jobs": len(cases),
        "excluded_sha3_jobs_counted_unrepaired": len(excluded),
        "candidate_rows": len(candidates), "aggregates": aggregates,
        "candidate_status_counts": status_payload,
        "executable_policy_hash": expected_policy_hash,
        "base_b3_executable_policy_hash": expected_base_b3_policy_hash,
        "base_b3_registry_file_sha256": expected_registry_hash,
        "inference_config_hash": expected_inference_hash,
        "method_commits": [method_commits[key] for key in sorted(method_commits)],
        "canonical_case_results_sha256": sha256_file(case_path),
        "canonical_candidates_merged_sha256": sha256_file(candidate_path),
        "historical_artifacts_overwritten": False,
    }
    aggregate_path = out / "AGGREGATE.json"
    aggregate_path.write_text(json.dumps(aggregate, indent=2, sort_keys=True) + "\n")

    audit = {
        "schema": "r3e-table2-evolved-blue-feedback-formal-audit-v2",
        "verdict": "PASS",
        "grain": "benchmark x seed x case; contiguous candidate_index within job",
        "coverage": {
            "parent_jobs": 425, "completed_ordinary_jobs": len(cases),
            "excluded_sha3_jobs": len(excluded), "candidate_rows": len(candidates),
        },
        "quality_checks": {
            "frozen_protocol_v1_v2_chain_verified": True,
            "ordinary_excluded_partition_complete_and_disjoint": True,
            "all_supervisors_complete_return_zero_without_resource_guard": True,
            "case_seed_coverage_complete_at_reported_denominators": True,
            "candidate_indices_contiguous_unique_and_within_budget": True,
            "candidate_zero_unconditioned_and_later_prompts_feedback_conditioned": True,
            "feedback_history_hash_and_latest_prior_residual_verified": True,
            "early_stop_after_pass_verified": True,
            "manifest_case_seed_hashes_match": True,
            "model_budget_policy_and_inference_hashes_match": True,
            "prompt_response_patch_hashes_verified_when_present": True,
            "oracle_compile_simulation_status_consistent": True,
            "state_write_counts_zero_and_cross_case_state_disabled": True,
            "method_commit_structured_and_consistent": True,
            "excluded_sha3_have_no_execution_artifacts": True,
        },
        "headline_eligibility": {
            "cirfix39": "eligible: complete 39-case x five-seed sequential-feedback result",
            "strider14": "eligible: complete 14-case x five-seed sequential-feedback result",
            "literature32": (
                "eligible only with dagger: exact 32-case fixed denominator under the frozen "
                "missing-as-failure rule; four SHA3 cases per seed were not executed"
            ),
        },
        "sha3_reporting_rule": (
            "The 20 excluded SHA3 jobs were not executed and are counted unrepaired only under "
            "the pre-frozen exact Literature-32 missing-as-failure rule; never describe them as "
            "model/evaluator attempts."
        ),
        "automatic_evolution_claim": (
            "not supported by this run: the Evolved overlay was human-authored and frozen; "
            "automatic distillation/promotion claims are false and all online writes are zero"
        ),
        "protocol_v1_sha256": sha256_file(V1_PROTOCOL),
        "protocol_v2_sha256": sha256_file(V2_PROTOCOL),
        "inventory_sha256": sha256_file(inventory_path),
        "jobs_sha256": sha256_file(jobs_path),
        "excluded_sha3_sha256": sha256_file(excluded_path),
        "aggregate_sha256": sha256_file(aggregate_path),
        "canonical_case_results_sha256": sha256_file(case_path),
        "canonical_candidates_merged_sha256": sha256_file(candidate_path),
        "status_counts_sha256": sha256_file(status_path),
        "network_or_model_calls": 0,
        "historical_artifacts_overwritten": False,
    }
    audit_path = out / "FORMAL_AUDIT.json"
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "verdict": "PASS", "aggregate": str(aggregate_path),
        "audit": str(audit_path), "aggregates": aggregates,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
