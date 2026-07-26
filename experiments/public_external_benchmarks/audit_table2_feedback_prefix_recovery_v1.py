#!/usr/bin/env python3
"""Audit and aggregate paired prefix-preserving feedback recovery."""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from table2_common import canonical_json, read_jsonl, sha256_bytes, sha256_file, write_jsonl


ROOT = Path(__file__).resolve().parents[2]
R3E = ROOT / ".cross_benchmark_r3e_202607/r3e_evolved_blue_feedback_20260717_v2/formal_aggregate"
CONSTRAINED = ROOT / ".cross_benchmark_r3e_202607/table2_constrained_baselines_20260722_v2"
STATE = ROOT / ".iso_semrepair/skills.json"
DENOMINATORS = {"cirfix39": 39, "literature32": 32, "strider14": 14}
SEEDS = (101, 202, 303, 404, 505)


def norm_benchmark(value: str) -> str:
    return "strider14" if value == "strider" else value


def key(row: dict) -> str:
    return f"{norm_benchmark(row['benchmark'])}|{row['seed']}|{row['case_id']}"


def is_api(row: dict) -> bool:
    return str(row.get("status", "")).startswith("api_")


def source_rows(job: dict) -> list[dict]:
    path = (ROOT / job["source_canonical_rel"]).resolve()
    if sha256_file(path) != job["source_canonical_sha256"]:
        raise RuntimeError("source canonical hash drift")
    rows = read_jsonl(path)
    if job["arm"] == "constrained":
        rows = [row for row in rows if row.get("method") == "feedback_constrained"]
    rows = sorted(rows, key=lambda row: int(row["candidate_index"]))
    if sha256_bytes(canonical_json(rows).encode()) != job["source_rows_sha256"]:
        raise RuntimeError("source rows hash drift")
    return rows


