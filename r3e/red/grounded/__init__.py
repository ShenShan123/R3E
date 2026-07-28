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
from .materializers import (
    AstMaterializationViolation,
    inverse_materialization,
    materialize_comparator,
    verify_materialization_receipt,
)

__all__ = [
    "GroundedAdmissionViolation",
    "GroundedRegistryBundle",
    "GroundedRegistryViolation",
    "MutationPlanViolation",
    "AstMaterializationViolation",
    "build_mutation_plan",
    "decide_grounded_admission",
    "inverse_materialization",
    "load_grounded_registries",
    "materialize_comparator",
    "verify_grounded_admission_decision",
    "verify_materialization_receipt",
    "verify_mutation_plan",
]
