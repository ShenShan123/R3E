"""Formal Policy Registry V2.

The registry embeds validated policy states, permits exactly one active policy,
rejects stale children, and performs every transition under a writer lock with
an exact rollback snapshot.
"""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from r3e.protocol.hashing import atomic_write_json, hash_payload, read_json, utc_now
from r3e.protocol.ledger import append_ledger, read_ledger, writer_lock
from r3e.protocol.events import EventLogger

from .schema import POLICY_SCHEMA_VERSION, PolicyState, PolicyValidationError


REGISTRY_SCHEMA_VERSION = "r3e-policy-registry-v2"
_REGISTRY_FIELDS = {
    "schema_version",
    "base_policy",
    "active_policy_id",
    "policies",
    "registry_parent_hash",
    "updated_at",
    "registry_hash",
}


class RegistryViolation(RuntimeError):
    """Raised when registry lineage or authority is ambiguous."""


def _registry_body(registry: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in registry.items() if key != "registry_hash"}


def registry_hash(registry: dict[str, Any]) -> str:
    return hash_payload(_registry_body(registry))


def _policy_entry(policy: PolicyState, *, status: str | None = None) -> dict[str, Any]:
    state = policy.with_updates(status=status or policy.status)
    return {
        "status": state.status,
        "policy_hash": state.policy_hash,
        "policy": state.to_dict(),
    }


def validate_registry(registry: dict[str, Any], *, formal_mode: bool = True) -> dict[str, Any]:
    if registry.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise RegistryViolation("registry schema mismatch")
    if registry.get("registry_hash") != registry_hash(registry):
        raise RegistryViolation("registry hash mismatch")
    if formal_mode and registry.get("legacy_manual_skills"):
        raise RegistryViolation("manual/legacy skills are forbidden in formal mode")
    unknown = set(registry) - _REGISTRY_FIELDS
    if unknown:
        raise RegistryViolation(f"undeclared registry fields: {sorted(unknown)}")
    policies = registry.get("policies")
    if not isinstance(policies, dict) or not policies:
        raise RegistryViolation("registry policies missing")
    active = []
    for policy_id, entry in policies.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("policy"), dict):
            raise RegistryViolation(f"malformed policy entry: {policy_id}")
        if set(entry) != {"status", "policy_hash", "policy"}:
            raise RegistryViolation(f"undeclared policy entry fields: {policy_id}")
        try:
            policy = PolicyState.from_dict(entry["policy"])
        except PolicyValidationError as exc:
            raise RegistryViolation(f"invalid policy {policy_id}: {exc}") from exc
        if policy.policy_id != policy_id:
            raise RegistryViolation(f"policy key/id mismatch: {policy_id}")
        if entry.get("policy_hash") != policy.policy_hash:
            raise RegistryViolation(f"policy hash mismatch: {policy_id}")
        if entry.get("status") != policy.status:
            raise RegistryViolation(f"policy status mismatch: {policy_id}")
        if policy.status == "active":
            active.append(policy_id)
    if len(active) != 1:
        raise RegistryViolation(f"formal registry must have exactly one active policy, got {active}")
    if registry.get("active_policy_id") != active[0]:
        raise RegistryViolation("active_policy_id does not match active entry")
    base = registry.get("base_policy")
    if not isinstance(base, dict):
        raise RegistryViolation("base policy binding missing")
    if set(base) != {"policy_id", "path", "hash"}:
        raise RegistryViolation("undeclared base policy binding fields")
    base_id = str(base.get("policy_id") or "")
    if base_id not in policies:
        raise RegistryViolation("base policy is not registered")
    base_policy = PolicyState.from_dict(policies[base_id]["policy"])
    if base.get("hash") != base_policy.base_policy_hash:
        raise RegistryViolation("base policy hash mismatch")
    for policy_id, entry in policies.items():
        policy = PolicyState.from_dict(entry["policy"])
        if policy.frozen_assets != base_policy.frozen_assets:
            raise RegistryViolation(
                f"policy frozen assets differ from base policy: {policy_id}"
            )
    return registry


def load_registry(path: str | Path, *, formal_mode: bool = True) -> dict[str, Any]:
    return validate_registry(read_json(path), formal_mode=formal_mode)


def get_active_policy(registry: dict[str, Any]) -> PolicyState:
    validate_registry(registry)
    return PolicyState.from_dict(
        registry["policies"][registry["active_policy_id"]]["policy"]
    )


