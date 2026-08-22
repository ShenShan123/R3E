#!/usr/bin/env python3
"""Freeze an explicitly authorized live raw-run summary into evidence files."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from competition.services.common import git_provenance, payload_hash


ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "competition" / "results" / "frozen"


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="raw summary.json produced by run-demo --mode live")
    args = parser.parse_args()
    source = Path(args.input).resolve()
    raw = json.loads(source.read_text(encoding="utf-8"))
    if raw.get("schema_version") != "r3e-aic-raw-run-summary-v1":
        raise SystemExit("input is not an R3E-AIC raw run summary")
    if raw.get("mode") != "live":
        raise SystemExit("refusing to headline guided-demo or deterministic-fixture results")
    if len(raw.get("runs", [])) != 3:
        raise SystemExit("expected all three competition cases before freezing")
    if any(not row.get("accepted_candidate_ids") for row in raw["runs"]):
        raise SystemExit("every case needs at least one accepted candidate")
    metrics = {
        "schema_version": "r3e-aic-frozen-metrics-v1",
        "status": "frozen_live_run",
        "source_summary_hash": payload_hash(raw),
        "headline_metrics": {
            row["case_id"]: {
                "accepted_candidates": len(row["accepted_candidate_ids"]),
                "candidate_budget": 3,
            }
            for row in raw["runs"]
        },
        "claim_boundary": "These are case-run counts, not benchmark generalization claims.",
    }
    manifest = {
        "schema_version": "r3e-aic-frozen-manifest-v1",
        "status": "verified_run_replay",
        "git": git_provenance(ROOT),
        "source_summary_hash": payload_hash(raw),
        "cases": [row["case_id"] for row in raw["runs"]],
        "evolution_events": [],
    }
    toolchain = {
        "schema_version": "r3e-aic-toolchain-v1",
        "source_summary_hash": payload_hash(raw),
        "per_case": {
            row["case_id"]: row["candidate_results"][0]["toolchain"]
            for row in raw["runs"]
        },
    }
    TARGET.mkdir(parents=True, exist_ok=True)
    write_json(TARGET / "metrics.json", metrics)
    write_json(TARGET / "manifest.json", manifest)
    write_json(TARGET / "toolchain.json", toolchain)
    print(f"frozen evidence written to {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
