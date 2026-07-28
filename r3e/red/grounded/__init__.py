"""Grounded Red Discovery authority protocols.

This package is intentionally separate from the legacy adapter-evidence
validity path.  Objects here are proposals or runner-owned authority records;
model output alone never admits a bug.
"""

from .admission import (
    GroundedAdmissionViolation,
    decide_grounded_admission,
    verify_grounded_admission_decision,
)
from .mutation_plan import (
    MutationPlanViolation,
    build_mutation_plan,
    verify_mutation_plan,
)
from .registry import (
    GroundedRegistryViolation,
    GroundedRegistryBundle,
    load_grounded_registries,
)

__all__ = [
    "GroundedAdmissionViolation",
    "GroundedRegistryBundle",
    "GroundedRegistryViolation",
    "MutationPlanViolation",
    "build_mutation_plan",
    "decide_grounded_admission",
    "load_grounded_registries",
    "verify_grounded_admission_decision",
    "verify_mutation_plan",
]
