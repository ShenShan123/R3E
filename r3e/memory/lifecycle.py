"""Memory lifecycle transition rules."""
from __future__ import annotations

from .schema import MEMORY_STATUSES


class MemoryLifecycleViolation(RuntimeError):
    """Raised when a memory permission transition is invalid."""


ALLOWED_TRANSITIONS = {
    "candidate": {"shadow_testing", "retired"},
    "shadow_testing": {
        "replay_qualified", "candidate", "stale", "harmful", "retired",
    },
    "replay_qualified": {"active_dormant", "revalidation_required", "retired"},
    "active_dormant": {
        "revalidation_required", "stale", "harmful", "superseded", "retired",
    },
    "revalidation_required": {
        "shadow_testing", "active_dormant", "stale", "harmful", "retired",
    },
    "stale": {"shadow_testing", "retired"},
    "harmful": {"retired"},
    "superseded": {"retired"},
    "retired": set(),
}


def validate_transition(previous: str, new: str) -> None:
    if previous not in MEMORY_STATUSES or new not in MEMORY_STATUSES:
        raise MemoryLifecycleViolation("unknown memory lifecycle status")
    if new not in ALLOWED_TRANSITIONS[previous]:
        raise MemoryLifecycleViolation(f"invalid memory transition: {previous} -> {new}")
