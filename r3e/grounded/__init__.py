"""Shared runner-owned Grounded Runtime protocol objects."""

from .receipts import (
    GroundedReceiptViolation,
    build_command_receipt,
    verify_command_receipt,
)
from .command_runner import (
    GroundedCommandExecution,
    GroundedCommandRunner,
    GroundedCommandViolation,
)
from .provider_receipts import (
    GroundedProviderViolation,
    build_provider_receipt,
    provider_implementation_hash,
    verify_provider_receipt,
)
from .yosys_formal import (
    YosysFormalProvider,
    YosysFormalProviderViolation,
    formal_triplet_from_assessment,
    verify_formal_execution_receipt,
    verify_formal_proof_assessment,
    verify_formal_proof_triplet,
)
from .failure_descriptor import (
    GroundedFailureDescriptorViolation,
    build_grounded_failure_descriptor,
    verify_grounded_failure_descriptor,
)

__all__ = [
    "GroundedReceiptViolation",
    "GroundedCommandExecution",
    "GroundedCommandRunner",
    "GroundedCommandViolation",
    "GroundedProviderViolation",
    "GroundedFailureDescriptorViolation",
    "YosysFormalProvider",
    "YosysFormalProviderViolation",
    "build_command_receipt",
    "build_provider_receipt",
    "build_grounded_failure_descriptor",
    "formal_triplet_from_assessment",
    "provider_implementation_hash",
    "verify_command_receipt",
    "verify_provider_receipt",
    "verify_formal_execution_receipt",
    "verify_formal_proof_assessment",
    "verify_formal_proof_triplet",
    "verify_grounded_failure_descriptor",
]
