"""Append-only relation graph for ControlMemory versions."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from r3e.protocol.ledger import append_ledger, read_ledger


RELATION_TYPES = {
    "derived_from", "generalizes", "specializes", "refines", "supersedes",
    "conflicts_with", "composes_with", "equivalent_effective_delta",
    "fails_under_policy", "revalidated_under_policy",
}
_ACYCLIC_RELATIONS = {"derived_from", "generalizes", "specializes", "refines", "supersedes"}


class MemoryGraphViolation(RuntimeError):
    """Raised when a memory relation is invalid or cyclic."""


class MemoryRelationGraph:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def edges(self) -> list[dict[str, Any]]:
        return read_ledger(self.path)

    def _would_cycle(self, source: str, target: str, relation: str) -> bool:
        adjacency: dict[str, set[str]] = {}
        for row in self.edges():
            if row.get("relation") in _ACYCLIC_RELATIONS:
                adjacency.setdefault(row["source"], set()).add(row["target"])
        adjacency.setdefault(source, set()).add(target)
        pending = [target]
        seen = set()
        while pending:
            node = pending.pop()
            if node == source:
                return True
            if node in seen:
                continue
            seen.add(node)
            pending.extend(adjacency.get(node, ()))
        return False

    def add(
        self,
        *,
        source: str,
        target: str,
        relation: str,
        evidence_hash: str,
    ) -> str:
        if relation not in RELATION_TYPES:
            raise MemoryGraphViolation("unknown memory relation")
        if not source or not target or source == target:
            raise MemoryGraphViolation("memory relation endpoints must be distinct")
        if any(
            row.get("source") == source
            and row.get("target") == target
            and row.get("relation") == relation
            for row in self.edges()
        ):
            raise MemoryGraphViolation("duplicate memory relation")
        if relation in _ACYCLIC_RELATIONS and self._would_cycle(source, target, relation):
            raise MemoryGraphViolation("memory relation would create a lineage cycle")
        body = {
            "operation": "add-memory-relation",
            "source": source,
            "target": target,
            "relation": relation,
            "evidence_hash": evidence_hash,
        }
        row = append_ledger(self.path, body)
        return row["ledger_entry_hash"]
