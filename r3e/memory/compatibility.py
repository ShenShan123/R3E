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
    if not affected and previous.frozen_assets == current.frozen_assets:
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
        "previous_policy_hash": previous.policy_hash,
        "current_policy_hash": current.policy_hash,
    }
