"""Real EDA verification for competition candidates.

The final oracle decision is delegated to the existing R³E differential gate.
The surrounding stages are deliberately explicit so the UI cannot collapse a
compile pass into a functional-correctness claim.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from r3e.semantic_repair_bench import oracle_gate

from .case_service import CaseCatalog, CaseDefinition
from .common import executable_versions, file_hash, payload_hash, run_capture
from .event_stream import EventStream


class VerificationService:
    STAGES = ("parse", "compile", "simulation", "oracle", "formal", "regression")

    def __init__(self, repo_root: str | Path, output_root: str | Path):
        self.repo_root = Path(repo_root).resolve()
        self.catalog = CaseCatalog(self.repo_root)
        self.output_root = Path(output_root).resolve()

    @staticmethod
    def _stage(name: str, status: str, **detail: Any) -> dict[str, Any]:
        return {"name": name, "status": status, **detail}

    @staticmethod
    def _safe_output_name(name: str) -> str:
        value = Path(name)
        if value.is_absolute() or ".." in value.parts or not value.name:
            raise ValueError(f"unsafe testbench output path: {name}")
        return name

    def _stage_sources(
        self,
        case: CaseDefinition,
        candidate_source: str,
        directory: Path,
        candidate_name: str,
    ) -> tuple[Path, list[str]]:
        directory.mkdir(parents=True, exist_ok=True)
        candidate_path = directory / candidate_name
        candidate_path.write_text(candidate_source, encoding="utf-8")
        names = [candidate_name]
        used: dict[str, Path] = {candidate_name: candidate_path}
        source_paths = [case.testbench] + [case.repo_path(path) for path in case.raw.get("deps", [])]
        for source in source_paths:
            prior = used.get(source.name)
            if prior is not None and prior.resolve() != source.resolve():
                raise ValueError(f"basename collision: {source.name}")
            destination = directory / source.name
            if source.name not in used:
                shutil.copy2(source, destination)
                used[source.name] = source
                names.append(source.name)
        return candidate_path, names

    def _simulate(
        self,
        case: CaseDefinition,
        candidate_source: str,
        directory: Path,
        candidate_name: str,
        label: str,
    ) -> dict[str, Any]:
        try:
            candidate_path, names = self._stage_sources(
                case, candidate_source, directory, candidate_name
            )
            output_name = self._safe_output_name(case.tb_output)
        except (OSError, ValueError) as exc:
            return {"ok": False, "stage": label, "error": str(exc), "directory": str(directory)}
        compiled = directory / f"{label}.out"
        compile_result = run_capture(
            ["iverilog", "-g2012", "-o", compiled.name, *names],
            directory,
            max(20.0, float(case.raw.get("sim_timeout", 20.0))),
        )
        if not compile_result["ok"]:
            return {
                "ok": False,
                "stage": "compile",
                "candidate_path": str(candidate_path),
                "compile": compile_result,
                "directory": str(directory),
            }
        simulated = run_capture(
            ["vvp", compiled.name],
            directory,
            max(20.0, float(case.raw.get("sim_timeout", 20.0))),
        )
        output_path = directory / output_name
        if not simulated["ok"] or not output_path.is_file():
            return {
                "ok": False,
                "stage": "simulation",
                "candidate_path": str(candidate_path),
                "compile": compile_result,
                "simulation": simulated,
                "output_path": str(output_path),
                "directory": str(directory),
            }
        return {
            "ok": True,
            "stage": "simulation",
            "candidate_path": str(candidate_path),
            "compile": compile_result,
            "simulation": simulated,
            "output_path": str(output_path),
            "output_sha256": file_hash(output_path),
            "directory": str(directory),
        }

    def verify(
        self,
        case_id: str,
        candidate_source: str,
        *,
        run_id: str = "run",
        stream: EventStream | None = None,
        write_artifact: bool = True,
    ) -> dict[str, Any]:
        case = self.catalog.get(case_id)
        stream = stream or EventStream()
        run_dir = self.output_root / case_id / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        stages: list[dict[str, Any]] = []
        candidate_path = run_dir / "candidate.v"
        candidate_path.write_text(candidate_source, encoding="utf-8")

        parse_result = run_capture(
            ["iverilog", "-g2012", "-t", "null", candidate_path.name],
            run_dir,
            max(20.0, float(case.raw.get("sim_timeout", 20.0))),
        )
        stages.append(self._stage("parse", "pass" if parse_result["ok"] else "fail", evidence=parse_result))
        stream.emit("PARSE", stages[-1]["status"], evidence=parse_result)
        if not parse_result["ok"]:
            for name in self.STAGES[1:]:
                stages.append(self._stage(name, "skipped", reason="parse_failed"))
            result = self._result(case, candidate_source, stages, run_dir, started, stream)
            return result

        golden_result = self._simulate(
            case, case.source("reference"), run_dir / "golden", "golden.v", "golden"
        )
        candidate_result = self._simulate(
            case, candidate_source, run_dir / "candidate", "candidate.v", "candidate"
        )
        compile_ok = bool(candidate_result.get("compile", {}).get("ok"))
        stages.append(self._stage("compile", "pass" if compile_ok else "fail", evidence=candidate_result.get("compile", candidate_result)))
        stream.emit("COMPILE", stages[-1]["status"], evidence=stages[-1]["evidence"])
        sim_ok = bool(candidate_result.get("ok"))
        stages.append(self._stage("simulation", "pass" if sim_ok else "fail", evidence=candidate_result))
        stream.emit("SIMULATION", stages[-1]["status"], evidence=candidate_result)

        core_gate: dict[str, Any]
        if not golden_result.get("ok"):
            core_gate = {
                "ok": False,
                "stage": "golden_sim",
                "error": "frozen reference did not produce an oracle output",
                "reference_evidence": golden_result,
            }
        else:
            try:
                outcome = oracle_gate.judge(
                    case.oracle_case(),
                    candidate_path,
                    run_dir / "core_oracle",
                    evidence_k=8,
                )
                core_gate = {
                    "ok": bool(outcome.ok),
                    "stage": outcome.stage,
                    "mismatch": outcome.mismatch,
                    "structured": outcome.structured,
                    "error": outcome.err,
                    "golden_lines": outcome.golden_lines,
                    "candidate_lines": outcome.cand_lines,
                    "detail": outcome.detail,
                }
            except (OSError, ValueError, RuntimeError) as exc:
                core_gate = {"ok": False, "stage": "oracle", "error": type(exc).__name__}
        stages.append(self._stage("oracle", "pass" if core_gate["ok"] else "fail", evidence=core_gate))
        stream.emit("ORACLE", stages[-1]["status"], evidence=core_gate)

        yosys = shutil.which("yosys")
        if not yosys:
            formal = {"ok": False, "error": "missing_tool:yosys"}
        else:
            formal = run_capture(
                [
                    "yosys", "-Q", "-p",
                    f"read_verilog -sv {candidate_path.name}; hierarchy -check -top {case.top_module}; proc; opt; check",
                ],
                run_dir,
                max(20.0, float(case.raw.get("sim_timeout", 20.0))),
            )
        stages.append(self._stage("formal", "pass" if formal["ok"] else "fail", evidence=formal, note="Yosys structural/formal-sanity gate"))
        stream.emit("FORMAL", stages[-1]["status"], evidence=formal)

        regression: dict[str, Any]
        if not candidate_result.get("ok"):
            regression = {"ok": False, "error": "candidate_simulation_failed"}
        else:
            repeat = self._simulate(
                case,
                candidate_source,
                run_dir / "repeat",
                "candidate.v",
                "repeat",
            )
            if not repeat.get("ok"):
                regression = {"ok": False, "error": "repeat_simulation_failed", "repeat": repeat}
            else:
                first = Path(str(candidate_result["output_path"])).read_text(encoding="utf-8", errors="ignore")
                second = Path(str(repeat["output_path"])).read_text(encoding="utf-8", errors="ignore")
                same, mismatch = oracle_gate._compare(first, second, max_evidence=2)
                regression = {
                    "ok": same,
                    "scope": "same_case_repeatability",
                    "mismatch": mismatch,
                    "first_output_sha256": candidate_result.get("output_sha256"),
                    "second_output_sha256": repeat.get("output_sha256"),
                }
        stages.append(self._stage("regression", "pass" if regression["ok"] else "fail", evidence=regression))
        stream.emit("REGRESSION", stages[-1]["status"], evidence=regression)
        return self._result(case, candidate_source, stages, run_dir, started, stream, core_gate=core_gate)

    def _result(
        self,
        case: CaseDefinition,
        candidate_source: str,
        stages: list[dict[str, Any]],
        run_dir: Path,
        started: float,
        stream: EventStream,
        *,
        core_gate: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        accepted = bool(stages) and all(stage["status"] == "pass" for stage in stages)
        result = {
            "schema_version": "r3e-aic-verification-result-v1",
            "case_id": case.case_id,
            "accepted": accepted,
            "stages": stages,
            "authority": "r3e.semantic_repair_bench.oracle_gate.judge",
            "candidate_sha256": payload_hash(candidate_source),
            "case_evidence": case.evidence(),
            "toolchain": executable_versions(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "artifact_directory": str(run_dir),
            "events": stream.events,
            "result_hash": payload_hash({
                "case_id": case.case_id,
                "accepted": accepted,
                "stages": stages,
            }),
        }
        if core_gate is not None:
            result["core_oracle"] = core_gate
        return result
