"""Compile policy plus an authorized control delta into an execution plan."""
from __future__ import annotations

from copy import deepcopy

from r3e.policy.schema import PolicyState

from .memory_store import MemoryStore
from .schema import ActiveMemoryBank, ExecutionPlan, ReactivationDecision


class PlanCompilationViolation(RuntimeError):
    """Raised when a plan attempts to consume unauthorized memory."""


class MemoryAwarePlanCompiler:
    def __init__(self, store: MemoryStore, active_bank: ActiveMemoryBank):
        self.store = store
        self.active_bank = active_bank

    @staticmethod
    def _default_controls(policy: PolicyState) -> dict:
        configuration = policy.configuration
        return {
            "enable_analyzers": ["first_divergence"],
            "disable_analyzers": [],
            "rtl_slice_mode": "none",
            "first_divergence_only": False,
            "initial_candidates": int(configuration["n_candidates"]),
            "revision_rounds": 1 if configuration["repair_loop"] == "critique-revise" else 0,
            "candidate_batch_size": int(configuration["n_candidates"]),
            "candidate_ranking": configuration["candidate_selection"],
            "early_stop": "first_verified",
            "verifier_order": list(configuration["verifier_order"]),
            "max_changed_blocks": 1,
            "prefer_local_patch": configuration["patch_scope"] == "local_block",
        }

    def compile(
        self,
        *,
        base_policy: PolicyState,
        reactivation: ReactivationDecision,
    ) -> ExecutionPlan:
        if reactivation.effective_policy_hash != base_policy.policy_hash:
            raise PlanCompilationViolation("reactivation policy hash mismatch")
        controls = self._default_controls(base_policy)
        if reactivation.abstained:
            if reactivation.activated_memory_ids:
                raise PlanCompilationViolation("abstained decision cannot activate memory")
        else:
            if len(reactivation.activated_memory_ids) != 1:
                raise PlanCompilationViolation("formal v1 plan requires exactly one memory")
            memory_id = reactivation.activated_memory_ids[0]
            if (
                (base_policy.memory_binding or {}).get("active_memory_bank_hash")
                != self.active_bank.bank_hash
            ):
                raise PlanCompilationViolation("policy does not bind compiler memory bank")
            binding = self.active_bank.memories.get(memory_id)
            if not binding:
                raise PlanCompilationViolation("policy binding omits activated memory")
            memory = self.store.get_version(memory_id, int(binding["memory_version"]))
            if memory.memory_hash != binding["memory_hash"]:
                raise PlanCompilationViolation("policy memory binding hash mismatch")
            if self.store.current_status(memory_id, memory.memory_version) != "active_dormant":
                raise PlanCompilationViolation("activated memory is not active_dormant")
            delta = deepcopy(memory.control_delta)
            enabled = set(controls["enable_analyzers"])
            enabled.update(delta.pop("enable_analyzers", []))
            enabled.difference_update(delta.get("disable_analyzers", []))
            controls.update(delta)
            controls["enable_analyzers"] = sorted(enabled)
        return ExecutionPlan.create(
            effective_policy_hash=base_policy.policy_hash,
            active_bank_hash=reactivation.active_bank_hash,
            activated_memory_ids=reactivation.activated_memory_ids,
            controls=controls,
        )