def _write_registry(path: Path, registry: dict[str, Any]) -> None:
    payload = deepcopy(registry)
    payload["registry_hash"] = registry_hash(payload)
    atomic_write_json(path, payload)
    load_registry(path)


def initialize_registry(
    base_policy_path: str | Path,
    registry_path: str | Path,
    *,
    base_path_record: str | None = None,
    ledger_path: str | Path | None = None,
    event_logger: EventLogger | None = None,
    round_id: str = "",
) -> dict[str, Any]:
    base_source = Path(base_policy_path)
    base_raw = read_json(base_source)
    base_raw["schema_version"] = POLICY_SCHEMA_VERSION
    base_raw["status"] = "active"
    policy = PolicyState.from_dict(base_raw)
    if policy.policy_id != "B0":
        raise RegistryViolation("frozen base policy must use policy_id B0")
    target = Path(registry_path)
    lock = target.with_suffix(target.suffix + ".lock")
    with writer_lock(lock):
        if target.exists():
            raise RegistryViolation(f"registry already exists: {target}")
        registry = {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "base_policy": {
                "policy_id": policy.policy_id,
                "path": base_path_record or str(base_source),
                "hash": policy.base_policy_hash,
            },
            "active_policy_id": policy.policy_id,
            "policies": {policy.policy_id: _policy_entry(policy, status="active")},
            "registry_parent_hash": "",
            "updated_at": utc_now(),
            "registry_hash": "",
        }
        _write_registry(target, registry)
        created = load_registry(target)
        if ledger_path:
            append_ledger(ledger_path, {
                "operation": "init",
                "active_policy_id": policy.policy_id,
                "active_policy_hash": policy.policy_hash,
                "registry_hash_after": created["registry_hash"],
            })
        if event_logger:
            event_logger.emit(
                "policy",
                "registry_initialized",
                round_id=round_id,
                active_policy_id=policy.policy_id,
                active_policy_hash=policy.policy_hash,
                registry_hash_before="",
                registry_hash_after=created["registry_hash"],
            )
        return created


def register_candidate(
    registry_path: str | Path,
    candidate: PolicyState | dict[str, Any],
    *,
    ledger_path: str | Path | None = None,
    event_logger: EventLogger | None = None,
    round_id: str = "",
) -> dict[str, Any]:
    child = candidate if isinstance(candidate, PolicyState) else PolicyState.from_dict(candidate)
    if child.status != "candidate":
        raise RegistryViolation("new child must have candidate status")
    path = Path(registry_path)
    with writer_lock(path.with_suffix(path.suffix + ".lock")):
        registry = load_registry(path)
        before = registry["registry_hash"]
        parent = get_active_policy(registry)
        if child.parent_policy_id != parent.policy_id or child.parent_policy_hash != parent.policy_hash:
            raise RegistryViolation("candidate is not bound to the current active parent")
        if child.base_policy_hash != registry["base_policy"]["hash"]:
            raise RegistryViolation("candidate base policy hash mismatch")
        if child.policy_id in registry["policies"]:
            raise RegistryViolation(f"policy already registered: {child.policy_id}")
        registry["policies"][child.policy_id] = _policy_entry(child)
        registry["registry_parent_hash"] = before
        registry["updated_at"] = utc_now()
        _write_registry(path, registry)
        after = load_registry(path)
        if ledger_path:
            append_ledger(ledger_path, {
                "operation": "create-child",
                "parent_policy_id": parent.policy_id,
                "parent_policy_hash": parent.policy_hash,
                "candidate_policy_id": child.policy_id,
                "candidate_policy_hash": child.policy_hash,
                "registry_hash_before": before,
                "registry_hash_after": after["registry_hash"],
            })
        if event_logger:
            event_logger.emit(
                "policy",
                "child_registered",
                round_id=round_id,
                parent_policy_hash=parent.policy_hash,
                child_policy_id=child.policy_id,
                child_policy_hash=child.policy_hash,
                registry_hash_before=before,
                registry_hash_after=after["registry_hash"],
            )
        return after


def _snapshot_path(registry_path: Path, digest: str) -> Path:
    safe = digest.replace(":", "_")
    return registry_path.parent / f".{registry_path.name}.versions" / f"{safe}.json"


def _audit_failure_path(registry_path: Path) -> Path:
    return registry_path.parent / f".{registry_path.name}.audit_failures.jsonl"


