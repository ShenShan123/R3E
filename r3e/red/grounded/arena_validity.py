"""Arena-facing validity records derived from Grounded Red authority."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from r3e.grounded.failure_descriptor import (
    verify_grounded_failure_descriptor,
)
from r3e.grounded.yosys_formal import verify_formal_proof_triplet
from r3e.protocol.hashing import hash_payload


ARENA_GROUNDED_AUTHORITY = "runner_owned_grounded_execution"
_EVIDENCE_FIELDS = {
    "authority_mode",
    "authority_bundle_hash",
    "execution_bundle_hash",
    "formal_proof_triplet_hash",
    "failure_descriptor_hash",
    "failure_descriptor_receipt_hash",
    "admission_decision_hash",
    "plan_hash",
    "clean_rtl_hash",
    "poison_rtl_hash",
    "semantic_diff_receipt_hash",
    "runtime_effect_receipt_hash",
}
_LEGACY_EVIDENCE_FIELDS = {
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


def _verify_decision_record(
    payload: Mapping[str, Any],
    *,
    evidence_fields: set[str],
) -> dict[str, Any]:
    value = deepcopy(dict(payload))
    if set(value) != _RECORD_FIELDS:
        raise GroundedArenaValidityViolation(
            "grounded arena validity fields mismatch"
        )
    if value["result_hash"] != hash_payload({
        key: item for key, item in value.items()
        if key != "result_hash"
    }):
        raise GroundedArenaValidityViolation(
            "grounded arena validity hash mismatch"
        )
    checks = value["checks"]
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
        or any(not isinstance(item, bool) for item in checks.values())
    ):
        raise GroundedArenaValidityViolation(
            "grounded arena validity checks mismatch"
        )
    failures = [
        name for name, passed in checks.items() if not passed
    ]
    if (
        not isinstance(value["proven_valid"], bool)
        or value["proven_valid"] != all(checks.values())
        or value["rejection_reasons"] != failures
    ):
        raise GroundedArenaValidityViolation(
            "grounded arena validity decision mismatch"
        )
    evidence = value["evidence"]
    if (
        not isinstance(evidence, dict)
        or set(evidence) != evidence_fields
    ):
        raise GroundedArenaValidityViolation(
            "grounded arena validity evidence fields mismatch"
        )
    if evidence["authority_mode"] != ARENA_GROUNDED_AUTHORITY:
        raise GroundedArenaValidityViolation(
            "grounded arena validity authority mismatch"
        )
    return value


def build_grounded_arena_validity(
    authority_bundle: Mapping[str, Any],
) -> dict[str, Any]:
    authority = deepcopy(dict(authority_bundle))
    execution = authority["execution_bundle"]
    formal = authority["formal_proof_triplet"]
    descriptor = authority["failure_descriptor"]
    descriptor_receipt = authority[
        "failure_descriptor_receipt"
    ]
    decision = execution["admission_decision"]
    evidence = execution["evidence"]
    record = {
        "proven_valid": bool(decision["admitted"]),
        "checks": deepcopy(dict(decision["checks"])),
        "rejection_reasons": list(decision["rejection_reasons"]),
        "evidence": {
            "authority_mode": ARENA_GROUNDED_AUTHORITY,
            "authority_bundle_hash": authority["authority_hash"],
            "execution_bundle_hash": execution["bundle_hash"],
            "formal_proof_triplet_hash": formal["triplet_hash"],
            "failure_descriptor_hash": descriptor[
                "descriptor_hash"
            ],
            "failure_descriptor_receipt_hash": descriptor_receipt[
                "receipt_hash"
            ],
            "admission_decision_hash": decision["decision_hash"],
            "plan_hash": execution["plan"]["plan_hash"],
            "clean_rtl_hash": execution["materialization_receipt"][
                "clean_rtl_hash"
            ],
            "poison_rtl_hash": execution["materialization_receipt"][
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
        record, authority_bundle=authority
    )


def verify_grounded_arena_validity(
    record: Mapping[str, Any],
    *,
    authority_bundle: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _verify_decision_record(
        record, evidence_fields=_EVIDENCE_FIELDS
    )
    evidence = payload["evidence"]
    if authority_bundle is not None:
        authority = dict(authority_bundle)
        if authority.get("authority_hash") != hash_payload({
            key: value for key, value in authority.items()
            if key != "authority_hash"
        }):
            raise GroundedArenaValidityViolation(
                "grounded authority hash mismatch"
            )
        execution = dict(authority.get("execution_bundle") or {})
        if execution.get("bundle_hash") != hash_payload({
            key: value for key, value in execution.items()
            if key != "bundle_hash"
        }):
            raise GroundedArenaValidityViolation(
                "grounded execution bundle hash mismatch"
            )
        try:
            formal = verify_formal_proof_triplet(
                authority.get("formal_proof_triplet") or {}
            )
            descriptor, descriptor_receipt = (
                verify_grounded_failure_descriptor(
                    authority.get("failure_descriptor") or {},
                    authority.get("failure_descriptor_receipt") or {},
                    execution_bundle=execution,
                    formal_proof_triplet=formal,
                )
            )
        except Exception as exc:
            raise GroundedArenaValidityViolation(
                "grounded formal or descriptor authority is invalid"
            ) from exc
        materialization = execution.get("materialization_receipt") or {}
        if (
            formal["clean"]["rtl_hash"]
            != materialization.get("clean_rtl_hash")
            or formal["poison"]["rtl_hash"]
            != materialization.get("poison_rtl_hash")
            or formal["revert"]["rtl_hash"]
            != materialization.get("clean_rtl_hash")
        ):
            raise GroundedArenaValidityViolation(
                "formal proof triplet is not bound to materialization"
            )
        descriptor = dict(descriptor)
        descriptor_receipt = dict(descriptor_receipt)
        decision = execution.get("admission_decision") or {}
        grounded_evidence = execution.get("evidence") or {}
        materialization = execution.get("materialization_receipt") or {}
        expected = {
            "authority_mode": ARENA_GROUNDED_AUTHORITY,
            "authority_bundle_hash": authority.get("authority_hash"),
            "execution_bundle_hash": execution.get("bundle_hash"),
            "formal_proof_triplet_hash": formal.get("triplet_hash"),
            "failure_descriptor_hash": descriptor.get(
                "descriptor_hash"
            ),
            "failure_descriptor_receipt_hash": descriptor_receipt.get(
                "receipt_hash"
            ),
            "admission_decision_hash": decision.get("decision_hash"),
            "plan_hash": (execution.get("plan") or {}).get(
                "plan_hash"
            ),
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
                "grounded arena validity is not bound to authority"
            )
    return payload


def verify_legacy_grounded_arena_validity(
    record: Mapping[str, Any],
    *,
    execution_bundle: Mapping[str, Any],
) -> dict[str, Any]:
    """Audit-only compatibility for pre-formal Grounded rounds."""
    payload = _verify_decision_record(
        record, evidence_fields=_LEGACY_EVIDENCE_FIELDS
    )
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
    if payload["evidence"] != expected:
        raise GroundedArenaValidityViolation(
            "legacy Grounded validity is not bound to execution"
        )
    return payload
