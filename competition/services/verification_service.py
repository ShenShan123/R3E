"""Runner-owned EDA, scope, and correctness gates for Competition M2."""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any, Mapping

from r3e.semantic_repair_bench import oracle_gate

from ..config import CompetitionConfig, load_config
from .case_service import CaseCatalog, CaseDefinition
from .common import executable_versions, file_hash, payload_hash, run_capture
from .event_stream import EventStream
from .scope_service import ScopeService


class VerificationService:
    """Execute the complete M2 gate sequence with fail-closed semantics."""

    STAGES = (
        "parse", "scope", "compile", "simulation", "oracle",
        "structural_check", "repeatability",
    )

    def __init__(
        self,
        repo_root: str | Path,
        output_root: str | Path,
        config: CompetitionConfig | None = None,
    ):
        self.repo_root = Path(repo_root).resolve()
        self.catalog = CaseCatalog(self.repo_root)
        self.output_root = Path(output_root).resolve()
        self.config = config or load_config(self.repo_root)
        self.stages = self.config.verification_stages
        self.scope = ScopeService()

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
        source_paths = [
            case.testbench,
            *[case.repo_path(path) for path in case.raw.get("deps", [])],
        ]
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
            return {
                "ok": False,
                "stage": label,
                "error": str(exc),
                "directory": str(directory),
            }
        timeout = max(self.config.timeout_seconds, float(case.raw.get("sim_timeout", 20.0)))
        compiled = directory / f"{label}.out"
        compile_result = run_capture(
            ["iverilog", "-g2012", "-o", compiled.name, *names],
            directory,
            timeout,
        )
        if not compile_result["ok"]:
            return {
                "ok": False,
                "stage": "compile",
                "candidate_path": str(candidate_path),
                "compile": compile_result,
                "directory": str(directory),
            }
        simulated = run_capture(["vvp", compiled.name], directory, timeout)
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
        timeout = max(self.config.timeout_seconds, float(case.raw.get("sim_timeout", 20.0)))

        parse_result = run_capture(
            ["iverilog", "-g2012", "-t", "null", candidate_path.name],
            run_dir,
            timeout,
        )
        stages.append(self._stage("parse", "pass" if parse_result["ok"] else "fail", evidence=parse_result))
        stream.emit("PARSE", stages[-1]["status"], evidence=parse_result)
        if not parse_result["ok"]:
            for name in self.stages[1:]:
                stages.append(self._stage(name, "skipped", reason="parse_failed"))
            return self._result(case, candidate_source, stages, run_dir, started, stream)

        scope_result = self.scope.inspect(
            buggy_source=case.source("buggy"),
            candidate_source=candidate_source,
            top_module=case.top_module,
            patch_scope="local_block",
        )
        stages.append(self._stage("scope", "pass" if scope_result["ok"] else "fail", evidence=scope_result))
        stream.emit("SCOPE", stages[-1]["status"], evidence=scope_result)
        if not scope_result["ok"]:
            for name in self.stages[2:]:
                stages.append(self._stage(name, "skipped", reason="scope_failed"))
            return self._result(case, candidate_source, stages, run_dir, started, stream)

        golden_result = self._simulate(
            case, case.source("reference"), run_dir / "golden", "golden.v", "golden"
        )
        candidate_result = self._simulate(
            case, candidate_source, run_dir / "candidate", "candidate.v", "candidate"
        )
        compile_ok = bool(candidate_result.get("compile", {}).get("ok"))
        compile_evidence = candidate_result.get("compile", candidate_result)
        stages.append(self._stage("compile", "pass" if compile_ok else "fail", evidence=compile_evidence))
        stream.emit("COMPILE", stages[-1]["status"], evidence=compile_evidence)
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
            structural = {"ok": False, "error": "missing_tool:yosys"}
        else:
            structural = run_capture(
                [
                    "yosys", "-Q", "-p",
                    f"read_verilog -sv {candidate_path.name}; hierarchy -check -top {case.top_module}; proc; opt; check",
                ],
                run_dir,
                timeout,
            )
        stages.append(self._stage(
            "structural_check",
            "pass" if structural["ok"] else "fail",
            evidence=structural,
            note="Yosys structural sanity check; not a formal property proof",
        ))
        stream.emit("STRUCTURAL_CHECK", stages[-1]["status"], evidence=structural)

        repeatability: dict[str, Any]
        if not candidate_result.get("ok"):
            repeatability = {"ok": False, "error": "candidate_simulation_failed"}
        else:
            repeat = self._simulate(
                case, candidate_source, run_dir / "repeat", "candidate.v", "repeat"
            )
            if not repeat.get("ok"):
                repeatability = {"ok": False, "error": "repeat_simulation_failed", "repeat": repeat}
            else:
                first = Path(str(candidate_result["output_path"])).read_text(encoding="utf-8", errors="ignore")
                second = Path(str(repeat["output_path"])).read_text(encoding="utf-8", errors="ignore")
                same, mismatch = oracle_gate._compare(first, second, max_evidence=2)
                repeatability = {
                    "ok": same,
                    "scope": "same_case_repeatability",
                    "mismatch": mismatch,
                    "first_output_sha256": candidate_result.get("output_sha256"),
                    "second_output_sha256": repeat.get("output_sha256"),
                }
        stages.append(self._stage(
            "repeatability",
            "pass" if repeatability["ok"] else "fail",
            evidence=repeatability,
            note="same-case deterministic repeat; not non-target regression",
        ))
        stream.emit("REPEATABILITY", stages[-1]["status"], evidence=repeatability)
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
            "schema_version": "r3e-aic-verification-result-v2",
            "case_id": case.case_id,
            "accepted": accepted,
            "stages": stages,
            "authority": "r3e.semantic_repair_bench.oracle_gate.judge + competition.scope_gate",
            "candidate_sha256": payload_hash(candidate_source),
            "case_evidence": case.evidence(),
            "toolchain": executable_versions(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "artifact_directory": str(run_dir),
            "events": stream.events,
        }
        if core_gate is not None:
            result["core_oracle"] = core_gate
        result["result_hash"] = payload_hash(result)
        return result


