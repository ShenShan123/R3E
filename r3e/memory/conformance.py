"""Conformance gate for model/tool adapters used by RAAM replay."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload

from .schema import ActiveMemoryBank, BudgetEnvelope, ExecutionPlan


class MemoryAdapterConformanceViolation(RuntimeError):
    pass


class MemoryAdapterConformanceGate:
    def __init__(self, adapter: Any):
        fingerprint = getattr(adapter, "toolchain_fingerprint", None)
        if not isinstance(fingerprint, dict) or not fingerprint:
            raise MemoryAdapterConformanceViolation(
                "memory adapter toolchain fingerprint missing"
            )
        self.fingerprint = deepcopy(fingerprint)
        self.toolchain_hash = hash_payload(self.fingerprint)

    @staticmethod
    def _command_hash(
        *,
        operation: str,
        policy: PolicyState,
        bank: ActiveMemoryBank | None,
        case: Mapping[str, Any],
        seed: int,
        execution_plan: ExecutionPlan | None = None,
        budget: BudgetEnvelope | None = None,
    ) -> str:
        return hash_payload({
            "schema_version": "r3e-memory-adapter-command-v1",
            "operation": operation,
            "policy_hash": policy.policy_hash,
            "active_memory_bank_hash": bank.bank_hash if bank else "",
            "case_hash": hash_payload(dict(case)),
            "seed": int(seed),
            "execution_plan_hash": execution_plan.plan_hash if execution_plan else "",
            "budget_hash": budget.budget_hash if budget else "",
        })

    def _validate(
        self,
        result: Mapping[str, Any],
        expected_command_hash: str,
        *,
        expected_budget_hash: str,
    ) -> dict:
        payload = deepcopy(dict(result))
        if payload.get("adapter_schema_version") != "r3e-memory-adapter-output-v1":
            raise MemoryAdapterConformanceViolation("memory adapter output schema mismatch")
        if payload.get("toolchain_hash") != self.toolchain_hash:
            raise MemoryAdapterConformanceViolation("memory adapter toolchain mismatch")
        if payload.get("model_id") != self.fingerprint.get("model_id"):
            raise MemoryAdapterConformanceViolation("memory adapter model mismatch")
        expected_verifier_hash = hash_payload({
            "verifier": self.fingerprint.get("verifier_id")
        })
        if payload.get("verifier_hash") != expected_verifier_hash:
            raise MemoryAdapterConformanceViolation("memory adapter verifier mismatch")
        if payload.get("budget_hash") != expected_budget_hash:
            raise MemoryAdapterConformanceViolation("memory adapter budget mismatch")
        if payload.get("adapter_command_hash") != expected_command_hash:
            raise MemoryAdapterConformanceViolation("memory adapter command hash mismatch")
        body = {
            key: value for key, value in payload.items()
            if key != "adapter_result_hash"
        }
        if payload.get("adapter_result_hash") != hash_payload(body):
            raise MemoryAdapterConformanceViolation("memory adapter result hash mismatch")
        return payload

    def validate_shadow(
        self,
        result: Mapping[str, Any],
        *,
        policy: PolicyState,
        case: Mapping[str, Any],
        seed: int,
        execution_plan: ExecutionPlan,
        budget: BudgetEnvelope,
    ) -> dict:
        return self._validate(
            result,
            self._command_hash(
                operation="shadow_replay",
                policy=policy,
                bank=None,
                case=case,
                seed=seed,
                execution_plan=execution_plan,
                budget=budget,
            ),
            expected_budget_hash=budget.budget_hash,
        )

    def validate_policy_replay(
        self,
        result: Mapping[str, Any],
        *,
        policy: PolicyState,
        bank: ActiveMemoryBank | None,
        case: Mapping[str, Any],
        seed: int,
    ) -> dict:
        return self._validate(
            result,
            self._command_hash(
                operation="policy_replay",
                policy=policy,
                bank=bank,
                case=case,
                seed=seed,
            ),
            expected_budget_hash=hash_payload(policy.budgets),
        )


def bind_memory_adapter_output(
    result: Mapping[str, Any],
    *,
    operation: str,
    policy: PolicyState,
    toolchain_fingerprint: dict[str, Any],
    case: Mapping[str, Any],
    seed: int,
    bank: ActiveMemoryBank | None = None,
    execution_plan: ExecutionPlan | None = None,
    budget: BudgetEnvelope | None = None,
) -> dict[str, Any]:
    payload = {
        **deepcopy(dict(result)),
        "adapter_schema_version": "r3e-memory-adapter-output-v1",
        "toolchain_hash": hash_payload(toolchain_fingerprint),
        "adapter_command_hash": MemoryAdapterConformanceGate._command_hash(
            operation=operation,
            policy=policy,
            bank=bank,
            case=case,
            seed=seed,
            execution_plan=execution_plan,
            budget=budget,
        ),
    }
    payload["adapter_result_hash"] = hash_payload(payload)
    return payload
