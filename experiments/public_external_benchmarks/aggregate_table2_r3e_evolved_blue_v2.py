#!/usr/bin/env python3
"""Rebuild Evolved-Blue V2 Table-2 aggregates from isolated canonical JSONL."""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from table2_common import read_jsonl, sha256_file


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-dir", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    control = args.control_dir.resolve()
    artifact_root = args.artifact_root.resolve()
    jobs_path = control / "FORMAL_JOBS.jsonl"
    jobs = read_jsonl(jobs_path)

    case_results: list[dict[str, Any]] = []
    merged_candidates: list[dict[str, Any]] = []
    incomplete: list[dict[str, str]] = []
    for job in jobs:
        output = artifact_root / job["artifact_rel"]
        meta_path = output / "SUPERVISOR.json"
        canonical_path = output / "artifact/canonical_candidates.jsonl"
        config_path = output / "artifact/run_config.json"
        if not (meta_path.is_file() and canonical_path.is_file() and config_path.is_file()):
            incomplete.append({"job_id": job["job_id"], "reason": "missing_artifact"})
            continue
        meta = json.loads(meta_path.read_text())
        if meta.get("status") != "complete" or meta.get("return_code") != 0:
            incomplete.append({"job_id": job["job_id"], "reason": "supervisor_failed"})
            continue
        config = json.loads(config_path.read_text())
        rows = read_jsonl(canonical_path)
        require(config.get("manifest_hash") == job["manifest_sha256"],
                f"manifest mismatch: {job['job_id']}")
        require(config.get("seed") == job["seed"] and config.get("selected_case_count") == 1,
                f"run identity mismatch: {job['job_id']}")
        require(1 <= len(rows) <= 3, f"candidate budget mismatch: {job['job_id']}")
        indices = [int(row.get("candidate_index", -1)) for row in rows]
        require(len(indices) == len(set(indices)), f"duplicate candidates: {job['job_id']}")
        require(all(row.get("case_id") == job["case_id"] for row in rows),
                f"case mismatch: {job['job_id']}")
        require(all(row.get("case_sha256") == job["case_sha256"] for row in rows),
                f"case hash mismatch: {job['job_id']}")
        require(all(row.get("manifest_hash") == job["manifest_sha256"] for row in rows),
                f"candidate manifest mismatch: {job['job_id']}")
        require(all(row.get("seed") == job["seed"] for row in rows),
                f"candidate seed mismatch: {job['job_id']}")
        passed = any(bool(row.get("oracle_ok")) for row in rows
                     if int(row.get("candidate_index", -1)) >= 0)
        case_results.append({
            "schema": "r3e-table2-evolved-blue-case-result-v2",
            "job_id": job["job_id"], "job_key_sha256": job["job_key_sha256"],
            "benchmark": job["benchmark"], "seed": job["seed"],
            "case_id": job["case_id"], "case_sha256": job["case_sha256"],
            "manifest_sha256": job["manifest_sha256"], "repaired": passed,
            "candidate_rows": len(rows), "canonical_sha256": sha256_file(canonical_path),
            "supervisor_sha256": sha256_file(meta_path),
        })
        for row in rows:
            merged_candidates.append({**row, "job_id": job["job_id"],
                                      "job_key_sha256": job["job_key_sha256"]})

    if incomplete and not args.allow_incomplete:
        raise SystemExit(f"formal aggregate blocked: {len(incomplete)} incomplete jobs")

    by_cell: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in case_results:
        by_cell[(row["benchmark"], row["seed"])].append(row)
    per_seed: list[dict[str, Any]] = []
    for (benchmark, seed), rows in sorted(by_cell.items()):
        repaired = sum(bool(row["repaired"]) for row in rows)
        per_seed.append({
            "benchmark": benchmark, "seed": seed, "completed_cases": len(rows),
            "repaired": repaired,
            "repair_rate_percent": 100.0 * repaired / len(rows),
        })
    aggregate: dict[str, Any] = {}
    for benchmark in sorted({job["benchmark"] for job in jobs}):
        cells = [row for row in per_seed if row["benchmark"] == benchmark]
        rates = [row["repair_rate_percent"] for row in cells]
        aggregate[benchmark] = {
            "seed_results": cells,
            "mean_repair_rate_percent": statistics.mean(rates) if rates else None,
            "sample_sd_percent": statistics.stdev(rates) if len(rates) >= 2 else None,
        }
    summary = {
        "schema": "r3e-table2-evolved-blue-aggregate-v2",
        "formal_jobs_sha256": sha256_file(jobs_path),
        "expected_jobs": len(jobs), "completed_jobs": len(case_results),
        "incomplete_jobs": incomplete, "aggregates": aggregate,
        "historical_artifacts_overwritten": False,
    }

    if args.out_dir:
        out = args.out_dir.resolve()
        out.mkdir(parents=True, exist_ok=True)
        (out / "canonical_case_results.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in case_results)
        )
        (out / "canonical_candidates_merged.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                    for row in merged_candidates)
        )
        (out / "AGGREGATE.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n"
        )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
