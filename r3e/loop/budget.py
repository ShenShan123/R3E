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
- ``mastery`` (curriculum loop: paired with/without-memory runs on Red's
  follow-up variants; their outcome feeds Red's next decision)

- ``cross_model`` (mastery tests repeated with other Blue models; measurement
  only, never fed back into either side)
- ``localization`` (repository tasks: Blue's file-selection call before a repair)
- ``confirmation`` (curriculum loop: repeat runs on a bug Blue failed, so
  only reproducible failures count as weak points; part of Red's cost)

Learning cost is ``red + escalation + qualification + authoring + mastery + confirmation``.
Inference cost is ``blue_inference + evaluation + localization``.

When ``on_call`` is set, every call is reported as it happens, successful or
failed. The CLI writes these reports to the run's hash-chained ``calls``
ledger, so cost survives an interrupted or stopped run (``cost_from_calls``
rebuilds the totals).
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, Mapping

PHASES = ("red", "blue_inference", "escalation", "qualification", "evaluation", "authoring", "mastery",
          "cross_model", "confirmation", "localization")
LEARNING_PHASES = ("red", "escalation", "qualification", "authoring", "mastery", "confirmation")


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
        self.failed: dict[str, int] = defaultdict(int)
        self.request_ids: list[str] = []
        self.on_call: Callable[[dict[str, Any]], None] | None = None
        # optional per-phase output caps (e.g. a larger budget for escalation);
        # each must not exceed the inner client's configured maximum
        self.output_caps: dict[str, int] = {}

    def _emit(self, entry: dict[str, Any]) -> None:
        if self.on_call is not None:
            self.on_call(entry)

    def readiness(self):
        return self.inner.readiness()

    def sibling(self, inner: Any) -> "BudgetedClient":
        """Another client (e.g. Red with different thinking settings) on the same cap and ledgers."""
        other = BudgetedClient(inner, max_calls=self.max_calls)
        other.calls, other.tokens, other.failed, other.request_ids = (
            self.calls, self.tokens, self.failed, self.request_ids)
        other.on_call = self.on_call
        other.output_caps = self.output_caps
        return other

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
        # JSON mode is refused by the provider unless the prompt mentions JSON;
        # catch that here, before a call is spent (fake transports do not check)
        if not any("json" in str(m.get("content", "")).lower() for m in kwargs.get("messages") or []):
            raise ValueError("a JSON-mode request must mention 'json' in its prompt (provider rule)")
        if self.total_calls >= self.max_calls:
            raise CallBudgetExceeded(
                f"call cap {self.max_calls} reached; raise it only with explicit approval"
            )
        self.calls[self.phase] += 1
        cap = self.output_caps.get(self.phase)
        if cap is not None and "maximum_output_tokens" not in kwargs:
            kwargs["maximum_output_tokens"] = min(int(cap), int(self.config.maximum_output_tokens))
        try:
            response = self.inner.complete_json(**kwargs)
        except Exception as exc:
            # failed calls still consume tokens (e.g. finish_reason=length)
            diag = getattr(exc, "diagnostics", None) or {}
            self.tokens[self.phase]["input"] += int(diag.get("input_tokens", 0) or 0)
            self.tokens[self.phase]["output"] += int(diag.get("output_tokens", 0) or 0)
            self.failed[self.phase] += 1
            self._emit({"phase": self.phase, "ok": False, "error": type(exc).__name__,
                        "input_tokens": int(diag.get("input_tokens", 0) or 0),
                        "output_tokens": int(diag.get("output_tokens", 0) or 0),
                        "finish_reason": diag.get("finish_reason")})
            raise
        self.tokens[self.phase]["input"] += int(response.get("input_tokens", 0) or 0)
        self.tokens[self.phase]["output"] += int(response.get("output_tokens", 0) or 0)
        request_id = response.get("provider_request_id") or response.get("request_hash")
        if request_id:
            self.request_ids.append(str(request_id))
        self._emit({"phase": self.phase, "ok": True,
                    "input_tokens": int(response.get("input_tokens", 0) or 0),
                    "output_tokens": int(response.get("output_tokens", 0) or 0),
                    "request_hash": response.get("request_hash")})
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
            "by_phase": {p: {"calls": self.calls[p], "failed_calls": self.failed[p], **self.tokens[p]}
                         for p in PHASES},
            "learning": total(LEARNING_PHASES),
            "inference": total(("blue_inference", "evaluation", "localization")),
            "total_calls": self.total_calls,
        }


def cost_from_calls(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Rebuild per-phase totals from the ``calls`` ledger (works for stopped runs)."""
    by_phase = {p: {"calls": 0, "failed_calls": 0, "input": 0, "output": 0} for p in PHASES}
    for row in rows:
        cell = by_phase[row["phase"]]
        cell["calls"] += 1
        cell["failed_calls"] += 0 if row.get("ok") else 1
        cell["input"] += int(row.get("input_tokens", 0) or 0)
        cell["output"] += int(row.get("output_tokens", 0) or 0)
    total = lambda ps: {"calls": sum(by_phase[p]["calls"] for p in ps),
                        "input_tokens": sum(by_phase[p]["input"] for p in ps),
                        "output_tokens": sum(by_phase[p]["output"] for p in ps)}
    return {"by_phase": by_phase, "learning": total(LEARNING_PHASES),
            "inference": total(("blue_inference", "evaluation", "localization")),
            "total_calls": sum(v["calls"] for v in by_phase.values())}
