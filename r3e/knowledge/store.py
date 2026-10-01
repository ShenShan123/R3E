"""Append-only knowledge store with a hash-chained lifecycle ledger.

Every item version is written once. Status changes are ledger events that
record a reason, and only these legal transitions are accepted:

    candidate -> qualified -> active
    candidate -> rejected
    qualified -> rejected
    active    -> suspended -> active
    any       -> retired

Only ``active`` items may be shown to Blue in ``matched`` mode. Evaluation
code may read other statuses explicitly, for example to replay candidates in
shadow mode.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from r3e.protocol.ledger import append_ledger, read_ledger

from .schema import KNOWLEDGE_STATUSES, KnowledgeItem, KnowledgeValidationError


_TRANSITIONS = {
    "candidate": {"qualified", "rejected", "retired"},
    "qualified": {"active", "rejected", "retired"},
    "active": {"suspended", "retired"},
    "suspended": {"active", "retired"},
    "rejected": {"retired"},
    "retired": set(),
}


class KnowledgeStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.items_path = self.root / "knowledge_items.jsonl"
        self.events_path = self.root / "knowledge_events.jsonl"

    def _items(self) -> dict[tuple[str, int], KnowledgeItem]:
        out: dict[tuple[str, int], KnowledgeItem] = {}
        for row in read_ledger(self.items_path):
            item = KnowledgeItem.from_dict(row["item"])
            out[(item.item_id, item.version)] = item
        return out

    def add(self, item: KnowledgeItem, *, reason: str = "authored") -> KnowledgeItem:
        key = (item.item_id, item.version)
        existing = self._items().get(key)
        if existing is not None:
            if existing.item_hash != item.item_hash:
                raise KnowledgeValidationError(
                    f"knowledge {key} already stored with different content"
                )
            return existing
        append_ledger(self.items_path, {"item": item.to_dict()})
        append_ledger(self.events_path, {
            "item_id": item.item_id, "version": item.version,
            "item_hash": item.item_hash, "from": None, "to": "candidate",
            "reason": reason,
        })
        return item

    def status(self, item_id: str, version: int) -> str:
        current = None
        for row in read_ledger(self.events_path):
            if row["item_id"] == item_id and row["version"] == version:
                current = row["to"]
        if current is None:
            raise KnowledgeValidationError(f"unknown knowledge item {item_id} v{version}")
        return current

    def transition(
        self, item_id: str, version: int, to: str, *, reason: str,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        if to not in KNOWLEDGE_STATUSES:
            raise KnowledgeValidationError(f"unknown status {to}")
        current = self.status(item_id, version)
        if to not in _TRANSITIONS[current]:
            raise KnowledgeValidationError(f"illegal transition {current} -> {to}")
        if not reason.strip():
            raise KnowledgeValidationError("transition requires a reason")
        item = self._items()[(item_id, version)]
        append_ledger(self.events_path, {
            "item_id": item_id, "version": version, "item_hash": item.item_hash,
            "from": current, "to": to, "reason": reason,
            "evidence": dict(evidence or {}),
        })

    def items_with_status(self, *statuses: str) -> list[KnowledgeItem]:
        wanted = set(statuses)
        latest: dict[tuple[str, int], str] = {}
        for row in read_ledger(self.events_path):
            latest[(row["item_id"], row["version"])] = row["to"]
        items = self._items()
        return [
            items[key] for key in sorted(latest)
            if latest[key] in wanted and key in items
        ]

    def active_items(self) -> list[KnowledgeItem]:
        return self.items_with_status("active")
