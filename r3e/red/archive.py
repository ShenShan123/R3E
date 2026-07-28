"""Persistent multi-elite residual archive."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from r3e.protocol.hashing import canonical_json, hash_payload, utc_now
from r3e.protocol.ledger import writer_lock

from .novelty import archive_cell, descriptor
from .lineage import validate_lineage_graph
from .selection import materialize_elites
from .validity import verify_validity_record
from .grounded.arena_validity import (
    ARENA_GROUNDED_AUTHORITY,
    GroundedArenaValidityViolation,
    verify_grounded_arena_validity,
)


class ArchiveViolation(RuntimeError):
    """Raised for invalid or non-policy-bound archive entries."""


def load_archive(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows = [
        json.loads(line)
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for row in rows:
        body = {key: value for key, value in row.items() if key != "archive_entry_hash"}
        if row.get("archive_entry_hash") != hash_payload(body):
            raise ArchiveViolation(f"archive entry hash mismatch: {row.get('poison_id')}")
    try:
        validate_lineage_graph(rows)
    except ValueError as exc:
        raise ArchiveViolation(str(exc)) from exc
    return rows


def update_archive(
    path: str | Path,
    poison: dict[str, Any],
    *,
    archive_kind: str = "residual",
) -> dict[str, Any]:
    if archive_kind not in {"residual", "covered"}:
        raise ArchiveViolation(f"unsupported archive kind: {archive_kind}")
    if not poison.get("validity", {}).get("proven_valid"):
        raise ArchiveViolation("invalid poison cannot enter residual archive")
    validity = poison.get("validity") or {}
    authority_mode = str(
        validity.get("evidence", {}).get("authority_mode") or ""
    )
    if authority_mode == ARENA_GROUNDED_AUTHORITY:
        try:
            verify_grounded_arena_validity(
                validity,
                authority_bundle=poison.get(
                    "grounded_authority_bundle"
                ),
            )
        except GroundedArenaValidityViolation as exc:
            raise ArchiveViolation(str(exc)) from exc
    else:
        formal_status = str(
            validity.get("evidence", {}).get("formal_status")
            or poison.get("formal_status")
            or ""
        )
        if formal_status != "PROVEN_NON_EQUIV":
            raise ArchiveViolation(
                "inconclusive formal oracle cannot enter residual archive"
            )
        try:
            verify_validity_record(validity)
        except ValueError as exc:
            raise ArchiveViolation(str(exc)) from exc
    if not poison.get("challenged_policy_hash"):
        raise ArchiveViolation("archive poison must bind challenged policy hash")
    hardness_class = str(poison.get("hardness_class") or "")
    if archive_kind == "residual":
        if hardness_class and hardness_class not in {
            "hard_residual",
            "borderline_residual",
        }:
            raise ArchiveViolation("covered poison cannot enter residual archive")
        learnability = poison.get("learnability") or {}
        label = (
            str(learnability.get("label") or "")
            if isinstance(learnability, dict)
            else str(learnability)
        )
        if label not in {"reachable", "weakly_reachable"}:
            raise ArchiveViolation("only reachable poison enters adaptation archive")
    elif hardness_class not in {"mostly_covered", "covered"}:
        raise ArchiveViolation("residual poison cannot enter covered archive")
    row = dict(poison)
    row["archive_kind"] = archive_kind
    row["descriptor"] = descriptor(row)
    row["archive_cell"] = archive_cell(row)
    row.setdefault("archived_at", utc_now())
    target = Path(path)
    with writer_lock(target.with_suffix(target.suffix + ".lock")):
        existing = load_archive(target)
        duplicate = next(
            (
                item for item in existing
                if item.get("challenged_policy_hash") == row["challenged_policy_hash"]
                and item.get("normalized_diff_hash") == row.get("normalized_diff_hash")
                and item.get("failure_signature") == row.get("failure_signature")
            ),
            None,
        )
        if duplicate:
            return duplicate
        current = existing + [row]
        views = materialize_elites(current)
        view_key = f"{row['challenged_policy_hash']}::{row['archive_cell']}"
        row["elite_roles_at_admission"] = sorted(
            role
            for role, winner in views[view_key].items()
            if winner is row
        )
        if not row["elite_roles_at_admission"]:
            row["elite_roles_at_admission"] = ["alternate"]
        try:
            validate_lineage_graph(existing + [row])
        except ValueError as exc:
            raise ArchiveViolation(str(exc)) from exc
        body = dict(row)
        row["archive_entry_hash"] = hash_payload(body)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as stream:
            stream.write(canonical_json(row) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return row
