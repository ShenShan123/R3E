#!/usr/bin/env python3
"""Summarize public external benchmark discovery/manifests/audits/plans."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> List[Dict]:
    if not path.is_file():
        return []
    rows: List[Dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def read_plan_rows(root: Path) -> List[Dict]:
    rows = read_jsonl(root / "planned_r3e_public_external_run.jsonl")
    for path in sorted(root.glob("plans/*/planned_r3e_public_external_run.jsonl")):
        rows.extend(read_jsonl(path))
    return rows


def discovery_map(root: Path) -> Dict[str, Dict]:
    payload = read_json(root / "public_benchmark_discovery.json")
    return {row["benchmark_name"]: row for row in payload.get("benchmarks", [])}


def collect_benchmark_names(root: Path, disc: Dict[str, Dict]) -> List[str]:
    names = set(disc)
    for path in (root / "manifests").glob("*_tasks.jsonl"):
        names.add(path.name[: -len("_tasks.jsonl")])
    for path in root.glob("audit_*.json"):
        names.add(path.name[len("audit_") : -len(".json")])
    return sorted(names)


def r3e_status(entry: Dict, task_count: int, eligible_count: int, planned_count: int, audit: Dict) -> str:
    status = entry.get("status", "unknown")
    if status == "paper_only_not_runnable":
        return "paper_only_not_runnable"
    if task_count == 0:
        return "metadata_only_no_tasks_extracted"
    if audit.get("audit_status") == "FAIL":
        return "manifest_audit_failed"
    if planned_count > 0:
        return "dry_run_plan_ready"
    if eligible_count == 0:
        return "manifest_ready_no_eligible_gate"
    return "manifest_ready"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    root = Path(args.root_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    disc = discovery_map(root)
    names = collect_benchmark_names(root, disc)
    all_plan_rows = read_plan_rows(root)
    rows: List[Dict] = []
    for name in names:
        entry = disc.get(name, {"benchmark_name": name, "display_name": name, "status": "unknown"})
        manifest_rows = read_jsonl(root / "manifests" / f"{name}_tasks.jsonl")
        audit = read_json(root / f"audit_{name}.json")
        planned_for_name = [row for row in all_plan_rows if row.get("benchmark") == name]
        gate_hist = Counter(row.get("gate_type", "unknown") for row in manifest_rows)
        rows.append({
            "Dataset": entry.get("display_name", name),
            "benchmark": name,
            "Task type": entry.get("expected_task_type", ""),
            "Gate": ", ".join(f"{k}:{v}" for k, v in sorted(gate_hist.items())) or entry.get("expected_gate", ""),
            "Public tasks": len(manifest_rows),
            "Eligible tasks": sum(1 for row in manifest_rows if row.get("eligible")),
            "Baseline source": entry.get("baseline_source", entry.get("source_url", "")),
            "R3E status": r3e_status(entry, len(manifest_rows), sum(1 for row in manifest_rows if row.get("eligible")), len(planned_for_name), audit),
            "Notes": f"discovery={entry.get('status', 'unknown')}; audit={audit.get('audit_status', 'not_run')}; commit={entry.get('commit_hash') or '-'}",
        })

    payload = {
        "schema_version": "public_external_benchmarks_summary.v1",
        "created_utc": utc_now(),
        "root_dir": str(root),
        "rows": rows,
        "redlines": [
            "no_llm_api_calls",
            "no_full_benchmark_execution",
            "no_memory_registry_template_or_promotion_write",
            "frozen_manifest_required_for_future_llm_swap_or_r3e_runs",
        ],
    }
    (out_dir / "public_external_benchmarks_summary.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Public External Testbench-Backed Evaluation Availability",
        "",
        "This is an availability and dry-run planning report, not a repair result.  No LLM/API calls or full benchmark runs are performed.",
        "",
        "| Dataset | Task type | Gate | Public tasks | Eligible tasks | Baseline source | R3E status | Notes |",
        "|---|---|---|---:|---:|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['Dataset']} | {row['Task type']} | {row['Gate']} | {row['Public tasks']} | "
            f"{row['Eligible tasks']} | {row['Baseline source']} | {row['R3E status']} | {row['Notes']} |"
        )
    (out_dir / "public_external_benchmarks_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"datasets": len(rows), "out_dir": str(out_dir)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
