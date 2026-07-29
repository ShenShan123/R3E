"""Runner-owned execution tracing for memory-aware control plans."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from r3e.protocol.hashing import hash_payload

from .schema import ExecutionPlan


TRACE_SCHEMA_VERSION = "r3e-memory-execution-trace-v1"
PORTFOLIO_TRACE_SCHEMA_VERSION = "r3e-memory-execution-trace-v2"


class ExecutionTraceViolation(RuntimeError):
    """Raised when an executor echoes a plan without applying its controls."""


class ExecutionTraceRecorder:
    """Execute instrumented callbacks and attest their observable results."""

    def __init__(self, plan: ExecutionPlan):
        self.plan = plan
        self.events: list[dict[str, Any]] = []

    def _run(
        self,
        event_type: str,
        name: str,
        operation: Callable[[], Any],
        **metadata: Any,
    ) -> Any:
        result = operation()
        event = {
            "event_type": event_type,
            "name": name,
            **deepcopy(metadata),
            "result_hash": hash_payload(result),
        }
        event["event_hash"] = hash_payload(event)
        self.events.append(event)
        return result

    def run_analyzer(self, name: str, operation: Callable[[], Any]) -> Any:
        return self._run("analyzer", name, operation)

    def run_slice(self, mode: str, operation: Callable[[], Any]) -> Any:
        return self._run("rtl_slice", mode, operation)

    def run_candidate_generation(
        self, count: int, operation: Callable[[], Any]
    ) -> Any:
        return self._run(
            "candidate_generation", "initial", operation, count=int(count)
        )

    def run_revision(self, index: int, operation: Callable[[], Any]) -> Any:
        return self._run("revision", str(index), operation, index=int(index))

    def run_verifier(self, name: str, operation: Callable[[], Any]) -> Any:
        return self._run("verifier", name, operation)

    def stop(self, reason: str) -> None:
        event = {"event_type": "stop", "name": reason}
        event["event_hash"] = hash_payload(event)
        self.events.append(event)

    def finalize(self) -> dict[str, Any]:
        controls = self.plan.controls
        analyzers = [
            event["name"] for event in self.events
            if event["event_type"] == "analyzer"
        ]
        expected_analyzers = list(controls.get("enable_analyzers") or [])
        if sorted(analyzers) != sorted(expected_analyzers):
            raise ExecutionTraceViolation("execution trace analyzer set differs from plan")
        disabled = set(controls.get("disable_analyzers") or [])
        if disabled & set(analyzers):
            raise ExecutionTraceViolation("execution trace invoked a disabled analyzer")
        slices = [
            event["name"] for event in self.events
            if event["event_type"] == "rtl_slice"
        ]
        expected_slice = str(controls.get("rtl_slice_mode") or "none")
        if slices != ([] if expected_slice == "none" else [expected_slice]):
            raise ExecutionTraceViolation("execution trace RTL slice differs from plan")
        candidate_events = [
            event for event in self.events
            if event["event_type"] == "candidate_generation"
        ]
        expected_candidates = int(controls.get("initial_candidates") or 0)
        if expected_candidates and (
            len(candidate_events) != 1
            or int(candidate_events[0].get("count") or -1) != expected_candidates
        ):
            raise ExecutionTraceViolation(
                "execution trace candidate count differs from plan"
            )
        revisions = [
            int(event["index"]) for event in self.events
            if event["event_type"] == "revision"
        ]
        expected_revisions = int(controls.get("revision_rounds") or 0)
        if revisions != list(range(1, expected_revisions + 1)):
            raise ExecutionTraceViolation(
                "execution trace revision rounds differ from plan"
            )
        verifiers = [
            event["name"] for event in self.events
            if event["event_type"] == "verifier"
        ]
        expected_verifiers = list(controls.get("verifier_order") or [])
        if verifiers != expected_verifiers:
            raise ExecutionTraceViolation(
                "execution trace verifier sequence differs from plan"
            )
        stops = [
            event["name"] for event in self.events
            if event["event_type"] == "stop"
        ]
        expected_stop = str(controls.get("early_stop") or "")
        if expected_stop and stops != [expected_stop]:
            raise ExecutionTraceViolation(
                "execution trace stopping rule differs from plan"
            )
        payload = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "execution_plan_hash": self.plan.plan_hash,
            "events": deepcopy(self.events),
        }
        payload["trace_hash"] = hash_payload(payload)
        return payload


class PortfolioExecutionTraceRecorder:
    """Record and verify every planned ACP candidate slot and verifier stage."""

    _VERIFICATION_STAGES = (
        ("candidate_parse", "parse_ok"),
        ("candidate_scope", "scope_ok"),
        ("candidate_compile", "compile_ok"),
        ("candidate_simulation", "simulation_receipt_hash"),
        ("candidate_formal", "formal_receipt_hash"),
        ("candidate_oracle", "oracle_ok"),
    )

    def __init__(self, allocation_plan: dict[str, Any]):
        self.plan = deepcopy(allocation_plan)
        self.events: list[dict[str, Any]] = []

    def _append(self, payload: dict[str, Any]) -> None:
        event = deepcopy(payload)
        event["event_hash"] = hash_payload(event)
        self.events.append(event)

    def record_generation(self, receipt: dict[str, Any]) -> None:
        self._append({
            "event_type": "candidate_generation",
            "candidate_id": receipt["candidate_id"],
            "slot_index": receipt["slot_index"],
            "lens_id": receipt["lens_id"],
            "lens_hash": receipt["lens_hash"],
            "generation_receipt_hash": receipt["generation_hash"],
        })

    def record_verification(self, receipt: dict[str, Any]) -> None:
        for event_type, field in self._VERIFICATION_STAGES:
            self._append({
                "event_type": event_type,
                "candidate_id": receipt["candidate_id"],
                "result": deepcopy(receipt[field]),
                "verification_receipt_hash": receipt["verification_hash"],
            })

    def record_semantic_signature(self, receipt: dict[str, Any]) -> None:
        self._append({
            "event_type": "candidate_signature",
            "candidate_id": receipt["candidate_id"],
            "semantic_signature_hash": receipt["signature"][
                "signature_hash"
            ],
            "signature_receipt_hash": receipt["receipt_hash"],
            "provider_hash": receipt["provider_hash"],
        })

    def record_diversity(self, receipt: dict[str, Any]) -> None:
        self._append({
            "event_type": "portfolio_diversity",
            "diversity_receipt_hash": receipt["diversity_hash"],
            "measurement_only": receipt["measurement_only"],
            "retry_calls": receipt["retry_calls"],
        })

    def record_selection(self, receipt: dict[str, Any]) -> None:
        self._append({
            "event_type": "candidate_selection",
            "selected_candidate_id": receipt["selected_candidate_id"],
            "selection_policy": receipt["selection_policy"],
            "selection_receipt_hash": receipt["selection_hash"],
        })

    def finalize(
        self,
        *,
        generation_receipts: list[dict[str, Any]],
        semantic_signature_receipts: list[dict[str, Any]],
        verification_receipts: list[dict[str, Any]],
        diversity_receipt: dict[str, Any],
        selection_receipt: dict[str, Any],
        provider_calls: int,
        total_tokens: int,
        max_provider_calls: int,
        max_tokens: int,
    ) -> dict[str, Any]:
        slots = list(self.plan.get("slots") or [])
        if int(self.plan.get("candidate_budget") or -1) != len(slots):
            raise ExecutionTraceViolation(
                "allocation plan candidate budget differs from slots"
            )
        if provider_calls != len(slots) or provider_calls > max_provider_calls:
            raise ExecutionTraceViolation(
                "portfolio provider call count exceeds or differs from plan"
            )
        if total_tokens < 0 or total_tokens > max_tokens:
            raise ExecutionTraceViolation("portfolio token budget exceeded")
        if len(generation_receipts) != len(slots):
            raise ExecutionTraceViolation(
                "execution trace does not cover every allocation slot"
            )
        generations = {
            int(row["slot_index"]): row for row in generation_receipts
        }
        if len(generations) != len(generation_receipts):
            raise ExecutionTraceViolation("an allocation slot was generated twice")
        for slot in slots:
            index = int(slot["slot_index"])
            receipt = generations.get(index)
            if receipt is None:
                raise ExecutionTraceViolation("an allocation slot was not generated")
            if (
                receipt["lens_id"] != slot["lens_id"]
                or receipt["lens_hash"] != slot["lens_hash"]
            ):
                raise ExecutionTraceViolation(
                    "candidate lens differs from allocation plan"
                )
        generation_ids = [row["candidate_id"] for row in generation_receipts]
        signature_ids = [
            row["candidate_id"] for row in semantic_signature_receipts
        ]
        verification_ids = [row["candidate_id"] for row in verification_receipts]
        if generation_ids != signature_ids or generation_ids != verification_ids:
            raise ExecutionTraceViolation(
                "every generated candidate must have signature and verification receipts"
            )
        for signature, verification in zip(
            semantic_signature_receipts,
            verification_receipts,
            strict=True,
        ):
            if (
                signature["patch_hash"] != verification["patch_hash"]
                or signature["signature"]["signature_hash"]
                != verification["semantic_patch_signature_hash"]
            ):
                raise ExecutionTraceViolation(
                    "semantic signature receipt chain mismatch"
                )
        generation_events = [
            event for event in self.events
            if event["event_type"] == "candidate_generation"
        ]
        if len(generation_events) != len(slots):
            raise ExecutionTraceViolation(
                "execution trace candidate event count differs from plan"
            )
        for candidate_id in generation_ids:
            event_types = [
                event["event_type"] for event in self.events
                if event.get("candidate_id") == candidate_id
            ]
            expected = [
                "candidate_generation",
                "candidate_signature",
                *[stage for stage, _field in self._VERIFICATION_STAGES],
            ]
            if event_types != expected:
                raise ExecutionTraceViolation(
                    "candidate verifier event sequence is incomplete"
                )
        diversity_events = [
            event for event in self.events
            if event["event_type"] == "portfolio_diversity"
        ]
        if (
            len(diversity_events) != 1
            or diversity_events[0]["diversity_receipt_hash"]
            != diversity_receipt["diversity_hash"]
            or diversity_receipt.get("retry_calls") != 0
        ):
            raise ExecutionTraceViolation(
                "execution trace diversity measurement is incomplete"
            )
        selection_events = [
            event for event in self.events
            if event["event_type"] == "candidate_selection"
        ]
        if len(selection_events) != 1:
            raise ExecutionTraceViolation(
                "execution trace must contain one runner-owned selection"
            )
        if (
            selection_events[0]["selection_receipt_hash"]
            != selection_receipt["selection_hash"]
        ):
            raise ExecutionTraceViolation("selection trace hash mismatch")
        payload = {
            "schema_version": PORTFOLIO_TRACE_SCHEMA_VERSION,
            "allocation_plan_hash": self.plan["plan_hash"],
            "provider_calls": provider_calls,
            "total_tokens": total_tokens,
            "events": deepcopy(self.events),
        }
        payload["trace_hash"] = hash_payload(payload)
        return payload
