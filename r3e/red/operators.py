"""Frozen, executable lineage-operator plans.

Adapters execute a plan, but the formal runner validates the returned poison
against the frozen transition contract.  The adapter therefore has no
authority to widen mutation scope or silently change lineage.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload, read_json

from .lineage import LINEAGE_OPERATORS


OPERATOR_SCHEMA_VERSION = "r3e-lineage-operator-space-v1"
PLAN_SCHEMA_VERSION = "r3e-lineage-plan-v1"
_PLAN_FIELDS = {
    "schema_version",
    "operator_space_hash",
    "challenged_policy_id",
    "challenged_policy_hash",
    "poison_id",
    "operator",
    "parent_poison_id",
    "parent_challenged_policy_hash",
    "expected_lineage_depth",
    "parent_family",
    "parent_effect",
    "parent_affected_role",
    "parent_sequential_depth",
    "parent_composition_depth",
    "plan_hash",
}


class LineageOperatorViolation(RuntimeError):
    """Raised when an operator plan or its execution violates the contract."""


def load_operator_space(path: str | Path) -> dict[str, Any]:
    payload = read_json(path)
    required = {
        "schema_version",
        "frozen",
        "max_lineage_depth",
        "max_composition_depth",
        "max_changed_modules",
        "max_changed_blocks",
        "operators",
    }
    if set(payload) != required:
        raise LineageOperatorViolation("lineage operator space schema mismatch")
    if payload["schema_version"] != OPERATOR_SCHEMA_VERSION or payload["frozen"] is not True:
        raise LineageOperatorViolation("lineage operator space must be frozen v1")
    if set(payload["operators"]) != LINEAGE_OPERATORS:
        raise LineageOperatorViolation("lineage operator set mismatch")
    for key in (
        "max_lineage_depth",
        "max_composition_depth",
        "max_changed_modules",
        "max_changed_blocks",
    ):
        if int(payload[key]) < 1:
            raise LineageOperatorViolation(f"{key} must be positive")
    result = dict(payload)
    result["operator_space_hash"] = hash_payload(payload)
    return result


def make_lineage_plan(
    policy: PolicyState,
    *,
    operator_space: dict[str, Any],
    operator: str,
    poison_id: str,
    parent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if operator not in operator_space["operators"]:
        raise LineageOperatorViolation(f"unsupported lineage operator: {operator}")
    if (parent is None) != (operator == "fresh"):
        raise LineageOperatorViolation("fresh has no parent; every other operator requires one")
    parent_depth = int((parent or {}).get("lineage_depth") or 0)
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "operator_space_hash": operator_space["operator_space_hash"],
        "challenged_policy_id": policy.policy_id,
        "challenged_policy_hash": policy.policy_hash,
        "poison_id": poison_id,
        "operator": operator,
        "parent_poison_id": str((parent or {}).get("poison_id") or ""),
        "parent_challenged_policy_hash": str(
            (parent or {}).get("challenged_policy_hash") or ""
        ),
        "expected_lineage_depth": 0 if parent is None else parent_depth + 1,
        "parent_family": str((parent or {}).get("family") or ""),
        "parent_effect": str((parent or {}).get("effect") or ""),
        "parent_affected_role": str((parent or {}).get("affected_role") or ""),
        "parent_sequential_depth": int((parent or {}).get("sequential_depth") or 0),
        "parent_composition_depth": int((parent or {}).get("composition_depth") or 1),
    }
    plan["plan_hash"] = hash_payload(plan)
    return plan


def verify_lineage_execution(
    poison: dict[str, Any],
    *,
    policy: PolicyState,
    operator_space: dict[str, Any],
    available_parents: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    plan = poison.get("lineage_plan")
    if not isinstance(plan, dict):
        raise LineageOperatorViolation("red candidate is missing lineage plan")
    if set(plan) != _PLAN_FIELDS:
        raise LineageOperatorViolation("lineage plan fields mismatch")
    if plan.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise LineageOperatorViolation("lineage plan schema mismatch")
    body = {key: value for key, value in plan.items() if key != "plan_hash"}
    if plan.get("plan_hash") != hash_payload(body):
        raise LineageOperatorViolation("lineage plan hash mismatch")
    if plan.get("operator_space_hash") != operator_space["operator_space_hash"]:
        raise LineageOperatorViolation("lineage plan uses a different operator space")
    if (
        plan.get("challenged_policy_id") != policy.policy_id
        or plan.get("challenged_policy_hash") != policy.policy_hash
        or poison.get("challenged_policy_hash") != policy.policy_hash
    ):
        raise LineageOperatorViolation("lineage execution is bound to the wrong policy")
    if plan.get("poison_id") != poison.get("poison_id"):
        raise LineageOperatorViolation("lineage plan poison id mismatch")

    operator = str(plan.get("operator") or "")
    if operator not in operator_space["operators"]:
        raise LineageOperatorViolation("lineage plan operator is not frozen")
    parent_id = str(plan.get("parent_poison_id") or "")
    parent = available_parents.get(parent_id) if parent_id else None
    if operator == "fresh":
        if parent_id or parent is not None:
            raise LineageOperatorViolation("fresh operator cannot bind a parent")
        expected_depth = 0
    else:
        if parent is None:
            raise LineageOperatorViolation("lineage parent is unavailable")
        if plan.get("parent_challenged_policy_hash") != parent.get(
            "challenged_policy_hash"
        ):
            raise LineageOperatorViolation("lineage parent policy hash mismatch")
        if (
            plan.get("parent_family") != parent.get("family")
            or plan.get("parent_effect") != parent.get("effect")
            or plan.get("parent_affected_role") != parent.get("affected_role")
            or int(plan.get("parent_sequential_depth") or 0)
            != int(parent.get("sequential_depth") or 0)
            or int(plan.get("parent_composition_depth") or 1)
            != int(parent.get("composition_depth") or 1)
        ):
            raise LineageOperatorViolation("lineage plan parent descriptor mismatch")
        expected_depth = int(parent.get("lineage_depth") or 0) + 1
        if operator != "compose" and poison.get("family") != parent.get("family"):
            raise LineageOperatorViolation("operator must preserve poison family")
        if operator == "relocate" and poison.get("affected_role") == parent.get(
            "affected_role"
        ):
            raise LineageOperatorViolation("relocate must change affected role")
        if operator == "temporalize" and int(poison.get("sequential_depth") or 0) <= int(
            parent.get("sequential_depth") or 0
        ):
            raise LineageOperatorViolation("temporalize must increase sequential depth")
        if operator == "counterexample_revise" and (
            poison.get("effect") == parent.get("effect")
            and poison.get("failure_signature") == parent.get("failure_signature")
        ):
            raise LineageOperatorViolation(
                "counterexample_revise must change effect or failure signature"
            )
        if operator == "compose" and int(poison.get("composition_depth") or 1) != int(
            parent.get("composition_depth") or 1
        ) + 1:
            raise LineageOperatorViolation(
                "compose must increase composition depth by one"
            )
    if int(plan.get("expected_lineage_depth", -1)) != expected_depth:
        raise LineageOperatorViolation("lineage plan depth mismatch")
    if (
        int(poison.get("lineage_depth") or 0) != expected_depth
        or poison.get("evolution_operator") != operator
        or str(poison.get("parent_poison_id") or "") != parent_id
    ):
        raise LineageOperatorViolation("lineage execution does not match its plan")
    if expected_depth > int(operator_space["max_lineage_depth"]):
        raise LineageOperatorViolation("maximum lineage depth exceeded")
    if int(poison.get("composition_depth") or 1) > int(
        operator_space["max_composition_depth"]
    ):
        raise LineageOperatorViolation("maximum composition depth exceeded")
    if int(poison.get("changed_modules") or 1) > int(
        operator_space["max_changed_modules"]
    ):
        raise LineageOperatorViolation("maximum changed modules exceeded")
    if int(poison.get("changed_blocks") or 1) > int(
        operator_space["max_changed_blocks"]
    ):
        raise LineageOperatorViolation("maximum changed blocks exceeded")
    return poison


def execute_lineage_operator(
    plan: dict[str, Any],
    parent: dict[str, Any] | None,
    *,
    policy: PolicyState,
    operator_space: dict[str, Any],
    executor: Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any]],
) -> dict[str, Any]:
    """Execute an adapter operation and fail closed on postcondition mismatch."""
    candidate = dict(executor(dict(plan), dict(parent) if parent else None))
    if candidate.get("lineage_plan") is None:
        candidate["lineage_plan"] = dict(plan)
    parents = (
        {str(parent["poison_id"]): parent}
        if parent is not None and parent.get("poison_id")
        else {}
    )
    return verify_lineage_execution(
        candidate,
        policy=policy,
        operator_space=operator_space,
        available_parents=parents,
    )