def _audit_failure_for_hash(
    registry_path: Path, policy_hash: str
) -> dict[str, Any] | None:
    return next(
        (
            row for row in read_ledger(_audit_failure_path(registry_path))
            if row.get("operation") == "audit-fail"
            and row.get("failed_policy_hash") == policy_hash
        ),
        None,
    )


def _save_snapshot(registry_path: Path, registry: dict[str, Any]) -> None:
    snapshot = _snapshot_path(registry_path, registry["registry_hash"])
    if snapshot.exists():
        existing = read_json(snapshot)
        if existing.get("registry_hash") != registry["registry_hash"]:
            raise RegistryViolation("registry snapshot collision")
        return
    atomic_write_json(snapshot, registry)


def promote_policy(
    registry_path: str | Path,
    candidate_policy_id: str,
    decision: dict[str, Any],
    *,
    ledger_path: str | Path | None = None,
    event_logger: EventLogger | None = None,
    round_id: str = "",
) -> dict[str, Any]:
    path = Path(registry_path)
    with writer_lock(path.with_suffix(path.suffix + ".lock")):
        registry = load_registry(path)
        before = registry["registry_hash"]
        parent = get_active_policy(registry)
        entry = registry["policies"].get(candidate_policy_id)
        if not entry:
            raise RegistryViolation("candidate policy is not registered")
        candidate = PolicyState.from_dict(entry["policy"])
        if candidate.status != "candidate":
            raise RegistryViolation("only candidate policy can be promoted")
        if _audit_failure_for_hash(path, candidate.policy_hash):
            raise RegistryViolation(
                "audit-failed policy hash is permanently barred from promotion"
            )
        if candidate.parent_policy_id != parent.policy_id:
            raise RegistryViolation("stale candidate parent id")
        if candidate.parent_policy_hash != parent.policy_hash:
            raise RegistryViolation("stale candidate parent hash")
        if not decision.get("promote") or decision.get("decision") != "strong_promotion":
            raise RegistryViolation("only a strong promotion decision may change active policy")
        if decision.get("candidate_policy_hash") != candidate.policy_hash:
            raise RegistryViolation("promotion decision candidate hash mismatch")
        if decision.get("parent_policy_hash") != parent.policy_hash:
            raise RegistryViolation("promotion decision parent hash mismatch")
        decision_body = {
            key: value for key, value in decision.items() if key != "decision_hash"
        }
        if decision.get("decision_hash") != hash_payload(decision_body):
            raise RegistryViolation("promotion decision hash mismatch")
        _save_snapshot(path, registry)
        old_parent = parent.with_updates(status="superseded")
        promoted = candidate.with_updates(
            status="active",
            promotion_decision_hash=decision["decision_hash"],
            validation_manifest_hash=str(decision.get("validation_manifest_hash") or ""),
            rollback_policy_id=parent.policy_id,
            rollback_registry_hash=before,
        )
        # Lifecycle updates do not alter the immutable policy hash.
        if promoted.policy_hash != candidate.policy_hash:
            raise RegistryViolation("promotion unexpectedly changed immutable policy hash")
        registry["policies"][parent.policy_id] = _policy_entry(old_parent)
        registry["policies"][candidate.policy_id] = _policy_entry(promoted)
        registry["active_policy_id"] = candidate.policy_id
        registry["registry_parent_hash"] = before
        registry["updated_at"] = utc_now()
        _write_registry(path, registry)
        after = load_registry(path)
        if ledger_path:
            append_ledger(ledger_path, {
                "operation": "promote",
                "parent_policy_id": parent.policy_id,
                "parent_policy_hash": parent.policy_hash,
                "candidate_policy_id": candidate.policy_id,
                "candidate_policy_hash": candidate.policy_hash,
                "decision_hash": decision["decision_hash"],
                "residual_manifest_hash": candidate.created_from_residual_manifest_hash,
                "validation_manifest_hash": promoted.validation_manifest_hash,
                "thresholds": decision.get("thresholds") or {},
                "provenance": decision.get("provenance") or {},
                "registry_hash_before": before,
                "registry_hash_after": after["registry_hash"],
            })
        if event_logger:
            event_logger.emit(
                "policy",
                "policy_promoted",
                round_id=round_id,
                parent_policy_hash=parent.policy_hash,
                child_policy_hash=candidate.policy_hash,
                registry_hash_before=before,
                registry_hash_after=after["registry_hash"],
                decision_hash=decision["decision_hash"],
            )
        return after


