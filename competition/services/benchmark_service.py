"""Expose only checked-in, frozen benchmark metrics."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class BenchmarkService:
    def __init__(self, repo_root: str | Path):
        self.repo_root = Path(repo_root).resolve()

    def dashboard(self) -> dict[str, Any]:
        path = self.repo_root / "competition" / "results" / "frozen" / "metrics.json"
        metrics = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
            "status": "missing"
        }
        return {
            "schema_version": "r3e-aic-benchmark-dashboard-v1",
            "metrics": metrics,
            "source": "competition/results/frozen/metrics.json",
            "warning": "Null or pending values are not measured results.",
        }
