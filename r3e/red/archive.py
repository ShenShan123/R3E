"""Persistent multi-elite residual archive."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from r3e.protocol.hashing import canonical_json, hash_payload, utc_now
from r3e.protocol.ledger import writer_lock

from .novelty import archive_cell, descriptor


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
    return rows


def _elite_kind(row: dict[str, Any]) -> str:
    return str(row.get("elite_kind") or "hardest")


def update_archive(path: str | Path, poison: dict[str, Any]) -> dict[str, Any]:
    if not poison.get("validity", {}).get("proven_valid"):
        raise ArchiveViolation("invalid poison cannot enter residual archive")
    if not poison.get("challenged_policy_hash"):
        raise ArchiveViolation("archive poison must bind challenged policy hash")
    learnability = str(poison.get("learnability") or "unknown")
    if learnability == "unlearnable_or_budget_exceeded":
        raise ArchiveViolation("unlearnable poison is excluded from adaptation archive")
    row = dict(poison)
    row["descriptor"] = descriptor(row)
    row["archive_cell"] = archive_cell(row)
    row.setdefault("elite_kind", "hardest")
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
        same_slot = [
            item for item in existing
            if item.get("challenged_policy_hash") == row["challenged_policy_hash"]
            and item.get("archive_cell") == row["archive_cell"]
            and _elite_kind(item) == _elite_kind(row)
        ]
        if same_slot and float(same_slot[0].get("hardness") or 0) >= float(row.get("hardness") or 0):
            row["elite_kind"] = "alternate"
        body = dict(row)
        row["archive_entry_hash"] = hash_payload(body)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as stream:
            stream.write(canonical_json(row) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return row
