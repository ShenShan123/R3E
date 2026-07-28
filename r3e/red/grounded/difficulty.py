"""Artifact-derived Grounded Red difficulty profiles."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from r3e.protocol.hashing import hash_payload
from .proofs import (
    verify_runtime_effect_receipt,
    verify_semantic_diff_receipt,
)


DIFFICULTY_SCHEMA_VERSION = "r3e-red-difficulty-profile-v1"
DIFFICULTY_BANDS = {"D0", "D1", "D2", "D3", "D4"}
REPAIR_LOCALITIES = {"expression", "local_block", "cross_block", "cross_module"}


class DifficultyProfileViolation(RuntimeError):
    """Raised when difficulty is asserted without grounded measurements."""


def build_difficulty_profile(
    *,
    current_blue_failure_rate: float,
    semantic_diff_receipt: Mapping[str, Any],
    runtime_effect_receipt: Mapping[str, Any],
    activation_rarity: float,
    repair_locality: str,
    candidate_ambiguity: int,
    composition_depth: int,
) -> dict[str, Any]:
    semantic = dict(semantic_diff_receipt)
    effect = dict(runtime_effect_receipt)
    temporal_depth = int(effect.get("temporal_depth") or 0)
    dependency_depth = int(semantic.get("dependency_depth") or 0)
    changed_modules = int(semantic.get("changed_module_count") or 0)
    changed_blocks = int(semantic.get("changed_block_count") or 0)
    if composition_depth == 2:
        band = "D4"
    elif changed_modules > 1 or activation_rarity < 0.1:
        band = "D3"
    elif dependency_depth >= 2 or temporal_depth >= 2 or changed_blocks > 1:
        band = "D2"
    elif temporal_depth >= 1:
        band = "D1"
    else:
        band = "D0"
    payload = {
        "schema_version": DIFFICULTY_SCHEMA_VERSION,
        "current_blue_failure_rate": current_blue_failure_rate,
        "temporal_depth": temporal_depth,
        "dependency_depth": dependency_depth,
        "changed_module_count": changed_modules,
        "changed_block_count": changed_blocks,
        "activation_rarity": activation_rarity,
        "composition_depth": composition_depth,
        "repair_locality": repair_locality,
        "candidate_ambiguity": candidate_ambiguity,
        "difficulty_band": band,
        "semantic_diff_receipt_hash": semantic.get("receipt_hash"),
        "runtime_effect_receipt_hash": effect.get("receipt_hash"),
    }
    payload["profile_hash"] = hash_payload(payload)
    return verify_difficulty_profile(payload)


def verify_difficulty_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(profile))
    required = {
        "schema_version",
        "current_blue_failure_rate",
        "temporal_depth",
        "dependency_depth",
        "changed_module_count",
        "changed_block_count",
        "activation_rarity",
        "composition_depth",
        "repair_locality",
        "candidate_ambiguity",
        "difficulty_band",
        "semantic_diff_receipt_hash",
        "runtime_effect_receipt_hash",
        "profile_hash",
    }
    if set(payload) != required:
        raise DifficultyProfileViolation("difficulty profile fields mismatch")
    if payload["schema_version"] != DIFFICULTY_SCHEMA_VERSION:
        raise DifficultyProfileViolation("difficulty profile schema mismatch")
    if payload["profile_hash"] != hash_payload({
        key: value for key, value in payload.items() if key != "profile_hash"
    }):
        raise DifficultyProfileViolation("difficulty profile hash mismatch")
    for field in ("current_blue_failure_rate", "activation_rarity"):
        value = payload[field]
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not 0.0 <= float(value) <= 1.0
        ):
            raise DifficultyProfileViolation(f"{field} must be in [0, 1]")
    for field in (
        "temporal_depth",
        "dependency_depth",
        "changed_module_count",
        "changed_block_count",
        "candidate_ambiguity",
    ):
        value = payload[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise DifficultyProfileViolation(f"{field} must be non-negative")
    if payload["composition_depth"] not in {1, 2}:
        raise DifficultyProfileViolation("composition depth must be one or two")
    if payload["difficulty_band"] not in DIFFICULTY_BANDS:
        raise DifficultyProfileViolation("difficulty band is invalid")
    if payload["repair_locality"] not in REPAIR_LOCALITIES:
        raise DifficultyProfileViolation("repair locality is invalid")
    for field in (
        "semantic_diff_receipt_hash",
        "runtime_effect_receipt_hash",
    ):
        value = str(payload[field] or "")
        if not value.startswith("sha256:") or len(value) != 71:
            raise DifficultyProfileViolation(f"{field} is invalid")
    if payload["difficulty_band"] == "D4" and payload["composition_depth"] != 2:
        raise DifficultyProfileViolation("D4 requires controlled composition")
    return payload


def difficulty_profile_from_challenge(
    challenge: Mapping[str, Any],
    *,
    candidate_ambiguity: int,
) -> dict[str, Any]:
    authority = dict(
        challenge.get("grounded_authority_bundle") or {}
    )
    execution = dict(authority.get("execution_bundle") or {})
    evidence = dict(execution.get("evidence") or {})
    semantic = verify_semantic_diff_receipt(
        evidence.get("semantic_diff") or {}
    )
    effect = verify_runtime_effect_receipt(
        evidence.get("runtime_effect") or {}
    )
    attempts = int(challenge.get("repair_attempts") or 0)
    successes = int(challenge.get("repair_successes") or 0)
    if attempts < 1 or not 0 <= successes <= attempts:
        raise DifficultyProfileViolation(
            "challenge repair counts are invalid"
        )
    features = dict(effect.get("observable_features") or {})
    first_cycle = features.get("first_divergence_cycle")
    if (
        not isinstance(first_cycle, int)
        or isinstance(first_cycle, bool)
        or first_cycle < 0
    ):
        first_cycle = 0
    activation_rarity = 1.0 / float(first_cycle + 1)
    if semantic["changed_module_count"] > 1:
        locality = "cross_module"
    elif semantic["changed_block_count"] > 1:
        locality = "cross_block"
    elif semantic["ast_edit_count"] == 1:
        locality = "expression"
    else:
        locality = "local_block"
    return build_difficulty_profile(
        current_blue_failure_rate=(
            float(attempts - successes) / float(attempts)
        ),
        semantic_diff_receipt=semantic,
        runtime_effect_receipt=effect,
        activation_rarity=activation_rarity,
        repair_locality=locality,
        candidate_ambiguity=candidate_ambiguity,
        composition_depth=int(
            challenge.get("composition_depth") or 1
        ),
    )
