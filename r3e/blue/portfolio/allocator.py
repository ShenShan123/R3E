"""Round-frozen, adaptation-only offline allocator for ACP-4."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import argparse
import json
from itertools import combinations_with_replacement
from math import isqrt
from pathlib import Path
import re
from typing import Any, Mapping

from r3e.arena.manifests import verify_manifest
from r3e.memory.schema import FailureDescriptor
from r3e.protocol.hashing import atomic_write_json, hash_payload, read_json

from .schema import CandidatePortfolio, LensRegistry


DESCRIPTOR_CLUSTER_SCHEMA = "r3e-descriptor-cluster-v1"
ALLOCATOR_STATE_SCHEMA = "r3e-offline-allocator-state-v1"
ALLOCATOR_RECEIPT_SCHEMA = "r3e-offline-allocation-receipt-v1"
CLUSTER_FIELDS = (
    "sequential_context",
    "temporal_relation",
    "cycle_offset_bucket",
    "assignment_type",
    "cone_depth_bucket",
    "mismatch_pattern",
    "first_divergence_bucket",
    "affected_roles",
)
DEFAULT_PARAMETERS = {
    "beta_milli": 250,
    "unique_weight_milli": 500,
    "duplicate_penalty_milli": 300,
    "cost_penalty_milli": 10,
    "overlap_penalty_milli": 350,
    "minimum_support": 1,
    "candidate_budget": 3,
    "require_general_slot": True,
    "max_specialist_slots": 2,
}
FROZEN_LENS_ORDER = (
    "generic_v1",
    "temporal_v1",
    "control_v1",
    "dataflow_v1",
)
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class OfflineAllocatorViolation(RuntimeError):
    """Raised when adaptation statistics or allocator authority is invalid."""


def _digest(value: Any, field: str) -> str:
    text = str(value or "")
    if not _HASH_RE.fullmatch(text):
        raise OfflineAllocatorViolation(
            f"{field} must be an exact sha256 digest"
        )
    return text


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise OfflineAllocatorViolation(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise OfflineAllocatorViolation(
            f"{field} must be an integer"
        ) from exc
    if result < 0:
        raise OfflineAllocatorViolation(
            f"{field} must be non-negative"
        )
    return result


def build_descriptor_cluster(
    descriptor: FailureDescriptor | Mapping[str, Any],
) -> dict[str, Any]:
    failure = (
        descriptor
        if isinstance(descriptor, FailureDescriptor)
        else FailureDescriptor.from_dict(descriptor)
    )
    features = {}
    for field in CLUSTER_FIELDS:
        value = deepcopy(failure.features.get(field))
        if field == "affected_roles":
            if not isinstance(value, list):
                raise OfflineAllocatorViolation(
                    "descriptor affected_roles must be a list"
                )
            value = sorted(str(item) for item in value)
        features[field] = value
    payload = {
        "schema_version": DESCRIPTOR_CLUSTER_SCHEMA,
        "features": features,
    }
    payload["descriptor_cluster_hash"] = hash_payload(payload)
    return payload


def verify_descriptor_cluster(raw: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(raw))
    if set(payload) != {
        "schema_version",
        "features",
        "descriptor_cluster_hash",
    }:
        raise OfflineAllocatorViolation(
            "descriptor cluster fields mismatch"
        )
    if (
        payload["schema_version"] != DESCRIPTOR_CLUSTER_SCHEMA
        or not isinstance(payload["features"], Mapping)
        or set(payload["features"]) != set(CLUSTER_FIELDS)
    ):
        raise OfflineAllocatorViolation(
            "descriptor cluster schema mismatch"
        )
    if payload["descriptor_cluster_hash"] != hash_payload({
        "schema_version": DESCRIPTOR_CLUSTER_SCHEMA,
        "features": payload["features"],
    }):
        raise OfflineAllocatorViolation(
            "descriptor cluster hash mismatch"
        )
    return payload


def _empty_lens(lens_id: str) -> dict[str, Any]:
    return {
        "lens_id": lens_id,
        "attempts": 0,
        "compile_passes": 0,
        "oracle_passes": 0,
        "unique_solves": 0,
        "co_solves": 0,
        "semantic_duplicates": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "wall_millis": 0,
    }


def _empty_pair(left: str, right: str) -> dict[str, Any]:
    return {
        "left_lens_id": left,
        "right_lens_id": right,
        "observations": 0,
        "semantic_duplicates": 0,
        "near_duplicates": 0,
        "orthogonal": 0,
        "overlap_milli_sum": 0,
    }


def _finalize_lens(raw: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(raw))
    attempts = _non_negative_int(payload["attempts"], "attempts")
    for field in (
        "compile_passes",
        "oracle_passes",
        "unique_solves",
        "co_solves",
        "semantic_duplicates",
        "input_tokens",
        "output_tokens",
        "wall_millis",
    ):
        payload[field] = _non_negative_int(payload[field], field)
    for field in (
        "compile_passes",
        "oracle_passes",
        "unique_solves",
        "co_solves",
        "semantic_duplicates",
    ):
        if payload[field] > attempts:
            raise OfflineAllocatorViolation(
                f"{field} cannot exceed attempts"
            )
    payload.update({
        "oracle_rate_milli": (
            payload["oracle_passes"] * 1000 // attempts
            if attempts else 0
        ),
        "unique_rate_milli": (
            payload["unique_solves"] * 1000 // attempts
            if attempts else 0
        ),
        "duplicate_rate_milli": (
            payload["semantic_duplicates"] * 1000 // attempts
            if attempts else 0
        ),
        "mean_input_tokens_milli": (
            payload["input_tokens"] * 1000 // attempts
            if attempts else 0
        ),
        "mean_output_tokens_milli": (
            payload["output_tokens"] * 1000 // attempts
            if attempts else 0
        ),
        "mean_wall_millis": (
            payload["wall_millis"] // attempts if attempts else 0
        ),
    })
    return payload


def _finalize_pair(raw: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(raw))
    observations = _non_negative_int(
        payload["observations"], "observations"
    )
    for field in (
        "semantic_duplicates",
        "near_duplicates",
        "orthogonal",
        "overlap_milli_sum",
    ):
        payload[field] = _non_negative_int(payload[field], field)
    if (
        payload["semantic_duplicates"]
        + payload["near_duplicates"]
        + payload["orthogonal"]
        != observations
    ):
        raise OfflineAllocatorViolation(
            "pair relation counts differ from observations"
        )
    payload["mean_overlap_milli"] = (
        payload["overlap_milli_sum"] // observations
        if observations else 0
    )
    return payload


def _descriptor_from_challenge(row: Mapping[str, Any]) -> dict[str, Any]:
    authority = row.get("grounded_authority_bundle")
    descriptor = (
        authority.get("failure_descriptor")
        if isinstance(authority, Mapping)
        else None
    )
    if not isinstance(descriptor, Mapping):
        raise OfflineAllocatorViolation(
            "adaptation row lacks Grounded FailureDescriptor"
        )
    return deepcopy(dict(descriptor))


def _accumulate_result(
    result: Mapping[str, Any],
    lens_rows: dict[str, dict[str, Any]],
    pair_rows: dict[str, dict[str, Any]],
) -> None:
    generations = result.get("candidate_generation_receipts")
    signatures = result.get("candidate_semantic_signature_receipts")
    verifications = result.get("candidate_verification_receipts")
    diversity = result.get("portfolio_diversity_receipt")
    if not all(
        isinstance(value, list)
        for value in (generations, signatures, verifications)
    ) or not isinstance(diversity, Mapping):
        raise OfflineAllocatorViolation(
            "adaptation BlueEvaluation lacks ACP receipts"
        )
    if not (
        len(generations) == len(signatures) == len(verifications) == 3
    ):
        raise OfflineAllocatorViolation(
            "ACP-4 requires fixed three-candidate evidence"
        )
    generation_by_id = {
        row["candidate_id"]: row for row in generations
    }
    signature_by_id = {
        row["candidate_id"]: row for row in signatures
    }
    verification_by_id = {
        row["candidate_id"]: row for row in verifications
    }
    if not (
        set(generation_by_id)
        == set(signature_by_id)
        == set(verification_by_id)
    ):
        raise OfflineAllocatorViolation(
            "adaptation candidate receipt chain mismatch"
        )
    successful = {
        candidate_id for candidate_id, verification
        in verification_by_id.items()
        if verification.get("oracle_ok") is True
    }
    duplicate_candidates: set[str] = set()
    for relation in diversity.get("relations") or []:
        left_id = str(relation.get("left_candidate_id") or "")
        right_id = str(relation.get("right_candidate_id") or "")
        relation_name = str(relation.get("relation") or "")
        if (
            left_id not in signature_by_id
            or right_id not in signature_by_id
            or relation_name not in {
                "semantic_duplicate",
                "near_duplicate",
                "orthogonal",
            }
        ):
            raise OfflineAllocatorViolation(
                "adaptation semantic relation is invalid"
            )
        left_lens = signature_by_id[left_id]["lens_id"]
        right_lens = signature_by_id[right_id]["lens_id"]
        pair_key = "|".join(sorted((left_lens, right_lens)))
        if pair_key not in pair_rows:
            first, second = sorted((left_lens, right_lens))
            pair_rows[pair_key] = _empty_pair(first, second)
        pair = pair_rows[pair_key]
        pair["observations"] += 1
        pair[{
            "semantic_duplicate": "semantic_duplicates",
            "near_duplicate": "near_duplicates",
            "orthogonal": "orthogonal",
        }[relation_name]] += 1
        pair["overlap_milli_sum"] += int(
            relation["overlap_milli"]
        )
        if relation_name == "semantic_duplicate":
            duplicate_candidates.update((left_id, right_id))
    wall_total = int(
        float(
            (result.get("resource_usage") or {}).get(
                "wall_time_seconds", 0.0
            )
        )
        * 1000
    )
    wall_per_candidate = wall_total // len(generations)
    for candidate_id in sorted(generation_by_id):
        generation = generation_by_id[candidate_id]
        signature = signature_by_id[candidate_id]
        verification = verification_by_id[candidate_id]
        lens_id = str(signature["lens_id"])
        if lens_id not in lens_rows:
            raise OfflineAllocatorViolation(
                "adaptation result uses an unauthorized lens"
            )
        lens = lens_rows[lens_id]
        lens["attempts"] += 1
        lens["compile_passes"] += int(
            verification.get("compile_ok") is True
        )
        lens["oracle_passes"] += int(
            verification.get("oracle_ok") is True
        )
        lens["unique_solves"] += int(
            len(successful) == 1 and candidate_id in successful
        )
        lens["co_solves"] += int(
            len(successful) > 1 and candidate_id in successful
        )
        lens["semantic_duplicates"] += int(
            candidate_id in duplicate_candidates
        )
        lens["input_tokens"] += int(generation["input_tokens"])
        lens["output_tokens"] += int(generation["output_tokens"])
        lens["wall_millis"] += wall_per_candidate


@dataclass(frozen=True)
class OfflineAllocatorState:
    state_id: str
    source_round_id: str
    source_effective_policy_hash: str
    adaptation_manifest_hash: str
    lens_registry_hash: str
    lens_ids: tuple[str, ...]
    general_lens_id: str
    parameters: dict[str, Any]
    clusters: dict[str, dict[str, Any]]
    global_lens_statistics: dict[str, dict[str, Any]]
    global_pair_statistics: dict[str, dict[str, Any]]

    @classmethod
    def from_dict(
        cls, raw: Mapping[str, Any]
    ) -> "OfflineAllocatorState":
        payload = deepcopy(dict(raw))
        expected = {
            "schema_version",
            "state_id",
            "source_round_id",
            "source_effective_policy_hash",
            "adaptation_manifest_hash",
            "lens_registry_hash",
            "lens_ids",
            "general_lens_id",
            "parameters",
            "clusters",
            "global_lens_statistics",
            "global_pair_statistics",
            "state_hash",
        }
        if set(payload) != expected:
            raise OfflineAllocatorViolation(
                "offline allocator state fields mismatch"
            )
        if payload["schema_version"] != ALLOCATOR_STATE_SCHEMA:
            raise OfflineAllocatorViolation(
                "offline allocator state schema mismatch"
            )
        if payload["state_hash"] != hash_payload({
            key: value for key, value in payload.items()
            if key != "state_hash"
        }):
            raise OfflineAllocatorViolation(
                "offline allocator state hash mismatch"
            )
        for field in (
            "source_effective_policy_hash",
            "adaptation_manifest_hash",
            "lens_registry_hash",
        ):
            _digest(payload[field], field)
        lens_ids = payload["lens_ids"]
        if (
            not isinstance(lens_ids, list)
            or len(lens_ids) != 4
            or len(set(lens_ids)) != 4
            or payload["general_lens_id"] not in lens_ids
        ):
            raise OfflineAllocatorViolation(
                "allocator lens authority is invalid"
            )
        if payload["parameters"] != DEFAULT_PARAMETERS:
            raise OfflineAllocatorViolation(
                "allocator parameters differ from frozen V1"
            )
        global_lenses = payload["global_lens_statistics"]
        if (
            not isinstance(global_lenses, Mapping)
            or set(global_lenses) != set(lens_ids)
        ):
            raise OfflineAllocatorViolation(
                "global lens statistics are incomplete"
            )
        for lens_id, row in global_lenses.items():
            if row != _finalize_lens({
                key: value for key, value in row.items()
                if key not in {
                    "oracle_rate_milli",
                    "unique_rate_milli",
                    "duplicate_rate_milli",
                    "mean_input_tokens_milli",
                    "mean_output_tokens_milli",
                    "mean_wall_millis",
                }
            }) or row["lens_id"] != lens_id:
                raise OfflineAllocatorViolation(
                    "global lens statistics are inconsistent"
                )
        global_pairs = payload["global_pair_statistics"]
        if not isinstance(global_pairs, Mapping):
            raise OfflineAllocatorViolation(
                "global pair statistics must be an object"
            )
        for key, row in global_pairs.items():
            if (
                key != "|".join(sorted((
                    row["left_lens_id"], row["right_lens_id"]
                )))
                or row != _finalize_pair({
                    field: value for field, value in row.items()
                    if field != "mean_overlap_milli"
                })
            ):
                raise OfflineAllocatorViolation(
                    "global pair statistics are inconsistent"
                )
        clusters = payload["clusters"]
        if not isinstance(clusters, Mapping):
            raise OfflineAllocatorViolation(
                "allocator clusters must be an object"
            )
        for cluster_hash, cluster_row in clusters.items():
            if set(cluster_row) != {
                "descriptor_cluster",
                "lens_statistics",
                "pair_statistics",
            }:
                raise OfflineAllocatorViolation(
                    "allocator cluster fields mismatch"
                )
            cluster = verify_descriptor_cluster(
                cluster_row["descriptor_cluster"]
            )
            if cluster_hash != cluster["descriptor_cluster_hash"]:
                raise OfflineAllocatorViolation(
                    "allocator cluster key mismatch"
                )
            if set(cluster_row["lens_statistics"]) != set(lens_ids):
                raise OfflineAllocatorViolation(
                    "cluster lens statistics are incomplete"
                )
            for lens_id, row in cluster_row[
                "lens_statistics"
            ].items():
                rebuilt = _finalize_lens({
                    key: value for key, value in row.items()
                    if key not in {
                        "oracle_rate_milli",
                        "unique_rate_milli",
                        "duplicate_rate_milli",
                        "mean_input_tokens_milli",
                        "mean_output_tokens_milli",
                        "mean_wall_millis",
                    }
                })
                if row["lens_id"] != lens_id or row != rebuilt:
                    raise OfflineAllocatorViolation(
                        "cluster lens statistic key mismatch"
                    )
            for key, row in cluster_row[
                "pair_statistics"
            ].items():
                rebuilt_pair = _finalize_pair({
                    field: value for field, value in row.items()
                    if field != "mean_overlap_milli"
                })
                if key != "|".join(sorted((
                    row["left_lens_id"], row["right_lens_id"]
                ))) or row != rebuilt_pair:
                    raise OfflineAllocatorViolation(
                        "cluster pair statistic key mismatch"
                    )
        return cls(
            state_id=str(payload["state_id"]),
            source_round_id=str(payload["source_round_id"]),
            source_effective_policy_hash=payload[
                "source_effective_policy_hash"
            ],
            adaptation_manifest_hash=payload[
                "adaptation_manifest_hash"
            ],
            lens_registry_hash=payload["lens_registry_hash"],
            lens_ids=tuple(lens_ids),
            general_lens_id=str(payload["general_lens_id"]),
            parameters=deepcopy(payload["parameters"]),
            clusters=deepcopy(dict(clusters)),
            global_lens_statistics=deepcopy(dict(global_lenses)),
            global_pair_statistics=deepcopy(dict(global_pairs)),
        )

    @property
    def state_hash(self) -> str:
        return hash_payload(self._body())

    def _body(self) -> dict[str, Any]:
        return {
            "schema_version": ALLOCATOR_STATE_SCHEMA,
            "state_id": self.state_id,
            "source_round_id": self.source_round_id,
            "source_effective_policy_hash": (
                self.source_effective_policy_hash
            ),
            "adaptation_manifest_hash": self.adaptation_manifest_hash,
            "lens_registry_hash": self.lens_registry_hash,
            "lens_ids": list(self.lens_ids),
            "general_lens_id": self.general_lens_id,
            "parameters": deepcopy(self.parameters),
            "clusters": deepcopy(self.clusters),
            "global_lens_statistics": deepcopy(
                self.global_lens_statistics
            ),
            "global_pair_statistics": deepcopy(
                self.global_pair_statistics
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._body()
        payload["state_hash"] = self.state_hash
        return payload


def build_offline_allocator_state(
    adaptation_manifest: Mapping[str, Any],
    *,
    state_id: str,
    source_round_id: str,
    source_effective_policy_hash: str,
    registry: LensRegistry,
    general_lens_id: str = "generic_v1",
) -> OfflineAllocatorState:
    manifest = verify_manifest(deepcopy(dict(adaptation_manifest)))
    if manifest.get("split") != "adaptation":
        raise OfflineAllocatorViolation(
            "allocator may consume only adaptation split"
        )
    _digest(source_effective_policy_hash, "source_effective_policy_hash")
    lens_ids = FROZEN_LENS_ORDER
    if (
        set(lens_ids) != set(registry.lenses)
        or general_lens_id not in lens_ids
    ):
        raise OfflineAllocatorViolation(
            "ACP-4 requires the frozen four-lens registry"
        )
    global_lenses = {
        lens_id: _empty_lens(lens_id) for lens_id in lens_ids
    }
    global_pairs: dict[str, dict[str, Any]] = {}
    clusters: dict[str, dict[str, Any]] = {}
    for challenge in manifest["rows"]:
        if (
            challenge.get("challenged_policy_hash")
            != source_effective_policy_hash
            and not any(
                result.get("effective_policy_hash")
                == source_effective_policy_hash
                for result in challenge.get("blue_results") or []
            )
        ):
            raise OfflineAllocatorViolation(
                "adaptation row is bound to another source policy"
            )
        cluster = build_descriptor_cluster(
            _descriptor_from_challenge(challenge)
        )
        cluster_hash = cluster["descriptor_cluster_hash"]
        cluster_row = clusters.setdefault(cluster_hash, {
            "descriptor_cluster": cluster,
            "lens_statistics": {
                lens_id: _empty_lens(lens_id)
                for lens_id in lens_ids
            },
            "pair_statistics": {},
        })
        blue_results = challenge.get("blue_results")
        if not isinstance(blue_results, list) or not blue_results:
            raise OfflineAllocatorViolation(
                "adaptation row lacks BlueEvaluation results"
            )
        for result in blue_results:
            if (
                result.get("effective_policy_hash")
                != source_effective_policy_hash
            ):
                raise OfflineAllocatorViolation(
                    "adaptation BlueEvaluation policy mismatch"
                )
            _accumulate_result(
                result,
                cluster_row["lens_statistics"],
                cluster_row["pair_statistics"],
            )
            _accumulate_result(
                result,
                global_lenses,
                global_pairs,
            )
    finalized_clusters = {}
    for cluster_hash in sorted(clusters):
        row = clusters[cluster_hash]
        finalized_clusters[cluster_hash] = {
            "descriptor_cluster": row["descriptor_cluster"],
            "lens_statistics": {
                lens_id: _finalize_lens(
                    row["lens_statistics"][lens_id]
                )
                for lens_id in lens_ids
            },
            "pair_statistics": {
                key: _finalize_pair(row["pair_statistics"][key])
                for key in sorted(row["pair_statistics"])
            },
        }
    state = OfflineAllocatorState(
        state_id=str(state_id),
        source_round_id=str(source_round_id),
        source_effective_policy_hash=source_effective_policy_hash,
        adaptation_manifest_hash=manifest["manifest_hash"],
        lens_registry_hash=registry.registry_hash,
        lens_ids=lens_ids,
        general_lens_id=general_lens_id,
        parameters=deepcopy(DEFAULT_PARAMETERS),
        clusters=finalized_clusters,
        global_lens_statistics={
            lens_id: _finalize_lens(global_lenses[lens_id])
            for lens_id in lens_ids
        },
        global_pair_statistics={
            key: _finalize_pair(global_pairs[key])
            for key in sorted(global_pairs)
        },
    )
    return OfflineAllocatorState.from_dict(state.to_dict())


def build_adaptive_portfolio(
    parent: CandidatePortfolio,
    state: OfflineAllocatorState,
) -> CandidatePortfolio:
    if (
        parent.mode not in {"descriptor_routed", "adaptive"}
        or parent.candidate_budget != 3
        or parent.lens_registry_hash != state.lens_registry_hash
        or set(parent.lens_ids) != set(state.lens_ids)
    ):
        raise OfflineAllocatorViolation(
            "allocator child requires compatible ACP-3/ACP-4 parent"
        )
    return CandidatePortfolio.create(
        portfolio_id=f"adaptive-{state.state_id}",
        mode="adaptive",
        candidate_budget=3,
        lens_registry_hash=parent.lens_registry_hash,
        router_hash=parent.router_hash,
        allocator_hash=state.state_hash,
        selector_hash=parent.selector_hash,
        semantic_signature_provider_hash=(
            parent.semantic_signature_provider_hash
        ),
        duplicate_policy="measure_only",
        early_stop_mode="all_candidates",
        lens_ids=state.lens_ids,
    )


class OfflineAdaptiveAllocator:
    """Choose a frozen three-slot portfolio from prior adaptation evidence."""

    def __init__(self, state: OfflineAllocatorState):
        self.state = state
        self.allocator_hash = state.state_hash

    def _utility(
        self,
        stats: Mapping[str, Any],
        total_attempts: int,
    ) -> dict[str, int]:
        parameters = self.state.parameters
        attempts = int(stats["attempts"])
        exploration_scaled = isqrt(
            (1 + total_attempts) * 1_000_000
            // (1 + attempts)
        )
        exploration = (
            parameters["beta_milli"] * exploration_scaled // 1000
        )
        cost_tokens_milli = (
            int(stats["mean_input_tokens_milli"])
            + int(stats["mean_output_tokens_milli"])
        )
        components = {
            "oracle_rate_milli": int(stats["oracle_rate_milli"]),
            "exploration_bonus_milli": exploration,
            "unique_bonus_milli": (
                parameters["unique_weight_milli"]
                * int(stats["unique_rate_milli"])
                // 1000
            ),
            "duplicate_penalty_milli": (
                parameters["duplicate_penalty_milli"]
                * int(stats["duplicate_rate_milli"])
                // 1000
            ),
            "cost_penalty_milli": (
                parameters["cost_penalty_milli"]
                * cost_tokens_milli
                // 1_000_000
            ),
        }
        components["utility_milli"] = (
            components["oracle_rate_milli"]
            + components["exploration_bonus_milli"]
            + components["unique_bonus_milli"]
            - components["duplicate_penalty_milli"]
            - components["cost_penalty_milli"]
        )
        return components

    def allocate(
        self,
        descriptor: FailureDescriptor | Mapping[str, Any],
        *,
        portfolio: CandidatePortfolio,
        registry: LensRegistry,
    ) -> dict[str, Any]:
        if (
            portfolio.mode != "adaptive"
            or portfolio.candidate_budget != 3
            or portfolio.allocator_hash != self.state.state_hash
            or portfolio.lens_registry_hash
            != self.state.lens_registry_hash
            or registry.registry_hash != self.state.lens_registry_hash
            or tuple(portfolio.lens_ids) != self.state.lens_ids
        ):
            raise OfflineAllocatorViolation(
                "adaptive portfolio/allocator authority mismatch"
            )
        cluster = build_descriptor_cluster(descriptor)
        cluster_row = self.state.clusters.get(
            cluster["descriptor_cluster_hash"]
        )
        cluster_support = (
            sum(
                int(row["attempts"])
                for row in cluster_row["lens_statistics"].values()
            )
            if cluster_row is not None else 0
        )
        fallback = (
            cluster_row is None
            or cluster_support
            < self.state.parameters["minimum_support"]
        )
        lens_stats = (
            self.state.global_lens_statistics
            if fallback else cluster_row["lens_statistics"]
        )
        pair_stats = (
            self.state.global_pair_statistics
            if fallback else cluster_row["pair_statistics"]
        )
        total_attempts = sum(
            int(row["attempts"]) for row in lens_stats.values()
        )
        utilities = {
            lens_id: self._utility(
                lens_stats[lens_id], total_attempts
            )
            for lens_id in self.state.lens_ids
        }
        order = {
            lens_id: index
            for index, lens_id in enumerate(self.state.lens_ids)
        }
        combinations = []
        for indices in combinations_with_replacement(
            range(len(self.state.lens_ids)), 3
        ):
            lens_ids = tuple(
                self.state.lens_ids[index] for index in indices
            )
            if (
                self.state.general_lens_id not in lens_ids
                or any(
                    lens_ids.count(lens_id)
                    > self.state.parameters["max_specialist_slots"]
                    for lens_id in self.state.lens_ids
                    if lens_id != self.state.general_lens_id
                )
            ):
                continue
            overlap_sum = 0
            for left_index in range(3):
                for right_index in range(left_index + 1, 3):
                    left = lens_ids[left_index]
                    right = lens_ids[right_index]
                    key = "|".join(sorted((left, right)))
                    overlap_sum += int(
                        pair_stats.get(
                            key,
                            {"mean_overlap_milli": (
                                1000 if left == right else 0
                            )},
                        )["mean_overlap_milli"]
                    )
            lens_utility = sum(
                utilities[lens_id]["utility_milli"]
                for lens_id in lens_ids
            )
            overlap_penalty = (
                self.state.parameters["overlap_penalty_milli"]
                * overlap_sum
                // 1000
            )
            combinations.append({
                "lens_ids": list(lens_ids),
                "lens_utility_milli": lens_utility,
                "overlap_milli_sum": overlap_sum,
                "overlap_penalty_milli": overlap_penalty,
                "joint_utility_milli": (
                    lens_utility - overlap_penalty
                ),
            })
        combinations.sort(
            key=lambda row: tuple(order[lens] for lens in row["lens_ids"])
        )
        selected = min(
            combinations,
            key=lambda row: (
                -row["joint_utility_milli"],
                tuple(order[lens] for lens in row["lens_ids"]),
            ),
        )
        payload = {
            "schema_version": ALLOCATOR_RECEIPT_SCHEMA,
            "allocator_state_hash": self.state.state_hash,
            "descriptor_hash": (
                descriptor.descriptor_hash
                if isinstance(descriptor, FailureDescriptor)
                else FailureDescriptor.from_dict(
                    descriptor
                ).descriptor_hash
            ),
            "descriptor_cluster": cluster,
            "used_global_fallback": fallback,
            "adaptation_manifest_hash": (
                self.state.adaptation_manifest_hash
            ),
            "lens_utilities": utilities,
            "combination_utilities": combinations,
            "allocated_lens_ids": selected["lens_ids"],
        }
        payload["receipt_hash"] = hash_payload(payload)
        return payload

    def verify_receipt(
        self,
        raw: Mapping[str, Any],
        descriptor: FailureDescriptor | Mapping[str, Any],
        *,
        portfolio: CandidatePortfolio,
        registry: LensRegistry,
    ) -> dict[str, Any]:
        payload = deepcopy(dict(raw))
        expected = self.allocate(
            descriptor,
            portfolio=portfolio,
            registry=registry,
        )
        if payload != expected:
            raise OfflineAllocatorViolation(
                "offline allocation receipt is not reconstructable"
            )
        return payload


def load_offline_allocator_state(
    path: str | Path,
) -> OfflineAllocatorState:
    return OfflineAllocatorState.from_dict(read_json(path))


def create_offline_allocator(
    config: Mapping[str, Any],
) -> OfflineAdaptiveAllocator:
    root = Path(str(config.get("project_root") or ".")).resolve()
    path = root / str(config.get("blue_offline_allocator_state") or "")
    if not path.is_file():
        raise OfflineAllocatorViolation(
            "blue_offline_allocator_state is required"
        )
    return OfflineAdaptiveAllocator(
        load_offline_allocator_state(path)
    )


def _main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a frozen ACP-4 allocator state from adaptation-only "
            "BlueEvaluation receipts"
        )
    )
    parser.add_argument("--adaptation-manifest", required=True)
    parser.add_argument("--lens-registry", required=True)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--state-id", required=True)
    parser.add_argument("--source-round-id", required=True)
    parser.add_argument(
        "--source-effective-policy-hash", required=True
    )
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    from .lens_registry import load_lens_registry

    state = build_offline_allocator_state(
        read_json(args.adaptation_manifest),
        state_id=args.state_id,
        source_round_id=args.source_round_id,
        source_effective_policy_hash=(
            args.source_effective_policy_hash
        ),
        registry=load_lens_registry(
            args.lens_registry, project_root=root
        ),
    )
    target = Path(args.out)
    if target.exists():
        existing = load_offline_allocator_state(target)
        if existing.state_hash != state.state_hash:
            raise OfflineAllocatorViolation(
                "refusing to overwrite a different frozen allocator state"
            )
    else:
        atomic_write_json(target, state.to_dict())
    print(json.dumps({
        "schema_version": ALLOCATOR_STATE_SCHEMA,
        "state_hash": state.state_hash,
        "adaptation_manifest_hash": state.adaptation_manifest_hash,
        "cluster_count": len(state.clusters),
        "out": str(target),
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    _main()
