"""Replay-Activated Adversarial Memory (RAAM) protocol.

This package is the formal, non-prompt memory path.  Legacy strategy-memory
modules remain outside this namespace and are not imported here.
"""

from .schema import (
    ActiveMemoryBank,
    BudgetEnvelope,
    ControlMemory,
    ExecutionPlan,
    FailureDescriptor,
    MemoryLifecycleEvent,
    MemoryMatch,
    ReactivationDecision,
    RuntimeContext,
    ShadowPairedResult,
    VerifiedEpisode,
)

__all__ = [
    "ActiveMemoryBank",
    "BudgetEnvelope",
    "ControlMemory",
    "ExecutionPlan",
    "FailureDescriptor",
    "MemoryLifecycleEvent",
    "MemoryMatch",
    "ReactivationDecision",
    "RuntimeContext",
    "ShadowPairedResult",
    "VerifiedEpisode",
]
