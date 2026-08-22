"""Conservative presentation facade for verified experience memory."""
from __future__ import annotations

from pathlib import Path
from typing import Any


class MemoryService:
    def __init__(self, repo_root: str | Path):
        self.repo_root = Path(repo_root).resolve()

    def list_memories(self) -> dict[str, Any]:
        return {
            "schema_version": "r3e-aic-memory-view-v1",
            "available": False,
            "message": "No public memory bank is frozen in this checkout; activation evidence is not fabricated.",
            "statuses": ["Shadow", "Qualified", "Active", "Rejected", "Suspended"],
            "memories": [],
        }
