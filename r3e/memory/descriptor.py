"""Build failure descriptors exclusively from current observable artifacts."""
from __future__ import annotations

from typing import Any, Mapping

from .schema import FORBIDDEN_FEATURE_FIELDS, FailureDescriptor, MemoryValidationError


def build_failure_descriptor(observations: Mapping[str, Any]) -> FailureDescriptor:
    """Validate and hash runner-produced, runtime-observable failure features."""
    leaked = set(observations) & FORBIDDEN_FEATURE_FIELDS
    if leaked:
        raise MemoryValidationError(
            f"descriptor input contains private/red-truth fields: {sorted(leaked)}"
        )
    return FailureDescriptor.create(observations)
