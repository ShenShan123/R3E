"""Policy-transition compatibility classification for persistent memories."""
from __future__ import annotations

from r3e.policy.schema import PolicyState

from .schema import ControlMemory


_DELTA_POLICY_DEPENDENCIES = {
    "candidate_ranking": {"candidate_selection"},
    "initial_candidates": {"n_candidates"},
    "candidate_batch_size": {"n_candidates", "blue_population"},
    "revision_rounds": {"repair_loop"},
    "verifier_order": {"verifier_order"},
    "max_changed_blocks": {"patch_scope"},
    "prefer_local_patch": {"patch_scope"},
    "evidence_window_before": {"evidence_mode", "evidence_k"},
    "evidence_window_after": {"evidence_mode", "evidence_k"},
    "rtl_slice_mode": {"evidence_mode", "evidence_k"},
    "cone_depth": {"evidence_mode", "evidence_k"},
}


def classify_policy_compatibility(
    memory: ControlMemory,
    previous: PolicyState,
    current: PolicyState,
) -> dict[str, object]:
    changed = {
        field for field in previous.configuration
        if previous.configuration[field] != current.configuration[field]
    }
    relevant = set()
    for delta_field in memory.control_delta:
        relevant.update(_DELTA_POLICY_DEPENDENCIES.get(delta_field, set()))
    affected = sorted(changed & relevant)
    changed_budgets = sorted(
        field for field in previous.budgets
        if previous.budgets[field] != current.budgets[field]
    )
    component_fields = {
        "retriever_hash",
        "activation_guard_hash",
        "memory_control_whitelist_hash",
    }
    previous_binding = previous.memory_binding or {}
    current_binding = current.memory_binding or {}
    component_changes = sorted(
        field for field in component_fields
        if previous_binding
        and current_binding
        and previous_binding.get(field) != current_binding.get(field)
    )
    assets_changed = previous.frozen_assets != current.frozen_assets
    if (
        not affected
        and not changed_budgets
        and not component_changes
        and not assets_changed
    ):
        classification = "static_compatible"
        next_status = "active_dormant"
    else:
        classification = "incremental_revalidation_required"
        next_status = "revalidation_required"
    return {
        "classification": classification,
        "next_status": next_status,
        "changed_policy_fields": sorted(changed),
        "affected_policy_fields": affected,
        "changed_budget_fields": changed_budgets,
        "changed_runtime_components": component_changes,
        "frozen_assets_changed": assets_changed,
        "plan_compiler_version": "r3e-memory-plan-compiler-v1",
        "conflict_policy_version": "r3e-memory-conflict-policy-v1",
        "execution_trace_schema": "r3e-memory-execution-trace-v1",
        "previous_policy_hash": previous.policy_hash,
        "current_policy_hash": current.policy_hash,
    }
