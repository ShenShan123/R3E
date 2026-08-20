"""Parser-backed RAAM challenge and controlled-composition materializers.

The adapter may choose a bounded operator recipe, but it cannot provide RTL.
Every edit is executed by the frozen GRD-2 AST materializers and every
intermediate source is admitted by reconstructing its parser and semantic
receipts.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from r3e.grounded.provider_receipts import verify_provider_receipt
from r3e.memory.schema import ActiveMemoryBank
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload
from r3e.red.memory_challenge import (
    verify_memory_capability_packet,
)

from .materializers import (
    AstMaterialization,
    inverse_materialization,
    materialize_operator,
    verify_materialization_receipt,
)
from .mutation_plan import (
    build_mutation_plan,
    verify_mutation_plan,
)
from .operator_ast import operator_nodes
from .proofs import verify_semantic_diff_receipt
from .registry import GroundedRegistryBundle
from .verilog_ast import normalized_ast_hash, source_hash


MEMORY_OPERATOR_PLAN_SCHEMA = "r3e-parser-memory-operator-plan-v1"
SEQUENTIAL_RECEIPT_SCHEMA = "r3e-parser-sequential-materialization-v1"
INTERMEDIATE_ADMISSION_SCHEMA = "r3e-parser-intermediate-admission-v1"
CONTROLLED_COMPOSITION_SCHEMA = "r3e-controlled-composition-plan-v1"
MEMORY_OPERATORS = {
    "memory_bypass",
    "memory_deepening",
    "memory_conflict",
    "memory_transfer",
}


class ParserMemoryMaterializationViolation(RuntimeError):
    """Raised when a RAAM red edit is not parser-backed and reconstructable."""


def _without_hash(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != field}


def _require_hash(value: Mapping[str, Any], field: str) -> None:
    if value.get(field) != hash_payload(_without_hash(value, field)):
        raise ParserMemoryMaterializationViolation(
            f"{field} cannot be reconstructed"
        )


def verify_sequential_plan(
    plan: Mapping[str, Any],
    *,
    policy: PolicyState,
    registries: GroundedRegistryBundle,
) -> dict[str, Any]:
    """Verify the immutable envelope shared by memory and composition plans."""
    payload = deepcopy(dict(plan))
    schema = payload.get("schema_version")
    if schema not in {
        MEMORY_OPERATOR_PLAN_SCHEMA,
        CONTROLLED_COMPOSITION_SCHEMA,
    }:
        raise ParserMemoryMaterializationViolation(
            "sequential materialization plan schema mismatch"
        )
    _require_hash(payload, "plan_hash")
    if (
        payload.get("challenged_policy_hash") != policy.policy_hash
        or payload.get("challenged_effective_policy_hash")
        != policy.effective_policy_hash
        or payload.get("registry_bundle_hash")
        != registries.registry_bundle_hash
    ):
        raise ParserMemoryMaterializationViolation(
            "sequential materialization authority binding mismatch"
        )
    subplans = [
        verify_mutation_plan(
            value,
            policy=policy,
            registries=registries,
        )
        for value in list(payload.get("subplans") or [])
    ]
    expected_hashes = list(
        payload.get("expected_stage_source_hashes") or []
    )
    if (
        not subplans
        or len(expected_hashes) != len(subplans) + 1
        or payload.get("clean_source_hash") != expected_hashes[0]
    ):
        raise ParserMemoryMaterializationViolation(
            "sequential source checkpoints are incomplete"
        )
    if schema == MEMORY_OPERATOR_PLAN_SCHEMA:
        operator = payload.get("operator")
        targets = list(payload.get("target_memory_ids") or [])
        if (
            operator not in MEMORY_OPERATORS
            or len(targets) != (2 if operator == "memory_conflict" else 1)
            or len(subplans) != (
                2 if operator == "memory_conflict" else 1
            )
            or not payload.get("active_memory_bank_hash")
            or not payload.get("capability_packet_hash")
            or not payload.get("parent_poison_id")
        ):
            raise ParserMemoryMaterializationViolation(
                "parser memory plan authority is incomplete"
            )
    else:
        parents = list(payload.get("parent_poison_ids") or [])
        authorities = dict(
            payload.get("parent_authority_hashes") or {}
        )
        if (
            len(parents) != 2
            or len(set(parents)) != 2
            or set(authorities) != set(parents)
            or len(subplans) != 2
            or any(
                not str(authorities[parent]).startswith("sha256:")
                or len(str(authorities[parent])) != 71
                for parent in parents
            )
            or payload.get("family_id")
            != "composition.controlled_pair"
            or payload.get("lineage_operator") != "composes_with"
            or payload.get("composition_depth") != 2
        ):
            raise ParserMemoryMaterializationViolation(
                "controlled composition authority is incomplete"
            )
    return {**payload, "subplans": subplans}


def _validate_targets(
    *,
    operator: str,
    targets: Iterable[str],
    bank: ActiveMemoryBank,
) -> list[str]:
    normalized = sorted(set(str(value) for value in targets))
    if not normalized or not set(normalized).issubset(bank.memories):
        raise ParserMemoryMaterializationViolation(
            "memory targets must belong to the active bank"
        )
    expected = 2 if operator == "memory_conflict" else 1
    if len(normalized) != expected:
        raise ParserMemoryMaterializationViolation(
            f"{operator} requires exactly {expected} memory target(s)"
        )
    return normalized


def _build_subplans(
    *,
    policy: PolicyState,
    registries: GroundedRegistryBundle,
    target_design: str,
    target_module: str,
    clean_source: str,
    plan_prefix: str,
    step_specs: Iterable[Mapping[str, Any]],
    parent_poison_ids: list[str],
    lineage_operator: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    current = clean_source
    subplans: list[dict[str, Any]] = []
    expected_hashes = [source_hash(current)]
    specs = [deepcopy(dict(value)) for value in step_specs]
    if not specs:
        raise ParserMemoryMaterializationViolation(
            "parser-backed materialization requires at least one step"
        )
    for index, spec in enumerate(specs):
        required = {
            "operator_id",
            "family_id",
            "expected_runtime_effect_id",
            "precondition",
            "node_ordinal",
        }
        if set(spec) != required:
            raise ParserMemoryMaterializationViolation(
                "materializer step fields mismatch"
            )
        operator_id = str(spec["operator_id"])
        nodes = operator_nodes(
            current,
            module=target_module,
            operator_id=operator_id,
        )
        ordinal = int(spec["node_ordinal"])
        if ordinal < 0 or ordinal >= len(nodes):
            raise ParserMemoryMaterializationViolation(
                "materializer node ordinal is unavailable"
            )
        registry_operator = registries.operators.get(operator_id)
        if registry_operator is None:
            raise ParserMemoryMaterializationViolation(
                "materializer operator is outside the frozen registry"
            )
        subplan = build_mutation_plan(
            plan_id=f"{plan_prefix}_S{index + 1}",
            policy=policy,
            registries=registries,
            target_design=target_design,
            target_module=target_module,
            target_ast_node_hash=nodes[ordinal].node_hash,
            family_id=str(spec["family_id"]),
            operator_id=operator_id,
            expected_runtime_effect_id=str(
                spec["expected_runtime_effect_id"]
            ),
            preconditions={str(spec["precondition"]): True},
            scope_limits={
                "maximum_changed_modules": 1,
                "maximum_changed_blocks": 1,
                "maximum_ast_edits": 1,
            },
            difficulty_target={
                "difficulty_band": "D0",
                "dependency_depth_delta": index,
                "temporal_depth_delta": 0,
            },
            parent_poison_ids=(
                parent_poison_ids[:1] if parent_poison_ids else None
            ),
            lineage_operator=(
                lineage_operator if parent_poison_ids else "fresh"
            ),
        )
        materialized = materialize_operator(
            subplan,
            clean_source=current,
        )
        current = materialized.poison_source
        subplans.append(subplan)
        expected_hashes.append(source_hash(current))
    return subplans, expected_hashes


def build_parser_memory_operator_plan(
    *,
    policy: PolicyState,
    bank: ActiveMemoryBank,
    capability_packet: Mapping[str, Any],
    registries: GroundedRegistryBundle,
    operator: str,
    poison_id: str,
    target_memory_ids: Iterable[str],
    parent_poison_id: str,
    source_design: str,
    target_design: str,
    target_module: str,
    clean_source: str,
    step_specs: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Freeze one of the four RAAM operators as parser-executable AST steps."""
    if operator not in MEMORY_OPERATORS:
        raise ParserMemoryMaterializationViolation(
            "unknown parser-backed memory operator"
        )
    try:
        verified_packet = verify_memory_capability_packet(
            dict(capability_packet),
            policy=policy,
            bank=bank,
        )
    except RuntimeError as exc:
        raise ParserMemoryMaterializationViolation(str(exc)) from exc
    if operator not in verified_packet["allowed_operators"]:
        raise ParserMemoryMaterializationViolation(
            "memory operator is outside the capability packet"
        )
    targets = _validate_targets(
        operator=operator,
        targets=target_memory_ids,
        bank=bank,
    )
    if operator == "memory_transfer":
        if not source_design or source_design == target_design:
            raise ParserMemoryMaterializationViolation(
                "memory transfer requires a distinct target design"
            )
    elif source_design != target_design:
        raise ParserMemoryMaterializationViolation(
            "only memory transfer may change target design"
        )
    specs = [deepcopy(dict(value)) for value in step_specs]
    if operator == "memory_conflict" and len(specs) != 2:
        raise ParserMemoryMaterializationViolation(
            "memory conflict requires exactly two AST steps"
        )
    if operator != "memory_conflict" and len(specs) != 1:
        raise ParserMemoryMaterializationViolation(
            "bypass, deepening, and transfer require one AST step"
        )
    subplans, expected_hashes = _build_subplans(
        policy=policy,
        registries=registries,
        target_design=target_design,
        target_module=target_module,
        clean_source=clean_source,
        plan_prefix=poison_id,
        step_specs=specs,
        parent_poison_ids=[parent_poison_id],
        lineage_operator={
            "memory_bypass": "bypasses",
            "memory_deepening": "deepens",
            "memory_conflict": "invalidates_memory",
            "memory_transfer": "transfers",
        }[operator],
    )
    payload = {
        "schema_version": MEMORY_OPERATOR_PLAN_SCHEMA,
        "operator": operator,
        "poison_id": poison_id,
        "challenged_policy_hash": policy.policy_hash,
        "challenged_effective_policy_hash": policy.effective_policy_hash,
        "active_memory_bank_hash": bank.bank_hash,
        "capability_packet_hash": verified_packet["packet_hash"],
        "registry_bundle_hash": registries.registry_bundle_hash,
        "target_memory_ids": targets,
        "parent_poison_id": parent_poison_id,
        "source_design": source_design,
        "target_design": target_design,
        "target_module": target_module,
        "clean_source_hash": source_hash(clean_source),
        "subplans": subplans,
        "expected_stage_source_hashes": expected_hashes,
    }
    payload["plan_hash"] = hash_payload(payload)
    return payload


