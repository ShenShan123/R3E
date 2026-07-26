"""Fail-closed protocol primitives for the R3E formal rerun.

This module is deliberately independent from the legacy manual B3 registry.
Formal runs start from an empty registry and accept only automatically distilled
artifacts with a current, reconstructable promotion decision.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import fcntl


PROTOCOL_VERSION = "r3e-formal-v1"
PROMOTION_SCHEMA = "r3e-promotion-v3"
FORMAL_RESULT_FIELDS = {
    "case_id", "seed", "arm", "agent_id", "lens", "candidate_index",
    "prompt_hash", "response_hash", "patch_hash", "compile_ok",
    "simulation_ok", "oracle_ok", "used_memory", "activated_artifact_ids",
    "policy_hash", "failure_reason",
}
ARTIFACT_FIELDS = {
    "artifact_id", "origin", "generation_mode", "source_residual_ids",
    "source_residual_hashes",
    "candidate_prompt_hash", "candidate_output_hash", "parent_policy_hash",
    "validation_manifest_hash", "promotion_decision_hash",
    "registry_parent_hash", "created_at", "runtime_status",
}
DECISION_FIELDS = {
    "schema_version", "candidate_hash", "parent_policy_hash",
    "validation_manifest_hash", "target_hits", "target_total", "target_rate",
    "baseline_target_rate", "delta_target", "non_target_hits",
    "non_target_total", "delta_non_target", "min_hits_pass", "replay_pass",
    "no_regression_pass", "leakage_pass", "scope_pass", "promote",
    "target_validation_hits", "covered_designs", "target_recovery_ratio",
    "target_gain", "support_pass", "coverage_pass", "recovery_pass",
    "gain_pass", "provenance_pass", "rollback_pass", "safety_pass",
    "thresholds", "validation_evidence", "gate_code_version", "metrics_hash",
    "decision_hash",
}


class FormalProtocolViolation(RuntimeError):
    pass


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def hash_payload(payload: Any) -> str:
    return sha256_text(canonical_json(payload))


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def empty_registry(path: Path) -> str:
    payload = {"schema_version": "r3e-registry-v1", "artifacts": []}
    atomic_write_json(path, payload)
    return hash_payload(payload)


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


@contextmanager
def _locked(lock_path: Path):
    """Serialize registry/ledger writers on the local filesystem."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def append_decision_ledger(path: Path, entry: dict[str, Any]) -> dict[str, Any]:
    """Append one hash-bound audit event without interleaving concurrent writers."""
    payload = dict(entry)
    payload.setdefault("created_at", utc_now())
    payload["ledger_entry_hash"] = hash_payload(
        {k: v for k, v in payload.items() if k != "ledger_entry_hash"}
    )
    with _locked(path.with_suffix(path.suffix + ".lock")):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as stream:
            stream.write(canonical_json(payload) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    return payload


def load_registry(path: Path, *, formal_mode: bool = True) -> dict:
    payload = json.loads(path.read_text())
    if payload.get("schema_version") != "r3e-registry-v1":
        raise FormalProtocolViolation("registry schema mismatch")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise FormalProtocolViolation("registry artifacts missing")
    for artifact in artifacts:
        missing = ARTIFACT_FIELDS - artifact.keys()
        if missing:
            raise FormalProtocolViolation(f"artifact missing fields: {sorted(missing)}")
        if formal_mode and artifact.get("origin") != "automatic":
            raise FormalProtocolViolation("manual/legacy artifact loaded in formal mode")
        if formal_mode and artifact.get("generation_mode") != "distilled":
            raise FormalProtocolViolation("non-distilled artifact loaded in formal mode")
    return payload


def policy_hash(registry: dict) -> str:
    active = [a for a in registry.get("artifacts", []) if a.get("runtime_status") == "promoted"]
    payload = {"protocol": PROTOCOL_VERSION, "active": active}
    # A frozen base policy may be bound into the effective policy lineage.
    # Historical registries omit this field and retain their original hash.
    if "base_policy" in registry:
        payload["base_policy"] = registry["base_policy"]
    return hash_payload(payload)


def _candidate_body(candidate: dict) -> dict:
    return {k: v for k, v in candidate.items() if k not in {
        "candidate_hash", "promotion_decision_hash", "runtime_status"
    }}


def candidate_hash(candidate: dict) -> str:
    return hash_payload(_candidate_body(candidate))


def distill_candidates(
    residuals: list[dict],
    *,
    parent_policy_hash: str,
    registry_parent_hash: str,
    validation_manifest_hash: str,
    distiller: Callable[[str, list[dict]], str] | None = None,
) -> list[dict]:
    """Automatically cluster residuals and materialize candidate provenance.

    `distiller` may call an LLM in a real run. Tests use a deterministic fake.
    Both prompt and raw output are always hashed and retained.
    """
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for residual in residuals:
        rid = residual.get("residual_id")
        family = residual.get("family")
        if not rid or not family:
            raise FormalProtocolViolation("residual missing residual_id/family")
        effect = str(
            residual.get("effect") or residual.get("failure_effect")
            or residual.get("failure_signature") or "unknown"
        )
        scope = str(residual.get("edit_scope") or "local_block")
        groups.setdefault((str(family), effect, scope), []).append(residual)
    out = []
    seen_strategy_keys: set[tuple[str, str, str, str]] = set()
    for (family, effect, scope), rows in sorted(groups.items()):
        compact = [{k: r.get(k) for k in (
            "residual_id", "failure_signature", "mutation_type", "effect", "edit_scope"
        )}
                   for r in rows]
        prompt = canonical_json({
            "task": "distill a generic RTL repair strategy without case-specific literals",
            "family": family,
            "effect": effect,
            "edit_scope": scope,
            "residuals": compact,
        })
        raw = (distiller(prompt, rows) if distiller else
               f"Inspect {family} signals and apply the smallest local correction consistent with oracle evidence.")
        normalized = " ".join(str(raw).split())
        strategy_key = (family, effect, scope, normalized)
        if strategy_key in seen_strategy_keys:
            continue
        seen_strategy_keys.add(strategy_key)
        group_hash = sha256_text(canonical_json({
            "family": family, "effect": effect, "scope": scope,
        }))[:8]
        artifact = {
            "artifact_id": f"auto_{family}_{group_hash}_{sha256_text(normalized)[:12]}",
            "origin": "automatic",
            "generation_mode": "distilled",
            "source_residual_ids": sorted(str(r["residual_id"]) for r in rows),
            "source_residual_hashes": {
                str(r["residual_id"]): hash_payload(r) for r in sorted(
                    rows, key=lambda item: str(item["residual_id"])
                )
            },
            "candidate_prompt": prompt,
            "candidate_prompt_hash": sha256_text(prompt),
            "candidate_raw_output": str(raw),
            "candidate_output_hash": sha256_text(str(raw)),
            "normalized_strategy": normalized,
            "trigger": {"bug_family": family, "effect": effect, "edit_scope": scope},
            "action_policy": {"evidence_k": 1, "n_candidates": 1, "patch_scope": scope},
            "parent_policy_hash": parent_policy_hash,
            "validation_manifest_hash": validation_manifest_hash,
            "promotion_decision_hash": "",
            "registry_parent_hash": registry_parent_hash,
            "created_at": utc_now(),
            "runtime_status": "candidate",
            "rollback_artifact": {
                "action": "restore_previous_registry",
                "previous_registry_hash": registry_parent_hash,
                "previous_policy_hash": parent_policy_hash,
            },
        }
        artifact["candidate_hash"] = candidate_hash(artifact)
        out.append(artifact)
    return out


def _canonical_validation_rows(rows: Iterable[dict]) -> list[dict[str, Any]]:
    canonical: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for raw in rows:
        arm = str(raw.get("arm") or "")
        split = str(raw.get("split") or "")
        case_id = str(raw.get("case_id") or "")
        if arm not in {"baseline", "candidate"}:
            raise FormalProtocolViolation(f"invalid validation arm: {arm!r}")
        if split not in {"target", "non_target"}:
            raise FormalProtocolViolation(f"invalid validation split: {split!r}")
        if not case_id:
            raise FormalProtocolViolation("validation row missing case_id")
        rep = int(raw.get("rep") or 0)
        key = (arm, split, case_id, rep)
        if key in seen:
            raise FormalProtocolViolation(f"duplicate validation row: {key}")
        seen.add(key)
        canonical.append({
            "case_id": case_id,
            "rep": rep,
            "design": str(raw.get("design") or ""),
            "arm": arm,
            "split": split,
            "oracle_ok": bool(raw.get("oracle_ok")),
            "strategy_hit": bool(raw.get("strategy_hit", arm == "candidate")),
        })
    return sorted(
        canonical,
        key=lambda row: (row["split"], row["case_id"], row["rep"], row["arm"]),
    )


def _paired_validation(rows: Iterable[dict]) -> tuple[list[dict], dict[str, dict]]:
    canonical = _canonical_validation_rows(rows)
    grouped: dict[str, dict[tuple[str, int], dict[str, dict]]] = {
        "target": {}, "non_target": {},
    }
    for row in canonical:
        key = (row["case_id"], row["rep"])
        grouped[row["split"]].setdefault(key, {})[row["arm"]] = row
    for split, pairs in grouped.items():
        if not pairs:
            raise FormalProtocolViolation(f"validation split is empty: {split}")
        for key, arms in pairs.items():
            if set(arms) != {"baseline", "candidate"}:
                raise FormalProtocolViolation(
                    f"unpaired validation row in {split}: {key} has {sorted(arms)}"
                )
            base_design = arms["baseline"]["design"]
            candidate_design = arms["candidate"]["design"]
            if base_design and candidate_design and base_design != candidate_design:
                raise FormalProtocolViolation(
                    f"paired validation design mismatch in {split}: {key}"
                )
    target_cases = {key[0] for key in grouped["target"]}
    non_target_cases = {key[0] for key in grouped["non_target"]}
    overlap = sorted(target_cases & non_target_cases)
    if overlap:
        raise FormalProtocolViolation(
            f"target and non-target validation sets overlap: {overlap[:5]}"
        )
    return canonical, grouped


def validation_manifest_hash(rows: Iterable[dict]) -> str:
    canonical, grouped = _paired_validation(rows)
    manifest = [
        {
            "case_id": case_id,
            "rep": rep,
            "split": split,
            "design": arms["candidate"]["design"] or arms["baseline"]["design"],
        }
        for split, pairs in grouped.items()
        for (case_id, rep), arms in pairs.items()
    ]
    return hash_payload(sorted(manifest, key=lambda row: (
        row["split"], row["case_id"], row["rep"], row["design"]
    )))


compute_validation_manifest_hash = validation_manifest_hash


def check_provenance(candidate: dict) -> bool:
    source_ids = candidate.get("source_residual_ids")
    source_hashes = candidate.get("source_residual_hashes")
    prompt = candidate.get("candidate_prompt")
    output = candidate.get("candidate_raw_output")
    return bool(
        isinstance(source_ids, list) and source_ids
        and isinstance(source_hashes, dict) and set(source_hashes) == set(source_ids)
        and all(isinstance(value, str) and len(value) == 64 for value in source_hashes.values())
        and isinstance(prompt, str) and prompt
        and isinstance(output, str) and output
        and candidate.get("candidate_prompt_hash") == sha256_text(prompt)
        and candidate.get("candidate_output_hash") == sha256_text(output)
        and candidate.get("candidate_hash") == candidate_hash(candidate)
        and candidate.get("parent_policy_hash")
        and candidate.get("registry_parent_hash")
    )


def check_trigger_and_edit_scope(candidate: dict) -> bool:
    trigger = candidate.get("trigger") or {}
    policy = candidate.get("action_policy") or {}
    try:
        evidence_k = int(policy.get("evidence_k"))
        n_candidates = int(policy.get("n_candidates"))
    except (TypeError, ValueError):
        return False
    return bool(
        trigger.get("bug_family")
        and policy.get("patch_scope") == "local_block"
        and evidence_k >= 1
        and 1 <= n_candidates <= 64
    )


def check_rollback_artifact(candidate: dict) -> bool:
    rollback = candidate.get("rollback_artifact") or {}
    return bool(
        rollback.get("action") == "restore_previous_registry"
        and rollback.get("previous_registry_hash") == candidate.get("registry_parent_hash")
        and rollback.get("previous_policy_hash") == candidate.get("parent_policy_hash")
    )


def decide_promotion(
    candidate: dict,
    validation_rows: list[dict],
    *,
    parent_policy_hash: str,
    validation_manifest_hash: str,
    min_hits: int,
    tau_r: float,
    epsilon: float,
    min_designs: int = 1,
    min_recovery_ratio: float = 0.0,
    min_gain: float | None = None,
) -> dict:
    if candidate.get("candidate_hash") != candidate_hash(candidate):
        raise FormalProtocolViolation("candidate hash mismatch")
    if candidate.get("parent_policy_hash") != parent_policy_hash:
        raise FormalProtocolViolation("parent policy hash mismatch")
    if candidate.get("validation_manifest_hash") != validation_manifest_hash:
        raise FormalProtocolViolation("validation manifest mismatch")
    canonical, grouped = _paired_validation(validation_rows)
    observed_manifest_hash = compute_validation_manifest_hash(canonical)
    if observed_manifest_hash != validation_manifest_hash:
        raise FormalProtocolViolation("validation rows do not match frozen manifest")
    if min_hits < 0 or min_designs < 0:
        raise FormalProtocolViolation("promotion support thresholds must be non-negative")
    if not 0.0 <= min_recovery_ratio <= 1.0:
        raise FormalProtocolViolation("minimum recovery ratio must be in [0, 1]")
    if epsilon < 0.0:
        raise FormalProtocolViolation("regression tolerance must be non-negative")
    min_gain = tau_r if min_gain is None else min_gain

    def split_metric(split: str) -> tuple[int, int, float, int, int, float]:
        pairs = grouped[split]
        candidate_hits = sum(bool(v["candidate"]["oracle_ok"]) for v in pairs.values())
        baseline_hits = sum(bool(v["baseline"]["oracle_ok"]) for v in pairs.values())
        total = len(pairs)
        return (
            candidate_hits, total, candidate_hits / total,
            baseline_hits, total, baseline_hits / total,
        )

    th, tt, tr, bh, bt, br = split_metric("target")
    nh, nt, nr, bnh, bnt, bnr = split_metric("non_target")
    delta_target = tr - br
    delta_non_target = nr - bnr
    target_pairs = grouped["target"].values()
    validation_hits = sum(bool(v["candidate"]["strategy_hit"]) for v in target_pairs)
    recovered_hits = sum(
        bool(v["candidate"]["strategy_hit"]) and bool(v["candidate"]["oracle_ok"])
        for v in target_pairs
    )
    covered_designs = sorted({
        v["candidate"]["design"] or v["baseline"]["design"]
        for v in target_pairs
        if v["candidate"]["strategy_hit"]
    })
    recovery_ratio = recovered_hits / validation_hits if validation_hits else 0.0
    provenance_pass = check_provenance(candidate)
    scope_pass = check_trigger_and_edit_scope(candidate)
    rollback_pass = check_rollback_artifact(candidate)
    leakage_pass = not bool(candidate.get("leakage_detected"))
    thresholds = {
        "min_validation_hits": min_hits,
        "min_covered_designs": min_designs,
        "min_target_recovery_ratio": min_recovery_ratio,
        "min_target_gain": min_gain,
        "non_target_regression_tolerance": epsilon,
    }
    metrics = {
        "target": [th, tt, tr, bh, bt, br],
        "non_target": [nh, nt, nr, bnh, bnt, bnr],
        "validation_hits": validation_hits,
        "recovered_hits": recovered_hits,
        "covered_designs": covered_designs,
        "thresholds": thresholds,
        "validation_evidence_hash": hash_payload(canonical),
    }
    decision = {
        "schema_version": PROMOTION_SCHEMA,
        "candidate_hash": candidate["candidate_hash"],
        "parent_policy_hash": parent_policy_hash,
        "validation_manifest_hash": validation_manifest_hash,
        "target_hits": th,
        "target_total": tt,
        "target_rate": tr,
        "baseline_target_rate": br,
        "delta_target": delta_target,
        "non_target_hits": nh,
        "non_target_total": nt,
        "delta_non_target": delta_non_target,
        "target_validation_hits": validation_hits,
        "covered_designs": covered_designs,
        "target_recovery_ratio": recovery_ratio,
        "target_gain": delta_target,
        "support_pass": validation_hits >= min_hits,
        "coverage_pass": len(covered_designs) >= min_designs,
        "recovery_pass": recovery_ratio >= min_recovery_ratio,
        "gain_pass": delta_target > min_gain,
        "min_hits_pass": validation_hits >= min_hits,
        "replay_pass": delta_target > min_gain,
        "no_regression_pass": delta_non_target >= -epsilon,
        "leakage_pass": leakage_pass,
        "provenance_pass": provenance_pass,
        "scope_pass": scope_pass,
        "rollback_pass": rollback_pass,
        "safety_pass": provenance_pass and scope_pass and rollback_pass,
        "thresholds": thresholds,
        "validation_evidence": canonical,
        "gate_code_version": PROTOCOL_VERSION,
        "metrics_hash": hash_payload(metrics),
    }
    decision["promote"] = all(decision[k] for k in (
        "support_pass", "coverage_pass", "recovery_pass", "gain_pass",
        "no_regression_pass", "leakage_pass", "safety_pass",
    ))
    decision["decision_hash"] = hash_payload(decision)
    return decision


def validate_decision(candidate: dict, decision: dict, *, parent_policy_hash: str,
                      validation_manifest_hash: str) -> None:
    missing = DECISION_FIELDS - decision.keys()
    if missing:
        raise FormalProtocolViolation(f"decision missing fields: {sorted(missing)}")
    raw = {k: v for k, v in decision.items() if k != "decision_hash"}
    if decision["decision_hash"] != hash_payload(raw):
        raise FormalProtocolViolation("stale/modified decision hash")
    if decision["candidate_hash"] != candidate.get("candidate_hash"):
        raise FormalProtocolViolation("decision candidate mismatch")
    if decision["parent_policy_hash"] != parent_policy_hash:
        raise FormalProtocolViolation("stale parent policy")
    if decision["validation_manifest_hash"] != validation_manifest_hash:
        raise FormalProtocolViolation("stale validation manifest")
    thresholds = decision.get("thresholds") or {}
    expected = decide_promotion(
        candidate,
        decision.get("validation_evidence") or [],
        parent_policy_hash=parent_policy_hash,
        validation_manifest_hash=validation_manifest_hash,
        min_hits=int(thresholds.get("min_validation_hits", -1)),
        min_designs=int(thresholds.get("min_covered_designs", -1)),
        min_recovery_ratio=float(thresholds.get("min_target_recovery_ratio", -1.0)),
        min_gain=float(thresholds.get("min_target_gain", 0.0)),
        tau_r=float(thresholds.get("min_target_gain", 0.0)),
        epsilon=float(thresholds.get("non_target_regression_tolerance", -1.0)),
    )
    if decision != expected:
        raise FormalProtocolViolation("decision gates/metrics do not reconstruct from evidence")


def _registry_version_path(registry_path: Path, registry_hash: str) -> Path:
    return registry_path.parent / f".{registry_path.name}.versions" / f"{registry_hash}.json"


def _save_registry_version(registry_path: Path, registry: dict) -> str:
    registry_hash = hash_payload(registry)
    version_path = _registry_version_path(registry_path, registry_hash)
    if not version_path.exists():
        atomic_write_json(version_path, registry)
    elif hash_payload(json.loads(version_path.read_text())) != registry_hash:
        raise FormalProtocolViolation("stored registry rollback version is corrupted")
    return registry_hash


def _decision_rejection_reasons(decision: dict) -> list[str]:
    gates = {
        "support_pass": "insufficient_validation_hits",
        "coverage_pass": "insufficient_covered_designs",
        "recovery_pass": "insufficient_target_recovery",
        "gain_pass": "insufficient_target_gain",
        "no_regression_pass": "non_target_regression",
        "leakage_pass": "validation_leakage",
        "provenance_pass": "incomplete_provenance",
        "scope_pass": "unsafe_trigger_or_edit_scope",
        "rollback_pass": "missing_rollback_artifact",
    }
    return [reason for gate, reason in gates.items() if not decision.get(gate)]


def commit_registry_atomically(
    registry_path: Path,
    candidate: dict,
    decision: dict,
    *,
    ledger_path: Path | None = None,
) -> tuple[str, str]:
    with _locked(registry_path.with_suffix(registry_path.suffix + ".lock")):
        registry = load_registry(registry_path, formal_mode=True)
        before = hash_payload(registry)
        current_policy = policy_hash(registry)
        if candidate.get("candidate_hash") != candidate_hash(candidate):
            raise FormalProtocolViolation("candidate hash mismatch before commit")
        validate_decision(candidate, decision, parent_policy_hash=current_policy,
                          validation_manifest_hash=candidate["validation_manifest_hash"])
        if candidate.get("registry_parent_hash") != before:
            if ledger_path:
                append_decision_ledger(ledger_path, {
                    "artifact_id": candidate.get("artifact_id"),
                    "decision_hash": decision.get("decision_hash"),
                    "status": "stale-parent-policy",
                    "registry_hash_before": before,
                })
            raise FormalProtocolViolation("registry parent hash mismatch")
        if not decision.get("promote"):
            if ledger_path:
                append_decision_ledger(ledger_path, {
                    "artifact_id": candidate.get("artifact_id"),
                    "decision_hash": decision.get("decision_hash"),
                    "status": "rejected",
                    "reasons": _decision_rejection_reasons(decision),
                    "registry_hash_before": before,
                    "registry_hash_after": before,
                })
            return before, before
        _save_registry_version(registry_path, registry)
        promoted = dict(candidate)
        promoted["promotion_decision_hash"] = decision["decision_hash"]
        promoted["runtime_status"] = "promoted"
        registry["artifacts"].append(promoted)
        atomic_write_json(registry_path, registry)
        after_payload = load_registry(registry_path, formal_mode=True)
        after = hash_payload(after_payload)
        if ledger_path:
            append_decision_ledger(ledger_path, {
                "artifact_id": candidate.get("artifact_id"),
                "decision_hash": decision.get("decision_hash"),
                "status": "promoted",
                "registry_hash_before": before,
                "registry_hash_after": after,
                "policy_hash_after": policy_hash(after_payload),
            })
        return before, after


def rollback_last_promotion(
    registry_path: Path,
    *,
    ledger_path: Path | None = None,
    reason: str = "later-audit-regression",
) -> tuple[str, str]:
    """Restore the exact registry version bound to the newest active strategy."""
    with _locked(registry_path.with_suffix(registry_path.suffix + ".lock")):
        current = load_registry(registry_path, formal_mode=True)
        before = hash_payload(current)
        promoted = [a for a in current["artifacts"] if a.get("runtime_status") == "promoted"]
        if not promoted:
            raise FormalProtocolViolation("no promoted strategy is available to roll back")
        latest = promoted[-1]
        target_hash = (latest.get("rollback_artifact") or {}).get("previous_registry_hash")
        if not target_hash:
            raise FormalProtocolViolation("promoted strategy has no rollback registry hash")
        target_path = _registry_version_path(registry_path, target_hash)
        if not target_path.exists():
            raise FormalProtocolViolation("rollback registry version is missing")
        target = json.loads(target_path.read_text())
        if hash_payload(target) != target_hash:
            raise FormalProtocolViolation("rollback registry version hash mismatch")
        _save_registry_version(registry_path, current)
        atomic_write_json(registry_path, target)
        restored = load_registry(registry_path, formal_mode=True)
        after = hash_payload(restored)
        if ledger_path:
            append_decision_ledger(ledger_path, {
                "artifact_id": latest.get("artifact_id"),
                "status": "rolled-back",
                "reason": reason,
                "registry_hash_before": before,
                "registry_hash_after": after,
                "policy_hash_after": policy_hash(restored),
            })
        return before, after


def rollback_if_audit_regression(
    registry_path: Path,
    audit: dict[str, Any],
    *,
    epsilon: float = 0.0,
    ledger_path: Path | None = None,
) -> tuple[str, str]:
    """Apply Phase 5 rollback when a later audit exceeds the frozen tolerance."""
    if epsilon < 0:
        raise FormalProtocolViolation("regression tolerance must be non-negative")
    delta = audit.get("non_target_delta")
    detected = bool(audit.get("regression_detected"))
    if delta is not None:
        detected = detected or float(delta) < -epsilon
    if not detected:
        current = hash_payload(load_registry(registry_path, formal_mode=True))
        return current, current
    return rollback_last_promotion(
        registry_path,
        ledger_path=ledger_path,
        reason=str(audit.get("reason") or "later-audit-regression"),
    )


def load_promoted_registry(registry_path: Path, decisions: dict[str, dict]) -> tuple[list[dict], str]:
    registry = load_registry(registry_path, formal_mode=True)
    active = []
    prefix = {"schema_version": registry["schema_version"], "artifacts": []}
    for artifact in registry["artifacts"]:
        if artifact.get("runtime_status") != "promoted":
            raise FormalProtocolViolation("formal registry contains a non-promoted artifact")
        expected_registry_parent = hash_payload(prefix)
        expected_policy_parent = policy_hash(prefix)
        if artifact.get("registry_parent_hash") != expected_registry_parent:
            raise FormalProtocolViolation("promoted artifact registry chain is broken")
        if artifact.get("parent_policy_hash") != expected_policy_parent:
            raise FormalProtocolViolation("promoted artifact policy chain is broken")
        decision = decisions.get(artifact.get("promotion_decision_hash"))
        if not decision:
            raise FormalProtocolViolation("promoted artifact has no current decision")
        validate_decision(artifact, decision,
                          parent_policy_hash=expected_policy_parent,
                          validation_manifest_hash=artifact["validation_manifest_hash"])
        active.append(artifact)
        prefix["artifacts"].append(artifact)
    return active, policy_hash(registry)


def activate(case: dict, artifacts: list[dict]) -> tuple[str, list[str]]:
    family = case.get("family") or case.get("bug_family")
    hits = [a for a in artifacts if a.get("trigger", {}).get("bug_family") == family]
    strategy = "\n".join(a["normalized_strategy"] for a in hits)
    return strategy, [a["artifact_id"] for a in hits]


def validate_case_result(row: dict) -> None:
    missing = FORMAL_RESULT_FIELDS - row.keys()
    if missing:
        raise FormalProtocolViolation(f"case result missing fields: {sorted(missing)}")
