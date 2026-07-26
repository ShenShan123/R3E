#!/usr/bin/env python3
"""Fail-closed audit for the 12 controlled-baseline Table-2 smokes."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / ".cross_benchmark_r3e_202607/smoke"
METHODS = ("direct_llm", "llm_tool_feedback", "r3e_single")
BENCHMARKS = ("cirfix39", "literature32", "strider14", "rtlfixer50")


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main() -> int:
    runs = []
    failures = []
    for method in METHODS:
        for benchmark in BENCHMARKS:
            run = SMOKE / method / benchmark / "seed_101_3cases"
            heartbeat_path = run / "heartbeat.json"
            canonical = run / "canonical_candidates.jsonl"
            reasons = []
            heartbeat = json.loads(heartbeat_path.read_text()) if heartbeat_path.is_file() else {}
            candidates = rows(canonical) if canonical.is_file() else []
            cases = {row.get("case_id") for row in candidates}
            evaluated = {row.get("case_id") for row in candidates if row.get("patch_hash") and row.get("gate")}
            if heartbeat.get("status") != "complete":
                reasons.append("heartbeat_not_complete")
            if heartbeat.get("total_cases") != 3 or heartbeat.get("completed_cases") != 3:
                reasons.append("not_3_of_3")
            if len(cases) != 3:
                reasons.append(f"canonical_unique_cases={len(cases)}")
            if not evaluated:
                reasons.append("no_candidate_reached_frozen_evaluator")
            api_failures = sum(str(row.get("status") or "").startswith("api_") or
                               row.get("status") == "api_failure" for row in candidates)
            parse_failures = sum(row.get("status") in {"parse_failed", "patch_parse_failure"}
                                 for row in candidates)
            transport_only = bool(candidates) and api_failures == len(candidates)
            if transport_only:
                reasons.append("transport_only_run")
            result = {
                "method": method, "benchmark": benchmark, "run_dir": str(run),
                "candidate_rows": len(candidates), "unique_cases": len(cases),
                "evaluated_cases": len(evaluated), "api_failures": api_failures,
                "parse_failures": parse_failures,
                "status_histogram": dict(Counter(row.get("status") for row in candidates)),
                "audit_ok": not reasons, "failure_reasons": reasons,
            }
            runs.append(result)
            if reasons:
                failures.append(result)
    summary = {
        "schema": "r3e-table2-smoke-audit-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expected_runs": 12, "audited_runs": len(runs),
        "passed_runs": sum(row["audit_ok"] for row in runs),
        "verdict": "PASS" if not failures and len(runs) == 12 else "FAIL",
        "runs": runs,
    }
    (SMOKE / "SMOKE_AUDIT.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    lines = ["# Table 2 Smoke Audit", "", f"- Verdict: `{summary['verdict']}`",
             f"- Passed runs: `{summary['passed_runs']}/12`", "",
             "| Method | Benchmark | Cases | Evaluated | API failures | Verdict |",
             "|---|---|---:|---:|---:|---|"]
    for row in runs:
        lines.append(f"| {row['method']} | {row['benchmark']} | {row['unique_cases']} | "
                     f"{row['evaluated_cases']} | {row['api_failures']} | "
                     f"{'PASS' if row['audit_ok'] else 'FAIL'} |")
    (SMOKE / "SMOKE_AUDIT.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({k: summary[k] for k in ("verdict", "audited_runs", "passed_runs")}, sort_keys=True))
    return 0 if summary["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
