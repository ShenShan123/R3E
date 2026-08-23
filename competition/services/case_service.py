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
        if not isinstance(relative, str) or not relative:
            raise CaseError(f"case path must be a non-empty string: {relative!r}")
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

    def guided_candidate_source(self) -> str:
        """Apply the checked-in minimal guided patch, never the golden RTL."""
        source = self.source("buggy")
        patch = self.raw.get("guided_patch")
        if not isinstance(patch, dict) or not isinstance(patch.get("operations"), list):
            raise CaseError(f"guided patch is missing: {self.case_id}")
        for operation in patch["operations"]:
            if (
                not isinstance(operation, dict)
                or set(operation) != {"old", "new"}
                or not isinstance(operation["old"], str)
                or not isinstance(operation["new"], str)
            ):
                raise CaseError(f"guided patch operation is invalid: {self.case_id}")
            old = operation["old"]
            new = operation["new"]
            if not old or old not in source:
                raise CaseError(f"guided patch context is absent: {self.case_id}")
            source = source.replace(old, new, 1)
        if source == self.source("buggy"):
            raise CaseError(f"guided patch made no change: {self.case_id}")
        return source

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
            if not isinstance(raw, dict):
                raise CaseError(f"case definition must be an object: {path}")
            required = {
                "schema_version", "case_id", "title", "dataset", "source",
                "buggy_rtl", "reference_rtl", "testbench", "tb_output",
                "top_module", "description", "sim_timeout", "deps",
                "demo_annotation", "guided_patch",
            }
            if not required.issubset(raw):
                raise CaseError(f"case schema is incomplete: {path}")
            if raw["schema_version"] != "r3e-aic-case-v2":
                raise CaseError(f"unsupported case schema: {path}")
            if not isinstance(raw["case_id"], str) or not raw["case_id"]:
                raise CaseError(f"case_id must be non-empty: {path}")
            if not isinstance(raw["deps"], list):
                raise CaseError(f"deps must be a list: {path}")
            if any(not isinstance(item, str) or not item for item in raw["deps"]):
                raise CaseError(f"deps must contain non-empty strings: {path}")
            for field in (
                "buggy_rtl", "reference_rtl", "testbench", "tb_output", "top_module"
            ):
                if not isinstance(raw[field], str) or not raw[field]:
                    raise CaseError(f"{field} must be a non-empty string: {path}")
            if not isinstance(raw["demo_annotation"], dict):
                raise CaseError(f"demo_annotation must be an object: {path}")
            case = CaseDefinition(self.repo_root, raw)
            if case.case_id in self._cases:
                raise CaseError(f"duplicate case id: {case.case_id}")
            case.buggy_rtl
            case.reference_rtl
            case.testbench
            case.guided_candidate_source()
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
                "description": case.raw["description"],
                "demo_annotation": case.raw["demo_annotation"],
                "evidence": case.evidence(),
            }
            for case in self.all()
        ]
