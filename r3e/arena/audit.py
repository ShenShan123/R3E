"""Offline reconstruction of a completed whole-policy evolution round."""
from __future__ import annotations

import argparse
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from r3e.arena.manifests import verify_manifest
from r3e.policy.promotion import (
    decide_policy_promotion,
    select_single_promotable_child,
)
from r3e.policy.registry_v2 import get_active_policy, validate_registry
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import (
    atomic_write_json,
    canonical_json,
    hash_payload,
    read_json,
    utc_now,
)
from r3e.protocol.ledger import read_ledger, writer_lock
from r3e.protocol.provenance import verify_run_context
from r3e.red.feedback_packet import FORBIDDEN_INPUT_KEYS
from r3e.red.feedback_packet import verify_red_search_context
from r3e.red.learnability import verify_learnability_result


ROUND_AUDIT_SCHEMA_VERSION = "r3e-round-audit-v1"


class RoundAuditViolation(RuntimeError):
    """Raised when a completed round cannot be reconstructed exactly."""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise RoundAuditViolation(f"required round artifact is missing: {path.name}")
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _manifest(path: Path) -> dict[str, Any]:
    try:
        return verify_manifest(read_json(path))
    except Exception as exc:
        raise RoundAuditViolation(f"invalid manifest {path.name}: {exc}") from exc


