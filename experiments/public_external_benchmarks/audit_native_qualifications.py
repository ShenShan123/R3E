#!/usr/bin/env python3
"""Build immutable qualification summaries for native Table-2 baselines."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
QUAL = ROOT / ".cross_benchmark_r3e_202607/qualification"


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def audit_rtlfixer() -> dict:
    rows = []
    for path in sorted((QUAL / "rtlfixer_formal5").glob("*/result.json")):
        result = load(path)
        rows.append({
            "case_id": result["case_id"], "result_path": str(path),
            "pipeline_completed": result["pipeline_completed"],
            "patch_produced": result["patch_produced"],
            "frozen_evaluator_ok": bool((result.get("compile_evaluator") or {}).get("ok")),
            "patch_hash": result["patch_hash"],
            "token_usage": result.get("token_usage_from_native_transcript") or {},
        })
    retry_result_path = QUAL / "rtlfixer_retry1/rtlfixer_verilogeval-syntax_andgate_34/result.json"
    replay_path = retry_result_path.parent / "frozen_gate_replay_1/replay_result.json"
    retry, replay = load(retry_result_path), load(replay_path)
    rows.append({
        "case_id": retry["case_id"], "result_path": str(retry_result_path),
        "pipeline_completed": retry["pipeline_completed"],
        "patch_produced": retry["patch_produced"],
        "frozen_evaluator_ok": bool(replay["gate"].get("ok")),
        "frozen_evaluator_replay_path": str(replay_path),
        "patch_hash": replay["candidate_hash"],
        "token_usage": retry.get("token_usage_from_native_transcript") or {},
        "adapter_note": "candidate extraction fixed; no-API frozen-gate replay",
    })
    passed = sum(row["pipeline_completed"] and row["patch_produced"] and row["frozen_evaluator_ok"]
                 for row in rows)
    return {
        "schema": "r3e-table2-native-qualification-audit-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "method": "rtlfixer_native_workflow_deepseek_adapter",
        "expected_cases": 5, "unique_cases": len({row["case_id"] for row in rows}),
        "qualified_cases": passed,
        "verdict": "PASS" if len(rows) == 5 and passed == 5 else "FAIL",
        "repair_algorithm_modified": False,
        "rows": sorted(rows, key=lambda row: row["case_id"]),
    }


def main() -> int:
    result = audit_rtlfixer()
    out = QUAL / "RTLFixer_NATIVE_QUALIFICATION_AUDIT.json"
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    lines = ["# RTLFixer Native Qualification", "",
             f"- Verdict: `{result['verdict']}`",
             f"- Qualified: `{result['qualified_cases']}/5`",
             "- Frozen upstream repair algorithm modified: `false`",
             "- Adapter scope: dependency compatibility, model endpoint, frozen compile evaluator",
             "", "| Case | Pipeline | Patch | Frozen evaluator |", "|---|---|---|---|"]
    for row in result["rows"]:
        lines.append(f"| {row['case_id']} | {'PASS' if row['pipeline_completed'] else 'FAIL'} | "
                     f"{'YES' if row['patch_produced'] else 'NO'} | "
                     f"{'PASS' if row['frozen_evaluator_ok'] else 'FAIL'} |")
    (QUAL / "RTLFixer_NATIVE_QUALIFICATION_AUDIT.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({k: result[k] for k in ("verdict", "expected_cases", "qualified_cases")},
                     sort_keys=True))
    return 0 if result["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
