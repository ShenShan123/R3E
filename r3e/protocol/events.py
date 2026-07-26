"""Uniform hash-chained JSONL observability events."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .hashing import canonical_json, hash_payload, utc_now
from .ledger import writer_lock


EVENT_SCHEMA_VERSION = "r3e-event-v1"
EVENT_STREAMS = {"policy", "red", "oracle", "arena", "rollback"}


class EventViolation(RuntimeError):
    """Raised when an event stream cannot be reconstructed."""


def detect_code_version(repo_root: str | Path | None = None) -> str:
    configured = os.environ.get("R3E_CODE_VERSION")
    if configured:
        return configured
    root = Path(repo_root or Path.cwd())
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        value = proc.stdout.strip()
        if proc.returncode == 0 and len(value) == 40:
            return value
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def read_events(path: str | Path) -> list[dict[str, Any]]:
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
        if row.get("schema_version") != EVENT_SCHEMA_VERSION:
            raise EventViolation(f"event schema mismatch at row {index}")
        if row.get("event_index") != index:
            raise EventViolation(f"event index mismatch at row {index}")
        if row.get("previous_event_hash") != previous:
            raise EventViolation(f"event chain mismatch at row {index}")
        body = {key: value for key, value in row.items() if key != "event_hash"}
        if row.get("event_hash") != hash_payload(body):
            raise EventViolation(f"event hash mismatch at row {index}")
        previous = row["event_hash"]
    return rows


class EventLogger:
    def __init__(self, root: str | Path, *, code_version: str):
        self.root = Path(root)
        self.code_version = code_version

    def path_for(self, stream: str) -> Path:
        if stream not in EVENT_STREAMS:
            raise EventViolation(f"unknown event stream: {stream}")
        return self.root / f"{stream}.jsonl"

    def ensure_streams(self) -> None:
        """Materialize the five documented streams without inventing events."""
        self.root.mkdir(parents=True, exist_ok=True)
        for stream in sorted(EVENT_STREAMS):
            self.path_for(stream).touch(exist_ok=True)

    def emit(
        self,
        stream: str,
        event_type: str,
        *,
        round_id: str = "",
        **fields: Any,
    ) -> dict[str, Any]:
        if not event_type:
            raise EventViolation("event_type must be non-empty")
        path = self.path_for(stream)
        with writer_lock(path.with_suffix(path.suffix + ".lock")):
            existing = read_events(path)
            event = {
                "schema_version": EVENT_SCHEMA_VERSION,
                "event_type": event_type,
                "round_id": round_id,
                **fields,
                "timestamp": utc_now(),
                "code_version": self.code_version,
                "event_index": len(existing),
                "previous_event_hash": (
                    existing[-1]["event_hash"] if existing else ""
                ),
            }
            event["event_hash"] = hash_payload(event)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream_handle:
                stream_handle.write(canonical_json(event) + "\n")
                stream_handle.flush()
                os.fsync(stream_handle.fileno())
            return event
