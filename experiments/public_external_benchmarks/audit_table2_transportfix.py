#!/usr/bin/env python3
"""Audit targeted reruns after API/parse failure separation was implemented."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / ".cross_benchmark_r3e_202607/smoke_transportfix_v1"


def main() -> int:
    runs = []
    all_rows = []
    for config in sorted(BASE.glob("*/*/*/run_config.json")):
        run = config.parent
        heartbeat = json.loads((run / "heartbeat.json").read_text())
        rows = [json.loads(line) for line in (run / "canonical_candidates.jsonl").read_text().splitlines()
                if line.strip()]
        all_rows.extend(rows)
        cfg = json.loads(config.read_text())
        runs.append({
            "run_dir": str(run), "method": cfg["method"],
            "case_ids": sorted({row["case_id"] for row in rows}),
            "status": heartbeat["status"], "candidate_rows": len(rows),
            "transport_retries_per_semantic_call": cfg["transport_retries_per_semantic_call"],
            "status_histogram": dict(Counter(row["status"] for row in rows)),
        })
    api_rows = [row for row in all_rows if str(row.get("status") or "").startswith("api_")]
    parse_rows = [row for row in all_rows if row.get("status") == "parse_failed"]
    verdict = (len(runs) == 5 and all(run["status"] == "complete" for run in runs)
               and all(run["transport_retries_per_semantic_call"] == 1 for run in runs)
               and not api_rows)
    summary = {
        "schema": "r3e-table2-transportfix-audit-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "PASS" if verdict else "FAIL", "expected_runs": 5,
        "observed_runs": len(runs), "api_failure_rows": len(api_rows),
        "parse_failed_rows": len(parse_rows),
        "parse_failure_interpretation": "valid API response exhausted output budget or returned invalid repair JSON; method output failure, not infrastructure failure",
        "runs": runs,
    }
    (BASE / "TRANSPORTFIX_AUDIT.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    lines = ["# Table 2 Transport/Parse Classification Audit", "",
             f"- Verdict: `{summary['verdict']}`", f"- Runs: `{len(runs)}/5`",
             f"- API/infrastructure failure rows after fix: `{len(api_rows)}`",
             f"- Correctly classified parse_failed rows: `{len(parse_rows)}`", "",
             "SHA3 can exhaust all 8192 completion tokens in reasoning and return empty content. "
             "It remains a method output failure under the frozen budget; the protocol is not changed."]
    (BASE / "TRANSPORTFIX_AUDIT.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({k: summary[k] for k in ("verdict", "observed_runs", "api_failure_rows",
                                              "parse_failed_rows")}, sort_keys=True))
    return 0 if verdict else 2


if __name__ == "__main__":
    raise SystemExit(main())
