"""Strict, policy-bound structured mutation plans."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload

from .registry import GroundedRegistryBundle


MUTATION_PLAN_SCHEMA_VERSION = "r3e-grounded-mutation-plan-v1"
DIFFICULTY_BANDS = {"D0", "D1", "D2", "D3", "D4"}
LINEAGE_OPERATORS = {
    "fresh",
    "deepens",
    "bypasses",
    "transfers",
    "composes_with",
    "minimized_from",
    "invalidates_memory",
}
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PLAN_FIELDS = {
    "schema_version",
    "plan_id",
    "challenged_policy_instance_hash",
    "challenged_effective_policy_hash",
    "registry_bundle_hash",
    "target_design",
    "target_module",
    "target_ast_node_hash",
    "family_id",
    "operator_id",
    "expected_runtime_effect_id",
    "preconditions",
    "scope_limits",
    "difficulty_target",
    "lineage",
    "plan_hash",
}


class MutationPlanViolation(RuntimeError):
    """Raised when a mutation plan widens or mislabels its authority."""


def _digest(value: Any, field: str) -> str:
    text = str(value or "")
    if not _HASH_RE.fullmatch(text):
        raise MutationPlanViolation(f"{field} must be an exact sha256 digest")
    return text


def build_mutation_plan(
    *,
    plan_id: str,
    policy: PolicyState,
    registries: GroundedRegistryBundle,
    target_design: str,
    target_module: str,
    target_ast_node_hash: str,
    family_id: str,
    operator_id: str,
    expected_runtime_effect_id: str,
    preconditions: Mapping[str, bool],
    scope_limits: Mapping[str, int],
    difficulty_target: Mapping[str, Any],
    parent_poison_ids: list[str] | None = None,
    lineage_operator: str = "fresh",
) -> dict[str, Any]:
    payload = {
        "schema_version": MUTATION_PLAN_SCHEMA_VERSION,
        "plan_id": plan_id,
        "challenged_policy_instance_hash": policy.policy_instance_hash,
        "challenged_effective_policy_hash": policy.effective_policy_hash,
        "registry_bundle_hash": registries.registry_bundle_hash,
        "target_design": target_design,
        "target_module": target_module,
        "target_ast_node_hash": target_ast_node_hash,
        "family_id": family_id,
        "operator_id": operator_id,
        "expected_runtime_effect_id": expected_runtime_effect_id,
        "preconditions": deepcopy(dict(preconditions)),
        "scope_limits": deepcopy(dict(scope_limits)),
        "difficulty_target": deepcopy(dict(difficulty_target)),
        "lineage": {
            "parent_poison_ids": list(parent_poison_ids or []),
            "evolution_operator": lineage_operator,
        },
    }
    payload["plan_hash"] = hash_payload(payload)
    return verify_mutation_plan(payload, policy=policy, registries=registries)


def verify_mutation_plan(
    plan: Mapping[str, Any],
    *,
    policy: PolicyState,
    registries: GroundedRegistryBundle,
) -> dict[str, Any]:
    payload = deepcopy(dict(plan))
    if set(payload) != _PLAN_FIELDS:
        raise MutationPlanViolation("mutation plan fields mismatch")
    if payload["schema_version"] != MUTATION_PLAN_SCHEMA_VERSION:
        raise MutationPlanViolation("mutation plan schema mismatch")
    body = {key: value for key, value in payload.items() if key != "plan_hash"}
    if payload["plan_hash"] != hash_payload(body):
        raise MutationPlanViolation("mutation plan hash mismatch")
    for field in ("plan_id", "target_design", "target_module"):
        if not isinstance(payload[field], str) or not payload[field]:
            raise MutationPlanViolation(f"{field} must be non-empty")
    _digest(payload["target_ast_node_hash"], "target_ast_node_hash")
    if (
        payload["challenged_policy_instance_hash"]
        != policy.policy_instance_hash
        or payload["challenged_effective_policy_hash"]
        != policy.effective_policy_hash
    ):
        raise MutationPlanViolation("mutation plan is bound to a stale policy")
    if payload["registry_bundle_hash"] != registries.registry_bundle_hash:
        raise MutationPlanViolation("mutation plan registry binding mismatch")
    family_id = str(payload["family_id"])
    operator_id = str(payload["operator_id"])
    effect_id = str(payload["expected_runtime_effect_id"])
    family = registries.families.get(family_id)
    operator = registries.operators.get(operator_id)
    effect = registries.effects.get(effect_id)
    if family is None or operator is None or effect is None:
        raise MutationPlanViolation("mutation plan uses an unknown registry entry")
    if family_id not in operator["supported_family_ids"]:
        raise MutationPlanViolation("operator does not support declared family")
    if family_id not in effect["supported_family_ids"]:
        raise MutationPlanViolation("runtime effect does not support declared family")
    preconditions = payload["preconditions"]
    if not isinstance(preconditions, dict) or any(
        not isinstance(key, str) or not isinstance(value, bool)
        for key, value in preconditions.items()
    ):
        raise MutationPlanViolation("preconditions must be a boolean object")
    if set(preconditions) != set(operator["preconditions"]):
        raise MutationPlanViolation("mutation plan preconditions mismatch operator")
    if not all(preconditions.values()):
        raise MutationPlanViolation("mutation operator precondition is not satisfied")
    scope = payload["scope_limits"]
    if set(scope) != {
        "maximum_changed_modules",
        "maximum_changed_blocks",
        "maximum_ast_edits",
    }:
        raise MutationPlanViolation("scope limit fields mismatch")
    for field in scope:
        value = scope[field]
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 1
            or value > int(operator[field])
        ):
            raise MutationPlanViolation(f"{field} exceeds operator authority")
    difficulty = payload["difficulty_target"]
    if set(difficulty) != {
        "difficulty_band",
        "dependency_depth_delta",
        "temporal_depth_delta",
    }:
        raise MutationPlanViolation("difficulty target fields mismatch")
    if difficulty["difficulty_band"] not in DIFFICULTY_BANDS:
        raise MutationPlanViolation("unknown difficulty band")
    for field in ("dependency_depth_delta", "temporal_depth_delta"):
        if (
            not isinstance(difficulty[field], int)
            or isinstance(difficulty[field], bool)
            or difficulty[field] < 0
        ):
            raise MutationPlanViolation(f"{field} must be non-negative")
    lineage = payload["lineage"]
    if not isinstance(lineage, dict) or set(lineage) != {
        "parent_poison_ids",
        "evolution_operator",
    }:
        raise MutationPlanViolation("mutation lineage fields mismatch")
    parent_ids = lineage["parent_poison_ids"]
    if (
        not isinstance(parent_ids, list)
        or len(parent_ids) != len(set(parent_ids))
        or any(not isinstance(value, str) or not value for value in parent_ids)
    ):
        raise MutationPlanViolation("parent poison ids must be unique strings")
    lineage_operator = lineage["evolution_operator"]
    if lineage_operator not in LINEAGE_OPERATORS:
        raise MutationPlanViolation("unknown mutation lineage operator")
    if (lineage_operator == "fresh") != (not parent_ids):
        raise MutationPlanViolation("fresh lineage cannot have parents")
    if lineage_operator == "composes_with" and len(parent_ids) != 2:
        raise MutationPlanViolation("composition requires exactly two parents")
    if lineage_operator != "composes_with" and len(parent_ids) > 1:
        raise MutationPlanViolation("non-composition lineage has at most one parent")
    if family_id == "composition.controlled_pair":
        if lineage_operator != "composes_with":
            raise MutationPlanViolation("composition family requires composition lineage")
    elif difficulty["difficulty_band"] == "D4":
        raise MutationPlanViolation("D4 is reserved for controlled composition")
    return payload
