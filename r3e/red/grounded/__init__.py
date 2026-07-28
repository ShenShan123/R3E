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
    materialize_operator,
    verify_materialization_receipt,
)
from .operator_ast import operator_nodes
from .formal_rejection import (
    GroundedFormalRejectionViolation,
    append_formal_rejection,
    build_formal_rejection,
    build_formal_rejection_validity,
    load_formal_rejection_archive,
    verify_formal_rejection,
    verify_formal_rejection_validity,
)

__all__ = [
    "GroundedAdmissionViolation",
    "GroundedRegistryBundle",
    "GroundedRegistryViolation",
    "GroundedFormalRejectionViolation",
    "MutationPlanViolation",
    "AstMaterializationViolation",
    "build_mutation_plan",
    "build_formal_rejection",
    "build_formal_rejection_validity",
    "append_formal_rejection",
    "decide_grounded_admission",
    "inverse_materialization",
    "load_grounded_registries",
    "load_formal_rejection_archive",
    "materialize_comparator",
    "materialize_operator",
    "operator_nodes",
    "verify_grounded_admission_decision",
    "verify_formal_rejection",
    "verify_formal_rejection_validity",
    "verify_materialization_receipt",
    "verify_mutation_plan",
]