def reconstruct_round(round_dir: str | Path) -> dict[str, Any]:
    root = Path(round_dir)
    context = verify_run_context(read_json(root / "toolchain.json"))
    config = read_json(root / "round_config.json")
    if context["round_config_hash"] != hash_payload(config):
        raise RoundAuditViolation("round config does not match frozen run context")
    parent = PolicyState.from_dict(read_json(root / "active_parent.json"))
    if parent.policy_hash != context["active_policy_hash"]:
        raise RoundAuditViolation("active parent does not match frozen run context")
    red_context = verify_red_search_context(
        read_json(root / "red_search_context.json")
    )
    if red_context["challenged_policy_hash"] != parent.policy_hash:
        raise RoundAuditViolation("red search context policy binding mismatch")
    registry_before = validate_registry(read_json(root / "registry_before.json"))
    if registry_before["registry_hash"] != context["registry_hash_before"]:
        raise RoundAuditViolation("registry_before does not match frozen run context")
    residual = _manifest(root / "residual_manifest.json")
    archive_updates = _read_jsonl(root / "archive_updates.jsonl")
    covered_updates = _read_jsonl(root / "covered_archive_updates.jsonl")
    archive_exclusions = _read_jsonl(root / "archive_exclusions.jsonl")
    selection = read_json(root / "residual_selection.json")
    if selection.get("schema_version") != "r3e-residual-selection-v1":
        raise RoundAuditViolation("residual selection schema mismatch")
    selection_body = {
        key: value for key, value in selection.items() if key != "selection_hash"
    }
    if selection.get("selection_hash") != hash_payload(selection_body):
        raise RoundAuditViolation("residual selection hash mismatch")
    if selection.get("admitted_rows_hash") != hash_payload(archive_updates):
        raise RoundAuditViolation("residual selection admission hash mismatch")
    if selection.get("challenged_policy_hash") != parent.policy_hash:
        raise RoundAuditViolation("residual selection policy binding mismatch")
    selected_ids = {str(row.get("poison_id") or "") for row in residual["rows"]}
    recorded_ids = {str(value) for value in selection.get("selected_poison_ids") or []}
    if (
        selected_ids != recorded_ids
        or len(selected_ids) != int(selection.get("selected_count", -1))
    ):
        raise RoundAuditViolation("residual manifest does not match elite selection")
    if residual.get("metadata", {}).get("selection_hash") != selection["selection_hash"]:
        raise RoundAuditViolation("residual manifest selection hash mismatch")
    adaptation = _manifest(root / "adaptation_manifest.json")
    target = _manifest(root / "target_manifest.json")
    non_target = _manifest(root / "non_target_manifest.json")
    promotion = _manifest(root / "promotion_manifest.json")
    if adaptation["source_residual_manifest_hash"] != residual["manifest_hash"]:
        raise RoundAuditViolation("adaptation manifest source mismatch")
    if target["source_residual_manifest_hash"] != residual["manifest_hash"]:
        raise RoundAuditViolation("target manifest source mismatch")
    adaptation_designs = {
        str(row.get("design") or row.get("design_id") or "")
        for row in adaptation["rows"]
    }
    target_designs = {
        str(row.get("design") or row.get("design_id") or "")
        for row in target["rows"]
    }
    if adaptation_designs & target_designs:
        raise RoundAuditViolation("adaptation/target design leakage")
    for row in adaptation["rows"]:
        leaked = FORBIDDEN_INPUT_KEYS & row.keys()
        if leaked:
            raise RoundAuditViolation(
                f"adaptation row contains hidden fields: {sorted(leaked)}"
            )
    if (
        promotion.get("metadata", {}).get("non_target_manifest_hash")
        != non_target["manifest_hash"]
    ):
        raise RoundAuditViolation("promotion/non-target manifest binding mismatch")
    if promotion["source_residual_manifest_hash"] != target["manifest_hash"]:
        raise RoundAuditViolation("promotion/target manifest binding mismatch")

    children = {
        child.policy_id: child
        for child in (
            PolicyState.from_dict(row)
            for row in _read_jsonl(root / "child_policies.jsonl")
        )
    }
    for child in children.values():
        if child.parent_policy_hash != parent.policy_hash:
            raise RoundAuditViolation("child is not bound to audited parent")
        if child.created_from_residual_manifest_hash != residual["manifest_hash"]:
            raise RoundAuditViolation("child residual-manifest binding mismatch")

    replay_rows = _read_jsonl(root / "paired_validation.jsonl")
    decisions = _read_jsonl(root / "promotion_decisions.jsonl")
    promoted_candidates = {
        str(row.get("policy_id") or "") for row in promotion["rows"]
    }
    decided_candidates = {
        str(row.get("candidate_policy_id") or "") for row in decisions
    }
    if promoted_candidates != decided_candidates:
        raise RoundAuditViolation("promotion candidates and decisions differ")
    reconstructed = []
    for decision in decisions:
        candidate_id = str(decision.get("candidate_policy_id") or "")
        child = children.get(candidate_id)
        if child is None:
            raise RoundAuditViolation(f"decision candidate is missing: {candidate_id}")
        rows = [
            row for row in replay_rows
            if row.get("candidate_policy_id") == candidate_id
        ]
        provenance = decision.get("provenance") or {}
        expected_provenance = {
            "round_id": context["round_id"],
            "residual_manifest_hash": residual["manifest_hash"],
            "adaptation_manifest_hash": adaptation["manifest_hash"],
            "target_manifest_hash": target["manifest_hash"],
            "non_target_manifest_hash": non_target["manifest_hash"],
            "paired_result_hash": hash_payload(rows),
            "code_commit_sha": context["code_version"],
            "run_context_hash": context["run_context_hash"],
            "toolchain_fingerprint_hash": context["toolchain_fingerprint_hash"],
            "toolchain_fingerprint": context["toolchain_fingerprint"],
        }
        if provenance != expected_provenance:
            raise RoundAuditViolation(
                f"promotion provenance mismatch: {candidate_id}"
            )
        if decision.get("validation_manifest_hash") != promotion["manifest_hash"]:
            raise RoundAuditViolation(
                f"promotion validation manifest mismatch: {candidate_id}"
            )
        rebuilt = decide_policy_promotion(
            parent,
            child,
            rows,
            validation_manifest_hash=str(decision["validation_manifest_hash"]),
            thresholds=deepcopy(decision.get("thresholds") or {}),
            provenance=deepcopy(provenance),
        )
        if rebuilt != decision:
            raise RoundAuditViolation(
                f"promotion decision cannot be reconstructed: {candidate_id}"
            )
        reconstructed.append(rebuilt)
    winner = read_json(root / "winner.json")
    rebuilt_winner = select_single_promotable_child(reconstructed) or {}
    if winner != rebuilt_winner:
        raise RoundAuditViolation("recorded winner does not match reconstructed decisions")

    learnability_rows = _read_jsonl(root / "learnability_results.jsonl")
    learnability_by_id = {}
    for row in learnability_rows:
        verified = verify_learnability_result(row)
        poison_id = verified["poison_id"]
        if poison_id in learnability_by_id:
            raise RoundAuditViolation("duplicate learnability result")
        learnability_by_id[poison_id] = verified
    for row in archive_updates:
        evidence = row.get("learnability")
        poison_id = str(row.get("poison_id") or "")
        if evidence != learnability_by_id.get(poison_id):
            raise RoundAuditViolation("residual learnability evidence mismatch")

    registry_after = validate_registry(read_json(root / "registry_after.json"))
    active_after = get_active_policy(registry_after)
    if winner:
        if (
            active_after.policy_id != winner.get("candidate_policy_id")
            or active_after.policy_hash != winner.get("candidate_policy_hash")
        ):
            raise RoundAuditViolation("registry active policy is not the audited winner")
    elif active_after.policy_hash != parent.policy_hash:
        raise RoundAuditViolation("registry changed active policy without a winner")
    renewed = read_json(root / "renewed_challenge_binding.json")
    if renewed.get("challenged_policy_hash") != active_after.policy_hash:
        raise RoundAuditViolation("renewed challenge is not bound to final active policy")
    record = {
        "schema_version": ROUND_AUDIT_SCHEMA_VERSION,
        "round_id": context["round_id"],
        "code_version": context["code_version"],
        "run_context_hash": context["run_context_hash"],
        "toolchain_fingerprint_hash": context["toolchain_fingerprint_hash"],
        "red_search_context_hash": red_context["context_hash"],
        "parent_policy_id": parent.policy_id,
        "parent_policy_hash": parent.policy_hash,
        "active_policy_id": active_after.policy_id,
        "active_policy_hash": active_after.policy_hash,
        "registry_hash_before": registry_before["registry_hash"],
        "registry_hash_after": registry_after["registry_hash"],
        "residual_manifest_hash": residual["manifest_hash"],
        "residual_selection_hash": selection["selection_hash"],
        "residual_archive_updates_hash": hash_payload(archive_updates),
        "covered_archive_updates_hash": hash_payload(covered_updates),
        "archive_exclusions_hash": hash_payload(archive_exclusions),
        "learnability_results_hash": hash_payload(learnability_rows),
        "adaptation_manifest_hash": adaptation["manifest_hash"],
        "target_manifest_hash": target["manifest_hash"],
        "non_target_manifest_hash": non_target["manifest_hash"],
        "promotion_manifest_hash": promotion["manifest_hash"],
        "paired_result_hash": hash_payload(replay_rows),
        "decisions_hash": hash_payload(decisions),
        "winner_decision_hash": str(winner.get("decision_hash") or ""),
        "promoted": active_after.policy_hash != parent.policy_hash,
        "promotion_eligible": target["row_count"] > 0,
        "defer_reason": (
            "" if target["row_count"] > 0 else "insufficient_residual_designs"
        ),
    }
    record["audit_record_hash"] = hash_payload(record)
    return record