def reject_policy(
    registry_path: str | Path,
    candidate_policy_id: str,
    reasons: list[str],
    *,
    provisional: bool = False,
    ledger_path: str | Path | None = None,
    event_logger: EventLogger | None = None,
    round_id: str = "",
) -> dict[str, Any]:
    path = Path(registry_path)
    with writer_lock(path.with_suffix(path.suffix + ".lock")):
        registry = load_registry(path)
        before = registry["registry_hash"]
        entry = registry["policies"].get(candidate_policy_id)
        if not entry:
            raise RegistryViolation("candidate policy is not registered")
        policy = PolicyState.from_dict(entry["policy"])
        if policy.status != "candidate":
            raise RegistryViolation("only candidate policy can be rejected")
        status = "provisional" if provisional else "rejected"
        registry["policies"][candidate_policy_id] = _policy_entry(
            policy.with_updates(status=status)
        )
        registry["registry_parent_hash"] = before
        registry["updated_at"] = utc_now()
        _write_registry(path, registry)
        after = load_registry(path)
        if ledger_path:
            append_ledger(ledger_path, {
                "operation": status,
                "candidate_policy_id": candidate_policy_id,
                "rejection_reasons": list(reasons),
                "registry_hash_before": before,
                "registry_hash_after": after["registry_hash"],
            })
        if event_logger:
            event_logger.emit(
                "policy",
                "policy_provisional" if provisional else "policy_rejected",
                round_id=round_id,
                candidate_policy_id=candidate_policy_id,
                candidate_policy_hash=policy.policy_hash,
                rejection_reasons=list(reasons),
                registry_hash_before=before,
                registry_hash_after=after["registry_hash"],
            )
        return after


def rollback_policy(
    registry_path: str | Path,
    *,
    expected_active_policy_hash: str | None = None,
    reason: str = "later-audit-regression",
    ledger_path: str | Path | None = None,
    event_logger: EventLogger | None = None,
    round_id: str = "",
) -> dict[str, Any]:
    path = Path(registry_path)
    with writer_lock(path.with_suffix(path.suffix + ".lock")):
        registry = load_registry(path)
        before = registry["registry_hash"]
        active = get_active_policy(registry)
        if expected_active_policy_hash and active.policy_hash != expected_active_policy_hash:
            raise RegistryViolation("active policy changed before rollback")
        rollback_id = active.rollback_policy_id
        if not rollback_id or rollback_id not in registry["policies"]:
            raise RegistryViolation("active policy has no registered rollback parent")
        target_hash = active.rollback_registry_hash
        if not target_hash:
            raise RegistryViolation("active policy has no rollback registry version")
        target_path = _snapshot_path(path, target_hash)
        if not target_path.is_file():
            raise RegistryViolation("rollback registry version is missing")
        restored_registry = read_json(target_path)
        if restored_registry.get("registry_hash") != target_hash:
            raise RegistryViolation("rollback registry version hash mismatch")
        validate_registry(restored_registry)
        restored = get_active_policy(restored_registry)
        if restored.policy_id != rollback_id:
            raise RegistryViolation("rollback snapshot does not restore the bound parent")
        _save_snapshot(path, registry)
        atomic_write_json(path, restored_registry)
        after = load_registry(path)
        if ledger_path:
            append_ledger(ledger_path, {
                "operation": "rollback",
                "reason": reason,
                "rolled_back_policy_id": active.policy_id,
                "restored_policy_id": restored.policy_id,
                "restored_policy_hash": restored.policy_hash,
                "registry_hash_before": before,
                "registry_hash_after": after["registry_hash"],
            })
        if event_logger:
            event_logger.emit(
                "rollback",
                "policy_rolled_back",
                round_id=round_id,
                rolled_back_policy_id=active.policy_id,
                rolled_back_policy_hash=active.policy_hash,
                restored_policy_id=restored.policy_id,
                restored_policy_hash=restored.policy_hash,
                reason=reason,
                registry_hash_before=before,
                registry_hash_after=after["registry_hash"],
            )
        return after


