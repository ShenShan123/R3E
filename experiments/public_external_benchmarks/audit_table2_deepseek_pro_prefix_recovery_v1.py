#!/usr/bin/env python3
"""Audit and aggregate DeepSeek-v4-Pro prefix-preserving recovery overlays."""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from table2_common import canonical_json, read_jsonl, sha256_bytes, sha256_file, write_jsonl


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / ".cross_benchmark_r3e_202607/table2_deepseek_v4_pro_20260720_v2/formal_aggregate"
STATE = ROOT / ".iso_semrepair/skills.json"
DENOMINATORS = {"cirfix39": 39, "literature32": 32, "strider14": 14}
SEEDS = (101, 202, 303, 404, 505)
METHODS = ("direct_llm_deepseek_v4_pro", "r3e_deepseek_v4_pro")


def is_api(row: dict) -> bool:
    return str(row.get("status", "")).startswith("api_")


def source_rows(job: dict) -> list[dict]:
    path = (ROOT / job["source_canonical_rel"]).resolve()
    if sha256_file(path) != job["source_canonical_sha256"]: raise RuntimeError("source hash drift")
    rows = sorted(read_jsonl(path), key=lambda row: int(row["candidate_index"]))
    if sha256_bytes(canonical_json(rows).encode()) != job["source_rows_sha256"]:
        raise RuntimeError("source rows hash drift")
    return rows


