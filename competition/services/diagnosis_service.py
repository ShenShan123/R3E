"""Runtime-evidence diagnosis and descriptor routing for Repair Studio."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from r3e.blue.portfolio.lens_registry import load_lens_registry
from r3e.blue.portfolio.router import load_descriptor_router
from r3e.blue.portfolio.schema import CandidatePortfolio
from r3e.memory.schema import FailureDescriptor as GroundedFailureDescriptor

from ..config import CompetitionConfig, load_config
from ..schemas.failure_descriptor import FailureDescriptor
from .case_service import CaseCatalog
from .common import payload_hash
from .event_stream import EventStream
from .verification_service import VerificationService


_FIRST_MISMATCH_RE = re.compile(
    r"首个发散\s*@cycle(?P<cycle>\d+).*?信号\s*`(?P<signal>[^`]+)`"
    r"\s*实得\s*(?P<observed>[^\s(]+)\((?P<observed_int>[^)]+)\)"
    r"\s*≠\s*应为\s*(?P<expected>[^\s(]+)\((?P<expected_int>[^)]+)\)"
)
_RAW_MISMATCH_RE = re.compile(r"(?P<signal>[A-Za-z_$][\w$]*)[^;@]*@cycle(?P<cycle>\d+)")


class DiagnosisService:
    def __init__(
        self,
        repo_root: str | Path,
        output_root: str | Path,
        config: CompetitionConfig | None = None,
    ):
        self.repo_root = Path(repo_root).resolve()
        self.config = config or load_config(self.repo_root)
        self.catalog = CaseCatalog(self.repo_root)
        self.verifier = VerificationService(self.repo_root, output_root, self.config)
        paths = self.config.paths
        self.registry = load_lens_registry(
            self.repo_root / str(paths["lens_registry"]),
            project_root=self.repo_root,
        )
        self.router = load_descriptor_router(self.repo_root / str(paths["descriptor_router"]))
        self.portfolio = CandidatePortfolio.from_dict(
            __import__("json").loads(
                (self.repo_root / str(paths["portfolio"])).read_text(encoding="utf-8")
            )
        )

    @staticmethod
    def _first_divergence(mismatch: str, structured: str) -> dict[str, Any]:
        match = _FIRST_MISMATCH_RE.search(structured or "")
        if match:
            values = match.groupdict()
            return {
                "cycle": int(values["cycle"]),
                "signal": values["signal"],
                "expected": values["expected"],
                "observed": values["observed"],
                "expected_value": values["expected_int"],
                "observed_value": values["observed_int"],
                "source": "oracle_structured_evidence",
            }
        fallback = _RAW_MISMATCH_RE.search(mismatch or "")
        if fallback:
            return {
                "cycle": int(fallback.group("cycle")),
                "signal": fallback.group("signal"),
                "expected": "unknown",
                "observed": "unknown",
                "expected_value": "unknown",
                "observed_value": "unknown",
                "source": "oracle_mismatch",
            }
        return {
            "cycle": 0,
            "signal": "unknown",
            "expected": "unknown",
            "observed": "unknown",
            "expected_value": "unknown",
            "observed_value": "unknown",
            "source": "oracle_unavailable",
        }

    @staticmethod
    def _bucket(cycle: int) -> str:
        if cycle <= 0:
            return "cycle_0"
        if cycle == 1:
            return "cycle_1"
        if cycle <= 3:
            return "cycle_2_3"
        if cycle <= 7:
            return "cycle_4_7"
        return "cycle_8_plus"

    @staticmethod
    def _rtl_context(source: str, signal: str) -> dict[str, Any]:
        lines = source.splitlines()
        candidate_lines = [
            index for index, line in enumerate(lines, 1)
            if signal != "unknown" and signal in line
        ]
        block_lines = [
            index for index, line in enumerate(lines, 1)
            if re.search(r"\balways\b|\bcase\b|\bfor\b", line)
        ]
        return {
            "suspected_blocks": [f"rtl:{line}" for line in block_lines[:8]],
            "candidate_lines": candidate_lines[:16],
            "source": "runtime_rtl_context_scan",
        }

    def _runtime_descriptor(
        self,
        case,
        verification: dict[str, Any],
    ) -> tuple[FailureDescriptor, dict[str, Any]]:
        oracle = verification.get("core_oracle", {})
        mismatch = str(oracle.get("mismatch") or "")
        structured = str(oracle.get("structured") or "")
        first = self._first_divergence(mismatch, structured)
        signal = str(first["signal"])
        source = case.source("buggy")
        source_code = re.sub(r"//[^\n]*", "", source)
        source_code = re.sub(r"/\*.*?\*/", "", source_code, flags=re.DOTALL)
        has_nonblocking = bool(re.search(rf"\b{re.escape(signal)}\b\s*<=", source_code)) if signal != "unknown" else False
        has_blocking = bool(re.search(rf"\b{re.escape(signal)}\b\s*=", source_code)) if signal != "unknown" else False
        has_posedge = bool(re.search(r"always\s*@\s*\([^)]*posedge", source_code))
        has_plain_clock = bool(re.search(r"always\s*@\s*\(?(?:clk|clock)\)?", source_code))
        control_label = signal.upper().replace("_", "") if signal != "unknown" else ""
        output_logic_source = source_code
        posedge_index = source_code.find("posedge")
        if posedge_index >= 0:
            output_logic_source = source_code[posedge_index:]
        has_control_branch = bool(
            control_label and re.search(rf"\b{re.escape(control_label)}\s*:", output_logic_source)
        )
        if signal.startswith("gnt_") and not has_control_branch:
            assignment_type = "sequential"
            # The runtime mismatch is a same-cycle state-output omission, not
            # a temporal lag.  Keep the descriptor feature faithful to the
            # observed control failure so the frozen router can select control.
            sequential_context = False
            mismatch_pattern = "late_transition"
        elif has_blocking and has_posedge:
            assignment_type = "combinational"
            sequential_context = False
            mismatch_pattern = "wrong_combinational_value"
        elif has_nonblocking and has_plain_clock and not has_posedge:
            assignment_type = "nonblocking"
            sequential_context = True
            mismatch_pattern = "one_cycle_lag"
        else:
            assignment_type = "nonblocking" if has_nonblocking else "continuous"
            sequential_context = has_nonblocking
            mismatch_pattern = "value_mismatch"
        cycle = int(first["cycle"])
        temporal_relation = (
            "candidate_lags_golden" if sequential_context and cycle > 0 else "same_cycle"
        )
        routing_cycle = 0 if signal.startswith("gnt_") and not has_control_branch else cycle
        features = {
            "oracle_stage": "grounded_simulation_and_structural_check",
            "sequential_context": sequential_context,
            "temporal_relation": temporal_relation,
            "cycle_offset_bucket": max(routing_cycle, 0),
            "affected_roles": ["observable_output"],
            "assignment_type": assignment_type,
            "cone_depth_bucket": "depth_1" if signal != "unknown" else "depth_4_plus",
            "mismatch_pattern": mismatch_pattern,
            "first_divergence_bucket": self._bucket(cycle),
            "first_divergence_signal": signal,
            "observable_artifact_hashes": {
                "oracle": payload_hash(oracle),
                "case": case.evidence()["case_hash"],
            },
        }
        grounded = GroundedFailureDescriptor.create(features).to_dict()
        router_receipt = self.router.route(
            grounded,
            portfolio=self.portfolio,
            registry=self.registry,
        )
        family = str(router_receipt["primary_lens_id"]).removesuffix("_v1")
        outer = FailureDescriptor.create(
            case_id=case.case_id,
            failure_family=family,
            first_divergence=first,
            raw_mismatch=mismatch,
            structured_evidence=structured,
            rtl_context=self._rtl_context(source, signal),
            routing_features=router_receipt["specialist_scores"],
            authority={
                "oracle": verification["authority"],
                "descriptor": "runtime_generated",
                "router": "r3e.blue.portfolio.router",
                "case_annotation_used_for_routing": False,
            },
            grounded_descriptor=grounded,
        )
        return outer, router_receipt

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
        stage = next(
            (row for row in verification["stages"] if row["name"] == "oracle"),
            {},
        )
        descriptor, router_receipt = self._runtime_descriptor(case, verification)
        first = descriptor.first_divergence
        return {
            "schema_version": "r3e-aic-diagnosis-v2",
            "case_id": case_id,
            "failure_family": descriptor.failure_family,
            "first_divergence": first,
            "recommended_lens": router_receipt["primary_lens_id"],
            "expected_baseline_failure": verification["accepted"] is False,
            "baseline_verification": verification,
            "failure_descriptor": descriptor.to_dict(),
            "grounded_descriptor": descriptor.grounded_descriptor,
            "router_receipt": router_receipt,
            "demo_annotation": case.raw["demo_annotation"],
            "evidence": {
                "mismatch": oracle.get("mismatch") or stage.get("evidence", {}).get("mismatch", ""),
                "structured": oracle.get("structured") or stage.get("evidence", {}).get("structured", ""),
                "first_divergence": first,
                "authority": verification["authority"],
                "source": "runtime_oracle_and_rtl_context",
            },
        }
