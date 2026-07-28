"""Immutable ControlMemory versions plus an append-only lifecycle ledger."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from r3e.protocol.hashing import atomic_write_json, hash_payload, read_json
from r3e.protocol.ledger import append_ledger, read_ledger, writer_lock

from .episode_store import EpisodeStore
from .lifecycle import validate_transition
from .schema import ControlMemory, MemoryLifecycleEvent


class MemoryStoreViolation(RuntimeError):
    """Raised when memory provenance, versions or lifecycle are inconsistent."""


class MemoryStore:
    def __init__(
        self,
        root: str | Path,
        *,
        episode_store: EpisodeStore | None = None,
    ):
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.index_path = self.root / "memory_versions.jsonl"
        self.lifecycle_path = self.root / "lifecycle.jsonl"
        self.episode_store = episode_store

    def _object_path(self, memory: ControlMemory) -> Path:
        safe = memory.memory_hash.replace(":", "_")
        return self.objects / f"{safe}.json"

    def add_candidate(self, memory: ControlMemory | dict[str, Any]) -> str:
        value = (
            memory
            if isinstance(memory, ControlMemory)
            else ControlMemory.from_dict(memory)
        )
        if self.episode_store is not None:
            for episode_id, expected_hash in value.source_episode_hashes.items():
                episode = self.episode_store.get(episode_id)
                if episode.episode_hash != expected_hash:
                    raise MemoryStoreViolation("source episode hash mismatch")
        with writer_lock(self.root / ".memory-store.lock"):
            rows = read_ledger(self.index_path)
            same_version = [
                row for row in rows
                if row.get("memory_id") == value.memory_id
                and row.get("memory_version") == value.memory_version
            ]
            if same_version:
                if same_version[0].get("memory_hash") != value.memory_hash:
                    raise MemoryStoreViolation("memory version is already bound to another hash")
                return value.memory_hash
            versions = [
                int(row["memory_version"])
                for row in rows if row.get("memory_id") == value.memory_id
            ]
            expected_version = max(versions, default=0) + 1
            if value.memory_version != expected_version:
                raise MemoryStoreViolation(
                    f"memory version must be contiguous: expected {expected_version}"
                )
            duplicate_delta = next(
                (
                    row for row in rows
                    if row.get("effective_delta_hash") == value.effective_delta_hash
                    and row.get("trigger_hash")
                    == hash_payload(value.trigger_predicate)
                ),
                None,
            )
            if duplicate_delta:
                raise MemoryStoreViolation(
                    "equivalent trigger/control delta already exists: "
                    f"{duplicate_delta['memory_id']}"
                )
            target = self._object_path(value)
            if not target.exists():
                atomic_write_json(target, value.to_dict())
            append_ledger(
                self.index_path,
                {
                    "operation": "add-control-memory-version",
                    "memory_id": value.memory_id,
                    "memory_version": value.memory_version,
                    "memory_hash": value.memory_hash,
                    "effective_delta_hash": value.effective_delta_hash,
                    "trigger_hash": hash_payload(value.trigger_predicate),
                    "object_path": str(target.relative_to(self.root)),
                },
            )
        return value.memory_hash

    def get_version(self, memory_id: str, version: int) -> ControlMemory:
        rows = read_ledger(self.index_path)
        matches = [
            row for row in rows
            if row.get("memory_id") == memory_id
            and int(row.get("memory_version") or 0) == int(version)
        ]
        if len(matches) != 1:
            raise MemoryStoreViolation(f"memory version not found: {memory_id}@{version}")
        memory = ControlMemory.from_dict(read_json(self.root / matches[0]["object_path"]))
        if memory.memory_hash != matches[0]["memory_hash"]:
            raise MemoryStoreViolation("memory index/object hash mismatch")
        return memory

    def lifecycle_events(self, memory_id: str | None = None) -> list[MemoryLifecycleEvent]:
        events = []
        for row in read_ledger(self.lifecycle_path):
            if memory_id is not None and row.get("memory_id") != memory_id:
                continue
            event_payload = {
                key: value for key, value in row.items()
                if key not in {
                    "operation", "timestamp", "ledger_index", "previous_entry_hash",
                    "ledger_entry_hash",
                }
            }
            events.append(MemoryLifecycleEvent.from_dict(event_payload))
        return events

    def current_status(self, memory_id: str, version: int) -> str:
        memory = self.get_version(memory_id, version)
        status = memory.status
        for event in self.lifecycle_events(memory_id):
            if event.memory_version != version:
                continue
            if event.memory_hash != memory.memory_hash or event.previous_status != status:
                raise MemoryStoreViolation("lifecycle chain does not match memory version")
            status = event.new_status
        return status

    def update_lifecycle(
        self,
        memory_id: str,
        event: MemoryLifecycleEvent | dict[str, Any],
    ) -> None:
        value = (
            event
            if isinstance(event, MemoryLifecycleEvent)
            else MemoryLifecycleEvent.from_dict(event)
        )
        if value.memory_id != memory_id:
            raise MemoryStoreViolation("lifecycle event memory id mismatch")
        with writer_lock(self.root / ".memory-lifecycle.lock"):
            memory = self.get_version(memory_id, value.memory_version)
            if memory.memory_hash != value.memory_hash:
                raise MemoryStoreViolation("lifecycle event memory hash mismatch")
            current = self.current_status(memory_id, value.memory_version)
            if current != value.previous_status:
                raise MemoryStoreViolation("stale lifecycle transition")
            validate_transition(current, value.new_status)
            append_ledger(
                self.lifecycle_path,
                {"operation": "memory-lifecycle-transition", **value.to_dict()},
            )

    def audit(self) -> dict[str, Any]:
        rows = read_ledger(self.index_path)
        for row in rows:
            self.get_version(row["memory_id"], int(row["memory_version"]))
            self.current_status(row["memory_id"], int(row["memory_version"]))
        return {
            "memory_version_count": len(rows),
            "lifecycle_event_count": len(read_ledger(self.lifecycle_path)),
        }
