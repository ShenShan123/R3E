"""Parser-backed semantic and artifact-derived runtime proof schemas."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

from r3e.protocol.hashing import hash_payload


SEMANTIC_DIFF_SCHEMA_VERSION = "r3e-red-semantic-diff-receipt-v1"
EFFECT_RECEIPT_SCHEMA_VERSION = "r3e-red-effect-receipt-v1"
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class GroundedProofViolation(RuntimeError):
    """Raised when semantic or runtime proof is not reconstructable."""


def _digest(value: Any, field: str) -> str:
    text = str(value or "")
    if not _HASH_RE.fullmatch(text):
        raise GroundedProofViolation(f"{field} must be an exact sha256 digest")
    return text


def build_semantic_diff_receipt(
    *,
    plan_hash: str,
    family_id: str,
    operator_id: str,
    clean_ast_hash: str,
    poison_ast_hash: str,
    target_ast_node_hash: str,
    normalized_ast_diff_hash: str,
    changed_module_count: int,
    changed_block_count: int,
    ast_edit_count: int,
    dependency_depth: int,
    collateral_edit_count: int,
    reachable: bool,
    preconditions_satisfied: bool,
) -> dict[str, Any]:
    payload = {
        "schema_version": SEMANTIC_DIFF_SCHEMA_VERSION,
        "plan_hash": plan_hash,
        "family_id": family_id,
        "operator_id": operator_id,
        "clean_ast_hash": clean_ast_hash,
        "poison_ast_hash": poison_ast_hash,
        "target_ast_node_hash": target_ast_node_hash,
        "normalized_ast_diff_hash": normalized_ast_diff_hash,
        "changed_module_count": changed_module_count,
        "changed_block_count": changed_block_count,
        "ast_edit_count": ast_edit_count,
        "dependency_depth": dependency_depth,
        "collateral_edit_count": collateral_edit_count,
        "reachable": reachable,
        "preconditions_satisfied": preconditions_satisfied,
    }
    payload["receipt_hash"] = hash_payload(payload)
    return verify_semantic_diff_receipt(payload)


def verify_semantic_diff_receipt(
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    payload = deepcopy(dict(receipt))
    required = {
        "schema_version",
        "plan_hash",
        "family_id",
        "operator_id",
        "clean_ast_hash",
        "poison_ast_hash",
        "target_ast_node_hash",
        "normalized_ast_diff_hash",
        "changed_module_count",
        "changed_block_count",
        "ast_edit_count",
        "dependency_depth",
        "collateral_edit_count",
        "reachable",
        "preconditions_satisfied",
        "receipt_hash",
    }
    if set(payload) != required:
        raise GroundedProofViolation("semantic diff receipt fields mismatch")
    if payload["schema_version"] != SEMANTIC_DIFF_SCHEMA_VERSION:
        raise GroundedProofViolation("semantic diff receipt schema mismatch")
    if payload["receipt_hash"] != hash_payload({
        key: value for key, value in payload.items() if key != "receipt_hash"
    }):
        raise GroundedProofViolation("semantic diff receipt hash mismatch")
    for field in (
        "plan_hash",
        "clean_ast_hash",
        "poison_ast_hash",
        "target_ast_node_hash",
        "normalized_ast_diff_hash",
    ):
        _digest(payload[field], field)
    if payload["clean_ast_hash"] == payload["poison_ast_hash"]:
        raise GroundedProofViolation("semantic diff must change normalized AST")
    for field in ("family_id", "operator_id"):
        if not isinstance(payload[field], str) or not payload[field]:
            raise GroundedProofViolation(f"{field} must be non-empty")
    for field in (
        "changed_module_count",
        "changed_block_count",
        "ast_edit_count",
        "dependency_depth",
        "collateral_edit_count",
    ):
        value = payload[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise GroundedProofViolation(f"{field} must be non-negative")
    if min(
        payload["changed_module_count"],
        payload["changed_block_count"],
        payload["ast_edit_count"],
    ) < 1:
        raise GroundedProofViolation("semantic diff must contain a real AST edit")
    if not isinstance(payload["reachable"], bool) or not isinstance(
        payload["preconditions_satisfied"], bool
    ):
        raise GroundedProofViolation("semantic proof flags must be boolean")
    return payload


def build_runtime_effect_receipt(
    *,
    plan_hash: str,
    effect_id: str,
    failure_signature: str,
    first_divergence_hash: str,
    mismatch_topology_hash: str,
    temporal_depth: int,
    observable_features: Mapping[str, Any],
) -> dict[str, Any]:
    payload = {
        "schema_version": EFFECT_RECEIPT_SCHEMA_VERSION,
        "plan_hash": plan_hash,
        "effect_id": effect_id,
        "failure_signature": failure_signature,
        "first_divergence_hash": first_divergence_hash,
        "mismatch_topology_hash": mismatch_topology_hash,
        "temporal_depth": temporal_depth,
        "observable_features": deepcopy(dict(observable_features)),
    }
    payload["receipt_hash"] = hash_payload(payload)
    return verify_runtime_effect_receipt(payload)


def verify_runtime_effect_receipt(
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    payload = deepcopy(dict(receipt))
    required = {
        "schema_version",
        "plan_hash",
        "effect_id",
        "failure_signature",
        "first_divergence_hash",
        "mismatch_topology_hash",
        "temporal_depth",
        "observable_features",
        "receipt_hash",
    }
    if set(payload) != required:
        raise GroundedProofViolation("runtime effect receipt fields mismatch")
    if payload["schema_version"] != EFFECT_RECEIPT_SCHEMA_VERSION:
        raise GroundedProofViolation("runtime effect receipt schema mismatch")
    if payload["receipt_hash"] != hash_payload({
        key: value for key, value in payload.items() if key != "receipt_hash"
    }):
        raise GroundedProofViolation("runtime effect receipt hash mismatch")
    for field in (
        "plan_hash",
        "first_divergence_hash",
        "mismatch_topology_hash",
    ):
        _digest(payload[field], field)
    for field in ("effect_id", "failure_signature"):
        if not isinstance(payload[field], str) or not payload[field]:
            raise GroundedProofViolation(f"{field} must be non-empty")
    if (
        not isinstance(payload["temporal_depth"], int)
        or isinstance(payload["temporal_depth"], bool)
        or payload["temporal_depth"] < 0
    ):
        raise GroundedProofViolation("temporal depth must be non-negative")
    if not isinstance(payload["observable_features"], dict):
        raise GroundedProofViolation("observable features must be an object")
    return payload
