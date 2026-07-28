"""Immutable ControlMemory versions plus an append-only lifecycle ledger."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from r3e.protocol.hashing import atomic_write_json, hash_payload, read_json
from r3e.protocol.ledger import append_ledger, read_ledger, writer_lock

from .episode_store import EpisodeStore
from .lifecycle import validate_transition
from .schema import ControlMemory, MemoryLifecycleEvent
from .evidence import (
    evidence_link,
    freeze_evidence_set,
    memory_definition,
    verify_memory_evidence_set,
)


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
        self.evidence_path = self.root / "evidence_links.jsonl"
        self.qualifications = self.root / "qualifications"
        self.qualification_index_path = self.root / "qualification_versions.jsonl"
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
                existing = self.get_version(
                    value.memory_id, value.memory_version
                )
                if existing.memory_hash != value.memory_hash:
                    if not (
                        existing.effective_delta_hash
                        == value.effective_delta_hash
                        and existing.trigger_predicate
                        == value.trigger_predicate
                    ):
                        raise MemoryStoreViolation(
                            "memory version is already bound to another definition"
                        )
                    self._append_evidence_links(existing, value)
                    return existing.memory_hash
                self._append_evidence_links(existing, value)
                return existing.memory_hash
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
                existing = self.get_version(
                    str(duplicate_delta["memory_id"]),
                    int(duplicate_delta["memory_version"]),
                )
                self._append_evidence_links(existing, value)
                return existing.memory_hash
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
                    "effective_policy_hash": (
                        value.created_under_effective_policy_hash
                    ),
                    "definition": memory_definition(value),
                    "definition_hash": memory_definition(value)[
                        "definition_hash"
                    ],
                    "object_path": str(target.relative_to(self.root)),
                },
            )
            self._append_evidence_links(value, value)
        return value.memory_hash

    def _append_evidence_links(
        self,
        definition: ControlMemory,
        evidence_source: ControlMemory,
    ) -> None:
        rows = read_ledger(self.evidence_path)
        known = {
            (str(row.get("memory_hash")), str(row.get("episode_id")))
            for row in rows
        }
        for episode_id, episode_hash in sorted(
            evidence_source.source_episode_hashes.items()
        ):
            if (definition.memory_hash, episode_id) in known:
                continue
            episode = (
                self.episode_store.get(episode_id)
                if self.episode_store is not None
                else None
            )
            append_ledger(
                self.evidence_path,
                {
                    "operation": "append-memory-evidence-link",
                    **evidence_link(
                        definition,
                        evidence_source=evidence_source,
                        episode_id=episode_id,
                        episode_hash=episode_hash,
                        observed_round_id=(
                            episode.round_id if episode is not None else None
                        ),
                        observed_policy_instance_hash=(
                            episode.challenged_policy_instance_hash
                            if episode is not None else None
                        ),
                        observed_effective_policy_hash=(
                            episode.challenged_effective_policy_hash
                            if episode is not None else None
                        ),
                    ),
                },
            )
            known.add((definition.memory_hash, episode_id))

    def resolve_candidate(
        self, memory: ControlMemory | dict[str, Any]
    ) -> ControlMemory:
        value = (
            memory
            if isinstance(memory, ControlMemory)
            else ControlMemory.from_dict(memory)
        )
        resolved_hash = self.add_candidate(value)
        matches = [
            row for row in read_ledger(self.index_path)
            if row.get("memory_hash") == resolved_hash
        ]
        if len(matches) != 1:
            raise MemoryStoreViolation("resolved memory definition is ambiguous")
        return self.get_version(
            str(matches[0]["memory_id"]), int(matches[0]["memory_version"])
        )

    def evidence_set(self, memory_id: str, version: int) -> dict[str, Any]:
        memory = self.get_version(memory_id, version)
        links = []
        for row in read_ledger(self.evidence_path):
            if row.get("memory_hash") != memory.memory_hash:
                continue
            link = {
                key: value for key, value in row.items()
                if key not in {
                    "operation",
                    "timestamp",
                    "ledger_index",
                    "previous_entry_hash",
                    "ledger_entry_hash",
                }
            }
            if link.get("link_hash") != hash_payload({
                key: value for key, value in link.items() if key != "link_hash"
            }):
                raise MemoryStoreViolation("memory evidence link hash mismatch")
            links.append(link)
        frozen = freeze_evidence_set(memory, links)
        episodes = None
        if self.episode_store is not None:
            episodes = {
                link["episode_id"]: self.episode_store.get(link["episode_id"])
                for link in links
            }
        return verify_memory_evidence_set(
            frozen, memory=memory, episodes=episodes
        )

    def store_qualification_bundle(self, bundle: dict[str, Any]) -> str:
        memory_hash = str(bundle.get("memory_hash") or "")
        decision_hash = str((bundle.get("decision") or {}).get("decision_hash") or "")
        if not memory_hash or not decision_hash:
            raise MemoryStoreViolation("qualification bundle binding is incomplete")
        target = self.qualifications / f"{decision_hash.replace(':', '_')}.json"
        if target.exists():
            if read_json(target) != bundle:
                raise MemoryStoreViolation("qualification bundle hash collision")
        else:
            atomic_write_json(target, bundle)
            append_ledger(
                self.qualification_index_path,
                {
                    "operation": "freeze-memory-qualification-version",
                    "memory_hash": memory_hash,
                    "decision_hash": decision_hash,
                    "evidence_set_hash": str(
                        (bundle.get("decision") or {}).get(
                            "evidence_set_hash"
                        )
                        or ""
                    ),
                    "qualified_under_policy_hash": str(
                        (bundle.get("decision") or {}).get(
                            "qualified_under_policy_hash"
                        )
                        or ""
                    ),
                    "object_path": str(target.relative_to(self.root)),
                },
            )
        return decision_hash

    def get_qualification_bundle(
        self, memory_hash: str, *, policy_hash: str | None = None
    ) -> dict[str, Any]:
        matches = [
            row for row in read_ledger(self.qualification_index_path)
            if row.get("memory_hash") == memory_hash
            and (
                not policy_hash
                or row.get("qualified_under_policy_hash") == policy_hash
            )
        ]
        if not matches:
            raise MemoryStoreViolation("qualification bundle not found")
        return read_json(self.root / matches[-1]["object_path"])

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
            "evidence_link_count": len(read_ledger(self.evidence_path)),
            "qualification_version_count": len(
                read_ledger(self.qualification_index_path)
            ),
        }
