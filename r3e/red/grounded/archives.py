"""Append-only valid/residual/covered/rejected Grounded Red archives."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload
from r3e.protocol.ledger import append_ledger, read_ledger, writer_lock

from .admission import (
    ADMISSION_DECISION_GROUNDED_SCHEMA_VERSION,
    verify_grounded_admission_decision,
)
from .difficulty import verify_difficulty_profile
from .lineage import validate_lineage_graph, verify_lineage
from .mutation_plan import verify_mutation_plan
from .registry import GroundedRegistryBundle


ARCHIVE_ENTRY_SCHEMA_VERSION = "r3e-grounded-red-archive-entry-v1"
ARCHIVE_KINDS = {"valid", "residual", "covered", "rejected"}


class GroundedArchiveViolation(RuntimeError):
    """Raised when an archive entry lacks admission or lineage authority."""


class GroundedRedArchive:
    def __init__(
        self,
        root: str | Path,
        *,
        require_grounded_execution: bool = True,
    ):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.require_grounded_execution = bool(
            require_grounded_execution
        )

    def _path(self, kind: str) -> Path:
        if kind not in ARCHIVE_KINDS:
            raise GroundedArchiveViolation(f"unknown archive kind: {kind}")
        return self.root / f"{kind}.jsonl"

    def load(self, kind: str | None = None) -> list[dict[str, Any]]:
        if kind is not None and kind not in ARCHIVE_KINDS:
            raise GroundedArchiveViolation(f"unknown archive kind: {kind}")
        paths = [self._path(value) for value in sorted(ARCHIVE_KINDS)]
        rows = []
        for path in paths:
            rows.extend(read_ledger(path))
        for row in rows:
            if row.get("schema_version") != ARCHIVE_ENTRY_SCHEMA_VERSION:
                raise GroundedArchiveViolation("archive entry schema mismatch")
            if row.get("entry_hash") != hash_payload({
                key: value for key, value in row.items() if key != "entry_hash"
                and key not in {
                    "timestamp",
                    "ledger_index",
                    "previous_entry_hash",
                    "ledger_entry_hash",
                }
            }):
                raise GroundedArchiveViolation("archive entry hash mismatch")
        lineage_by_poison: dict[str, dict[str, Any]] = {}
        for row in rows:
            poison_id = str(row["poison_id"])
            lineage = row["lineage"]
            if (
                poison_id in lineage_by_poison
                and lineage_by_poison[poison_id] != lineage
            ):
                raise GroundedArchiveViolation(
                    "archive copies disagree on poison lineage"
                )
            lineage_by_poison[poison_id] = lineage
        try:
            validate_lineage_graph(list(lineage_by_poison.values()))
        except RuntimeError as exc:
            raise GroundedArchiveViolation(str(exc)) from exc
        return [
            row for row in rows
            if kind is None or row["archive_kind"] == kind
        ]

    def add(
        self,
        *,
        kind: str,
        poison_id: str,
        archived_round_id: str,
        policy: PolicyState,
        registries: GroundedRegistryBundle,
        plan: Mapping[str, Any],
        evidence: Mapping[str, Any],
        admission_decision: Mapping[str, Any],
        difficulty_profile: Mapping[str, Any],
        lineage: Mapping[str, Any],
    ) -> dict[str, Any]:
        if kind not in ARCHIVE_KINDS:
            raise GroundedArchiveViolation(f"unknown archive kind: {kind}")
        verified_plan = verify_mutation_plan(
            plan, policy=policy, registries=registries
        )
        decision = verify_grounded_admission_decision(
            admission_decision,
            plan=verified_plan,
            policy=policy,
            registries=registries,
            evidence=evidence,
        )
        if (
            self.require_grounded_execution
            and decision["schema_version"]
            != ADMISSION_DECISION_GROUNDED_SCHEMA_VERSION
        ):
            raise GroundedArchiveViolation(
                "formal archive requires runner-owned grounded execution"
            )
        if kind == "rejected" and decision["admitted"]:
            raise GroundedArchiveViolation(
                "admitted poison cannot enter rejected archive"
            )
        if kind != "rejected" and not decision["admitted"]:
            raise GroundedArchiveViolation(
                "unadmitted poison cannot enter formal bug archives"
            )
        profile = verify_difficulty_profile(difficulty_profile)
        verified_lineage = verify_lineage(lineage)
        if verified_lineage["poison_id"] != poison_id:
            raise GroundedArchiveViolation("archive poison/lineage id mismatch")
        semantic = evidence["semantic_diff"]
        effect = evidence["runtime_effect"]
        novelty_signature = hash_payload({
            "family_id": verified_plan["family_id"],
            "operator_id": verified_plan["operator_id"],
            "normalized_ast_diff_hash": semantic["normalized_ast_diff_hash"],
            "runtime_effect_id": effect["effect_id"],
            "failure_signature": effect["failure_signature"],
            "design_id": verified_plan["target_design"],
            "difficulty_band": profile["difficulty_band"],
        })
        row = {
            "schema_version": ARCHIVE_ENTRY_SCHEMA_VERSION,
            "archive_kind": kind,
            "poison_id": poison_id,
            "archived_round_id": archived_round_id,
            "challenged_policy_instance_hash": policy.policy_instance_hash,
            "challenged_effective_policy_hash": policy.effective_policy_hash,
            "plan": deepcopy(verified_plan),
            "evidence": deepcopy(dict(evidence)),
            "admission_decision": deepcopy(decision),
            "difficulty_profile": deepcopy(profile),
            "lineage": deepcopy(verified_lineage),
            "novelty_signature": novelty_signature,
        }
        with writer_lock(self.root / ".grounded-red-archive.lock"):
            existing = self.load()
            duplicate = next(
                (
                    item for item in existing
                    if item["archive_kind"] == kind
                    and item["novelty_signature"] == novelty_signature
                    and item["challenged_effective_policy_hash"]
                    == policy.effective_policy_hash
                ),
                None,
            )
            if duplicate:
                return duplicate
            parent_ids = set(verified_lineage["parent_poison_ids"])
            known_ids = {item["poison_id"] for item in existing}
            if parent_ids - known_ids:
                raise GroundedArchiveViolation(
                    "archive lineage parent is unavailable"
                )
            lineages = {
                item["poison_id"]: item["lineage"] for item in existing
            }
            lineages[poison_id] = verified_lineage
            try:
                validate_lineage_graph(list(lineages.values()))
            except RuntimeError as exc:
                raise GroundedArchiveViolation(str(exc)) from exc
            row["entry_hash"] = hash_payload(row)
            append_ledger(self._path(kind), row)
        return row