def aggregate(cases: list[dict]) -> dict:
    per_seed = []
    for method in METHODS:
        for benchmark, denominator in DENOMINATORS.items():
            for seed in SEEDS:
                rows = [row for row in cases if row["method"] == method
                        and row["benchmark"] == benchmark and int(row["seed"]) == seed]
                if len(rows) != denominator:
                    raise RuntimeError(f"coverage drift {method} {benchmark} {seed}: {len(rows)}")
                p1 = sum(bool(row["pass_at_1"]) for row in rows)
                pn = sum(bool(row["pass_within_budget"]) for row in rows)
                per_seed.append({
                    "method": method, "benchmark": benchmark, "seed": seed,
                    "denominator": denominator,
                    "excluded_unexecuted": sum(row.get("execution") == "not_run" for row in rows),
                    "pass_at_1_repaired": p1, "pass_within_budget_repaired": pn,
                    "pass_at_1_pct": 100 * p1 / denominator,
                    "pass_within_budget_pct": 100 * pn / denominator,
                })
    summary = []
    for method in METHODS:
        for benchmark in DENOMINATORS:
            rows = [row for row in per_seed if row["method"] == method and row["benchmark"] == benchmark]
            p1 = [row["pass_at_1_pct"] for row in rows]
            pn = [row["pass_within_budget_pct"] for row in rows]
            summary.append({
                "method": method, "benchmark": benchmark, "seeds": list(SEEDS),
                "pass_at_1_counts": [row["pass_at_1_repaired"] for row in rows],
                "pass_within_budget_counts": [row["pass_within_budget_repaired"] for row in rows],
                "pass_at_1_mean_pct": statistics.mean(p1),
                "pass_at_1_sample_sd_pct": statistics.stdev(p1),
                "pass_within_budget_mean_pct": statistics.mean(pn),
                "pass_within_budget_sample_sd_pct": statistics.stdev(pn),
            })
    return {"schema": "r3e-table2-deepseek-pro-prefix-recovery-aggregate-v1",
            "reporting": "five-seed mean and sample SD; Literature SHA3 unexecuted-as-failure",
            "per_seed": per_seed, "summary": summary}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(); protocol = json.loads(args.protocol.read_text())
    jobs = read_jsonl(root / "JOBS.jsonl"); errors: list[str] = []
    if len(jobs) != 92 or Counter(row["arm"] for row in jobs) != {"r3e": 61, "direct": 31}:
        errors.append("frozen recovery inventory count mismatch")
    recovered: dict[str, list[dict]] = {}; replacements = []
    for job in jobs:
        output = root / job["artifact_rel"]; artifact = output / "artifact"
        try:
            supervisor = json.loads((output / "SUPERVISOR.json").read_text())
            old = source_rows(job); new = read_jsonl(artifact / "canonical_candidates.jsonl")
            meta = json.loads((artifact / "RECOVERY_META.json").read_text())
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{job['recovery_job_id']}: artifact read failure {exc}"); continue
        if supervisor.get("status") != "complete" or supervisor.get("return_code") != 0:
            errors.append(f"{job['recovery_job_id']}: supervisor not clean")
        for flag in ("timed_out", "memory_limit_exceeded", "process_limit_exceeded"):
            if supervisor.get(flag): errors.append(f"{job['recovery_job_id']}: {flag}")
        first = int(job["first_api_candidate_index"])
        if new[:first] != old[:first]: errors.append(f"{job['recovery_job_id']}: prefix drift")
        expected_len = 1 if job["arm"] == "direct" else 3
        if not 1 <= len(new) <= expected_len: errors.append(f"{job['recovery_job_id']}: budget violation")
        if [int(row["candidate_index"]) for row in new] != list(range(len(new))):
            errors.append(f"{job['recovery_job_id']}: candidate indices not contiguous")
        if any(is_api(row) for row in new): errors.append(f"{job['recovery_job_id']}: API failure remains")
        if any(not row.get("recovered_candidate") for row in new[first:]):
            errors.append(f"{job['recovery_job_id']}: suffix provenance missing")
        if any(row.get("oracle_ok") for row in new[:-1]): errors.append(f"{job['recovery_job_id']}: early-stop violation")
        if first < len(new) and new[first].get("prompt_hash") != old[first].get("prompt_hash"):
            errors.append(f"{job['recovery_job_id']}: first semantic prompt changed")
        if meta.get("first_api_candidate_index") != first or meta.get("remaining_api_rows") != 0:
            errors.append(f"{job['recovery_job_id']}: recovery metadata mismatch")
        for row in new:
            tag = f"{job['recovery_job_id']}:{row.get('candidate_index')}"
            if row.get("model") != "deepseek-v4-pro" or row.get("temperature") != 0.2 \
                    or row.get("max_output_tokens") != 8192 or row.get("model_timeout_sec") != 120:
                errors.append(f"{tag}: model config drift")
            if row.get("used_memory") is not False or row.get("red_enabled") is not False \
                    or row.get("population_size") != 1 or row.get("cross_case_state") is not False:
                errors.append(f"{tag}: state boundary drift")
            if job["arm"] == "r3e":
                if row.get("method") != "r3e_evolved_blue_feedback_deepseek_v4_pro" \
                        or row.get("policy_hash") != protocol["semantic_protocol"]["r3e_executable_policy_sha256"] \
                        or row.get("inference_config_hash") != protocol["semantic_protocol"]["r3e_inference_config_sha256"]:
                    errors.append(f"{tag}: R3E method/policy drift")
            elif row.get("method") != "direct_llm":
                errors.append(f"{tag}: Direct method drift")
            for stem in ("prompt", "response"):
                path = Path(str(row.get(f"{stem}_path", "")))
                if not path.is_file() or sha256_file(path) != row.get(f"{stem}_hash"):
                    errors.append(f"{tag}: {stem} artifact/hash invalid")
            patch_raw = row.get("patch_path")
            if patch_raw:
                path = Path(str(patch_raw))
                if not path.is_file() or sha256_file(path) != row.get("patch_hash"):
                    errors.append(f"{tag}: patch artifact/hash invalid")
        recovered[job["job_id"]] = new
        replacements.append({
            "recovery_job_id": job["recovery_job_id"], "job_id": job["job_id"],
            "arm": job["arm"], "first_api_candidate_index": first,
            "preserved_prefix_rows": first,
            "source_canonical_sha256": job["source_canonical_sha256"],
            "recovered_canonical_sha256": sha256_file(artifact / "canonical_candidates.jsonl"),
        })

    base_cases = read_jsonl(SOURCE / "canonical_case_results.jsonl")
    base_candidates = read_jsonl(SOURCE / "canonical_candidates_merged.jsonl")
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in base_candidates: grouped[row["job_id"]].append(row)
    canonical_cases: list[dict[str, Any]] = []; canonical_candidates: list[dict[str, Any]] = []
    source_ledger = []
    for case in base_cases:
        job_id = case["job_id"]
        if job_id in recovered:
            rows = recovered[job_id]; source = "prefix-recovery"
        else:
            rows = sorted(grouped.get(job_id, []), key=lambda row: int(row["candidate_index"])); source = "original"
        if any(is_api(row) for row in rows): errors.append(f"{job_id}: overlay retains API failure")
        copied_case = dict(case); copied_case.update({
            "artifact_source": source,
            "pass_at_1": any(int(row["candidate_index"]) == 0 and row.get("oracle_ok") for row in rows),
            "pass_within_budget": any(row.get("oracle_ok") for row in rows),
            "candidate_rows": len(rows),
        })
        if source == "prefix-recovery": copied_case["execution"] = "prefix_recovery"
        canonical_cases.append(copied_case)
        for row in rows:
            copied = dict(row); copied["formal_method"] = case["method"]
            copied["job_id"] = job_id; copied["canonical_overlay_source"] = source
            canonical_candidates.append(copied)
        source_ledger.append({"method": case["method"], "job_id": job_id, "artifact_source": source})
    counts = Counter((row["method"], row["artifact_source"]) for row in source_ledger)
    expected = {("direct_llm_deepseek_v4_pro", "original"): 394,
                ("direct_llm_deepseek_v4_pro", "prefix-recovery"): 31,
                ("r3e_deepseek_v4_pro", "original"): 364,
                ("r3e_deepseek_v4_pro", "prefix-recovery"): 61}
    if dict(counts) != expected: errors.append(f"overlay source count mismatch: {dict(counts)}")
    if len(canonical_cases) != 850 or len(replacements) != 92:
        errors.append("canonical/replacement cardinality mismatch")
    remaining_api = sum(is_api(row) for row in canonical_candidates)
    if remaining_api: errors.append(f"canonical overlay has {remaining_api} API rows")
    before = protocol["state_guard_hashes_before"]["strategy_registry"]["sha256"]
    after = sha256_file(STATE)
    if before != after: errors.append("strategy registry state guard changed")
    canonical_cases.sort(key=lambda row: (row["method"], row["benchmark"], row["seed"], row["case_id"]))
    canonical_candidates.sort(key=lambda row: (row["formal_method"], row["benchmark"], row["seed"], row["case_id"], row["candidate_index"]))
    write_jsonl(root / "CANONICAL_CASES.jsonl", canonical_cases)
    write_jsonl(root / "CANONICAL_CANDIDATES.jsonl", canonical_candidates)
    write_jsonl(root / "CANONICAL_SOURCE_LEDGER.jsonl", source_ledger)
    write_jsonl(root / "REPLACEMENT_LEDGER.jsonl", replacements)
    if not errors:
        (root / "AGGREGATE.json").write_text(json.dumps(aggregate(canonical_cases), indent=2, sort_keys=True) + "\n")
    audit = {
        "schema": "r3e-table2-deepseek-pro-prefix-recovery-formal-audit-v1",
        "verdict": "PASS" if not errors else "FAIL", "errors": errors,
        "protocol_sha256": sha256_file(args.protocol), "recovery_jobs": len(jobs),
        "canonical_case_rows": len(canonical_cases),
        "canonical_candidate_rows": len(canonical_candidates),
        "canonical_transport_api_failures": remaining_api,
        "source_counts": {f"{method}:{source}": count for (method, source), count in counts.items()},
        "replacement_ledger_rows": len(replacements),
        "state_guard_before": before, "state_guard_after": after,
    }
    (root / "FORMAL_AUDIT.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    print(json.dumps(audit, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
