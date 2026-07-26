#!/usr/bin/env python3
"""Run per-seed v4 chain audits and aggregate the frozen v5 replications."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "r3e"))

from semantic_repair_bench.formal_protocol import atomic_write_json, hash_payload  # noqa: E402


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--log-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    cfg = json.loads(args.protocol.read_text())
    bad: list[str] = []
    audits = []
    for seed in cfg["seeds"]:
        audit_path = args.log_root / f"seed_{seed}.audit.json"
        command = [
            sys.executable,
            str(ROOT / "experiments/audit_continual_policy_multigen_v4.py"),
            "--protocol", str(args.protocol),
            "--run-root", str(args.run_root),
            "--out", str(audit_path),
            "--seed", str(seed),
        ]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        if not audit_path.is_file():
            bad.append(f"seed {seed} audit artifact missing")
            continue
        audit = json.loads(audit_path.read_text())
        audits.append(audit)
        if completed.returncode != 0 or audit.get("verdict") != "PASS":
            bad.append(f"seed {seed} audit failed")
        cache_root = args.log_root / "common_response_cache" / f"seed_{seed}"
        cache_files = sorted(cache_root.glob("*.json"))
        if len(cache_files) != 2:
            bad.append(f"seed {seed} response-cache count is {len(cache_files)}, expected 2")
        for path in cache_files:
            row = json.loads(path.read_text())
            if row.get("response_hash") != hash_payload(row.get("response")):
                bad.append(f"seed {seed} response-cache hash mismatch: {path.name}")
            request = row.get("request", {})
            if request.get("seed") != str(seed):
                bad.append(f"seed {seed} response-cache seed mismatch: {path.name}")
            if request.get("model") != cfg["model_name"]:
                bad.append(f"seed {seed} response-cache model mismatch: {path.name}")

    if len(audits) != len(cfg["seeds"]):
        bad.append("not all frozen seeds produced audits")
    if any(audit.get("transitions") != 2 for audit in audits):
        bad.append("a seed lacks two policy transitions")
    if any(audit.get("heldout_repaired") != 4 or audit.get("heldout_total") != 4 for audit in audits):
        bad.append("a seed lacks 4/4 held-out repair")
    payload = {
        "schema": "r3e-continual-policy-multigen-v5-multiseed-audit-v1",
        "verdict": "PASS" if not bad else "FAIL",
        "bad": bad,
        "seeds": cfg["seeds"],
        "completed_seed_audits": len(audits),
        "passing_seed_audits": sum(audit.get("verdict") == "PASS" for audit in audits),
        "transitions_per_seed": {str(audit.get("seed")): audit.get("transitions") for audit in audits},
        "heldout_per_seed": {
            str(audit.get("seed")): [audit.get("heldout_repaired"), audit.get("heldout_total")]
            for audit in audits
        },
        "policy_hashes": {
            str(audit.get("seed")): {
                "M0": audit.get("policy_hash_M0"),
                "M1": audit.get("policy_hash_M1"),
                "M2": audit.get("policy_hash_M2"),
            }
            for audit in audits
        },
        "per_seed_audit_sha256": {
            str(audit.get("seed")): sha(args.log_root / f"seed_{audit.get('seed')}.audit.json")
            for audit in audits
        },
        "protocol_sha256": sha(args.protocol),
        "model_name": cfg["model_name"],
        "model_calls_expected": 2 * len(cfg["seeds"]),
        "llm_patch_generation_allowed": False,
    }
    payload["audit_payload_sha256"] = hash_payload(payload)
    atomic_write_json(args.out, payload)
    print(json.dumps(payload, indent=2))
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
