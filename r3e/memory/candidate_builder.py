"""Deterministic ControlMemory construction from verified episode clusters."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload

from .schema import ControlMemory, VerifiedEpisode
from .evidence import definition_hash_for_parts


def _trigger(episode: VerifiedEpisode) -> dict:
    descriptor = episode.failure_descriptor
    return {
        key: descriptor[key]
        for key in (
            "oracle_stage",
            "sequential_context",
            "temporal_relation",
            "cycle_offset_bucket",
            "affected_roles",
            "assignment_type",
            "cone_depth_bucket",
            "mismatch_pattern",
            "first_divergence_bucket",
            "first_divergence_signal",
        )
        if key in descriptor
    }


def _control_delta(trigger: dict) -> dict:
    if trigger.get("sequential_context"):
        return {
            "enable_analyzers": [
                "temporal_alignment",
                "state_transition_slice",
            ],
            "rtl_slice_mode": "sequential_cone",
            "evidence_window_before": 2,
            "evidence_window_after": 4,
            "initial_candidates": 1,
            "revision_rounds": 2,
            "candidate_ranking": "verifier_guided",
            "early_stop": "first_verified",
        }
    return {
        "enable_analyzers": ["first_divergence", "combinational_cone"],
        "rtl_slice_mode": "combinational_cone",
        "first_divergence_only": True,
        "initial_candidates": 2,
        "revision_rounds": 1,
        "candidate_ranking": "verifier_guided",
        "early_stop": "first_verified",
    }


def build_memory_candidates(
    episodes: Iterable[VerifiedEpisode],
    *,
    policy: PolicyState,
    origin_round_id: str,
    minimum_support: int = 1,
) -> list[ControlMemory]:
    """Cluster unresolved observable failures and emit non-language deltas."""
    if minimum_support < 1:
        raise ValueError("minimum_support must be positive")
    groups: dict[str, list[VerifiedEpisode]] = defaultdict(list)
    triggers = {}
    for episode in episodes:
        if episode.final_outcome != "unresolved":
            continue
        if episode.challenged_effective_policy_hash != policy.effective_policy_hash:
            continue
        trigger = _trigger(episode)
        if not trigger:
            continue
        key = hash_payload(trigger)
        triggers[key] = trigger
        groups[key].append(episode)
    candidates = []
    for trigger_hash, rows in sorted(groups.items()):
        if len(rows) < minimum_support:
            continue
        rows.sort(key=lambda item: (item.round_id, item.episode_id))
        source_hashes = {
            episode.episode_id: episode.episode_hash for episode in rows
        }
        identity = definition_hash_for_parts(
            triggers[trigger_hash],
            _control_delta(triggers[trigger_hash]),
        ).split(":", 1)[1][:16]
        candidates.append(ControlMemory.create(
            memory_id=f"CM_{identity}",
            memory_version=1,
            origin_round_id=origin_round_id,
            source_episode_ids=list(source_hashes),
            source_episode_hashes=source_hashes,
            created_under_policy_instance_hash=policy.policy_instance_hash,
            created_under_effective_policy_hash=policy.effective_policy_hash,
            trigger_predicate=triggers[trigger_hash],
            control_delta=_control_delta(triggers[trigger_hash]),
            status="candidate",
            qualification_summary={},
            compatibility={},
        ))
    return candidates
