"""Evidence-only failure diagnosis for the Repair Studio."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .case_service import CaseCatalog
from .event_stream import EventStream
from .verification_service import VerificationService


class DiagnosisService:
    def __init__(self, repo_root: str | Path, output_root: str | Path):
        self.repo_root = Path(repo_root).resolve()
        self.catalog = CaseCatalog(self.repo_root)
        self.verifier = VerificationService(self.repo_root, output_root)

    def diagnose(self, case_id: str, *, run_id: str = "diagnosis") -> dict[str, Any]:
        case = self.catalog.get(case_id)
        stream = EventStream()
        verification = self.verifier.verify(
            case_id,
            case.source("buggy"),
            run_id=run_id,
            stream=stream,
        )
        oracle = verification.get("core_oracle", {})
        stage = next((row for row in verification["stages"] if row["name"] == "oracle"), {})
        return {
            "schema_version": "r3e-aic-diagnosis-v1",
            "case_id": case_id,
            "failure_type": case.raw["failure_type"],
            "first_divergence_cycle": case.raw.get("first_divergence_cycle"),
            "signal": case.raw.get("signal"),
            "suspected_region": case.raw.get("suspected_region"),
            "recommended_lens": case.raw["recommended_lens"],
            "expected_baseline_failure": verification["accepted"] is False,
            "baseline_verification": verification,
            "evidence": {
                "mismatch": oracle.get("mismatch") or stage.get("evidence", {}).get("mismatch", ""),
                "structured": oracle.get("structured") or stage.get("evidence", {}).get("structured", ""),
                "authority": verification["authority"],
            },
        }
