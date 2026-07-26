#!/usr/bin/env python3
"""Fail-closed audit for the v4 two-family, two-transition experiment."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "r3e"))

from semantic_repair_bench.formal_protocol import (  # noqa: E402
    candidate_hash,
    hash_payload,
    load_registry,
    policy_hash,
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def audit_chain(rows: list[dict], bad: list[str]) -> None:
    previous = "0" * 64
    for index, row in enumerate(rows, 1):
        if row.get("sequence") != index:
            bad.append(f"ledger sequence mismatch at {index}")
        if row.get("previous_ledger_entry_hash") != previous:
            bad.append(f"ledger parent mismatch at {index}")
        raw = {key: value for key, value in row.items() if key != "ledger_entry_hash"}
        if row.get("ledger_entry_hash") != hash_payload(raw):
            bad.append(f"ledger hash mismatch at {index}")
        previous = row.get("ledger_entry_hash", "")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / ".formal_r3e/protocols/continual_policy_multigen_v4_seed101.json",
    )
    parser.add_argument(
        "--run-root",
        type=Path,
        default=ROOT / ".formal_r3e/runs/batches/continual_policy_multigen_20260721_v4_seed101",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / ".formal_r3e/launch_logs/continual_policy_multigen_20260721_v4_seed101/final_audit.json",
    )
    parser.add_argument("--seed", type=int, default=101)
    args = parser.parse_args()
    bad: list[str] = []

    cfg = json.loads(args.protocol.read_text())
    raw_cfg = dict(cfg)
    recorded_protocol_payload = raw_cfg.pop("protocol_payload_sha256", None)
    if recorded_protocol_payload != hash_payload(raw_cfg):
        bad.append("protocol payload hash mismatch")
    for relative, expected in cfg.get("code_sha256", {}).items():
        path = ROOT / relative
        if not path.is_file() or sha(path) != expected:
            bad.append(f"frozen code mismatch: {relative}")

    manifest_path = ROOT / cfg["manifest"]
    if sha(manifest_path) != cfg.get("manifest_sha256"):
        bad.append("manifest file hash mismatch")
    manifest = json.loads(manifest_path.read_text())
    raw_manifest = dict(manifest)
    recorded_manifest_payload = raw_manifest.pop("manifest_payload_sha256", None)
    if recorded_manifest_payload != hash_payload(raw_manifest):
        bad.append("manifest payload hash mismatch")

    discovery = manifest.get("discovery", [])
    replay = manifest.get("replay_universe", [])
    heldout = manifest.get("final_heldout", [])
    if {row.get("family") for row in discovery} != {"off_by_one", "constant_error"}:
        bad.append("discovery does not contain exactly two required families")
    if len([row for row in discovery if row.get("generation") == 1]) != 4:
        bad.append("G1 discovery count is not four")
    if len([row for row in discovery if row.get("generation") == 2]) != 4:
        bad.append("G2 discovery count is not four")
    if len(heldout) != 4 or len({row.get("design") for row in heldout}) != 4:
        bad.append("held-out set is not four design-distinct cases")
    prior_designs = {row.get("design") for row in discovery + replay}
    prior_golden = {row.get("golden_sha256") for row in discovery + replay}
    if prior_designs & {row.get("design") for row in heldout}:
        bad.append("held-out design overlap")
    if prior_golden & {row.get("golden_sha256") for row in heldout}:
        bad.append("held-out golden-SHA overlap")

    if args.seed not in cfg.get("seeds", []):
        bad.append(f"seed {args.seed} is not frozen in protocol")
    out = args.run_root / "Auto-Promote" / f"seed_{args.seed}"
    required = [
        out / "result.json",
        out / "run_manifest.json",
        out / "formal_registry.json",
        out / "final_heldout_activation.json",
        out / "work/a1/candidate_strategies.jsonl",
        out / "work/a1/decision_ledger.jsonl",
        out / "work/a1/runtime_policy_uses.jsonl",
    ]
    for path in required:
        if not path.is_file():
            bad.append(f"missing artifact: {path.relative_to(ROOT)}")
    if bad:
        payload = {"verdict": "FAIL", "bad": bad}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2) + "\n")
        print(json.dumps(payload, indent=2))
        raise SystemExit(1)

    result = json.loads((out / "result.json").read_text())
    run_manifest = json.loads((out / "run_manifest.json").read_text())
    if run_manifest.get("result_sha256") != sha(out / "result.json"):
        bad.append("run manifest/result hash mismatch")
    if run_manifest.get("final_heldout_activation_sha256") != sha(out / "final_heldout_activation.json"):
        bad.append("run manifest/held-out hash mismatch")
    opportunities = result.get("opportunities", [])
    if result.get("policy_transition_count") != 2 or len(opportunities) != 2:
        bad.append("did not complete exactly two policy transitions")
    else:
        g1, g2 = opportunities
        if g1.get("selected_family") != "off_by_one" or g2.get("selected_family") != "constant_error":
            bad.append("promotion family order mismatch")
        if g1.get("fresh_red_failures") != 4 or g2.get("fresh_red_failures") != 4:
            bad.append("a transition was not triggered by four current-policy residual failures")
        if not g1.get("promoted") or not g2.get("promoted"):
            bad.append("a required promotion did not occur")
        if g2.get("parent_policy_hash") != g1.get("policy_hash_after"):
            bad.append("G2 residual did not attack M1")
        if result.get("policy_hash_M0") != g1.get("parent_policy_hash"):
            bad.append("G1 did not attack M0")
        if result.get("policy_hash_final") != g2.get("policy_hash_after"):
            bad.append("final policy is not M2")
        if len({result.get("policy_hash_M0"), g1.get("policy_hash_after"), g2.get("policy_hash_after")}) != 3:
            bad.append("M0/M1/M2 policy hashes are not distinct")
        for label, opportunity in (("G1", g1), ("G2", g2)):
            metrics = opportunity.get("metrics", {})
            checks = opportunity.get("checks", {})
            if metrics.get("validation_hits") != 4 or metrics.get("covered_designs") != 4:
                bad.append(f"{label} target replay did not recover 4/4 designs")
            if metrics.get("target_recovery_ratio") != 1.0 or metrics.get("target_gain") != 1.0:
                bad.append(f"{label} target replay gain mismatch")
            if metrics.get("non_target_delta") != 0.0:
                bad.append(f"{label} non-target regression detected")
            if not opportunity.get("eligible") or not all(checks.values()):
                bad.append(f"{label} correctness gate did not fully pass")

    registry = load_registry(out / "formal_registry.json", formal_mode=True)
    active = [row for row in registry["artifacts"] if row.get("runtime_status") == "promoted"]
    if len(active) != 2:
        bad.append("active registry does not contain two promoted strategies")
    if policy_hash(registry) != result.get("policy_hash_final"):
        bad.append("registry policy hash does not equal result M2 hash")
    if {row.get("trigger", {}).get("bug_type") for row in active} != {"off_by_one", "constant_error"}:
        bad.append("active strategies do not cover both evolved families")

    candidates = read_jsonl(out / "work/a1/candidate_strategies.jsonl")
    if len(candidates) != 2:
        bad.append("candidate strategy count is not two")
    for candidate in candidates:
        if candidate.get("candidate_hash") != candidate_hash(candidate):
            bad.append("candidate hash mismatch")
        if candidate.get("origin") != "automatic" or not candidate.get("source_trajectory_ids"):
            bad.append("candidate lacks automatic trajectory provenance")

    ledger = read_jsonl(out / "work/a1/decision_ledger.jsonl")
    audit_chain(ledger, bad)
    if len([row for row in ledger if row.get("event") == "promoted"]) != 2:
        bad.append("ledger does not contain two promotion events")

    heldout_evidence = json.loads((out / "final_heldout_activation.json").read_text())
    heldout_payload = dict(heldout_evidence)
    heldout_hash = heldout_payload.pop("evidence_payload_sha256", None)
    if heldout_hash != hash_payload(heldout_payload):
        bad.append("held-out evidence payload hash mismatch")
    if heldout_evidence.get("policy_hash") != result.get("policy_hash_final"):
        bad.append("held-out cases did not use M2")
    if heldout_evidence.get("repaired") != 4 or heldout_evidence.get("case_count") != 4:
        bad.append("held-out M2 repair is not 4/4")
    constant_ids = {
        row["artifact_id"] for row in active
        if row.get("trigger", {}).get("bug_type") == "constant_error"
    }
    for row in heldout_evidence.get("rows", []):
        if row.get("policy_hash") != result.get("policy_hash_final"):
            bad.append("held-out row policy hash mismatch")
        if not constant_ids <= set(row.get("activated_artifact_ids", [])):
            bad.append("held-out row did not invoke the M2 constant strategy")
        if not row.get("result", {}).get("used_accumulated_template"):
            bad.append("held-out row lacks an active strategy hit")

    runtime_uses = read_jsonl(out / "work/a1/runtime_policy_uses.jsonl")
    heldout_uses = [row for row in runtime_uses if str(row.get("use_id", "")).startswith("fresh-red:G3-heldout:")]
    if len(heldout_uses) != 4:
        bad.append("runtime ledger does not contain four held-out strategy uses")
    for row in heldout_uses:
        if row.get("policy_hash") != result.get("policy_hash_final"):
            bad.append("runtime held-out use did not bind M2 policy hash")
        if not constant_ids <= set(row.get("activated_artifact_ids", [])):
            bad.append("runtime held-out use omitted the M2 strategy ID")

    payload = {
        "schema": "r3e-continual-policy-multigen-v4-final-audit-v1",
        "verdict": "PASS" if not bad else "FAIL",
        "bad": bad,
        "families": ["off_by_one", "constant_error"],
        "seed": args.seed,
        "policy_hash_M0": result.get("policy_hash_M0"),
        "policy_hash_M1": opportunities[0].get("policy_hash_after") if len(opportunities) == 2 else None,
        "policy_hash_M2": result.get("policy_hash_final"),
        "transitions": result.get("policy_transition_count"),
        "heldout_repaired": heldout_evidence.get("repaired"),
        "heldout_total": heldout_evidence.get("case_count"),
        "heldout_designs": heldout_evidence.get("design_count"),
        "candidate_count": len(candidates),
        "promotion_events": len([row for row in ledger if row.get("event") == "promoted"]),
        "heldout_runtime_uses": len(heldout_uses),
        "protocol_sha256": sha(args.protocol),
        "manifest_sha256": sha(manifest_path),
    }
    payload["audit_payload_sha256"] = hash_payload(payload)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