def retire_policy(
    registry_path: str | Path,
    policy_id: str,
    *,
    reason: str,
    ledger_path: str | Path | None = None,
    event_logger: EventLogger | None = None,
    round_id: str = "",
) -> dict[str, Any]:
    """Retire a non-authoritative policy without changing the active policy."""
    if not reason.strip():
        raise RegistryViolation("retirement reason is required")
    path = Path(registry_path)
    with writer_lock(path.with_suffix(path.suffix + ".lock")):
        registry = load_registry(path)
        before = registry["registry_hash"]
        entry = registry["policies"].get(policy_id)
        if not entry:
            raise RegistryViolation("retirement policy is not registered")
        policy = PolicyState.from_dict(entry["policy"])
        active = get_active_policy(registry)
        if policy.policy_id == registry["base_policy"]["policy_id"]:
            raise RegistryViolation("frozen base policy cannot be retired")
        if policy.policy_id == active.policy_id:
            raise RegistryViolation("active policy cannot be retired")
        if policy.policy_id == active.rollback_policy_id:
            raise RegistryViolation("active rollback parent cannot be retired")
        if policy.status in {"retired", "audit_failed"}:
            raise RegistryViolation(f"policy is already terminal: {policy.status}")
        retired = policy.with_updates(status="retired")
        registry["policies"][policy_id] = _policy_entry(retired)
        registry["registry_parent_hash"] = before
        registry["updated_at"] = utc_now()
        _write_registry(path, registry)
        after = load_registry(path)
        record = {
            "operation": "retire",
            "policy_id": policy.policy_id,
            "policy_hash": policy.policy_hash,
            "reason": reason,
            "registry_hash_before": before,
            "registry_hash_after": after["registry_hash"],
        }
        if ledger_path:
            append_ledger(ledger_path, record)
        if event_logger:
            event_logger.emit(
                "policy",
                "policy_retired",
                round_id=round_id,
                **{key: value for key, value in record.items() if key != "operation"},
            )
        return after


def audit_fail_policy(
    registry_path: str | Path,
    policy_id: str,
    evidence: dict[str, Any],
    *,
    expected_policy_hash: str | None = None,
    ledger_path: str | Path | None = None,
    event_logger: EventLogger | None = None,
    round_id: str = "",
) -> dict[str, Any]:
    """Tombstone an audit-failed hash and exactly restore its promotion parent.

    The tombstone lives outside registry snapshots, so exact rollback cannot
    accidentally make the failed candidate promotable again.
    """
    body = {key: value for key, value in evidence.items() if key != "evidence_hash"}
    if (
        evidence.get("schema_version") != "r3e-policy-audit-failure-v1"
        or evidence.get("audit_result") != "fail"
        or evidence.get("policy_id") != policy_id
        or evidence.get("evidence_hash") != hash_payload(body)
    ):
        raise RegistryViolation("invalid audit-failure evidence")
    evidence_policy_hash = str(evidence.get("policy_hash") or "")
    if expected_policy_hash and evidence_policy_hash != expected_policy_hash:
        raise RegistryViolation("audit evidence policy hash does not match expectation")

    path = Path(registry_path)
    tombstone_path = _audit_failure_path(path)
    with writer_lock(path.with_suffix(path.suffix + ".lock")):
        registry = load_registry(path)
        entry = registry["policies"].get(policy_id)
        if not entry:
            raise RegistryViolation("audit-failure policy is not registered")
        policy = PolicyState.from_dict(entry["policy"])
        if policy.policy_hash != evidence_policy_hash:
            raise RegistryViolation("audit evidence policy hash mismatch")
        existing = _audit_failure_for_hash(path, policy.policy_hash)
        if existing:
            if existing.get("evidence_hash") != evidence["evidence_hash"]:
                raise RegistryViolation("conflicting audit-failure evidence")
            if (
                existing.get("active_at_failure") is True
                and get_active_policy(registry).policy_id != policy.policy_id
            ):
                return registry
            if (
                existing.get("active_at_failure") is False
                and policy.status == "audit_failed"
            ):
                return registry

        before = registry["registry_hash"]
        active = get_active_policy(registry)
        restored_registry: dict[str, Any] | None = None
        restored_policy: PolicyState | None = None
        if active.policy_id == policy.policy_id:
            if not active.rollback_policy_id or not active.rollback_registry_hash:
                raise RegistryViolation("active audit-failed policy has no rollback binding")
            target_path = _snapshot_path(path, active.rollback_registry_hash)
            if not target_path.is_file():
                raise RegistryViolation("audit-failure rollback version is missing")
            restored_registry = read_json(target_path)
            validate_registry(restored_registry)
            restored_policy = get_active_policy(restored_registry)
            if restored_policy.policy_id != active.rollback_policy_id:
                raise RegistryViolation("audit-failure snapshot restores wrong parent")
        elif policy.policy_id == registry["base_policy"]["policy_id"]:
            raise RegistryViolation("frozen base policy cannot be audit-failed")

        tombstone = existing or append_ledger(tombstone_path, {
            "operation": "audit-fail",
            "failed_policy_id": policy.policy_id,
            "failed_policy_hash": policy.policy_hash,
            "evidence_hash": evidence["evidence_hash"],
            "reason": str(evidence.get("reason") or ""),
            "active_at_failure": active.policy_id == policy.policy_id,
            "registry_hash_observed": before,
        })
        if active.policy_id == policy.policy_id:
            _save_snapshot(path, registry)
            assert restored_registry is not None
            atomic_write_json(path, restored_registry)
        else:
            registry["policies"][policy_id] = _policy_entry(
                policy.with_updates(status="audit_failed")
            )
            registry["registry_parent_hash"] = before
            registry["updated_at"] = utc_now()
            _write_registry(path, registry)
        after = load_registry(path)
        record = {
            "operation": "audit-fail",
            "failed_policy_id": policy.policy_id,
            "failed_policy_hash": policy.policy_hash,
            "evidence_hash": evidence["evidence_hash"],
            "tombstone_hash": tombstone["ledger_entry_hash"],
            "restored_policy_id": (
                restored_policy.policy_id if restored_policy else ""
            ),
            "restored_policy_hash": (
                restored_policy.policy_hash if restored_policy else ""
            ),
            "registry_hash_before": before,
            "registry_hash_after": after["registry_hash"],
        }
        if ledger_path:
            append_ledger(ledger_path, record)
        if event_logger:
            event_logger.emit(
                "policy",
                "policy_audit_failed",
                round_id=round_id,
                **{key: value for key, value in record.items() if key != "operation"},
            )
            if restored_policy:
                event_logger.emit(
                    "rollback",
                    "audit_failure_rollback",
                    round_id=round_id,
                    **{key: value for key, value in record.items() if key != "operation"},
                )
        return after


