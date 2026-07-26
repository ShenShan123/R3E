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
from r3e.protocol.ledger import append_ledger, writer_lock

from .schema import POLICY_SCHEMA_VERSION, PolicyState, PolicyValidationError


REGISTRY_SCHEMA_VERSION = "r3e-policy-registry-v2"


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
    policies = registry.get("policies")
    if not isinstance(policies, dict) or not policies:
        raise RegistryViolation("registry policies missing")
    active = []
    for policy_id, entry in policies.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("policy"), dict):
            raise RegistryViolation(f"malformed policy entry: {policy_id}")
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
    base_id = str(base.get("policy_id") or "")
    if base_id not in policies:
        raise RegistryViolation("base policy is not registered")
    base_policy = PolicyState.from_dict(policies[base_id]["policy"])
    if base.get("hash") != base_policy.base_policy_hash:
        raise RegistryViolation("base policy hash mismatch")
    if formal_mode and registry.get("legacy_manual_skills"):
        raise RegistryViolation("manual/legacy skills are forbidden in formal mode")
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
        return created


def register_candidate(
    registry_path: str | Path,
    candidate: PolicyState | dict[str, Any],
    *,
    ledger_path: str | Path | None = None,
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
        return after


def _snapshot_path(registry_path: Path, digest: str) -> Path:
    safe = digest.replace(":", "_")
    return registry_path.parent / f".{registry_path.name}.versions" / f"{safe}.json"


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
        return after


def reject_policy(
    registry_path: str | Path,
    candidate_policy_id: str,
    reasons: list[str],
    *,
    provisional: bool = False,
    ledger_path: str | Path | None = None,
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
        return after


def rollback_policy(
    registry_path: str | Path,
    *,
    expected_active_policy_hash: str | None = None,
    reason: str = "later-audit-regression",
    ledger_path: str | Path | None = None,
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
        parent = PolicyState.from_dict(registry["policies"][rollback_id]["policy"])
        rolled_back = active.with_updates(status="rolled_back")
        restored = parent.with_updates(status="active")
        registry["policies"][active.policy_id] = _policy_entry(rolled_back)
        registry["policies"][parent.policy_id] = _policy_entry(restored)
        registry["active_policy_id"] = parent.policy_id
        registry["registry_parent_hash"] = before
        registry["updated_at"] = utc_now()
        _write_registry(path, registry)
        after = load_registry(path)
        if ledger_path:
            append_ledger(ledger_path, {
                "operation": "rollback",
                "reason": reason,
                "rolled_back_policy_id": active.policy_id,
                "restored_policy_id": parent.policy_id,
                "restored_policy_hash": parent.policy_hash,
                "registry_hash_before": before,
                "registry_hash_after": after["registry_hash"],
            })
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
    else:
        result = reject_policy(
            args.registry,
            args.candidate_id,
            args.reason,
            provisional=args.provisional,
            ledger_path=args.ledger,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
