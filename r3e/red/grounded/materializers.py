"""Parser-backed Tier-1 AST materializers and exact inverses."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Mapping

from r3e.grounded.provider_receipts import (
    build_provider_receipt,
    verify_provider_receipt,
)
from r3e.protocol.hashing import hash_payload

from .proofs import build_semantic_diff_receipt
from .verilog_ast import (
    VerilogAstViolation,
    changed_token_count,
    comparison_nodes,
    normalized_ast_hash,
    require_comparison_node,
    source_hash,
)


MATERIALIZATION_SCHEMA_VERSION = "r3e-red-ast-materialization-v1"
_COMPARATOR_REPLACEMENTS = {
    "<": "<=",
    "<=": "<",
    ">": ">=",
    ">=": ">",
    "==": "!=",
    "!=": "==",
}
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class AstMaterializationViolation(RuntimeError):
    """Raised when an AST edit or its inverse is not exact."""


@dataclass(frozen=True)
class AstMaterialization:
    poison_source: str
    receipt: dict[str, Any]
    semantic_diff_receipt: dict[str, Any]
    parser_provider_receipt: dict[str, Any]


_MATERIALIZATION_FIELDS = {
    "schema_version",
    "plan_hash",
    "operator_id",
    "target_module",
    "clean_rtl_hash",
    "poison_rtl_hash",
    "clean_ast_hash",
    "poison_ast_hash",
    "clean_ast_node_hash",
    "poison_ast_node_hash",
    "old_operator",
    "new_operator",
    "ast_edit_count",
    "materialization_hash",
}


def verify_materialization_receipt(
    materialization_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    receipt = deepcopy(dict(materialization_receipt))
    if set(receipt) != _MATERIALIZATION_FIELDS:
        raise AstMaterializationViolation(
            "materialization receipt fields mismatch"
        )
    if receipt["schema_version"] != MATERIALIZATION_SCHEMA_VERSION:
        raise AstMaterializationViolation(
            "materialization receipt schema mismatch"
        )
    for field in (
        "plan_hash",
        "clean_rtl_hash",
        "poison_rtl_hash",
        "clean_ast_hash",
        "poison_ast_hash",
        "clean_ast_node_hash",
        "poison_ast_node_hash",
    ):
        value = receipt[field]
        if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
            raise AstMaterializationViolation(
                f"materialization {field} is not a sha256 digest"
            )
    for field in ("operator_id", "target_module", "old_operator", "new_operator"):
        if not isinstance(receipt[field], str) or not receipt[field]:
            raise AstMaterializationViolation(
                f"materialization {field} is empty"
            )
    if (
        receipt["operator_id"] != "replace_comparator"
        or _COMPARATOR_REPLACEMENTS.get(receipt["old_operator"])
        != receipt["new_operator"]
        or _COMPARATOR_REPLACEMENTS.get(receipt["new_operator"])
        != receipt["old_operator"]
    ):
        raise AstMaterializationViolation(
            "materialization comparator contract mismatch"
        )
    if (
        receipt["clean_rtl_hash"] == receipt["poison_rtl_hash"]
        or receipt["clean_ast_hash"] == receipt["poison_ast_hash"]
        or receipt["clean_ast_node_hash"] == receipt["poison_ast_node_hash"]
        or receipt["ast_edit_count"] != 1
    ):
        raise AstMaterializationViolation(
            "materialization does not describe one semantic AST edit"
        )
    body = {
        key: value for key, value in receipt.items()
        if key != "materialization_hash"
    }
    if receipt["materialization_hash"] != hash_payload(body):
        raise AstMaterializationViolation(
            "materialization receipt hash mismatch"
        )
    return receipt


def _receipt_body(
    *,
    plan: Mapping[str, Any],
    clean_source: str,
    poison_source: str,
    clean_node_hash: str,
    poison_node_hash: str,
    old_operator: str,
    new_operator: str,
) -> dict[str, Any]:
    return {
        "schema_version": MATERIALIZATION_SCHEMA_VERSION,
        "plan_hash": plan["plan_hash"],
        "operator_id": plan["operator_id"],
        "target_module": plan["target_module"],
        "clean_rtl_hash": source_hash(clean_source),
        "poison_rtl_hash": source_hash(poison_source),
        "clean_ast_hash": normalized_ast_hash(clean_source),
        "poison_ast_hash": normalized_ast_hash(poison_source),
        "clean_ast_node_hash": clean_node_hash,
        "poison_ast_node_hash": poison_node_hash,
        "old_operator": old_operator,
        "new_operator": new_operator,
        "ast_edit_count": changed_token_count(clean_source, poison_source),
    }


def materialize_comparator(
    plan: Mapping[str, Any],
    *,
    clean_source: str,
) -> AstMaterialization:
    if plan.get("operator_id") != "replace_comparator":
        raise AstMaterializationViolation(
            "comparator materializer received another operator"
        )
    if plan.get("family_id") != "combinational.comparator_boundary":
        raise AstMaterializationViolation(
            "comparator materializer family mismatch"
        )
    try:
        clean_node = require_comparison_node(
            clean_source,
            module=str(plan["target_module"]),
            node_hash=str(plan["target_ast_node_hash"]),
        )
    except VerilogAstViolation as exc:
        raise AstMaterializationViolation(str(exc)) from exc
    replacement = _COMPARATOR_REPLACEMENTS.get(clean_node.operator)
    if replacement is None:
        raise AstMaterializationViolation(
            "target comparator has no frozen inverse"
        )
    poison_source = (
        clean_source[:clean_node.operator_start]
        + replacement
        + clean_source[clean_node.operator_end:]
    )
    if changed_token_count(clean_source, poison_source) != 1:
        raise AstMaterializationViolation(
            "comparator materializer changed more than one AST token"
        )
    poison_nodes = [
        node for node in comparison_nodes(
            poison_source, module=str(plan["target_module"])
        )
        if node.ordinal == clean_node.ordinal
    ]
    if len(poison_nodes) != 1:
        raise AstMaterializationViolation("poison comparator node is ambiguous")
    poison_node = poison_nodes[0]
    body = _receipt_body(
        plan=plan,
        clean_source=clean_source,
        poison_source=poison_source,
        clean_node_hash=clean_node.node_hash,
        poison_node_hash=poison_node.node_hash,
        old_operator=clean_node.operator,
        new_operator=replacement,
    )
    receipt = {**body, "materialization_hash": hash_payload(body)}
    semantic = build_semantic_diff_receipt(
        plan_hash=str(plan["plan_hash"]),
        family_id=str(plan["family_id"]),
        operator_id=str(plan["operator_id"]),
        clean_ast_hash=body["clean_ast_hash"],
        poison_ast_hash=body["poison_ast_hash"],
        target_ast_node_hash=clean_node.node_hash,
        normalized_ast_diff_hash=hash_payload({
            "node_type": "comparison_expression",
            "module": plan["target_module"],
            "old_operator": clean_node.operator,
            "new_operator": replacement,
            "left_token": clean_node.left_token,
            "right_token": clean_node.right_token,
        }),
        changed_module_count=1,
        changed_block_count=1,
        ast_edit_count=1,
        dependency_depth=0,
        collateral_edit_count=0,
        reachable=True,
        preconditions_satisfied=True,
    )
    provider = build_provider_receipt(
        provider_kind="semantic_parser",
        provider_id="r3e-verilog-token-ast",
        provider_version="1",
        input_artifact_hashes={
            "clean_rtl": body["clean_rtl_hash"],
            "poison_rtl": body["poison_rtl_hash"],
        },
        result={
            "materialization_hash": receipt["materialization_hash"],
            "semantic_diff_receipt_hash": semantic["receipt_hash"],
        },
    )
    return AstMaterialization(
        poison_source=poison_source,
        receipt=verify_materialization_receipt(receipt),
        semantic_diff_receipt=semantic,
        parser_provider_receipt=verify_provider_receipt(provider),
    )


def inverse_materialization(
    materialization_receipt: Mapping[str, Any],
    *,
    poison_source: str,
) -> str:
    receipt = verify_materialization_receipt(materialization_receipt)
    if source_hash(poison_source) != receipt["poison_rtl_hash"]:
        raise AstMaterializationViolation("inverse poison source hash mismatch")
    try:
        poison_node = require_comparison_node(
            poison_source,
            module=receipt["target_module"],
            node_hash=receipt["poison_ast_node_hash"],
        )
    except VerilogAstViolation as exc:
        raise AstMaterializationViolation(str(exc)) from exc
    if (
        poison_node.operator != receipt["new_operator"]
        or _COMPARATOR_REPLACEMENTS.get(poison_node.operator)
        != receipt["old_operator"]
    ):
        raise AstMaterializationViolation("inverse comparator contract mismatch")
    restored = (
        poison_source[:poison_node.operator_start]
        + receipt["old_operator"]
        + poison_source[poison_node.operator_end:]
    )
    if (
        source_hash(restored) != receipt["clean_rtl_hash"]
        or normalized_ast_hash(restored) != receipt["clean_ast_hash"]
    ):
        raise AstMaterializationViolation(
            "inverse did not restore exact clean semantics"
        )
    return restored