def _main() -> None:
    parser = argparse.ArgumentParser(description="R³E Formal Policy Registry V2")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--base", required=True)
    init.add_argument("--registry", required=True)
    init.add_argument("--ledger")
    show = sub.add_parser("show-active")
    show.add_argument("--registry", required=True)
    rollback = sub.add_parser("rollback")
    rollback.add_argument("--registry", required=True)
    rollback.add_argument("--expected-policy-hash")
    rollback.add_argument("--reason", default="operator-requested-audit-rollback")
    rollback.add_argument("--ledger")
    commit = sub.add_parser("commit")
    commit.add_argument("--registry", required=True)
    commit.add_argument("--candidate-id", required=True)
    commit.add_argument("--decision", required=True)
    commit.add_argument("--ledger")
    reject = sub.add_parser("reject")
    reject.add_argument("--registry", required=True)
    reject.add_argument("--candidate-id", required=True)
    reject.add_argument("--reason", action="append", required=True)
    reject.add_argument("--provisional", action="store_true")
    reject.add_argument("--ledger")
    retire = sub.add_parser("retire")
    retire.add_argument("--registry", required=True)
    retire.add_argument("--policy-id", required=True)
    retire.add_argument("--reason", required=True)
    retire.add_argument("--ledger")
    audit_fail = sub.add_parser("audit-fail")
    audit_fail.add_argument("--registry", required=True)
    audit_fail.add_argument("--policy-id", required=True)
    audit_fail.add_argument("--expected-policy-hash")
    audit_fail.add_argument("--evidence", required=True)
    audit_fail.add_argument("--ledger")
    args = parser.parse_args()
    if args.command == "init":
        result = initialize_registry(args.base, args.registry, ledger_path=args.ledger)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "show-active":
        print(json.dumps(get_active_policy(load_registry(args.registry)).to_dict(), indent=2))
    elif args.command == "rollback":
        result = rollback_policy(
            args.registry,
            expected_active_policy_hash=args.expected_policy_hash,
            reason=args.reason,
            ledger_path=args.ledger,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "commit":
        result = promote_policy(
            args.registry,
            args.candidate_id,
            read_json(args.decision),
            ledger_path=args.ledger,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "reject":
        result = reject_policy(
            args.registry,
            args.candidate_id,
            args.reason,
            provisional=args.provisional,
            ledger_path=args.ledger,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "retire":
        result = retire_policy(
            args.registry,
            args.policy_id,
            reason=args.reason,
            ledger_path=args.ledger,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        result = audit_fail_policy(
            args.registry,
            args.policy_id,
            read_json(args.evidence),
            expected_policy_hash=args.expected_policy_hash,
            ledger_path=args.ledger,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
