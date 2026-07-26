#!/usr/bin/env python3
"""Audit v4e deterministic replay from canonical per-case evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
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
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    cfg = json.loads(args.protocol.read_text())
    result_path = args.run_root / "result.json"
    canonical_path = args.run_root / "canonical.jsonl"
    result = json.loads(result_path.read_text())
    rows = [json.loads(line) for line in canonical_path.read_text().splitlines() if line.strip()]
    bad: list[str] = []

    if result.get("status") != "complete":
        bad.append("replay result is not complete")
    if result.get("model_calls") != 0 or result.get("model_call_attempts") != 0:
        bad.append("model call or attempted model call recorded")
    if result.get("network_calls") != 0:
        bad.append("network call recorded")
    if result.get("canonical_rows") != len(rows) or result.get("canonical_jsonl_sha256") != sha(canonical_path):
        bad.append("canonical JSONL count/hash mismatch")
    if any(row.get("row_payload_sha256") != hash_payload({k: v for k, v in row.items() if k != "row_payload_sha256"}) for row in rows):
        bad.append("canonical row payload hash mismatch")

    expected_counts = {
        ("target_G2", "M1"): len(cfg["target_G2_ids"]),
        ("target_G2", "M2"): len(cfg["target_G2_ids"]),
        ("non_target_G2", "M1"): len(cfg["non_target_G2_ids"]),
        ("non_target_G2", "M2"): len(cfg["non_target_G2_ids"]),
        ("final_heldout", "M2"): len(cfg["final_heldout_ids"]),
    }
    for key, expected in expected_counts.items():
        actual = sum((row["split"], row["arm"]) == key for row in rows)
        if actual != expected:
            bad.append(f"row count mismatch for {key}: {actual} != {expected}")

    by_key = {(row["split"], row["arm"], row["case_id"]): row for row in rows}
    target_m1 = [by_key[("target_G2", "M1", case_id)] for case_id in cfg["target_G2_ids"]]
    target_m2 = [by_key[("target_G2", "M2", case_id)] for case_id in cfg["target_G2_ids"]]
    non_m1 = [by_key[("non_target_G2", "M1", case_id)] for case_id in cfg["non_target_G2_ids"]]
    non_m2 = [by_key[("non_target_G2", "M2", case_id)] for case_id in cfg["non_target_G2_ids"]]
    heldout = [by_key[("final_heldout", "M2", case_id)] for case_id in cfg["final_heldout_ids"]]

    if any(row["result"]["repaired"] for row in target_m1):
        bad.append("M1 unexpectedly repairs a G2 target")
    if not all(row["result"]["repaired"] for row in target_m2):
        bad.append("M2 does not repair all frozen G2 targets")
    if [row["result"]["repaired"] for row in non_m1] != [row["result"]["repaired"] for row in non_m2]:
        bad.append("non-target per-case regression detected")
    if not all(row["result"]["repaired"] for row in heldout):
        bad.append("M2 does not repair all final held-out cases")

    for row in target_m2 + heldout:
        candidates = row["result"].get("candidates", [])
        winners = [candidate for candidate in candidates if candidate.get("formal_equiv")]
        if len(winners) != 1:
            bad.append(f"expected one winning candidate: {row['case_id']}")
            continue
        winner = winners[0]
        if winner.get("proof_method") != "exact_sha256" or winner.get("patch_hash") != row["golden_sha256"]:
            bad.append(f"winning candidate is not exact-identity certified: {row['case_id']}")
        patched = Path(winner.get("patched_rtl") or "")
        if not patched.is_file() or sha(patched) != winner.get("patch_hash"):
            bad.append(f"winning candidate artifact missing/hash mismatch: {row['case_id']}")
        proof = Path(winner.get("proof_artifact") or "")
        if not proof.is_file():
            bad.append(f"exact-identity proof artifact missing: {row['case_id']}")

    candidate_paths = [
        candidate.get("patched_rtl")
        for row in rows
        for candidate in row["result"].get("candidates", [])
        if candidate.get("patched_rtl")
    ]
    if len(candidate_paths) != len(set(candidate_paths)):
        bad.append("candidate artifact paths are not unique")
    if result.get("policy_hash_M1") != cfg["policy_hash_M1"] or result.get("policy_hash_M2") != cfg["policy_hash_M2"]:
        bad.append("replayed policy hashes differ from frozen source hashes")

    audit = {
        "schema": "r3e-continual-policy-deterministic-replay-audit-v1",
        "verdict": "PASS" if not bad else "FAIL",
        "bad": bad,
        "protocol_sha256": sha(args.protocol),
        "result_sha256": sha(result_path),
        "canonical_jsonl_sha256": sha(canonical_path),
        "canonical_rows": len(rows),
        "policy_hash_M1": result.get("policy_hash_M1"),
        "policy_hash_M2": result.get("policy_hash_M2"),
        "target_G2": result.get("target_G2"),
        "non_target_G2": result.get("non_target_G2"),
        "final_heldout": result.get("final_heldout"),
        "model_calls": result.get("model_calls"),
        "model_call_attempts": result.get("model_call_attempts"),
        "network_calls": result.get("network_calls"),
        "candidate_artifact_paths_unique": len(candidate_paths) == len(set(candidate_paths)),
    }
    audit["audit_payload_sha256"] = hash_payload(audit)
    atomic_write_json(args.out, audit)
    print(json.dumps(audit, indent=2))
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
