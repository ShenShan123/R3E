"""Conservative presentation facade for verified experience memory."""
from __future__ import annotations

from pathlib import Path
import json
from typing import Any


class MemoryService:
    def __init__(self, repo_root: str | Path):
        self.repo_root = Path(repo_root).resolve()

    def list_memories(self) -> dict[str, Any]:
        path = self.repo_root / "competition" / "results" / "frozen" / "memory" / "audit.json"
        if path.is_file():
            audit = json.loads(path.read_text(encoding="utf-8"))
            if audit.get("available") is True and audit.get("status") == "verified_activation_audit":
                return {
                    "schema_version": "r3e-aic-memory-view-v2",
                    "available": True,
                    "message": "Experience memory is backed by a frozen activation audit.",
                    "statuses": ["Shadow", "Qualified", "Active", "Rejected", "Suspended"],
                    "memories": audit.get("memories", []),
                }
        return {
            "schema_version": "r3e-aic-memory-view-v2",
            "available": False,
            "message": "No verified memory activation audit is frozen; activation evidence is not fabricated.",
            "statuses": ["Shadow", "Qualified", "Active", "Rejected", "Suspended"],
            "memories": [],
        }
