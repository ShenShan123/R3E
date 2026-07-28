"""Model-free RAAM adapter used only for protocol/state-machine tests."""
from __future__ import annotations

from typing import Any, Mapping

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload

from .schema import (
    ActiveMemoryBank,
    BudgetEnvelope,
    ExecutionPlan,
)
from .conformance import bind_memory_adapter_output


DETERMINISTIC_MEMORY_TOOLCHAIN = {
    "schema_version": "r3e-memory-fake-toolchain-v1",
    "adapter_id": "deterministic-memory-fake",
    "model_id": "deterministic-memory-model",
    "verifier_id": "deterministic-memory-verifier",
    "runtime_id": "python-fixture",
}


class DeterministicMemoryAdapter:
    """Shadow helps target cases; bank child preserves non-target cases."""

    toolchain_fingerprint = DETERMINISTIC_MEMORY_TOOLCHAIN

    @staticmethod
    def _is_target(case: Mapping[str, Any]) -> bool:
        return bool(
            case.get("expected_shadow_help")
            or case.get("challenged_policy_hash")
        )

    def replay_memory(
        self,
        policy: PolicyState,
        case: Mapping[str, Any],
        seed: int,
        execution_plan: ExecutionPlan,
        budget: BudgetEnvelope,
    ) -> dict[str, Any]:
        activated = bool(execution_plan.activated_memory_ids)
        target = self._is_target(case)
        oracle_ok = (not target) or activated
        return bind_memory_adapter_output({
            "oracle_ok": oracle_ok,
            "model_id": self.toolchain_fingerprint["model_id"],
            "budget_hash": budget.budget_hash,
            "verifier_hash": hash_payload({
                "verifier": self.toolchain_fingerprint["verifier_id"]
            }),
            "oracle_evidence_hash": hash_payload({
                "case_id": case.get("case_id"),
                "seed": seed,
                "plan_hash": execution_plan.plan_hash,
                "oracle_ok": oracle_ok,
            }),
            "triggered": activated,
            "resource_usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "llm_calls": 1,
                "verifier_calls": 1,
                "wall_time_seconds": 1.0,
            },
        },
            operation="shadow_replay",
            policy=policy,
            toolchain_fingerprint=self.toolchain_fingerprint,
            case=case,
            seed=seed,
            execution_plan=execution_plan,
            budget=budget,
        )

    def replay_policy(
        self,
        policy: PolicyState,
        bank: ActiveMemoryBank | None,
        case: Mapping[str, Any],
        seed: int,
    ) -> dict[str, Any]:
        target = self._is_target(case)
        oracle_ok = (not target) or bank is not None
        return bind_memory_adapter_output({
            "policy_hash": policy.policy_hash,
            "seed": seed,
            "oracle_ok": oracle_ok,
            "model_id": self.toolchain_fingerprint["model_id"],
            "budget_hash": hash_payload(policy.budgets),
            "verifier_hash": hash_payload({
                "verifier": self.toolchain_fingerprint["verifier_id"]
            }),
            "cost": 1.0,
        },
            operation="policy_replay",
            policy=policy,
            toolchain_fingerprint=self.toolchain_fingerprint,
            case=case,
            seed=seed,
            bank=bank,
        )