def freeze_round_audit(round_dir: str | Path) -> dict[str, Any]:
    root = Path(round_dir)
    target = root / "round_audit.json"
    rebuilt = reconstruct_round(root)
    if target.exists():
        existing = read_json(target)
        if existing != rebuilt:
            raise RoundAuditViolation("frozen round audit differs from reconstruction")
        return existing
    atomic_write_json(target, rebuilt)
    return rebuilt


def append_round_ledger(path: str | Path, audit: dict[str, Any]) -> dict[str, Any]:
    """Idempotently append one completed round to a hash-chain ledger."""
    target = Path(path)
    with writer_lock(target.with_suffix(target.suffix + ".lock")):
        existing = read_ledger(target)
        same_round = [
            row for row in existing
            if row.get("round_id") == audit.get("round_id")
        ]
        if same_round:
            if (
                len(same_round) != 1
                or same_round[0].get("audit_record_hash")
                != audit.get("audit_record_hash")
            ):
                raise RoundAuditViolation("round ledger contains a conflicting round")
            return same_round[0]
        payload = {
            "operation": "round_complete",
            "round_id": audit["round_id"],
            "audit_record_hash": audit["audit_record_hash"],
            "parent_policy_hash": audit["parent_policy_hash"],
            "active_policy_hash": audit["active_policy_hash"],
            "registry_hash_before": audit["registry_hash_before"],
            "registry_hash_after": audit["registry_hash_after"],
            "timestamp": utc_now(),
            "ledger_index": len(existing),
            "previous_entry_hash": (
                existing[-1]["ledger_entry_hash"] if existing else ""
            ),
        }
        payload["ledger_entry_hash"] = hash_payload(payload)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as stream:
            stream.write(canonical_json(payload) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return payload


def verify_frozen_round(round_dir: str | Path) -> dict[str, Any]:
    target = Path(round_dir) / "round_audit.json"
    if not target.is_file():
        raise RoundAuditViolation("frozen round audit is missing")
    existing = read_json(target)
    rebuilt = reconstruct_round(round_dir)
    if existing != rebuilt:
        raise RoundAuditViolation("round artifacts differ from frozen audit")
    return rebuilt


def _main() -> None:
    parser = argparse.ArgumentParser(description="Reconstruct and audit an R³E round")
    parser.add_argument("--round-dir", required=True)
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()
    result = (
        freeze_round_audit(args.round_dir)
        if args.freeze
        else verify_frozen_round(args.round_dir)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
