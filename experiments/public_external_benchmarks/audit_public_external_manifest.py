#!/usr/bin/env python3
"""Audit a public external benchmark manifest."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List


REQUIRED_FIELDS = {
    "benchmark",
    "task_id",
    "task_type",
    "gate_type",
    "buggy_path",
    "reference_path",
    "testbench_path",
    "runner_command",
    "repo_root",
    "metadata",
    "eligible",
    "reject_reason",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_jsonl(path: Path) -> List[Dict]:
    rows: List[Dict] = []
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{lineno}: invalid json: {exc}") from exc
    return rows


def benchmark_from_manifest(path: Path, rows: List[Dict]) -> str:
    if rows:
        return str(rows[0].get("benchmark") or "unknown")
    name = path.name
    return name[:-len("_tasks.jsonl")] if name.endswith("_tasks.jsonl") else path.stem


def exists_if_path(value: str | None) -> bool:
    return bool(value and Path(value).exists())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    manifest = Path(args.manifest).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = read_jsonl(manifest)
    benchmark = benchmark_from_manifest(manifest, rows)

    task_ids = [row.get("task_id") for row in rows]
    duplicate_ids = sorted([task_id for task_id, count in Counter(task_ids).items() if task_id and count > 1])
    missing_fields = {
        str(row.get("task_id", f"row_{idx}")): sorted(REQUIRED_FIELDS - set(row))
        for idx, row in enumerate(rows)
        if REQUIRED_FIELDS - set(row)
    }
    gate_hist = Counter(row.get("gate_type", "unknown") for row in rows)
    reject_hist = Counter((row.get("reject_reason") or "") for row in rows if not row.get("eligible"))
    eligible_count = sum(1 for row in rows if row.get("eligible") is True)
    runner_available_count = sum(1 for row in rows if row.get("runner_command"))
    testbench_available_count = sum(1 for row in rows if exists_if_path(row.get("testbench_path")))
    reference_available_count = sum(1 for row in rows if exists_if_path(row.get("reference_path")))
    buggy_path_count = sum(1 for row in rows if exists_if_path(row.get("buggy_path")))

    failures: List[str] = []
    if duplicate_ids:
        failures.append("duplicate_task_id")
    if missing_fields:
        failures.append("missing_required_fields")
    if any(row.get("eligible") and not (row.get("runner_command") or row.get("testbench_path") or row.get("gate_type") == "compile") for row in rows):
        failures.append("eligible_without_gate_evidence")
    audit_status = "FAIL" if failures else "PASS"
    payload = {
        "schema_version": "public_external_manifest_audit.v1",
        "created_utc": utc_now(),
        "audit_status": audit_status,
        "benchmark": benchmark,
        "manifest": str(manifest),
        "task_count": len(rows),
        "eligible_count": eligible_count,
        "rejected_count": len(rows) - eligible_count,
        "gate_histogram": dict(sorted(gate_hist.items())),
        "reject_reason_histogram": dict(sorted(reject_hist.items())),
        "runner_available_count": runner_available_count,
        "testbench_available_count": testbench_available_count,
        "reference_available_count": reference_available_count,
        "buggy_path_count": buggy_path_count,
        "duplicate_task_ids": duplicate_ids,
        "missing_fields": missing_fields,
        "failures": failures,
        "memory_write_policy": "disabled",
    }
    json_path = out_dir / f"audit_{benchmark}.json"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        f"# Audit: {benchmark}",
        "",
        f"- audit_status: `{audit_status}`",
        f"- task_count: `{len(rows)}`",
        f"- eligible_count: `{eligible_count}`",
        f"- runner_available_count: `{runner_available_count}`",
        f"- testbench_available_count: `{testbench_available_count}`",
        f"- reference_available_count: `{reference_available_count}`",
        "",
        "## Gate Histogram",
        "",
        "| gate_type | count |",
        "|---|---:|",
    ]
    for gate, count in sorted(gate_hist.items()):
        lines.append(f"| {gate} | {count} |")
    lines.extend(["", "## Reject Reason Histogram", "", "| reject_reason | count |", "|---|---:|"])
    for reason, count in sorted(reject_hist.items()):
        lines.append(f"| {reason or '-'} | {count} |")
    if failures:
        lines.extend(["", "## Failures", ""])
        lines.extend(f"- {item}" for item in failures)
    (out_dir / f"audit_{benchmark}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"benchmark": benchmark, "audit_status": audit_status, "task_count": len(rows), "eligible_count": eligible_count}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
