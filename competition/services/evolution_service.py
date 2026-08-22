"""Evidence-bound evolution timeline for the competition UI."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class EvolutionService:
    def __init__(self, repo_root: str | Path):
        self.repo_root = Path(repo_root).resolve()

    def timeline(self) -> dict[str, Any]:
        frozen = self.repo_root / "competition" / "results" / "frozen" / "evolution" / "manifest.json"
        manifest = json.loads(frozen.read_text(encoding="utf-8")) if frozen.is_file() else {}
        ready = manifest.get("available") is True and manifest.get("status") == "verified_policy_evolution"
        if not ready:
            return {
                "schema_version": "r3e-aic-evolution-timeline-v1",
                "available": False,
                "mode": "not_frozen",
                "message": "No verified policy-evolution artifact is frozen; the UI must not invent B0→B1 evidence.",
                "events": [],
            }
        return {
            "schema_version": "r3e-aic-evolution-timeline-v1",
            "available": True,
            "mode": "verified_policy_evolution",
            "message": "Replay from verified run",
            "events": manifest.get("evolution_events", []),
        }
