"""Current-case execution authorization for active-dormant memories."""
from __future__ import annotations

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload

from .conflict_resolver import conflict_fields
from .memory_store import MemoryStore
from .schema import (
    ActiveMemoryBank,
    MemoryMatch,
    ReactivationDecision,
    RuntimeContext,
    validate_control_delta,
)
from .whitelist import default_whitelist, validate_whitelist


class ActivationViolation(RuntimeError):
    """Raised when an activation request has invalid authority bindings."""


class ActivationGuard:
    schema_version = "r3e-memory-activation-guard-v1"

    def __init__(
        self,
        store: MemoryStore,
        *,
        minimum_score: float = 1.0,
        control_whitelist: dict | None = None,
    ):
        self.store = store
        self.minimum_score = minimum_score
        self.control_whitelist = validate_whitelist(
            control_whitelist or default_whitelist()
        )

    @property
    def control_whitelist_hash(self) -> str:
        return self.control_whitelist["whitelist_hash"]

    @property
    def guard_hash(self) -> str:
        return hash_payload({
            "schema_version": self.schema_version,
            "minimum_score": self.minimum_score,
            "maximum_activation": 1,
            "conflict_behavior": "abstain",
            "control_whitelist_hash": self.control_whitelist_hash,
        })

    def _abstain(
        self, reason: str, policy: PolicyState, bank: ActiveMemoryBank
    ) -> ReactivationDecision:
        return ReactivationDecision.create(
            activated_memory_ids=[],
            abstained=True,
            reason_code=reason,
            effective_policy_hash=policy.effective_policy_hash,
            active_bank_hash=bank.bank_hash,
        )

    @staticmethod
    def _delta_within_budget(memory, runtime_context: RuntimeContext) -> bool:
        delta = memory.control_delta
        initial = int(delta.get("initial_candidates", 1))
        revisions = int(delta.get("revision_rounds", 0))
        batch = int(delta.get("candidate_batch_size", initial))
        estimated_llm_calls = max(initial, batch) + revisions
        verifier_order = delta.get("verifier_order", ["simulation"])
        estimated_verifier_calls = max(initial, batch) * len(verifier_order)
        return (
            estimated_llm_calls <= runtime_context.budget.max_llm_calls
            and estimated_verifier_calls
            <= runtime_context.budget.max_verifier_calls
        )

    def reactivate(
        self,
        *,
        matches: list[MemoryMatch],
        active_policy: PolicyState,
        active_bank: ActiveMemoryBank,
        runtime_context: RuntimeContext,
    ) -> ReactivationDecision:
        if active_bank.activation_guard_hash != self.guard_hash:
            raise ActivationViolation("active bank activation guard hash mismatch")
        if (
            active_bank.control_whitelist_hash != runtime_context.control_whitelist_hash
            or active_bank.control_whitelist_hash != self.control_whitelist_hash
        ):
            raise ActivationViolation("memory control whitelist hash mismatch")
        if runtime_context.effective_policy_hash != active_policy.effective_policy_hash:
            raise ActivationViolation("runtime effective policy hash mismatch")
        if runtime_context.policy_instance_hash != active_policy.policy_instance_hash:
            raise ActivationViolation("runtime policy instance hash mismatch")
        if active_bank.effective_policy_hash != active_policy.effective_policy_hash:
            raise ActivationViolation("active bank is not bound to effective policy")
        binding = active_policy.memory_binding or {}
        if binding != active_bank.policy_binding:
            raise ActivationViolation("policy does not authorize exact active memory bank")
        policy_budgets = active_policy.budgets
        max_policy_verifier_calls = (
            int(policy_budgets["max_llm_calls_per_case"])
            * len(active_policy.configuration["verifier_order"])
        )
        if (
            runtime_context.budget.max_llm_calls
            > int(policy_budgets["max_llm_calls_per_case"])
            or runtime_context.budget.max_tokens
            > int(policy_budgets["max_tokens_per_case"])
            or runtime_context.budget.max_wall_seconds
            > float(policy_budgets["max_wall_seconds_per_case"])
            or runtime_context.budget.max_verifier_calls
            > max_policy_verifier_calls
        ):
            raise ActivationViolation("runtime memory budget exceeds active policy")
        if not matches:
            return self._abstain("no_match", active_policy, active_bank)
        eligible = []
        for match in matches:
            bank_binding = active_bank.memories.get(match.memory_id)
            if not bank_binding:
                raise ActivationViolation("retriever returned memory outside active bank")
            if (
                bank_binding["memory_version"] != match.memory_version
                or bank_binding["memory_hash"] != match.memory_hash
            ):
                raise ActivationViolation("retrieval match does not bind bank version")
            status = self.store.current_status(match.memory_id, match.memory_version)
            if status != "active_dormant":
                continue
            memory = self.store.get_version(match.memory_id, match.memory_version)
            validate_control_delta(memory.control_delta)
            if not set(memory.control_delta).issubset(
                self.control_whitelist["allowed_control_fields"]
            ):
                continue
            required = set(memory.control_delta.get("enable_analyzers", []))
            if not required.issubset(runtime_context.available_analyzers):
                continue
            if not self._delta_within_budget(memory, runtime_context):
                continue
            if match.score >= self.minimum_score:
                eligible.append(memory)
        if not eligible:
            return self._abstain("no_eligible_match", active_policy, active_bank)
        if len(eligible) > 1:
            if conflict_fields(eligible):
                return self._abstain("control_delta_conflict", active_policy, active_bank)
            return self._abstain("unqualified_memory_bundle", active_policy, active_bank)
        return ReactivationDecision.create(
            activated_memory_ids=[eligible[0].memory_id],
            abstained=False,
            reason_code="reactivated",
            effective_policy_hash=active_policy.effective_policy_hash,
            active_bank_hash=active_bank.bank_hash,
        )
