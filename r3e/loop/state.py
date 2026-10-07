"""Persistent loop state: knowledge store, episodes and append-only ledgers.

Every record is written to a hash-chained JSONL ledger (``r3e.protocol.ledger``)
under the run directory, so a chain can be audited and resumed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from r3e.knowledge import (
    BugTypeInference,
    KnowledgeItem,
    KnowledgeMatcher,
    KnowledgeStore,
    RepairEpisode,
)
from r3e.protocol.hashing import atomic_write_json, read_json
from r3e.protocol.ledger import append_ledger, read_ledger


LEDGERS = ("episodes", "encounters", "red", "qualification", "monitor", "rounds", "evaluation", "calls", "lineages", "memory_usage", "population", "screens")


@dataclass
class RunState:
    root: Path
    matcher: KnowledgeMatcher = field(default_factory=KnowledgeMatcher)
    static_items: tuple[KnowledgeItem, ...] = ()

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = KnowledgeStore(self.root / "store")

    # ------------------------------------------------------------ ledgers
    def path(self, name: str) -> Path:
        if name not in LEDGERS:
            raise ValueError(f"unknown ledger {name}")
        return self.root / f"{name}.jsonl"

    def append(self, name: str, entry: dict[str, Any]) -> dict[str, Any]:
        return append_ledger(self.path(name), entry)

    def read(self, name: str) -> list[dict[str, Any]]:
        return read_ledger(self.path(name))

    # ----------------------------------------------------------- episodes
    def episodes(self) -> list[RepairEpisode]:
        return [RepairEpisode(row["episode"]) for row in self.read("episodes")]

    def record_episode(self, episode: RepairEpisode, *, round_index: int) -> None:
        self.append("episodes", {"round": round_index, "episode": episode.to_dict()})

    def inference(self, episodes: Iterable[RepairEpisode] | None = None) -> BugTypeInference:
        return BugTypeInference().fit(self.episodes() if episodes is None else episodes)

    # ---------------------------------------------------------- knowledge
    def active_items(self) -> list[KnowledgeItem]:
        return self.store.active_items()

    def items_by_key(self, keys: Iterable[tuple[str, int]]) -> list[KnowledgeItem]:
        wanted = set(keys)
        everything = self.store.items_with_status(*(
            "candidate", "qualified", "active", "rejected", "suspended", "retired"
        ))
        return [i for i in everything if (i.item_id, i.version) in wanted]

    # --------------------------------------------------------- checkpoint
    def checkpoint(self) -> dict[str, Any]:
        path = self.root / "checkpoint.json"
        return read_json(path) if path.exists() else {"completed_rounds": 0}

    def save_checkpoint(self, payload: dict[str, Any]) -> None:
        atomic_write_json(self.root / "checkpoint.json", payload)
