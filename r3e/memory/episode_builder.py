"""Runner-owned conversion of challenge artifacts into VerifiedEpisode facts."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_file, hash_payload
from r3e.grounded.failure_descriptor import (
    verify_grounded_failure_descriptor,
)
from .descriptor import build_failure_descriptor
from .episode_store import EpisodeStore
from .schema import VerifiedEpisode
from r3e.red.poison_payload import verify_poison_payload
from r3e.red.grounded.proofs import (
    verify_runtime_effect_receipt,
    verify_semantic_diff_receipt,
)


class EpisodeBuildViolation(RuntimeError):
    """Raised when a challenge lacks enough evidence for immutable storage."""


def episode_from_challenge(
    challenge: dict[str, Any],
    *,
    policy: PolicyState,
    round_id: str,
) -> VerifiedEpisode:
    if challenge.get("challenged_policy_hash") != policy.policy_hash:
        raise EpisodeBuildViolation("challenge is not bound to episode policy")
    poison_payload_hash = verify_poison_payload(challenge)
    poison_id = str(challenge.get("poison_id") or "")
    if not poison_id:
        raise EpisodeBuildViolation("challenge poison_id is missing")
    buggy_hash = str(challenge.get("buggy_rtl_hash") or "")
    if not buggy_hash:
        buggy_path = Path(str(challenge.get("buggy_rtl") or ""))
        if not buggy_path.is_file():
            raise EpisodeBuildViolation("buggy RTL is unavailable for hashing")
        buggy_hash = hash_file(buggy_path)
    blue_results = challenge.get("blue_results")
    if not isinstance(blue_results, list) or not blue_results:
        raise EpisodeBuildViolation("challenge blue attempts are missing")
    validity_evidence = (
        (challenge.get("validity") or {}).get("evidence") or {}
    )
    authority = challenge.get("grounded_authority_bundle")
    if validity_evidence.get("authority_bundle_hash") and not isinstance(
        authority, dict
    ):
        raise EpisodeBuildViolation(
            "Grounded Runtime challenge lacks its authority bundle"
        )
    if authority is not None and not isinstance(authority, dict):
        raise EpisodeBuildViolation(
            "Grounded authority bundle must be an object"
        )
    grounded_authority_hash = ""
    if isinstance(authority, dict):
        if authority.get("authority_hash") != hash_payload({
            key: value for key, value in authority.items()
            if key != "authority_hash"
        }):
            raise EpisodeBuildViolation(
                "Grounded authority hash mismatch"
            )
        try:
            descriptor_payload, _descriptor_receipt = (
                verify_grounded_failure_descriptor(
                    authority.get("failure_descriptor") or {},
                    authority.get("failure_descriptor_receipt") or {},
                    execution_bundle=(
                        authority.get("execution_bundle") or {}
                    ),
                    formal_proof_triplet=(
                        authority.get("formal_proof_triplet") or {}
                    ),
                )
            )
        except Exception as exc:
            raise EpisodeBuildViolation(str(exc)) from exc
        if (
            validity_evidence.get("authority_bundle_hash")
            != authority["authority_hash"]
            or validity_evidence.get("failure_descriptor_hash")
            != descriptor_payload["descriptor_hash"]
            or validity_evidence.get(
                "failure_descriptor_receipt_hash"
            )
            != authority["failure_descriptor_receipt"]["receipt_hash"]
        ):
            raise EpisodeBuildViolation(
                "Grounded descriptor is not bound to challenge validity"
            )
        descriptor = build_failure_descriptor({
            key: value
            for key, value in descriptor_payload.items()
            if key not in {"schema_version", "descriptor_hash"}
        })
        grounded_authority_hash = str(authority["authority_hash"])
    else:
        descriptor = build_failure_descriptor({
            "oracle_stage": "functional_compare",
            "sequential_context": (
                int(challenge.get("sequential_depth") or 0) > 0
            ),
            "affected_roles": [
                str(challenge.get("affected_role") or "unknown")
            ],
            "mismatch_pattern": str(
                challenge.get("effect") or "unknown"
            ),
            "first_divergence_bucket": str(
                challenge.get(
                    "first_divergence_cycle_bucket"
                )
                or "unknown"
            ),
            "observable_artifact_hashes": {
                "challenge": str(
                    challenge.get("challenge_result_hash") or ""
                ),
                "validity": str(
                    (challenge.get("validity") or {}).get(
                        "result_hash"
                    )
                    or ""
                ),
            },
        })
    successes = [row for row in blue_results if row.get("oracle_ok")]
    successful_hashes = [
        str(row.get("successful_patch_hash") or "")
        for row in successes if row.get("successful_patch_hash")
    ]
    if successful_hashes:
        if len(set(successful_hashes)) != 1:
            raise EpisodeBuildViolation("successful attempts disagree on patch hash")
        outcome = "resolved"
        successful_patch_hash = successful_hashes[0]
    elif successes:
        outcome = "inconclusive"
        successful_patch_hash = ""
    else:
        outcome = "unresolved"
        successful_patch_hash = ""
    resource_usage: dict[str, int | float] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "llm_calls": 0,
        "verifier_calls": 0,
        "wall_time_seconds": 0.0,
    }
    for result in blue_results:
        usage = result.get("resource_usage") or {}
        for field in resource_usage:
            value = usage.get(field, 0)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise EpisodeBuildViolation("blue resource usage is invalid")
            resource_usage[field] += value
    oracle_evidence_hash = hash_payload({
        "validity_result_hash": (challenge.get("validity") or {}).get("result_hash"),
        "grounded_authority_hash": grounded_authority_hash,
        "blue_result_hashes": [hash_payload(row) for row in blue_results],
    })
    identity = hash_payload({
        "round_id": round_id,
        "poison_id": poison_id,
        "policy_hash": policy.policy_hash,
        "challenge_result_hash": challenge["challenge_result_hash"],
    }).split(":", 1)[1][:16]
    return VerifiedEpisode.create(
        episode_id=f"E_{round_id}_{identity}",
        round_id=round_id,
        challenged_policy_instance_hash=policy.policy_instance_hash,
        challenged_effective_policy_hash=policy.effective_policy_hash,
        poison_id=poison_id,
        poison_payload_hash=poison_payload_hash,
        buggy_rtl_hash=buggy_hash,
        oracle_evidence_hash=oracle_evidence_hash,
        failure_descriptor=descriptor.to_dict(),
        blue_attempts=blue_results,
        final_outcome=outcome,
        successful_patch_hash=successful_patch_hash,
        activated_memory_ids=sorted({
            memory_id
            for result in blue_results
            for memory_id in result.get("activated_memory_ids", [])
        }),
        resource_usage=resource_usage,
    )


def append_round_episodes(
    challenges: Iterable[dict[str, Any]],
    *,
    policy: PolicyState,
    round_id: str,
    store: EpisodeStore,
    formal_rejections: Iterable[dict[str, Any]] = (),
) -> list[VerifiedEpisode]:
    episodes = [
        episode_from_challenge(row, policy=policy, round_id=round_id)
        for row in challenges
    ]
    episodes.extend(
        episode_from_formal_rejection(
            row,
            policy=policy,
            round_id=round_id,
        )
        for row in formal_rejections
    )
    for episode in episodes:
        store.append_episode(episode)
    return episodes


def episode_from_formal_rejection(
    rejection: dict[str, Any],
    *,
    policy: PolicyState,
    round_id: str,
) -> VerifiedEpisode:
    """Persist a Grounded candidate rejected before blue challenge."""
    if (
        rejection.get("round_id") != round_id
        or (rejection.get("policy_state") or {}).get("policy_hash")
        != policy.policy_hash
        or not rejection.get("rejection_reasons")
    ):
        raise EpisodeBuildViolation(
            "formal rejection is not bound to episode round/policy"
        )
    poison = dict(rejection.get("poison_payload") or {})
    poison_payload_hash = verify_poison_payload(poison)
    execution = dict(rejection.get("execution_bundle") or {})
    assessment = dict(
        rejection.get("formal_proof_assessment") or {}
    )
    if (
        rejection.get("rejection_hash") != hash_payload({
            key: value for key, value in rejection.items()
            if key != "rejection_hash"
        })
        or execution.get("bundle_hash") != hash_payload({
            key: value for key, value in execution.items()
            if key != "bundle_hash"
        })
        or assessment.get("assessment_hash") != hash_payload({
            key: value for key, value in assessment.items()
            if key != "assessment_hash"
        })
    ):
        raise EpisodeBuildViolation(
            "formal rejection authority hash mismatch"
        )
    evidence = dict(execution.get("evidence") or {})
    runtime = verify_runtime_effect_receipt(
        evidence.get("runtime_effect") or {}
    )
    features = dict(runtime.get("observable_features") or {})
    required = {
        "first_divergence_signal",
        "first_divergence_cycle",
        "cycle_offset",
        "temporal_relation",
        "assignment_type",
        "cone_depth",
        "mismatch_pattern",
    }
    if required - features.keys():
        raise EpisodeBuildViolation(
            "formal rejection lacks waveform descriptor evidence"
        )
    cycle = int(features["first_divergence_cycle"])
    depth = int(features["cone_depth"])
    if cycle == 0:
        cycle_bucket = "cycle_0"
    elif cycle == 1:
        cycle_bucket = "cycle_1"
    elif cycle <= 3:
        cycle_bucket = "cycle_2_3"
    elif cycle <= 7:
        cycle_bucket = "cycle_4_7"
    else:
        cycle_bucket = "cycle_8_plus"
    if depth == 0:
        depth_bucket = "depth_0"
    elif depth == 1:
        depth_bucket = "depth_1"
    elif depth <= 3:
        depth_bucket = "depth_2_3"
    else:
        depth_bucket = "depth_4_plus"
    sequential = (
        str(features["assignment_type"])
        not in {"combinational", "continuous"}
        or str(features["temporal_relation"])
        not in {"same_cycle", "combinational"}
        or int(features["cycle_offset"]) > 0
    )
    descriptor = build_failure_descriptor({
        "oracle_stage": "grounded_formal_rejected",
        "sequential_context": sequential,
        "temporal_relation": str(
            features["temporal_relation"]
        ),
        "cycle_offset_bucket": int(features["cycle_offset"]),
        "affected_roles": ["formal_rejected_output"],
        "assignment_type": str(features["assignment_type"]),
        "cone_depth_bucket": depth_bucket,
        "mismatch_pattern": str(features["mismatch_pattern"]),
        "first_divergence_bucket": cycle_bucket,
        "first_divergence_signal": str(
            features["first_divergence_signal"]
        ),
        "observable_artifact_hashes": {
            "grounded_execution_bundle": execution["bundle_hash"],
            "formal_proof_assessment": assessment[
                "assessment_hash"
            ],
            "formal_rejection": rejection["rejection_hash"],
            "runtime_effect": runtime["receipt_hash"],
        },
    })
    materialization = dict(
        execution.get("materialization_receipt") or {}
    )
    oracle_evidence_hash = hash_payload({
        "execution_bundle_hash": execution["bundle_hash"],
        "formal_assessment_hash": assessment["assessment_hash"],
        "formal_rejection_hash": rejection["rejection_hash"],
        "rejection_reasons": rejection["rejection_reasons"],
    })
    identity = rejection["rejection_hash"].split(":", 1)[1][:16]
    return VerifiedEpisode.create(
        episode_id=f"E_{round_id}_REJECTED_{identity}",
        round_id=round_id,
        challenged_policy_instance_hash=policy.policy_instance_hash,
        challenged_effective_policy_hash=(
            policy.effective_policy_hash
        ),
        poison_id=str(poison["poison_id"]),
        poison_payload_hash=poison_payload_hash,
        buggy_rtl_hash=str(materialization["poison_rtl_hash"]),
        oracle_evidence_hash=oracle_evidence_hash,
        failure_descriptor=descriptor.to_dict(),
        blue_attempts=[],
        final_outcome="inconclusive",
        successful_patch_hash="",
        activated_memory_ids=[],
        resource_usage={
            "input_tokens": 0,
            "output_tokens": 0,
            "llm_calls": 0,
            "verifier_calls": 0,
            "wall_time_seconds": 0.0,
        },
    )