class CompetitionCandidateVerifier:
    """Adapter from the full competition result to the Core receipt contract."""

    verifier_hash = payload_hash({
        "verifier": "r3e-aic-competition-gates-v2",
        "stages": VerificationService.STAGES,
    })

    def __init__(self, service: VerificationService, run_prefix: str):
        self.service = service
        self.run_prefix = run_prefix
        self.results: dict[str, dict[str, Any]] = {}
        self.toolchain_fingerprint = {
            "schema_version": "r3e-adapter-toolchain-v1",
            "adapter_id": "r3e-aic-competition-verifier",
            "adapter_version": "2",
            "model_id": "runner-owned-verifier",
            "model_hash": payload_hash({"model": "runner-owned-verifier-v2"}),
            "verifier_id": "r3e-aic-competition-verifier",
            "verifier_hash": self.verifier_hash,
            "runtime_id": "python-subprocess-eda",
            "runtime_hash": payload_hash({"runtime": "python-subprocess-eda"}),
        }

    def __call__(
        self,
        *,
        policy: Any,
        case: Mapping[str, Any],
        current_case_evidence: Mapping[str, Any],
        slot: Mapping[str, Any],
        candidate_id: str,
        patch_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        replacement = patch_payload.get("replacement_rtl")
        if not isinstance(replacement, str) or not replacement:
            raise ValueError("candidate replacement_rtl is required")
        result = self.service.verify(
            str(case["case_id"]),
            replacement,
            run_id=f"{self.run_prefix}-{candidate_id}",
        )
        self.results[candidate_id] = result
        by_name = {stage["name"]: stage for stage in result.get("stages", [])}
        scope = by_name.get("scope", {}).get("evidence", {})
        core = result.get("core_oracle", {})
        return {
            "parse_ok": by_name.get("parse", {}).get("status") == "pass",
            "scope_ok": bool(scope.get("ok")),
            "compile_ok": by_name.get("compile", {}).get("status") == "pass",
            "compile_receipt_hash": payload_hash(by_name.get("compile", {})),
            "simulation_receipt_hash": payload_hash(by_name.get("simulation", {})),
            "formal_receipt_hash": payload_hash(by_name.get("structural_check", {})),
            "oracle_ok": bool(core.get("ok")),
            "changed_modules": len(scope.get("changed_modules", [])),
            "changed_blocks": len(scope.get("changed_blocks", [])),
            "ast_edit_count": int(scope.get("ast_edit_count", 0)),
        }