def build_controlled_composition_plan(
    *,
    policy: PolicyState,
    registries: GroundedRegistryBundle,
    poison_id: str,
    parent_poison_ids: Iterable[str],
    parent_authority_hashes: Mapping[str, str],
    target_design: str,
    target_module: str,
    clean_source: str,
    step_specs: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Freeze a two-parent, two-stage composition with intermediate admission."""
    parents = sorted(set(str(value) for value in parent_poison_ids))
    if len(parents) != 2 or set(parent_authority_hashes) != set(parents):
        raise ParserMemoryMaterializationViolation(
            "controlled composition requires two authority-bound parents"
        )
    if any(
        not str(parent_authority_hashes[parent]).startswith("sha256:")
        or len(str(parent_authority_hashes[parent])) != 71
        for parent in parents
    ):
        raise ParserMemoryMaterializationViolation(
            "controlled composition parent authority hash is invalid"
        )
    specs = [deepcopy(dict(value)) for value in step_specs]
    if len(specs) != 2:
        raise ParserMemoryMaterializationViolation(
            "controlled composition requires exactly two AST steps"
        )
    subplans, expected_hashes = _build_subplans(
        policy=policy,
        registries=registries,
        target_design=target_design,
        target_module=target_module,
        clean_source=clean_source,
        plan_prefix=poison_id,
        step_specs=specs,
        parent_poison_ids=[],
        lineage_operator="fresh",
    )
    payload = {
        "schema_version": CONTROLLED_COMPOSITION_SCHEMA,
        "poison_id": poison_id,
        "challenged_policy_hash": policy.policy_hash,
        "challenged_effective_policy_hash": policy.effective_policy_hash,
        "registry_bundle_hash": registries.registry_bundle_hash,
        "family_id": "composition.controlled_pair",
        "lineage_operator": "composes_with",
        "parent_poison_ids": parents,
        "parent_authority_hashes": {
            key: str(parent_authority_hashes[key])
            for key in parents
        },
        "target_design": target_design,
        "target_module": target_module,
        "clean_source_hash": source_hash(clean_source),
        "subplans": subplans,
        "expected_stage_source_hashes": expected_hashes,
        "difficulty_band": "D4",
        "composition_depth": 2,
    }
    payload["plan_hash"] = hash_payload(payload)
    return payload


def _admit_intermediate(
    *,
    index: int,
    subplan: Mapping[str, Any],
    materialized: AstMaterialization,
) -> dict[str, Any]:
    receipt = verify_materialization_receipt(materialized.receipt)
    semantic = verify_semantic_diff_receipt(
        materialized.semantic_diff_receipt
    )
    provider = verify_provider_receipt(
        materialized.parser_provider_receipt
    )
    if (
        receipt["plan_hash"] != subplan["plan_hash"]
        or semantic["plan_hash"] != subplan["plan_hash"]
        or provider["provider_kind"] != "semantic_parser"
        or provider["result"]["materialization_hash"]
        != receipt["materialization_hash"]
        or provider["result"]["semantic_diff_receipt_hash"]
        != semantic["receipt_hash"]
        or int(semantic["ast_edit_count"]) != 1
        or int(semantic["collateral_edit_count"]) != 0
        or semantic["reachable"] is not True
        or semantic["preconditions_satisfied"] is not True
    ):
        raise ParserMemoryMaterializationViolation(
            "intermediate parser admission failed"
        )
    body = {
        "schema_version": INTERMEDIATE_ADMISSION_SCHEMA,
        "stage_index": index,
        "subplan_hash": subplan["plan_hash"],
        "input_source_hash": receipt["clean_rtl_hash"],
        "output_source_hash": receipt["poison_rtl_hash"],
        "input_ast_hash": receipt["clean_ast_hash"],
        "output_ast_hash": receipt["poison_ast_hash"],
        "materialization_hash": receipt["materialization_hash"],
        "semantic_diff_receipt_hash": semantic["receipt_hash"],
        "parser_provider_receipt_hash": provider["receipt_hash"],
        "admitted": True,
    }
    return {**body, "admission_hash": hash_payload(body)}


def materialize_sequential_plan(
    plan: Mapping[str, Any],
    *,
    policy: PolicyState,
    registries: GroundedRegistryBundle,
    clean_source: str,
) -> dict[str, Any]:
    """Execute and verify every AST stage, including exact reverse restoration."""
    payload = verify_sequential_plan(
        plan,
        policy=policy,
        registries=registries,
    )
    if (
        payload["clean_source_hash"] != source_hash(clean_source)
    ):
        raise ParserMemoryMaterializationViolation(
            "sequential materialization authority binding mismatch"
        )
    current = clean_source
    materializations: list[AstMaterialization] = []
    admissions = []
    expected_hashes = list(payload["expected_stage_source_hashes"])
    if expected_hashes[0] != source_hash(current):
        raise ParserMemoryMaterializationViolation(
            "initial source checkpoint mismatch"
        )
    for index, raw_subplan in enumerate(payload["subplans"]):
        subplan = verify_mutation_plan(
            raw_subplan,
            policy=policy,
            registries=registries,
        )
        materialized = materialize_operator(
            subplan,
            clean_source=current,
        )
        admission = _admit_intermediate(
            index=index,
            subplan=subplan,
            materialized=materialized,
        )
        if (
            admission["input_source_hash"] != source_hash(current)
            or admission["output_source_hash"]
            != expected_hashes[index + 1]
        ):
            raise ParserMemoryMaterializationViolation(
                "intermediate source continuity mismatch"
            )
        current = materialized.poison_source
        materializations.append(materialized)
        admissions.append(admission)
    restored = current
    for materialized in reversed(materializations):
        restored = inverse_materialization(
            materialized.receipt,
            poison_source=restored,
        )
    if restored != clean_source:
        raise ParserMemoryMaterializationViolation(
            "sequential inverse did not restore the exact source"
        )
    body = {
        "schema_version": SEQUENTIAL_RECEIPT_SCHEMA,
        "plan_hash": payload["plan_hash"],
        "plan_schema_version": payload["schema_version"],
        "clean_source_hash": source_hash(clean_source),
        "poison_source_hash": source_hash(current),
        "clean_ast_hash": normalized_ast_hash(clean_source),
        "poison_ast_hash": normalized_ast_hash(current),
        "stage_admissions": admissions,
        "stage_materialization_hashes": [
            value.receipt["materialization_hash"]
            for value in materializations
        ],
        "exact_inverse_source_hash": source_hash(restored),
        "intermediate_admission_complete": True,
    }
    receipt = {**body, "receipt_hash": hash_payload(body)}
    return {
        "poison_source": current,
        "receipt": receipt,
        "materializations": [
            {
                "receipt": value.receipt,
                "semantic_diff_receipt": value.semantic_diff_receipt,
                "parser_provider_receipt": value.parser_provider_receipt,
            }
            for value in materializations
        ],
    }


def verify_sequential_materialization(
    result: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    policy: PolicyState,
    registries: GroundedRegistryBundle,
    clean_source: str,
) -> dict[str, Any]:
    rebuilt = materialize_sequential_plan(
        plan,
        policy=policy,
        registries=registries,
        clean_source=clean_source,
    )
    payload = deepcopy(dict(result))
    if payload != rebuilt:
        raise ParserMemoryMaterializationViolation(
            "sequential materialization cannot be reconstructed"
        )
    return payload
