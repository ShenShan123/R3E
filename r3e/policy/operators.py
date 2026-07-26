"""Frozen, auditable whole-policy mutation operators."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .schema import PolicyState


MUTABLE_DIMENSIONS = ("evidence_mode", "evidence_k", "n_candidates", "repair_loop")


def one_factor_neighbors(
    parent: PolicyState,
    search_space: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    dimensions = search_space.get("dimensions") or {}
    children: list[tuple[str, dict[str, Any]]] = []
    for dimension in MUTABLE_DIMENSIONS:
        allowed = dimensions.get(dimension) or []
        current = parent.configuration[dimension]
        for value in allowed:
            if value == current:
                continue
            configuration = deepcopy(parent.configuration)
            configuration[dimension] = value
            children.append((f"one_factor:{dimension}={value}", configuration))
    return children


def residual_conditioned_neighbors(
    parent: PolicyState,
    residuals: list[dict[str, Any]],
    search_space: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    dimensions = search_space.get("dimensions") or {}
    proposals: list[tuple[str, dict[str, Any]]] = []
    temporal = sum(
        int(row.get("sequential_depth") or 0) > 0
        or str(row.get("first_divergence_cycle_bucket") or "") not in {"", "same_cycle", "0"}
        for row in residuals
    )
    partial = sum(int(row.get("repair_successes") or 0) > 0 for row in residuals)
    signatures = {
        str(row.get("failure_signature") or row.get("normalized_diff_hash") or "")
        for row in residuals
    } - {""}
    dataflow = sum(
        str(row.get("affected_role") or "") in {"datapath", "dataflow", "output"}
        for row in residuals
    )

    def add(reason: str, dimension: str, preferred: Any) -> None:
        allowed = dimensions.get(dimension) or []
        if preferred not in allowed or parent.configuration[dimension] == preferred:
            return
        config = deepcopy(parent.configuration)
        config[dimension] = preferred
        proposals.append((f"residual_conditioned:{reason}", config))

    if temporal:
        add("temporal_failures", "evidence_k", 6 if temporal >= max(1, len(residuals) // 2) else 3)
    if partial:
        add("near_correct_patches", "repair_loop", "critique-revise")
    if len(signatures) > 1:
        add("candidate_diversity", "n_candidates", 3)
    if dataflow:
        add("dataflow_evidence", "evidence_mode", "hybrid")
    return proposals


def configuration_key(configuration: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(configuration[key] for key in MUTABLE_DIMENSIONS)
