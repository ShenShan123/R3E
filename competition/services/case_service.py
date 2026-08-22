"""Load and validate the three public AIC demonstration cases."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import file_hash, payload_hash


class CaseError(RuntimeError):
    """Raised for an invalid or non-portable competition case."""


@dataclass(frozen=True)
class CaseDefinition:
    root: Path
    raw: dict[str, Any]

    @property
    def case_id(self) -> str:
        return str(self.raw["case_id"])

    @property
    def title(self) -> str:
        return str(self.raw["title"])

    @property
    def buggy_rtl(self) -> Path:
        return self.repo_path(str(self.raw["buggy_rtl"]))

    @property
    def reference_rtl(self) -> Path:
        return self.repo_path(str(self.raw["reference_rtl"]))

    @property
    def testbench(self) -> Path:
        return self.repo_path(str(self.raw["testbench"]))

    @property
    def top_module(self) -> str:
        return str(self.raw["top_module"])

    @property
    def tb_output(self) -> str:
        return str(self.raw["tb_output"])

    def repo_path(self, relative: str) -> Path:
        value = Path(relative)
        if value.is_absolute() or ".." in value.parts:
            raise CaseError(f"non-portable case path: {relative}")
        path = (self.root / value).resolve()
        try:
            path.relative_to(self.root.resolve())
        except ValueError as exc:
            raise CaseError(f"case path escapes repository: {relative}") from exc
        if not path.is_file():
            raise CaseError(f"case input is missing: {relative}")
        return path

    def source(self, role: str) -> str:
        if role == "buggy":
            return self.buggy_rtl.read_text(encoding="utf-8")
        if role == "reference":
            return self.reference_rtl.read_text(encoding="utf-8")
        raise CaseError(f"unknown source role: {role}")

    def oracle_case(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "golden_rtl": str(self.reference_rtl),
            "tb_sources": [str(self.testbench)],
            "deps": [str(self.repo_path(path)) for path in self.raw.get("deps", [])],
            "tb_output": self.tb_output,
            "top_module": self.top_module,
            "sim_timeout": float(self.raw.get("sim_timeout", 20.0)),
        }

    def evidence(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "dataset": self.raw["dataset"],
            "source": self.raw["source"],
            "buggy_rtl": str(self.buggy_rtl.relative_to(self.root)),
            "reference_rtl": str(self.reference_rtl.relative_to(self.root)),
            "testbench": str(self.testbench.relative_to(self.root)),
            "buggy_sha256": file_hash(self.buggy_rtl),
            "reference_sha256": file_hash(self.reference_rtl),
            "testbench_sha256": file_hash(self.testbench),
            "case_hash": payload_hash(self.raw),
        }


class CaseCatalog:
    def __init__(self, repo_root: str | Path):
        self.repo_root = Path(repo_root).resolve()
        self.case_root = self.repo_root / "competition" / "cases"
        self._cases: dict[str, CaseDefinition] = {}
        self._load()

    def _load(self) -> None:
        for path in sorted(self.case_root.glob("*/case.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            required = {
                "schema_version", "case_id", "title", "dataset", "source",
                "buggy_rtl", "reference_rtl", "testbench", "tb_output",
                "top_module", "failure_type", "recommended_lens", "sim_timeout",
            }
            if set(raw) < required:
                raise CaseError(f"case schema is incomplete: {path}")
            if raw["schema_version"] != "r3e-aic-case-v1":
                raise CaseError(f"unsupported case schema: {path}")
            case = CaseDefinition(self.repo_root, raw)
            if case.case_id in self._cases:
                raise CaseError(f"duplicate case id: {case.case_id}")
            case.buggy_rtl
            case.reference_rtl
            case.testbench
            self._cases[case.case_id] = case
        if set(self._cases) != {"demo_counter", "demo_fsm", "demo_shift"}:
            raise CaseError("the competition facade must expose exactly three demos")

    def get(self, case_id: str) -> CaseDefinition:
        try:
            return self._cases[case_id]
        except KeyError as exc:
            raise CaseError(f"unknown competition case: {case_id}") from exc

    def all(self) -> list[CaseDefinition]:
        return list(self._cases.values())

    def summaries(self) -> list[dict[str, Any]]:
        return [
            {
                "case_id": case.case_id,
                "title": case.title,
                "failure_type": case.raw["failure_type"],
                "recommended_lens": case.raw["recommended_lens"],
                "description": case.raw["description"],
                "evidence": case.evidence(),
            }
            for case in self.all()
        ]
