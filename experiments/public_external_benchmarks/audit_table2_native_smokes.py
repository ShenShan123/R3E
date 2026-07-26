#!/usr/bin/env python3
"""Fail closed unless each native APR adapter completed a three-case smoke."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--expected-runs", type=int, default=4)
    args = parser.parse_args()
    reports = []
    bad = []
    for canonical in sorted(args.root.glob("*/*/canonical_results.jsonl")):
        rows = [json.loads(line) for line in canonical.read_text().splitlines() if line.strip()]
        reasons = []
        if len(rows) != 3:
            reasons.append(f"rows={len(rows)}")
        run_candidate_count = 0
        for row in rows:
            cid = row.get("case_id")
            native = row.get("native") or {}
            candidates = row.get("candidates") or []
            if row.get("method") == "strider":
                pipeline_bad = (
                    native.get("timed_out") or native.get("native_error") or
                    not native.get("native_completed")
                )
            else:
                pipeline_bad = (
                    native.get("timed_out") or native.get("result_parse_error") or
                    not native.get("result_toml")
                )
            if pipeline_bad:
                reasons.append(f"{cid}:native_pipeline_incomplete")
            # A native APR method may legitimately return no repair for an
            # individual case.  Qualification requires at least one real patch
            # across the three difficulty probes, while every case must still
            # complete its native result contract.
            run_candidate_count += len(candidates)
            for candidate in candidates:
                gate = candidate.get("gate") or {}
                if gate.get("stage") not in {"compare", "validation"}:
                    reasons.append(f"{cid}:evaluator_not_completed:{gate.get('stage')}")
        if run_candidate_count == 0:
            reasons.append("three_case_smoke:no_patch_produced")
        record = {"run": str(canonical.parent), "rows": len(rows), "reasons": reasons}
        reports.append(record)
        if reasons:
            bad.append(record)
    if len(reports) != args.expected_runs:
        bad.append({"run": str(args.root), "reasons": [f"runs={len(reports)} expected={args.expected_runs}"]})
    summary = {
        "schema": "r3e-table2-native-smoke-audit-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "PASS" if not bad else "FAIL",
        "runs": reports,
        "bad": bad,
    }
    (args.root / "SMOKE_AUDIT.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0 if not bad else 2


if __name__ == "__main__":
    raise SystemExit(main())
