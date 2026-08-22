"""Public schemas owned by the R³E-AIC competition layer."""

from .failure_descriptor import (
    FAILURE_DESCRIPTOR_SCHEMA,
    FailureDescriptor,
    FailureDescriptorError,
)

__all__ = [
    "FAILURE_DESCRIPTOR_SCHEMA",
    "FailureDescriptor",
    "FailureDescriptorError",
]
