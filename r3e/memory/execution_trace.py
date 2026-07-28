"""Runner-owned execution tracing for memory-aware control plans."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from r3e.protocol.hashing import hash_payload

from .schema import ExecutionPlan


TRACE_SCHEMA_VERSION = "r3e-memory-execution-trace-v1"


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

