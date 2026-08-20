"""Runner-owned candidate generation, verification, and selection receipts."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

from r3e.protocol.hashing import hash_payload


GENERATION_SCHEMA = "r3e-blue-candidate-generation-v2"
VERIFICATION_SCHEMA = "r3e-blue-candidate-verification-v1"
SELECTION_SCHEMA = "r3e-blue-candidate-selection-v1"
SELECTION_POLICY = "oracle_then_minimality_v1"
SELECTION_POLICIES = {SELECTION_POLICY, "first_verified_v1"}
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class CandidateReceiptViolation(RuntimeError):
    """Raised when candidate evidence cannot be reconstructed."""


def _digest(value: Any, field: str) -> str:
    text = str(value or "")
    if not _HASH_RE.fullmatch(text):
        raise CandidateReceiptViolation(f"{field} must be an exact sha256 digest")
    return text


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise CandidateReceiptViolation(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise CandidateReceiptViolation(f"{field} must be an integer") from exc
    if result < 0:
        raise CandidateReceiptViolation(f"{field} must be non-negative")
    return result


def _require_exact(raw: Mapping[str, Any], fields: set[str], name: str) -> None:
    if set(raw) != fields:
        raise CandidateReceiptViolation(f"{name} fields mismatch")


def build_generation_receipt(
    *,
    provider_output: Mapping[str, Any],
    expected_candidate_id: str,
    expected_slot: Mapping[str, Any],
    expected_prompt_hash: str,
    current_case_evidence_hash: str,
    current_case_artifact_hash: str,
) -> dict[str, Any]:
    if provider_output.get("candidate_id") != expected_candidate_id:
        raise CandidateReceiptViolation("provider candidate ID mismatch")
    if provider_output.get("slot_index") != expected_slot["slot_index"]:
        raise CandidateReceiptViolation("provider candidate slot mismatch")
    if provider_output.get("lens_id") != expected_slot["lens_id"]:
        raise CandidateReceiptViolation("provider candidate lens ID mismatch")
    if provider_output.get("lens_hash") != expected_slot["lens_hash"]:
        raise CandidateReceiptViolation("provider candidate lens hash mismatch")
    if provider_output.get("candidate_seed") != expected_slot["candidate_seed"]:
        raise CandidateReceiptViolation("provider candidate seed mismatch")
    if provider_output.get("prompt_hash") != expected_prompt_hash:
        raise CandidateReceiptViolation("provider prompt hash mismatch")
    if provider_output.get("current_case_evidence_hash") != current_case_evidence_hash:
        raise CandidateReceiptViolation("provider current-case evidence mismatch")
    if provider_output.get("current_case_artifact_hash") != current_case_artifact_hash:
        raise CandidateReceiptViolation("provider current-case artifact mismatch")
    patch_payload = provider_output.get("patch_payload")
    if not isinstance(patch_payload, Mapping):
        raise CandidateReceiptViolation("provider patch payload must be an object")
    if provider_output.get("patch_payload_hash") != hash_payload(patch_payload):
        raise CandidateReceiptViolation("provider patch payload hash mismatch")
    payload = {
        "schema_version": GENERATION_SCHEMA,
        "candidate_id": expected_candidate_id,
        "slot_index": int(expected_slot["slot_index"]),
        "lens_id": str(expected_slot["lens_id"]),
        "lens_hash": _digest(expected_slot["lens_hash"], "lens_hash"),
        "prompt_hash": _digest(expected_prompt_hash, "prompt_hash"),
        "current_case_evidence_hash": _digest(
            current_case_evidence_hash, "current_case_evidence_hash"
        ),
        "current_case_artifact_hash": _digest(
            current_case_artifact_hash, "current_case_artifact_hash"
        ),
        "provider_receipt_hash": _digest(
            provider_output.get("result_hash"), "provider_receipt_hash"
        ),
        "raw_response_hash": _digest(
            provider_output.get("raw_response_hash"), "raw_response_hash"
        ),
        "patch_payload_hash": _digest(
            provider_output.get("patch_payload_hash"), "patch_payload_hash"
        ),
        "input_tokens": _non_negative_int(
            provider_output.get("input_tokens"), "input_tokens"
        ),
        "output_tokens": _non_negative_int(
            provider_output.get("output_tokens"), "output_tokens"
        ),
    }
    payload["generation_hash"] = hash_payload(payload)
    return payload


def verify_generation_receipt(raw: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(raw))
    _require_exact(
        payload,
        {
            "schema_version",
            "candidate_id",
            "slot_index",
            "lens_id",
            "lens_hash",
            "prompt_hash",
            "current_case_evidence_hash",
            "current_case_artifact_hash",
            "provider_receipt_hash",
            "raw_response_hash",
            "patch_payload_hash",
            "input_tokens",
            "output_tokens",
            "generation_hash",
        },
        "generation receipt",
    )
    if payload["schema_version"] != GENERATION_SCHEMA:
        raise CandidateReceiptViolation("generation receipt schema mismatch")
    for field in (
        "candidate_id",
        "lens_id",
    ):
        if not isinstance(payload[field], str) or not payload[field]:
            raise CandidateReceiptViolation(f"{field} must be non-empty")
    for field in (
        "lens_hash",
        "prompt_hash",
        "current_case_evidence_hash",
        "current_case_artifact_hash",
        "provider_receipt_hash",
        "raw_response_hash",
        "patch_payload_hash",
    ):
        _digest(payload[field], field)
    _non_negative_int(payload["slot_index"], "slot_index")
    _non_negative_int(payload["input_tokens"], "input_tokens")
    _non_negative_int(payload["output_tokens"], "output_tokens")
    expected = hash_payload({
        key: value for key, value in payload.items() if key != "generation_hash"
    })
    if payload["generation_hash"] != expected:
        raise CandidateReceiptViolation("generation receipt hash mismatch")
    return payload


def build_verification_receipt(
    *,
    candidate_id: str,
    patch_hash: str,
    semantic_patch_signature_hash: str,
    verifier_output: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {
        "parse_ok",
        "scope_ok",
        "compile_ok",
        "compile_receipt_hash",
        "simulation_receipt_hash",
        "formal_receipt_hash",
        "oracle_ok",
        "changed_modules",
        "changed_blocks",
        "ast_edit_count",
    }
    _require_exact(verifier_output, expected, "candidate verifier output")
    for field in ("parse_ok", "scope_ok", "compile_ok", "oracle_ok"):
        if not isinstance(verifier_output[field], bool):
            raise CandidateReceiptViolation(f"{field} must be boolean")
    if verifier_output["oracle_ok"] and not (
        verifier_output["parse_ok"]
        and verifier_output["scope_ok"]
        and verifier_output["compile_ok"]
    ):
        raise CandidateReceiptViolation(
            "oracle success requires parse and scope admission"
        )
    payload = {
        "schema_version": VERIFICATION_SCHEMA,
        "candidate_id": str(candidate_id),
        "patch_hash": _digest(patch_hash, "patch_hash"),
        "parse_ok": verifier_output["parse_ok"],
        "scope_ok": verifier_output["scope_ok"],
        "compile_ok": verifier_output["compile_ok"],
        "compile_receipt_hash": _digest(
            verifier_output["compile_receipt_hash"], "compile_receipt_hash"
        ),
        "simulation_receipt_hash": _digest(
            verifier_output["simulation_receipt_hash"],
            "simulation_receipt_hash",
        ),
        "formal_receipt_hash": _digest(
            verifier_output["formal_receipt_hash"], "formal_receipt_hash"
        ),
        "oracle_ok": verifier_output["oracle_ok"],
        "semantic_patch_signature_hash": _digest(
            semantic_patch_signature_hash,
            "semantic_patch_signature_hash",
        ),
        "changed_modules": _non_negative_int(
            verifier_output["changed_modules"], "changed_modules"
        ),
        "changed_blocks": _non_negative_int(
            verifier_output["changed_blocks"], "changed_blocks"
        ),
        "ast_edit_count": _non_negative_int(
            verifier_output["ast_edit_count"], "ast_edit_count"
        ),
    }
    payload["verification_hash"] = hash_payload(payload)
    return payload


def verify_verification_receipt(raw: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(raw))
    fields = {
        "schema_version",
        "candidate_id",
        "patch_hash",
        "parse_ok",
        "scope_ok",
        "compile_ok",
        "compile_receipt_hash",
        "simulation_receipt_hash",
        "formal_receipt_hash",
        "oracle_ok",
        "semantic_patch_signature_hash",
        "changed_modules",
        "changed_blocks",
        "ast_edit_count",
        "verification_hash",
    }
    _require_exact(payload, fields, "verification receipt")
    if payload["schema_version"] != VERIFICATION_SCHEMA:
        raise CandidateReceiptViolation("verification receipt schema mismatch")
    rebuilt = build_verification_receipt(
        candidate_id=payload["candidate_id"],
        patch_hash=payload["patch_hash"],
        semantic_patch_signature_hash=payload[
            "semantic_patch_signature_hash"
        ],
        verifier_output={
            key: payload[key]
            for key in fields
            if key not in {
                "schema_version",
                "candidate_id",
                "patch_hash",
                "semantic_patch_signature_hash",
                "verification_hash",
            }
        },
    )
    if rebuilt != payload:
        raise CandidateReceiptViolation("verification receipt hash mismatch")
    return payload


def build_selection_receipt(
    verification_receipts: list[Mapping[str, Any]],
    *,
    selection_policy: str = SELECTION_POLICY,
) -> dict[str, Any]:
    if selection_policy not in SELECTION_POLICIES:
        raise CandidateReceiptViolation(
            "selection policy must be deterministic and oracle-backed"
        )
    verified = [verify_verification_receipt(row) for row in verification_receipts]
    candidate_ids = [row["candidate_id"] for row in verified]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise CandidateReceiptViolation("selection has duplicate candidate IDs")
    passing = [
        row for row in verified
        if row["parse_ok"] and row["scope_ok"] and row["oracle_ok"]
    ]
    if selection_policy == SELECTION_POLICY:
        passing.sort(key=lambda row: (
            row["changed_modules"],
            row["changed_blocks"],
            row["ast_edit_count"],
            row["candidate_id"],
        ))
    selected = passing[0] if passing else None
    tie_break_values = {
        row["candidate_id"]: {
            "changed_modules": row["changed_modules"],
            "changed_blocks": row["changed_blocks"],
            "ast_edit_count": row["ast_edit_count"],
        }
        for row in passing
    }
    payload = {
        "schema_version": SELECTION_SCHEMA,
        "candidate_ids": candidate_ids,
        "verified_candidate_ids": [row["candidate_id"] for row in passing],
        "selected_candidate_id": (
            selected["candidate_id"] if selected is not None else None
        ),
        "selection_policy": selection_policy,
        "tie_break_values": tie_break_values,
    }
    payload["selection_hash"] = hash_payload(payload)
    return payload


def verify_selection_receipt(
    raw: Mapping[str, Any],
    verification_receipts: list[Mapping[str, Any]],
) -> dict[str, Any]:
    payload = deepcopy(dict(raw))
    _require_exact(
        payload,
        {
            "schema_version",
            "candidate_ids",
            "verified_candidate_ids",
            "selected_candidate_id",
            "selection_policy",
            "tie_break_values",
            "selection_hash",
        },
        "selection receipt",
    )
    rebuilt = build_selection_receipt(
        verification_receipts,
        selection_policy=str(payload.get("selection_policy") or ""),
    )
    if payload != rebuilt:
        raise CandidateReceiptViolation(
            "selection receipt is not reconstructable from oracle evidence"
        )
    return payload
