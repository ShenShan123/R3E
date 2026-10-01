"""Call budget and per-phase cost accounting for every model call in the loop.

``BudgetedClient`` wraps a JSON client. It refuses a call before it is sent
when the hard cap is reached, and it attributes every call's tokens to the
loop phase active at that time. The phases are:

- ``red``
- ``blue_inference``
- ``escalation``
- ``qualification``
- ``evaluation``
- ``authoring``

Learning cost is ``red + escalation + qualification + authoring``.
Inference cost is ``blue_inference + evaluation``.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from typing import Any, Iterator

PHASES = ("red", "blue_inference", "escalation", "qualification", "evaluation", "authoring")
LEARNING_PHASES = ("red", "escalation", "qualification", "authoring")


class CallBudgetExceeded(RuntimeError):
    """Raised before a call that would exceed the approved cap."""


class BudgetedClient:
    def __init__(self, inner: Any, *, max_calls: int):
        if max_calls < 0:
            raise ValueError("max_calls must be non-negative")
        self.inner = inner
        self.config = inner.config
        self.max_calls = int(max_calls)
        self.phase = "blue_inference"
        self.calls: dict[str, int] = defaultdict(int)
        self.tokens: dict[str, dict[str, int]] = defaultdict(lambda: {"input": 0, "output": 0})
        self.request_ids: list[str] = []

    def readiness(self):
        return self.inner.readiness()

    @property
    def total_calls(self) -> int:
        return sum(self.calls.values())

    @contextmanager
    def in_phase(self, phase: str) -> Iterator[None]:
        if phase not in PHASES:
            raise ValueError(f"unknown phase {phase}")
        previous, self.phase = self.phase, phase
        try:
            yield
        finally:
            self.phase = previous

    def complete_json(self, **kwargs: Any) -> dict[str, Any]:
        if self.total_calls >= self.max_calls:
            raise CallBudgetExceeded(
                f"call cap {self.max_calls} reached; raise it only with explicit approval"
            )
        self.calls[self.phase] += 1
        response = self.inner.complete_json(**kwargs)
        self.tokens[self.phase]["input"] += int(response.get("input_tokens", 0) or 0)
        self.tokens[self.phase]["output"] += int(response.get("output_tokens", 0) or 0)
        request_id = response.get("provider_request_id") or response.get("request_hash")
        if request_id:
            self.request_ids.append(str(request_id))
        return response

    def report(self) -> dict[str, Any]:
        def total(phases):
            return {
                "calls": sum(self.calls[p] for p in phases),
                "input_tokens": sum(self.tokens[p]["input"] for p in phases),
                "output_tokens": sum(self.tokens[p]["output"] for p in phases),
            }
        return {
            "max_calls": self.max_calls,
            "by_phase": {p: {"calls": self.calls[p], **self.tokens[p]} for p in PHASES},
            "learning": total(LEARNING_PHASES),
            "inference": total(("blue_inference", "evaluation")),
            "total_calls": self.total_calls,
        }
