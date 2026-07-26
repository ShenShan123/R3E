#!/usr/bin/env python3
"""Audit one Direct and one R3E Kimi smoke against the frozen protocol."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from table2_common import read_jsonl, sha256_file


def audit_artifact(path: Path, expected_model: str, direct: bool) -> dict:
    heartbeat = json.loads((path / "heartbeat.json").read_text())
    rows = read_jsonl(path / "canonical_candidates.jsonl")
    candidates = [row for row in rows if int(row.get("candidate_index", -1)) >= 0]
    if heartbeat.get("status") != "complete" or heartbeat.get("completed_cases") != 1:
        raise RuntimeError(f"incomplete smoke heartbeat: {path}")
    if not candidates or any(row.get("model") != expected_model for row in candidates):
        raise RuntimeError(f"missing candidates or model mismatch: {path}")
    if any(str(row.get("status", "")).startswith("api_") for row in candidates):
        raise RuntimeError(f"model transport failed in smoke: {path}")
    if direct and (len(candidates) != 1 or candidates[0].get("candidate_index") != 0):
        raise RuntimeError("Direct smoke did not perform exactly one model call")
    if not direct and len(candidates) > 3:
        raise RuntimeError("R3E smoke exceeded three verified revisions")
    if not any(row.get("status") in {"pass", "functional_failure"} for row in candidates):
        raise RuntimeError(f"smoke did not reach the frozen evaluator: {path}")
    return {
        "artifact": str(path),
        "canonical_sha256": sha256_file(path / "canonical_candidates.jsonl"),
        "candidate_rows": len(candidates),
        "statuses": [row.get("status") for row in candidates],
        "oracle_pass": any(row.get("oracle_ok") for row in candidates),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--direct", type=Path, required=True)
    parser.add_argument("--r3e", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    protocol = json.loads(args.protocol.read_text())
    model = protocol["model"]["model"]
    payload = {
        "schema": "r3e-table2-kimi3-smoke-audit-v1",
        "verdict": "PASS",
        "protocol": str(args.protocol),
        "protocol_sha256": sha256_file(args.protocol),
        "model": model,
        "direct": audit_artifact(args.direct, model, True),
        "r3e": audit_artifact(args.r3e, model, False),
        "network_or_model_calls": "performed by smoke runners; audit made none",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        raise SystemExit(f"refusing to overwrite {args.out}")
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
