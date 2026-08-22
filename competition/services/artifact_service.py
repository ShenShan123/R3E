"""Hash-bound, local-only run artifact handling for demo evidence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import canonical_json, file_hash, payload_hash


class ArtifactService:
    def __init__(self, output_root: str | Path):
        self.output_root = Path(output_root).resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)

    def write_json(self, relative: str, payload: dict[str, Any]) -> Path:
        path = (self.output_root / relative).resolve()
        path.relative_to(self.output_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    def record_run(self, case_id: str, payload: dict[str, Any]) -> Path:
        body = {
            "schema_version": "r3e-aic-run-artifact-v1",
            "case_id": case_id,
            "payload": payload,
            "payload_hash": payload_hash(payload),
        }
        return self.write_json(f"cases/{case_id}/run.json", body)

    def manifest(self, files: list[Path]) -> dict[str, Any]:
        rows = []
        for path in sorted(files):
            rows.append({
                "path": str(path.relative_to(self.output_root)),
                "sha256": file_hash(path),
                "size_bytes": path.stat().st_size,
            })
        return {
            "schema_version": "r3e-aic-artifact-manifest-v1",
            "files": rows,
            "manifest_hash": payload_hash(rows),
        }
