"""Arena-facing validity records derived from Grounded Red authority."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from r3e.protocol.hashing import hash_payload


ARENA_GROUNDED_AUTHORITY = "runner_owned_grounded_execution"
_EVIDENCE_FIELDS = {
    "authority_mode",
    "execution_bundle_hash",
    "admission_decision_hash",
    "plan_hash",
    "clean_rtl_hash",
    "poison_rtl_hash",
    "semantic_diff_receipt_hash",
    "runtime_effect_receipt_hash",
}
_RECORD_FIELDS = {
    "proven_valid",
    "checks",
    "rejection_reasons",
    "evidence",
    "result_hash",
}


class GroundedArenaValidityViolation(RuntimeError):
    """Raised when an arena validity record is not reconstructable."""


def build_grounded_arena_validity(
    bundle: Mapping[str, Any],
) -> dict[str, Any]:
    payload = deepcopy(dict(bundle))
    decision = payload["admission_decision"]
    evidence = payload["evidence"]
    record = {
        "proven_valid": bool(decision["admitted"]),
        "checks": deepcopy(dict(decision["checks"])),
        "rejection_reasons": list(decision["rejection_reasons"]),
        "evidence": {
            "authority_mode": ARENA_GROUNDED_AUTHORITY,
            "execution_bundle_hash": payload["bundle_hash"],
            "admission_decision_hash": decision["decision_hash"],
            "plan_hash": payload["plan"]["plan_hash"],
            "clean_rtl_hash": payload["materialization_receipt"][
                "clean_rtl_hash"
            ],
            "poison_rtl_hash": payload["materialization_receipt"][
                "poison_rtl_hash"
            ],
            "semantic_diff_receipt_hash": evidence["semantic_diff"][
                "receipt_hash"
            ],
            "runtime_effect_receipt_hash": evidence["runtime_effect"][
                "receipt_hash"
            ],
        },
    }
    record["result_hash"] = hash_payload(record)
    return verify_grounded_arena_validity(
        record, execution_bundle=payload
    )


def verify_grounded_arena_validity(
    record: Mapping[str, Any],
    *,
    execution_bundle: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = deepcopy(dict(record))
    if set(payload) != _RECORD_FIELDS:
        raise GroundedArenaValidityViolation(
            "grounded arena validity fields mismatch"
        )
    if payload["result_hash"] != hash_payload({
        key: value for key, value in payload.items()
        if key != "result_hash"
    }):
        raise GroundedArenaValidityViolation(
            "grounded arena validity hash mismatch"
        )
    checks = payload["checks"]
    if (
        not isinstance(checks, dict)
        or set(checks) != {f"G{index}_{name}" for index, name in (
            (1, "source_integrity"),
            (2, "parser_elaboration"),
            (3, "clean_baseline"),
            (4, "poison_executability"),
            (5, "functional_failure"),
            (6, "determinism"),
            (7, "revert_proof"),
            (8, "semantic_family_proof"),
            (9, "runtime_effect_proof"),
            (10, "non_triviality"),
            (11, "minimization"),
        )}
        or any(not isinstance(value, bool) for value in checks.values())
    ):
        raise GroundedArenaValidityViolation(
            "grounded arena validity checks mismatch"
        )
    failures = [name for name, passed in checks.items() if not passed]
    if (
        not isinstance(payload["proven_valid"], bool)
        or payload["proven_valid"] != all(checks.values())
        or payload["rejection_reasons"] != failures
    ):
        raise GroundedArenaValidityViolation(
            "grounded arena validity decision mismatch"
        )
    evidence = payload["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != _EVIDENCE_FIELDS:
        raise GroundedArenaValidityViolation(
            "grounded arena validity evidence fields mismatch"
        )
    if evidence["authority_mode"] != ARENA_GROUNDED_AUTHORITY:
        raise GroundedArenaValidityViolation(
            "grounded arena validity authority mismatch"
        )
    if execution_bundle is not None:
        bundle = dict(execution_bundle)
        decision = bundle.get("admission_decision") or {}
        grounded_evidence = bundle.get("evidence") or {}
        materialization = bundle.get("materialization_receipt") or {}
        expected = {
            "authority_mode": ARENA_GROUNDED_AUTHORITY,
            "execution_bundle_hash": bundle.get("bundle_hash"),
            "admission_decision_hash": decision.get("decision_hash"),
            "plan_hash": (bundle.get("plan") or {}).get("plan_hash"),
            "clean_rtl_hash": materialization.get("clean_rtl_hash"),
            "poison_rtl_hash": materialization.get("poison_rtl_hash"),
            "semantic_diff_receipt_hash": (
                grounded_evidence.get("semantic_diff") or {}
            ).get("receipt_hash"),
            "runtime_effect_receipt_hash": (
                grounded_evidence.get("runtime_effect") or {}
            ).get("receipt_hash"),
        }
        if evidence != expected:
            raise GroundedArenaValidityViolation(
                "grounded arena validity is not bound to its bundle"
            )
    return payload