def aggregate(cases: list[dict]) -> dict:
    per_seed = []
    for arm in ("r3e", "constrained"):
        for benchmark, denominator in DENOMINATORS.items():
            for seed in SEEDS:
                rows = [row for row in cases if row["arm"] == arm and row["benchmark"] == benchmark
                        and int(row["seed"]) == seed]
                expected_executed = denominator - (4 if benchmark == "literature32" else 0)
                if len(rows) != expected_executed:
                    raise RuntimeError(f"aggregate coverage drift {arm} {benchmark} {seed}: {len(rows)}")
                p1 = sum(bool(row["pass_at_1"]) for row in rows)
                p3 = sum(bool(row["within_3"]) for row in rows)
                per_seed.append({
                    "arm": arm, "benchmark": benchmark, "seed": seed,
                    "denominator": denominator, "executed_cases": len(rows),
                    "excluded_as_failure": denominator - len(rows),
                    "pass_at_1_repaired": p1, "within_3_repaired": p3,
                    "pass_at_1_pct": 100.0 * p1 / denominator,
                    "within_3_pct": 100.0 * p3 / denominator,
                })
    summary = []
    for arm in ("r3e", "constrained"):
        for benchmark in DENOMINATORS:
            rows = [row for row in per_seed if row["arm"] == arm and row["benchmark"] == benchmark]
            p1 = [row["pass_at_1_pct"] for row in rows]; p3 = [row["within_3_pct"] for row in rows]
            summary.append({
                "arm": arm, "benchmark": benchmark, "seeds": list(SEEDS),
                "pass_at_1_counts": [row["pass_at_1_repaired"] for row in rows],
                "within_3_counts": [row["within_3_repaired"] for row in rows],
                "pass_at_1_mean_pct": statistics.mean(p1),
                "pass_at_1_sample_sd_pct": statistics.stdev(p1),
                "within_3_mean_pct": statistics.mean(p3),
                "within_3_sample_sd_pct": statistics.stdev(p3),
            })
    return {"schema": "r3e-table2-feedback-prefix-recovery-aggregate-v1",
            "reporting": "five-seed mean and sample SD; Literature SHA3 missing-as-failure",
            "per_seed": per_seed, "summary": summary}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(); protocol = json.loads(args.protocol.read_text())
    jobs = read_jsonl(root / "JOBS.jsonl")
    errors: list[str] = []
    if len(jobs) != 104 or Counter(row["arm"] for row in jobs) != {"r3e": 56, "constrained": 48}:
        errors.append("frozen recovery inventory count mismatch")
    canonical_cases: list[dict[str, Any]] = []
    canonical_candidates: list[dict[str, Any]] = []
    source_ledger: list[dict[str, Any]] = []
    replacement_ledger: list[dict[str, Any]] = []
    recovered_rows: dict[str, list[dict]] = {}
    for job in jobs:
        output = root / job["artifact_rel"]
        meta_path = output / "SUPERVISOR.json"; artifact = output / "artifact"
        if not meta_path.is_file():
            errors.append(f"{job['recovery_job_id']}: supervisor missing"); continue
        supervisor = json.loads(meta_path.read_text())
        if supervisor.get("status") != "complete" or supervisor.get("return_code") != 0:
            errors.append(f"{job['recovery_job_id']}: supervisor not clean")
        for flag in ("timed_out", "memory_limit_exceeded", "process_limit_exceeded"):
            if supervisor.get(flag): errors.append(f"{job['recovery_job_id']}: {flag}")
        try:
            old = source_rows(job); new = read_jsonl(artifact / "canonical_candidates.jsonl")
            meta = json.loads((artifact / "RECOVERY_META.json").read_text())
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{job['recovery_job_id']}: artifact read failure {exc}"); continue
        first = int(job["first_api_candidate_index"])
        if new[:first] != old[:first]: errors.append(f"{job['recovery_job_id']}: preserved prefix drift")
        if [int(row["candidate_index"]) for row in new] != list(range(len(new))):
            errors.append(f"{job['recovery_job_id']}: candidate indices not contiguous")
        if not 1 <= len(new) <= 3: errors.append(f"{job['recovery_job_id']}: candidate budget violation")
        if any(is_api(row) for row in new): errors.append(f"{job['recovery_job_id']}: API failure remains")
        if any(not row.get("recovered_candidate") for row in new[first:]):
            errors.append(f"{job['recovery_job_id']}: suffix lacks recovery provenance")
        if any(row.get("oracle_ok") for row in new[:-1]):
            errors.append(f"{job['recovery_job_id']}: did not early-stop after pass")
        if first < len(new):
            old_prompt_hash = old[first].get("prompt_hash")
            if new[first].get("prompt_hash") != old_prompt_hash:
                errors.append(f"{job['recovery_job_id']}: first failed semantic prompt changed")
        if meta.get("first_api_candidate_index") != first or meta.get("remaining_api_rows") != 0:
            errors.append(f"{job['recovery_job_id']}: recovery metadata mismatch")
        if new and old and new[0].get("policy_hash") != old[0].get("policy_hash"):
            errors.append(f"{job['recovery_job_id']}: policy hash changed")
        expected_inference = protocol["semantic_protocol"][f"{job['arm']}_inference_config_sha256"]
        expected_policy = protocol["semantic_protocol"][
            "r3e_executable_policy_sha256" if job["arm"] == "r3e" else "constrained_policy_sha256"
        ]
        for row in new:
            tag = f"{job['recovery_job_id']}:{row.get('candidate_index')}"
            if row.get("inference_config_hash") != expected_inference:
                errors.append(f"{tag}: inference config hash drift")
            if row.get("policy_hash") != expected_policy:
                errors.append(f"{tag}: executable policy hash drift")
            if row.get("model") != "deepseek-v4-flash" or row.get("temperature") != 0.2 \
                    or row.get("max_output_tokens") != 8192:
                errors.append(f"{tag}: model configuration drift")
            for field in ("prompt", "response"):
                path_value = row.get(f"{field}_path")
                hash_value = row.get(f"{field}_hash")
                if not path_value or not Path(path_value).is_file() \
                        or sha256_file(Path(path_value)) != hash_value:
                    errors.append(f"{tag}: {field} artifact/hash invalid")
            patch_value = row.get("patch_path")
            if patch_value and (not Path(patch_value).is_file()
                                or sha256_file(Path(patch_value)) != row.get("patch_hash")):
                errors.append(f"{tag}: patch artifact/hash invalid")
            if row.get("registry_write_count", 0) != 0 or row.get("template_write_count", 0) != 0 \
                    or row.get("promotion_write_count", 0) != 0 or row.get("cross_case_state"):
                errors.append(f"{tag}: forbidden state write/use")
        recovered_rows[job["recovery_job_id"]] = new
        replacement_ledger.append({
            "recovery_job_id": job["recovery_job_id"], "arm": job["arm"], "job_id": job["job_id"],
            "first_api_candidate_index": first, "preserved_prefix_rows": first,
            "source_canonical_sha256": job["source_canonical_sha256"],
            "recovered_canonical_sha256": sha256_file(artifact / "canonical_candidates.jsonl"),
        })

    # Rebuild R3E and constrained from their original sources with recovery overlays.
    r3e_cases = read_jsonl(R3E / "canonical_case_results.jsonl")
    r3e_all = read_jsonl(R3E / "canonical_candidates_merged.jsonl")
    r3e_by: dict[str, list[dict]] = defaultdict(list)
    for row in r3e_all: r3e_by[key(row)].append(row)
    constrained_jobs = read_jsonl(CONSTRAINED / "JOBS.jsonl")
    for arm in ("r3e", "constrained"):
        base_jobs = ([{"job_id": row["job_id"], "benchmark": norm_benchmark(row["benchmark"]),
                       "seed": row["seed"], "case_id": row["case_id"]} for row in r3e_cases]
                     if arm == "r3e" else constrained_jobs)
        for base_job in base_jobs:
            job_id = base_job["job_id"]; recovery_id = f"{arm}|{job_id}"
            if recovery_id in recovered_rows:
                rows = recovered_rows[recovery_id]; source = "prefix-recovery"
            elif arm == "r3e":
                rows = sorted(r3e_by[job_id], key=lambda row: int(row["candidate_index"])); source = "original"
            else:
                path = CONSTRAINED / base_job["artifact_rel"] / "artifact/canonical_candidates.jsonl"
                rows = sorted([row for row in read_jsonl(path) if row.get("method") == "feedback_constrained"],
                              key=lambda row: int(row["candidate_index"])); source = "original"
            if any(is_api(row) for row in rows): errors.append(f"{arm}|{job_id}: canonical overlay retains API error")
            benchmark = norm_benchmark(base_job["benchmark"])
            case_row = {"arm": arm, "job_id": job_id, "benchmark": benchmark,
                        "seed": int(base_job["seed"]), "case_id": base_job["case_id"],
                        "artifact_source": source,
                        "pass_at_1": any(int(row["candidate_index"]) == 0 and row.get("oracle_ok") for row in rows),
                        "within_3": any(row.get("oracle_ok") for row in rows),
                        "candidate_rows": len(rows)}
            canonical_cases.append(case_row)
            for row in rows:
                copied = dict(row); copied["canonical_overlay_arm"] = arm
                copied["canonical_overlay_source"] = source; canonical_candidates.append(copied)
            source_ledger.append({"arm": arm, "job_id": job_id, "artifact_source": source})
    source_counts = Counter((row["arm"], row["artifact_source"]) for row in source_ledger)
    expected_counts = {("r3e", "original"): 349, ("r3e", "prefix-recovery"): 56,
                       ("constrained", "original"): 357, ("constrained", "prefix-recovery"): 48}
    if dict(source_counts) != expected_counts: errors.append(f"overlay source count mismatch {dict(source_counts)}")
    if len(canonical_cases) != 810 or len(replacement_ledger) != 104:
        errors.append("canonical/replacement cardinality mismatch")
    remaining_api = sum(is_api(row) for row in canonical_candidates)
    if remaining_api: errors.append(f"canonical overlay has {remaining_api} API rows")
    before = protocol["state_guard_hashes_before"]["strategy_registry"]["sha256"]
    after = sha256_file(STATE)
    if before != after: errors.append("strategy registry state guard changed")
    write_jsonl(root / "CANONICAL_CASES.jsonl", canonical_cases)
    write_jsonl(root / "CANONICAL_CANDIDATES.jsonl", canonical_candidates)
    write_jsonl(root / "CANONICAL_SOURCE_LEDGER.jsonl", source_ledger)
    write_jsonl(root / "REPLACEMENT_LEDGER.jsonl", replacement_ledger)
    if not errors:
        result = aggregate(canonical_cases)
        (root / "AGGREGATE.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    audit = {
        "schema": "r3e-table2-feedback-prefix-recovery-formal-audit-v1",
        "verdict": "PASS" if not errors else "FAIL", "protocol_sha256": sha256_file(args.protocol),
        "recovery_jobs": len(jobs), "canonical_case_rows": len(canonical_cases),
        "canonical_candidate_rows": len(canonical_candidates),
        "canonical_transport_api_failures": remaining_api,
        "source_counts": {f"{arm}:{source}": count for (arm, source), count in source_counts.items()},
        "replacement_ledger_rows": len(replacement_ledger),
        "state_guard_before": before, "state_guard_after": after, "errors": errors,
    }
    (root / "FORMAL_AUDIT.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    print(json.dumps(audit, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
