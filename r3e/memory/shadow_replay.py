"""Paired shadow replay for a candidate ControlMemory."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload

from .plan_compiler import MemoryAwarePlanCompiler
from .schema import (
    BudgetEnvelope,
    ControlMemory,
    ExecutionPlan,
    SHADOW_SCHEMA_VERSION,
    ShadowPairedResult,
)


class ShadowReplayViolation(RuntimeError):
    """Raised when control and shadow arms are not truly paired."""


_PAIR_FIELDS = {"model_id", "budget_hash", "verifier_hash", "toolchain_hash"}
_RESOURCE_FIELDS = {
    "input_tokens", "output_tokens", "llm_calls", "verifier_calls",
    "wall_time_seconds",
}


def _validate_result(
    result: Mapping[str, Any],
    budget: BudgetEnvelope,
    *,
    command_hash: str,
    design: str,
    triggered: bool,
) -> dict[str, Any]:
    payload = deepcopy(dict(result))
    required = _PAIR_FIELDS | {"oracle_ok", "resource_usage", "oracle_evidence_hash"}
    if not required.issubset(payload):
        raise ShadowReplayViolation("shadow evaluator result is incomplete")
    if not isinstance(payload["oracle_ok"], bool):
        raise ShadowReplayViolation("oracle_ok must be boolean")
    if payload["budget_hash"] != budget.budget_hash:
        raise ShadowReplayViolation("evaluator budget hash mismatch")
    if payload.get("design") not in {None, "", design}:
        raise ShadowReplayViolation("evaluator returned wrong replay design")
    evidence_hash = str(payload.get("oracle_evidence_hash") or "")
    if not evidence_hash.startswith("sha256:") or len(evidence_hash) != 71:
        raise ShadowReplayViolation("oracle evidence hash is missing")
    usage = payload["resource_usage"]
    if not isinstance(usage, dict) or set(usage) != _RESOURCE_FIELDS:
        raise ShadowReplayViolation("resource usage fields mismatch")
    if any(
        not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0
        for value in usage.values()
    ):
        raise ShadowReplayViolation("resource usage must be non-negative")
    if (
        usage["llm_calls"] > budget.max_llm_calls
        or usage["verifier_calls"] > budget.max_verifier_calls
        or usage["input_tokens"] + usage["output_tokens"] > budget.max_tokens
        or usage["wall_time_seconds"] > budget.max_wall_seconds
    ):
        raise ShadowReplayViolation("shadow arm exceeded frozen budget")
    payload["schema_version"] = "r3e-memory-shadow-arm-result-v1"
    payload["design"] = design
    payload["triggered"] = triggered
    payload["command_hash"] = command_hash
    payload.pop("result_hash", None)
    payload["result_hash"] = hash_payload(payload)
    return payload


def _shadow_plan(
    policy: PolicyState,
    memory: ControlMemory,
    *,
    trigger_matched: bool,
) -> ExecutionPlan:
    controls = MemoryAwarePlanCompiler._default_controls(policy)
    if not trigger_matched:
        return ExecutionPlan.create(
            effective_policy_hash=policy.effective_policy_hash,
            active_bank_hash="",
            activated_memory_ids=[],
            controls=controls,
        )
    delta = deepcopy(memory.control_delta)
    enabled = set(controls["enable_analyzers"])
    enabled.update(delta.pop("enable_analyzers", []))
    enabled.difference_update(delta.get("disable_analyzers", []))
    controls.update(delta)
    controls["enable_analyzers"] = sorted(enabled)
    return ExecutionPlan.create(
        effective_policy_hash=policy.effective_policy_hash,
        active_bank_hash="",
        activated_memory_ids=[memory.memory_id],
        controls=controls,
    )


def run_shadow_replay(
    *,
    case: Mapping[str, Any],
    policy: PolicyState,
    memory: ControlMemory,
    seed: int,
    budget: BudgetEnvelope,
    evaluator: Callable[
        [PolicyState, Mapping[str, Any], int, ExecutionPlan, BudgetEnvelope],
        Mapping[str, Any],
    ],
) -> ShadowPairedResult:
    case_id = str(case.get("case_id") or case.get("poison_id") or "")
    if not case_id:
        raise ShadowReplayViolation("replay case must bind case_id")
    design = str(case.get("design") or case.get("design_id") or "")
    if not design:
        raise ShadowReplayViolation("replay case must bind design")
    control_plan = ExecutionPlan.create(
        effective_policy_hash=policy.effective_policy_hash,
        active_bank_hash="",
        activated_memory_ids=[],
        controls=MemoryAwarePlanCompiler._default_controls(policy),
    )
    trigger_matched = bool(case.get("trigger_matched", True))
    shadow_plan = _shadow_plan(
        policy, memory, trigger_matched=trigger_matched
    )
    def evaluate(plan: ExecutionPlan, *, triggered: bool) -> dict[str, Any]:
        command_hash = hash_payload({
            "schema_version": "r3e-memory-shadow-command-v1",
            "policy_hash": policy.policy_hash,
            "case_hash": hash_payload(dict(case)),
            "seed": int(seed),
            "execution_plan_hash": plan.plan_hash,
            "budget_hash": budget.budget_hash,
        })
        return _validate_result(
            evaluator(policy, case, seed, plan, budget),
            budget,
            command_hash=command_hash,
            design=design,
            triggered=triggered,
        )

    control = evaluate(control_plan, triggered=False)
    shadow = evaluate(shadow_plan, triggered=trigger_matched)
    for field in _PAIR_FIELDS:
        if control[field] != shadow[field]:
            raise ShadowReplayViolation(f"paired shadow {field} mismatch")
    outcome = {
        (False, True): "helped",
        (True, False): "harmed",
        (True, True): "neutral_pass",
        (False, False): "neutral_fail",
    }[(control["oracle_ok"], shadow["oracle_ok"])]
    resource_delta = {
        field: float(shadow["resource_usage"][field] - control["resource_usage"][field])
        for field in sorted(_RESOURCE_FIELDS)
    }
    body = {
        "schema_version": SHADOW_SCHEMA_VERSION,
        "case_id": case_id,
        "seed": int(seed),
        "policy_hash": policy.policy_hash,
        "memory_hash": memory.memory_hash,
        "control": control,
        "shadow": shadow,
        "outcome": outcome,
        "resource_delta": resource_delta,
    }
    return ShadowPairedResult(
        case_id=case_id,
        seed=int(seed),
        policy_hash=policy.policy_hash,
        memory_hash=memory.memory_hash,
        control=control,
        shadow=shadow,
        outcome=outcome,
        resource_delta=resource_delta,
        pair_hash=hash_payload(body),
    )
