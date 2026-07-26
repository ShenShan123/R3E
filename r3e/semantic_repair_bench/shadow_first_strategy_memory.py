"""Algorithm A1: shadow-first, correctness-gated strategy memory.

This module is a fail-closed compatibility layer over the frozen
``r3e-registry-v1`` format.  It deliberately does not rewrite historical
formal artifacts.  New runs get a canonical shadow store, a hash-chained
decision ledger, explicit target/non-target replay gates, compare-and-swap
promotion, per-use policy provenance, and rollback snapshots.

The module contains no model or benchmark-specific code.  A formal runner
must supply the distiller and replay callbacks so the resulting evidence can
be reproduced from its frozen manifests.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from semantic_repair_bench.formal_protocol import (
    FormalProtocolViolation,
    atomic_write_json,
    candidate_hash,
    canonical_json,
    hash_file,
    hash_payload,
    load_registry,
    policy_hash,
    utc_now,
)


A1_SCHEMA = "r3e-shadow-first-strategy-memory-v1"
LEDGER_SCHEMA = "r3e-strategy-decision-ledger-v1"
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")

CANONICAL_TRAJECTORY_FIELDS = {
    "trajectory_id",
    "case_id",
    "design_id",
    "red_generation",
    "seed",
    "effect",
    "bug_type",
    "scope",
    "failure_signature",
    "repair_attempt_summary",
    "red_trace_hash",
    "buggy_rtl_hash",
    "repair_patch_hash",
    "prompt_hash",
    "response_hash",
    "evaluator_manifest_hash",
    "artifact_manifest_hash",
    "parent_policy_hash",
    "compile_ok",
    "simulation_ok",
    "oracle_ok",
    "rebuild_command",
}


@dataclass(frozen=True)
class PromotionThresholds:
    """Frozen thresholds used by one A1 run."""

    min_validation_hits: int
    min_covered_designs: int
    min_target_recovery_ratio: float
    min_target_gain: float
    non_target_regression_tolerance: float

    def __post_init__(self) -> None:
        if self.min_validation_hits < 1 or self.min_covered_designs < 1:
            raise ValueError("support and design thresholds must be positive")
        if not 0.0 <= self.min_target_recovery_ratio <= 1.0:
            raise ValueError("target recovery ratio must be in [0, 1]")
        if self.min_target_gain < 0.0:
            raise ValueError("target gain must be non-negative")
        if self.non_target_regression_tolerance < 0.0:
            raise ValueError("regression tolerance must be non-negative")

    def as_dict(self) -> dict[str, Any]:
        return {
            "min_validation_hits": self.min_validation_hits,
            "min_covered_designs": self.min_covered_designs,
            "min_target_recovery_ratio": self.min_target_recovery_ratio,
            "min_target_gain": self.min_target_gain,
            "non_target_regression_tolerance": self.non_target_regression_tolerance,
        }


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(HEX_SHA256.fullmatch(value))


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line_no, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FormalProtocolViolation(f"invalid JSONL at {path}:{line_no}") from exc
        if not isinstance(row, dict):
            raise FormalProtocolViolation(f"non-object JSONL row at {path}:{line_no}")
        rows.append(row)
    return rows


def _append_jsonl_fsync(path: Path, row: dict) -> None:
    """Append one complete record while holding an advisory process lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(canonical_json(row) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _exclusive_file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def canonicalize_trajectory(trajectory: dict) -> dict:
    """Return the stable, minimum A1 representation of a repair trajectory."""
    aliases = {
        "trajectory_id": ("trajectory_id", "residual_id", "record_id"),
        "bug_type": ("bug_type", "family", "bug_family", "mutation_type"),
        "scope": ("scope", "patch_scope", "edit_scope"),
        "design_id": ("design_id", "design"),
    }
    normalized = dict(trajectory)
    for destination, sources in aliases.items():
        if normalized.get(destination) not in (None, ""):
            continue
        for source in sources:
            if trajectory.get(source) not in (None, ""):
                normalized[destination] = trajectory[source]
                break
    entry = {field: normalized.get(field) for field in sorted(CANONICAL_TRAJECTORY_FIELDS)}
    entry["schema_version"] = A1_SCHEMA
    entry["runtime_status"] = "inactive"
    entry["canonicalized_at"] = utc_now()
    entry["trajectory_hash"] = hash_payload({
        key: value for key, value in entry.items()
        if key not in {"canonicalized_at", "trajectory_hash"}
    })
    return entry


def has_rebuildable_outcome(entry: dict) -> tuple[bool, list[str]]:
    missing = []
    for field in (
        "buggy_rtl_hash", "repair_patch_hash", "evaluator_manifest_hash",
        "artifact_manifest_hash",
    ):
        if not _is_sha256(entry.get(field)):
            missing.append(field)
    for field in ("compile_ok", "simulation_ok", "oracle_ok"):
        if not isinstance(entry.get(field), bool):
            missing.append(field)
    if not isinstance(entry.get("rebuild_command"), str) or not entry["rebuild_command"].strip():
        missing.append("rebuild_command")
    return not missing, sorted(missing)


def has_complete_provenance(entry: dict) -> tuple[bool, list[str]]:
    missing = []
    for field in (
        "trajectory_id", "case_id", "design_id", "red_generation", "seed",
        "effect", "bug_type", "scope",
        "failure_signature", "repair_attempt_summary",
    ):
        if entry.get(field) in (None, ""):
            missing.append(field)
    for field in ("red_trace_hash", "prompt_hash", "response_hash", "parent_policy_hash"):
        if not _is_sha256(entry.get(field)):
            missing.append(field)
    return not missing, sorted(missing)


class ShadowFirstStrategyMemory:
    """Persistent implementation of Algorithm A1.

    ``target_replay`` and ``non_target_replay`` are frozen case descriptors.
    Their hashes, and the disjointness check, are bound into every candidate.
    """

    def __init__(
        self,
        root: Path,
        active_registry_path: Path,
        target_replay: Sequence[dict],
        non_target_replay: Sequence[dict],
        thresholds: PromotionThresholds,
        allowed_edit_scopes: Sequence[str] = ("local_block",),
    ) -> None:
        self.root = Path(root)
        self.registry_path = Path(active_registry_path)
        self.shadow_path = self.root / "shadow_trajectories.jsonl"
        self.candidates_path = self.root / "candidate_strategies.jsonl"
        self.ledger_path = self.root / "decision_ledger.jsonl"
        self.runtime_uses_path = self.root / "runtime_policy_uses.jsonl"
        self.rollback_dir = self.root / "rollback"
        self.target_replay = list(target_replay)
        self.non_target_replay = list(non_target_replay)
        self.thresholds = thresholds
        self.allowed_edit_scopes = frozenset(str(scope) for scope in allowed_edit_scopes)
        if not self.allowed_edit_scopes:
            raise ValueError("at least one edit scope must be allowed")
        self.target_manifest_hash = hash_payload(self.target_replay)
        self.non_target_manifest_hash = hash_payload(self.non_target_replay)
        self._assert_disjoint_replay_sets()
        load_registry(self.registry_path, formal_mode=True)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _case_keys(rows: Sequence[dict]) -> set[str]:
        keys = set()
        for row in rows:
            case_id = row.get("case_id")
            if not case_id:
                raise FormalProtocolViolation("replay manifest row missing case_id")
            keys.add(str(case_id))
        return keys

    def _assert_disjoint_replay_sets(self) -> None:
        overlap = self._case_keys(self.target_replay) & self._case_keys(self.non_target_replay)
        if overlap:
            raise FormalProtocolViolation(
                f"target/non-target replay overlap: {sorted(overlap)}"
            )

    def _log(self, entity: dict, event: str, **details: Any) -> dict:
        with _exclusive_file_lock(self.ledger_path.with_suffix(".lock")):
            previous = _read_jsonl(self.ledger_path)
            previous_hash = previous[-1]["ledger_entry_hash"] if previous else "0" * 64
            row = {
                "schema_version": LEDGER_SCHEMA,
                "sequence": len(previous) + 1,
                "timestamp": utc_now(),
                "event": event,
                "entity_id": entity.get("artifact_id") or entity.get("trajectory_id") or "registry",
                "entity_hash": entity.get("candidate_hash") or entity.get("trajectory_hash")
                               or entity.get("policy_hash") or hash_payload(entity),
                "previous_ledger_entry_hash": previous_hash,
                "details": details,
            }
            row["ledger_entry_hash"] = hash_payload(row)
            _append_jsonl_fsync(self.ledger_path, row)
        return row

    def audit_ledger(self) -> list[dict]:
        """Verify the append-only hash chain and return its records."""
        rows = _read_jsonl(self.ledger_path)
        previous_hash = "0" * 64
        for expected_sequence, row in enumerate(rows, 1):
            if row.get("sequence") != expected_sequence:
                raise FormalProtocolViolation("decision ledger sequence gap")
            if row.get("previous_ledger_entry_hash") != previous_hash:
                raise FormalProtocolViolation("decision ledger chain mismatch")
            raw = {key: value for key, value in row.items() if key != "ledger_entry_hash"}
            if row.get("ledger_entry_hash") != hash_payload(raw):
                raise FormalProtocolViolation("decision ledger entry hash mismatch")
            previous_hash = row["ledger_entry_hash"]
        return rows

    def record_event(self, event: str, entity: dict, **details: Any) -> dict:
        """Add a runner-level lifecycle event to the same auditable ledger."""
        return self._log(entity, event, **details)

    def ingest(self, trajectories: Iterable[dict]) -> list[dict]:
        accepted = []
        existing_ids = {row.get("trajectory_id") for row in _read_jsonl(self.shadow_path)}
        for trajectory in trajectories:
            entry = canonicalize_trajectory(trajectory)
            outcome_ok, outcome_missing = has_rebuildable_outcome(entry)
            provenance_ok, provenance_missing = has_complete_provenance(entry)
            if not outcome_ok or not provenance_ok:
                self._log(
                    entry,
                    "trajectory-rejected",
                    outcome_ok=outcome_ok,
                    provenance_ok=provenance_ok,
                    missing_outcome_fields=outcome_missing,
                    missing_provenance_fields=provenance_missing,
                )
                continue
            if entry["trajectory_id"] in existing_ids:
                self._log(entry, "trajectory-duplicate")
                continue
            _append_jsonl_fsync(self.shadow_path, entry)
            existing_ids.add(entry["trajectory_id"])
            accepted.append(entry)
            self._log(entry, "shadow-recorded", runtime_status="inactive")
        return accepted

    @staticmethod
    def group_shadow(entries: Iterable[dict]) -> list[list[dict]]:
        groups: dict[tuple[str, str, str], list[dict]] = {}
        for entry in entries:
            key = (str(entry["effect"]), str(entry["bug_type"]), str(entry["scope"]))
            groups.setdefault(key, []).append(entry)
        return [groups[key] for key in sorted(groups)]

    def _make_rollback_snapshot(self, registry: dict) -> dict:
        registry_hash = hash_payload(registry)
        path = self.rollback_dir / f"registry_{registry_hash}.json"
        if path.exists():
            if hash_payload(json.loads(path.read_text())) != registry_hash:
                raise FormalProtocolViolation("rollback snapshot collision")
        else:
            atomic_write_json(path, registry)
        return {
            "path": str(path.resolve()),
            "registry_hash": registry_hash,
            "file_sha256": hash_file(path),
        }

    def distill(
        self,
        entries: Iterable[dict],
        distiller: Callable[[dict, list[dict]], str],
    ) -> list[dict]:
        registry = load_registry(self.registry_path, formal_mode=True)
        parent_policy = policy_hash(registry)
        registry_parent = hash_payload(registry)
        rollback = self._make_rollback_snapshot(registry)
        existing = _read_jsonl(self.candidates_path)
        fingerprints = {
            hash_payload({"trigger": row.get("trigger"), "strategy": row.get("normalized_strategy")})
            for row in existing
        }
        candidates = []
        for group in self.group_shadow(entries):
            trigger = {
                "effect": group[0]["effect"],
                "bug_type": group[0]["bug_type"],
                "scope": group[0]["scope"],
            }
            prompt_payload = {
                "task": "distill a generic RTL repair strategy from red-discovered traces",
                "trigger": trigger,
                "source_trajectories": [
                    {
                        "red_trace_hash": row["red_trace_hash"],
                        "repair_patch_hash": row["repair_patch_hash"],
                        "oracle_ok": row["oracle_ok"],
                        "failure_signature": row["failure_signature"],
                        "repair_attempt_summary": row["repair_attempt_summary"],
                    }
                    for row in sorted(group, key=lambda item: item["trajectory_id"])
                ],
            }
            prompt = canonical_json(prompt_payload)
            raw = str(distiller(prompt_payload, group))
            normalized = " ".join(raw.split())
            if not normalized:
                self._log({"trajectory_id": group[0]["trajectory_id"]}, "candidate-rejected",
                          reason="empty-distillation")
                continue
            fingerprint = hash_payload({"trigger": trigger, "strategy": normalized})
            if fingerprint in fingerprints:
                self._log({"trajectory_id": group[0]["trajectory_id"]}, "candidate-deduplicated",
                          fingerprint=fingerprint)
                continue
            forbidden_identifiers = sorted({
                str(row[field])
                for row in group
                for field in ("trajectory_id", "case_id", "design_id")
                if row.get(field)
            })
            candidate = {
                "artifact_id": f"auto_{trigger['bug_type']}_{fingerprint[:12]}",
                "origin": "automatic",
                "generation_mode": "distilled",
                "source_trajectory_ids": sorted(row["trajectory_id"] for row in group),
                # Kept for compatibility with r3e-registry-v1 readers.
                "source_residual_ids": sorted(row["trajectory_id"] for row in group),
                "source_residual_hashes": {
                    str(row["trajectory_id"]): str(row["trajectory_hash"])
                    for row in sorted(group, key=lambda item: item["trajectory_id"])
                },
                "candidate_prompt": prompt,
                "candidate_prompt_hash": hash_payload(prompt_payload),
                "candidate_raw_output": raw,
                "candidate_output_hash": hash_payload(raw),
                "normalized_strategy": normalized,
                "trigger": trigger,
                "action_policy": {"patch_scope": trigger["scope"]},
                "parent_policy_hash": parent_policy,
                "registry_parent_hash": registry_parent,
                "validation_manifest_hash": hash_payload({
                    "target": self.target_manifest_hash,
                    "non_target": self.non_target_manifest_hash,
                }),
                "target_manifest_hash": self.target_manifest_hash,
                "non_target_manifest_hash": self.non_target_manifest_hash,
                "rollback_artifact": rollback,
                "promotion_decision_hash": "",
                "created_at": utc_now(),
                "runtime_status": "candidate",
                "leakage_detected": any(
                    re.search(
                        rf"(?<![A-Za-z0-9_]){re.escape(identifier)}(?![A-Za-z0-9_])",
                        normalized,
                    )
                    for identifier in forbidden_identifiers
                ),
                "leakage_audit_hash": hash_payload(forbidden_identifiers),
            }
            candidate["candidate_hash"] = candidate_hash(candidate)
            _append_jsonl_fsync(self.candidates_path, candidate)
            fingerprints.add(fingerprint)
            candidates.append(candidate)
            self._log(candidate, "candidate-distilled", parent_policy_hash=parent_policy)
        return candidates

    @staticmethod
    def _paired(rows: Sequence[dict], split: str) -> list[tuple[dict, dict]]:
        selected = [row for row in rows if row.get("split") == split]
        by_arm: dict[str, dict[tuple[str, Any], dict]] = {"baseline": {}, "candidate": {}}
        for row in selected:
            arm = row.get("arm")
            if arm not in by_arm:
                raise FormalProtocolViolation(f"unknown replay arm: {arm}")
            if not row.get("case_id") or not row.get("design_id"):
                raise FormalProtocolViolation("replay row missing case_id/design_id")
            key = (str(row["case_id"]), row.get("seed"))
            if key in by_arm[arm]:
                raise FormalProtocolViolation(f"duplicate replay row: {split}/{arm}/{key}")
            if not isinstance(row.get("oracle_ok"), bool):
                raise FormalProtocolViolation("replay row oracle_ok must be bool")
            by_arm[arm][key] = row
        if not by_arm["baseline"] or by_arm["baseline"].keys() != by_arm["candidate"].keys():
            raise FormalProtocolViolation(f"unpaired or empty {split} replay")
        return [(by_arm["baseline"][key], by_arm["candidate"][key])
                for key in sorted(by_arm["baseline"])]

    def decide(self, candidate: dict, replay_rows: Sequence[dict]) -> dict:
        if candidate.get("candidate_hash") != candidate_hash(candidate):
            raise FormalProtocolViolation("candidate hash mismatch")
        if candidate.get("target_manifest_hash") != self.target_manifest_hash:
            raise FormalProtocolViolation("target replay manifest mismatch")
        if candidate.get("non_target_manifest_hash") != self.non_target_manifest_hash:
            raise FormalProtocolViolation("non-target replay manifest mismatch")

        target = self._paired(replay_rows, "target")
        non_target = self._paired(replay_rows, "non_target")
        observed_target_ids = {base["case_id"] for base, _ in target}
        observed_non_target_ids = {base["case_id"] for base, _ in non_target}
        if observed_target_ids != self._case_keys(self.target_replay):
            raise FormalProtocolViolation("target replay rows do not reconstruct frozen manifest")
        if observed_non_target_ids != self._case_keys(self.non_target_replay):
            raise FormalProtocolViolation("non-target replay rows do not reconstruct frozen manifest")
        recovered = [(base, cand) for base, cand in target
                     if not base["oracle_ok"] and cand["oracle_ok"]]
        baseline_failures = sum(not base["oracle_ok"] for base, _ in target)
        target_baseline_hits = sum(base["oracle_ok"] for base, _ in target)
        target_candidate_hits = sum(cand["oracle_ok"] for _, cand in target)
        non_target_baseline_hits = sum(base["oracle_ok"] for base, _ in non_target)
        non_target_candidate_hits = sum(cand["oracle_ok"] for _, cand in non_target)
        recovery_ratio = len(recovered) / baseline_failures if baseline_failures else 0.0
        target_gain = (target_candidate_hits - target_baseline_hits) / len(target)
        non_target_delta = (non_target_candidate_hits - non_target_baseline_hits) / len(non_target)
        covered_designs = len({cand["design_id"] for _, cand in recovered})

        rollback = candidate.get("rollback_artifact", {})
        rollback_path = Path(rollback.get("path", ""))
        shadow_ids = {row.get("trajectory_id") for row in _read_jsonl(self.shadow_path)}
        source_ids = candidate.get("source_trajectory_ids", [])
        provenance_ok = (
            candidate.get("origin") == "automatic"
            and candidate.get("generation_mode") == "distilled"
            and bool(source_ids)
            and set(source_ids) <= shadow_ids
            and _is_sha256(candidate.get("parent_policy_hash"))
            and candidate.get("candidate_prompt_hash")
                == hash_payload(json.loads(candidate.get("candidate_prompt", "null")))
            and candidate.get("candidate_output_hash")
                == hash_payload(candidate.get("candidate_raw_output"))
        )
        scope_ok = (
            candidate.get("trigger", {}).get("scope")
            == candidate.get("action_policy", {}).get("patch_scope")
            and candidate.get("action_policy", {}).get("patch_scope") in self.allowed_edit_scopes
            and not candidate.get("leakage_detected", False)
        )
        rollback_ok = (
            rollback_path.is_file()
            and _is_sha256(rollback.get("registry_hash"))
            and _is_sha256(rollback.get("file_sha256"))
            and hash_file(rollback_path) == rollback.get("file_sha256")
            and rollback.get("registry_hash") == candidate.get("registry_parent_hash")
        )
        checks = {
            "support_ok": len(recovered) >= self.thresholds.min_validation_hits,
            "coverage_ok": covered_designs >= self.thresholds.min_covered_designs,
            "recovery_ok": recovery_ratio >= self.thresholds.min_target_recovery_ratio,
            "gain_ok": target_gain > self.thresholds.min_target_gain,
            "regression_ok": non_target_delta >= -self.thresholds.non_target_regression_tolerance,
            "provenance_ok": provenance_ok,
            "scope_ok": scope_ok,
            "rollback_ok": rollback_ok,
        }
        metrics = {
            "validation_hits": len(recovered),
            "covered_designs": covered_designs,
            "baseline_target_failures": baseline_failures,
            "target_recovery_ratio": recovery_ratio,
            "target_gain": target_gain,
            "non_target_delta": non_target_delta,
            "target_total": len(target),
            "non_target_total": len(non_target),
        }
        decision = {
            "schema_version": A1_SCHEMA,
            "candidate_hash": candidate["candidate_hash"],
            "parent_policy_hash": candidate["parent_policy_hash"],
            "registry_parent_hash": candidate["registry_parent_hash"],
            "target_manifest_hash": self.target_manifest_hash,
            "non_target_manifest_hash": self.non_target_manifest_hash,
            "thresholds": self.thresholds.as_dict(),
            "metrics": metrics,
            "checks": checks,
            "eligible": all(checks.values()),
            "rejection_reasons": sorted(key for key, value in checks.items() if not value),
            "decided_at": utc_now(),
        }
        decision["metrics_hash"] = hash_payload(metrics)
        decision["decision_hash"] = hash_payload(decision)
        self._log(candidate, "promotion-eligible" if decision["eligible"] else "promotion-rejected",
                  decision=decision)
        return decision

    def promote(self, candidate: dict, decision: dict) -> tuple[str, str]:
        if decision.get("decision_hash") != hash_payload({
            key: value for key, value in decision.items() if key != "decision_hash"
        }):
            raise FormalProtocolViolation("decision hash mismatch")
        if decision.get("candidate_hash") != candidate.get("candidate_hash"):
            raise FormalProtocolViolation("decision candidate mismatch")
        if not decision.get("eligible"):
            self._log(candidate, "kept-inactive", rejection_reasons=decision.get("rejection_reasons", []))
            current = load_registry(self.registry_path, formal_mode=True)
            current_hash = hash_payload(current)
            return current_hash, current_hash

        with _exclusive_file_lock(self.registry_path.with_suffix(".lock")):
            registry = load_registry(self.registry_path, formal_mode=True)
            before_registry_hash = hash_payload(registry)
            before_policy_hash = policy_hash(registry)
            if candidate.get("parent_policy_hash") != before_policy_hash:
                self._log(candidate, "stale-parent-policy", expected=before_policy_hash,
                          recorded=candidate.get("parent_policy_hash"))
                return before_registry_hash, before_registry_hash
            if candidate.get("registry_parent_hash") != before_registry_hash:
                self._log(candidate, "stale-parent-registry", expected=before_registry_hash,
                          recorded=candidate.get("registry_parent_hash"))
                return before_registry_hash, before_registry_hash

            promoted = dict(candidate)
            promoted["promotion_decision_hash"] = decision["decision_hash"]
            promoted["runtime_status"] = "promoted"
            registry["artifacts"].append(promoted)
            atomic_write_json(self.registry_path, registry)
            persisted = load_registry(self.registry_path, formal_mode=True)
            after_registry_hash = hash_payload(persisted)
            after_policy_hash = policy_hash(persisted)
        self._log(candidate, "promoted", decision_hash=decision["decision_hash"],
                  registry_hash_before=before_registry_hash,
                  registry_hash_after=after_registry_hash,
                  policy_hash_before=before_policy_hash,
                  policy_hash_after=after_policy_hash)
        return before_registry_hash, after_registry_hash

    def keep_inactive(self, candidate: dict, decision: dict, *, reason: str) -> None:
        """Record an experimental-arm decision that deliberately cannot activate."""
        if decision.get("candidate_hash") != candidate.get("candidate_hash"):
            raise FormalProtocolViolation("decision candidate mismatch")
        self._log(candidate, "kept-inactive", reason=reason,
                  eligible=bool(decision.get("eligible")),
                  decision_hash=decision.get("decision_hash"),
                  rejection_reasons=decision.get("rejection_reasons", []))

    def load_active(self) -> tuple[list[dict], str]:
        registry = load_registry(self.registry_path, formal_mode=True)
        active = [row for row in registry["artifacts"] if row.get("runtime_status") == "promoted"]
        ledger = self.audit_ledger()
        promoted_decisions = {
            row.get("details", {}).get("decision_hash")
            for row in ledger if row.get("event") == "promoted"
        }
        for artifact in active:
            if not artifact.get("promotion_decision_hash"):
                raise FormalProtocolViolation("active artifact missing promotion decision")
            if artifact["promotion_decision_hash"] not in promoted_decisions:
                raise FormalProtocolViolation("active artifact decision absent from ledger")
        return active, policy_hash(registry)

    def record_runtime_use(self, use_id: str, activated_artifact_ids: Sequence[str]) -> dict:
        active, current_policy_hash = self.load_active()
        active_ids = {row["artifact_id"] for row in active}
        requested = set(activated_artifact_ids)
        if not requested <= active_ids:
            raise FormalProtocolViolation("runtime attempted to use inactive strategy")
        row = {
            "schema_version": A1_SCHEMA,
            "use_id": use_id,
            "activated_artifact_ids": sorted(requested),
            "policy_hash": current_policy_hash,
            "used_at": utc_now(),
        }
        row["use_hash"] = hash_payload(row)
        _append_jsonl_fsync(self.runtime_uses_path, row)
        return row

    def rollback_if_regressed(self, later_audit_rows: Sequence[dict]) -> bool:
        """Restore the most recent pre-promotion registry when audit regresses."""
        pairs = self._paired(later_audit_rows, "non_target")
        baseline = sum(base["oracle_ok"] for base, _ in pairs) / len(pairs)
        candidate = sum(cand["oracle_ok"] for _, cand in pairs) / len(pairs)
        delta = candidate - baseline
        if delta >= -self.thresholds.non_target_regression_tolerance:
            self._log({"policy_hash": self.load_active()[1]}, "later-audit-passed", delta=delta)
            return False

        active, current_policy_hash = self.load_active()
        if not active:
            raise FormalProtocolViolation("regression detected without active strategy")
        rollback = active[-1].get("rollback_artifact", {})
        snapshot = Path(rollback.get("path", ""))
        if not snapshot.is_file() or hash_file(snapshot) != rollback.get("file_sha256"):
            raise FormalProtocolViolation("rollback artifact missing or modified")
        restored = json.loads(snapshot.read_text())
        if hash_payload(restored) != rollback.get("registry_hash"):
            raise FormalProtocolViolation("rollback registry hash mismatch")
        with _exclusive_file_lock(self.registry_path.with_suffix(".lock")):
            current = load_registry(self.registry_path, formal_mode=True)
            if policy_hash(current) != current_policy_hash:
                raise FormalProtocolViolation("policy changed before rollback")
            atomic_write_json(self.registry_path, restored)
            restored_policy_hash = policy_hash(load_registry(self.registry_path, formal_mode=True))
        self._log({"policy_hash": restored_policy_hash}, "rolled-back", delta=delta,
                  policy_hash_before=current_policy_hash,
                  policy_hash_after=restored_policy_hash,
                  rollback_artifact=rollback)
        return True

    def run(
        self,
        trajectories: Iterable[dict],
        distiller: Callable[[dict, list[dict]], str],
        replay: Callable[[dict, Sequence[dict], Sequence[dict]], Sequence[dict]],
    ) -> tuple[list[dict], list[dict]]:
        """Execute Phases 1--4. Runtime loading/rollback remain explicit."""
        accepted = self.ingest(trajectories)
        candidates = self.distill(accepted, distiller)
        decisions = []
        for candidate in candidates:
            decision = self.decide(
                candidate,
                replay(candidate, self.target_replay, self.non_target_replay),
            )
            self.promote(candidate, decision)
            decisions.append(decision)
        return candidates, decisions
