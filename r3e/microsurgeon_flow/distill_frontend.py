"""
distill_frontend.py — build frontend skill payload from RepairResult and distill.

Writes only to MEMORY_ROOT/frontend_lib (gated by three_lib_gate safety checks).
Never touches .micro_surgeon_memory real library.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make sibling three_lib_gate importable regardless of CWD or invocation path.
_FLOW_DIR = Path(__file__).parent
if str(_FLOW_DIR) not in sys.path:
    sys.path.insert(0, str(_FLOW_DIR))

from three_lib_gate import make_domain_io  # noqa: E402


def build_frontend_skill_payload(
    repair_result,
    *,
    case_id: str,
    error_signature: str,
    context_pattern: str,
    skill_name: str,
    rtl_rel_path: str,
) -> dict:
    """
    Assemble a frontend skill payload dict from a successful RepairResult.

    Raises ValueError if repair_result.success is False — failed repairs are
    never distilled.  case_id is intentionally absent from the returned dict;
    it must be passed separately to vet_distill as a kwarg.
    """
    if not repair_result.success:
        raise ValueError(
            f"Cannot distill a failed repair (success=False): "
            f"old_identifier={repair_result.old_identifier!r}"
        )

    return {
        "skill_name": skill_name,
        "precondition": {
            "error_signature": error_signature,
            "context_pattern": context_pattern,
        },
        "action_template": {
            "repair_strategy": "llm_single_site_identifier_replacement",
            "allowed_edit_scope": [f"{rtl_rel_path}:L{repair_result.target_line}"],
            "key_actions": [
                f"replace identifier {repair_result.old_identifier}"
                f" -> {repair_result.new_identifier}"
            ],
            "source_model": repair_result.model,
        },
        "validation": {
            "frontend_metric": "equiv_induct_proven",
            "equiv_result": (
                f"{repair_result.proven}/{repair_result.total} proven,"
                f" {repair_result.unproven} unproven"
            ),
            "equiv_config": "equiv_induct -undef -seq 4",
        },
        "rollback_condition": {
            "equiv_fails_or_frontend_regression": True,
        },
    }


def distill_frontend_skill(
    repair_result,
    *,
    case_id: str,
    error_signature: str,
    context_pattern: str,
    skill_name: str,
    rtl_rel_path: str,
) -> tuple:
    """
    Build frontend skill payload and distill it through the three-lib safety gate.

    Returns (happened: bool, vet_result: VetResult, artifact_or_None).
    Exceptions from build / make_domain_io / vet_distill propagate unchanged.
    """
    payload = build_frontend_skill_payload(
        repair_result,
        case_id=case_id,
        error_signature=error_signature,
        context_pattern=context_pattern,
        skill_name=skill_name,
        rtl_rel_path=rtl_rel_path,
    )
    mgr, guard = make_domain_io("frontend")
    return guard.vet_distill(mgr, **payload, case_id=case_id)
