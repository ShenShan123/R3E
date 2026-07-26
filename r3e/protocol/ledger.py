"""Hash-chained JSONL decision ledger."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import fcntl

from .hashing import canonical_json, hash_payload, utc_now


class LedgerViolation(RuntimeError):
    """Raised when a ledger cannot be reconstructed exactly."""


@contextmanager
def writer_lock(path: str | Path) -> Iterator[None]:
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def read_ledger(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows = [
        json.loads(line)
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    previous = ""
    for index, row in enumerate(rows):
        if row.get("ledger_index") != index:
            raise LedgerViolation(f"ledger index mismatch at row {index}")
        if row.get("previous_entry_hash", "") != previous:
            raise LedgerViolation(f"ledger chain mismatch at row {index}")
        body = {key: value for key, value in row.items() if key != "ledger_entry_hash"}
        if row.get("ledger_entry_hash") != hash_payload(body):
            raise LedgerViolation(f"ledger entry hash mismatch at row {index}")
        previous = row["ledger_entry_hash"]
    return rows


def append_ledger(path: str | Path, entry: dict[str, Any]) -> dict[str, Any]:
    target = Path(path)
    lock_path = target.with_suffix(target.suffix + ".lock")
    with writer_lock(lock_path):
        existing = read_ledger(target)
        payload = dict(entry)
        payload.setdefault("timestamp", utc_now())
        payload["ledger_index"] = len(existing)
        payload["previous_entry_hash"] = (
            existing[-1]["ledger_entry_hash"] if existing else ""
        )
        payload["ledger_entry_hash"] = hash_payload(payload)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as stream:
            stream.write(canonical_json(payload) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return payload
