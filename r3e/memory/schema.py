"""Strict schemas for Replay-Activated Adversarial Memory.

The executable payload of a memory is a small, validated control delta.  Free
text, historical patches, red mutation truth and authority-changing fields are
rejected at the schema boundary.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Mapping

from r3e.protocol.hashing import hash_payload


EPISODE_SCHEMA_VERSION = "r3e-verified-episode-v1"
MEMORY_SCHEMA_VERSION = "r3e-control-memory-v1"
DESCRIPTOR_SCHEMA_VERSION = "r3e-failure-descriptor-v1"
BANK_SCHEMA_VERSION = "r3e-active-memory-bank-v1"
LIFECYCLE_SCHEMA_VERSION = "r3e-memory-lifecycle-event-v1"
PLAN_SCHEMA_VERSION = "r3e-memory-execution-plan-v1"
SHADOW_SCHEMA_VERSION = "r3e-memory-shadow-paired-v1"
BANK_PLAN_COMPILER_VERSION = "r3e-memory-plan-compiler-v1"
BANK_CONFLICT_POLICY_VERSION = "r3e-memory-conflict-policy-v1"
BANK_MAXIMUM_ACTIVATION = 3
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

MEMORY_STATUSES = {
    "candidate",
    "shadow_testing",
    "replay_qualified",
    "bank_candidate",
    "active_dormant",
    "revalidation_required",
    "stale",
    "harmful",
    "superseded",
    "retired",
}
EXECUTABLE_MEMORY_STATUSES = {"active_dormant"}
FINAL_OUTCOMES = {"resolved", "unresolved", "inconclusive"}
SHADOW_OUTCOMES = {"helped", "harmed", "neutral_pass", "neutral_fail"}

TRIGGER_FIELDS = {
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
}
DESCRIPTOR_FIELDS = TRIGGER_FIELDS | {"observable_artifact_hashes"}
FORBIDDEN_FEATURE_FIELDS = {
    "reference_patch",
    "reference_patch_hash",
    "golden_patch",
    "mutation_family",
    "mutation_operator",
    "red_truth",
    "red_label",
    "poison_family",
}
CONTROL_DELTA_FIELDS = {
    "enable_analyzers",
    "disable_analyzers",
    "evidence_window_before",
    "evidence_window_after",
    "max_signals",
    "rtl_slice_mode",
    "cone_depth",
    "first_divergence_only",
    "initial_candidates",
    "revision_rounds",
    "candidate_batch_size",
    "early_stop",
    "retry_after_compile_fail",
    "retry_after_oracle_fail",
    "candidate_ranking",
    "pre_oracle_filters",
    "verifier_order",
    "max_changed_blocks",
    "prefer_local_patch",
    "candidate_plan",
    "candidate_portfolio_template_id",
    "specialist_slot_budget",
    "diversity_retry_budget",
    "portfolio_early_stop",
}
FORBIDDEN_CONTROL_FIELDS = {
    "oracle",
    "oracle_path",
    "testbench",
    "golden_rtl",
    "promotion_threshold",
    "registry",
    "registry_path",
    "model_credentials",
    "secret",
    "prompt",
    "prompt_fragment",
    "history_patch",
    "patch",
    "model_route_id",
    "ast_rewrite",
    "lens_prompt",
    "lens_prompt_hash",
    "lens_definition",
    "candidate_budget",
    "portfolio_slots",
    "portfolio_hash",
    "allocator_hash",
    "selector_hash",
    "semantic_signature_provider_hash",
}
ANALYZERS = {
    "temporal_alignment",
    "state_transition_slice",
    "first_divergence",
    "combinational_cone",
    "sequential_cone",
    "assignment_trace",
}
RTL_SLICE_MODES = {"none", "combinational_cone", "sequential_cone", "local_block"}
RANKING_MODES = {"first_verified", "critic_ranked", "verifier_guided"}
EARLY_STOP_MODES = {"first_verified", "budget_exhausted", "all_candidates"}


class MemoryValidationError(ValueError):
    """Raised when a RAAM object violates its frozen schema."""


def _digest(value: Any, field: str, *, allow_empty: bool = False) -> str:
    text = str(value or "")
    if allow_empty and not text:
        return ""
    if not _HASH_RE.fullmatch(text):
        raise MemoryValidationError(f"{field} must be a sha256-prefixed digest")
    return text


def _string(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise MemoryValidationError(f"{field} must be a non-empty string")
    return value


def _exact_fields(raw: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise MemoryValidationError(f"{name} has undeclared fields: {sorted(unknown)}")


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise MemoryValidationError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise MemoryValidationError(f"{field} must be an integer") from exc
    if result < 0:
        raise MemoryValidationError(f"{field} must be non-negative")
    return result


def _validate_runtime_features(raw: Mapping[str, Any], *, descriptor: bool) -> dict[str, Any]:
    payload = deepcopy(dict(raw))
    allowed = DESCRIPTOR_FIELDS if descriptor else TRIGGER_FIELDS
    _exact_fields(payload, allowed, "failure descriptor" if descriptor else "trigger")
    leaked = set(payload) & FORBIDDEN_FEATURE_FIELDS
    if leaked:
        raise MemoryValidationError(f"private/red-truth features are forbidden: {sorted(leaked)}")
    if descriptor:
        hashes = payload.get("observable_artifact_hashes")
        if not isinstance(hashes, dict) or not hashes:
            raise MemoryValidationError("descriptor must bind observable artifacts")
        for name, digest in hashes.items():
            _string(name, "observable artifact name")
            _digest(digest, f"observable_artifact_hashes.{name}")
    if "affected_roles" in payload:
        roles = payload["affected_roles"]
        if not isinstance(roles, list) or not roles or any(
            not isinstance(role, str) or not role for role in roles
        ):
            raise MemoryValidationError("affected_roles must be a non-empty string list")
        payload["affected_roles"] = sorted(set(roles))
    for key in (
        "oracle_stage",
        "temporal_relation",
        "assignment_type",
        "cone_depth_bucket",
        "mismatch_pattern",
        "first_divergence_bucket",
        "first_divergence_signal",
    ):
        if key in payload:
            _string(payload[key], key)
    if "sequential_context" in payload and not isinstance(
        payload["sequential_context"], bool
    ):
        raise MemoryValidationError("sequential_context must be boolean")
    if "cycle_offset_bucket" in payload:
        payload["cycle_offset_bucket"] = _non_negative_int(
            payload["cycle_offset_bucket"], "cycle_offset_bucket"
        )
    return payload


def validate_control_delta(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or not raw:
        raise MemoryValidationError("control_delta must be a non-empty object")
    payload = deepcopy(dict(raw))
    _exact_fields(payload, CONTROL_DELTA_FIELDS, "control_delta")
    leaked = set(payload) & FORBIDDEN_CONTROL_FIELDS
    if leaked:
        raise MemoryValidationError(f"authority-changing control fields: {sorted(leaked)}")
    candidate_plan = payload.pop("candidate_plan", None)
    if candidate_plan is not None:
        if not isinstance(candidate_plan, Mapping):
            raise MemoryValidationError("candidate_plan must be an object")
        _exact_fields(
            candidate_plan,
            {
                "initial_candidates",
                "revision_rounds",
                "candidate_batch_size",
                "early_stop",
                "retry_after_compile_fail",
                "retry_after_oracle_fail",
            },
            "candidate_plan",
        )
        overlap = set(payload) & set(candidate_plan)
        if overlap:
            raise MemoryValidationError(f"duplicate candidate plan controls: {sorted(overlap)}")
        payload.update(deepcopy(dict(candidate_plan)))
    for key in ("enable_analyzers", "disable_analyzers"):
        if key in payload:
            values = payload[key]
            if not isinstance(values, list) or any(value not in ANALYZERS for value in values):
                raise MemoryValidationError(f"{key} contains an unknown analyzer")
            payload[key] = sorted(set(values))
    if set(payload.get("enable_analyzers", [])) & set(
        payload.get("disable_analyzers", [])
    ):
        raise MemoryValidationError("an analyzer cannot be enabled and disabled")
    for key in (
        "evidence_window_before",
        "evidence_window_after",
        "max_signals",
        "cone_depth",
        "initial_candidates",
        "revision_rounds",
        "candidate_batch_size",
        "max_changed_blocks",
        "specialist_slot_budget",
        "diversity_retry_budget",
    ):
        if key in payload:
            payload[key] = _non_negative_int(payload[key], key)
    for key in (
        "first_divergence_only",
        "retry_after_compile_fail",
        "retry_after_oracle_fail",
        "prefer_local_patch",
    ):
        if key in payload and not isinstance(payload[key], bool):
            raise MemoryValidationError(f"{key} must be boolean")
    if "rtl_slice_mode" in payload and payload["rtl_slice_mode"] not in RTL_SLICE_MODES:
        raise MemoryValidationError("invalid rtl_slice_mode")
    if "candidate_ranking" in payload and payload["candidate_ranking"] not in RANKING_MODES:
        raise MemoryValidationError("invalid candidate_ranking")
    if "early_stop" in payload and payload["early_stop"] not in EARLY_STOP_MODES:
        raise MemoryValidationError("invalid early_stop")
    portfolio_fields = {
        "candidate_portfolio_template_id",
        "specialist_slot_budget",
        "diversity_retry_budget",
        "portfolio_early_stop",
    }
    present_portfolio_fields = set(payload) & portfolio_fields
    if present_portfolio_fields:
        if present_portfolio_fields != portfolio_fields:
            raise MemoryValidationError(
                "portfolio control must bind all bounded fields"
            )
        _string(
            payload["candidate_portfolio_template_id"],
            "candidate_portfolio_template_id",
        )
        if payload["portfolio_early_stop"] not in EARLY_STOP_MODES:
            raise MemoryValidationError("invalid portfolio_early_stop")
    if "verifier_order" in payload:
        order = payload["verifier_order"]
        if not isinstance(order, list) or not order or any(
            item not in {"simulation", "formal"} for item in order
        ):
            raise MemoryValidationError("invalid verifier_order")
    if "pre_oracle_filters" in payload:
        filters = payload["pre_oracle_filters"]
        if not isinstance(filters, list) or any(
            item not in {"compile", "lint", "scope"} for item in filters
        ):
            raise MemoryValidationError("invalid pre_oracle_filters")
    return payload


@dataclass(frozen=True)
class VerifiedEpisode:
    episode_id: str
    round_id: str
    challenged_policy_instance_hash: str
    challenged_effective_policy_hash: str
    poison_id: str
    poison_payload_hash: str
    buggy_rtl_hash: str
    oracle_evidence_hash: str
    failure_descriptor: dict[str, Any]
    blue_attempts: list[dict[str, Any]]
    final_outcome: str
    successful_patch_hash: str
    activated_memory_ids: list[str]
    resource_usage: dict[str, int | float]
    episode_hash: str
    schema_version: str = EPISODE_SCHEMA_VERSION

    @classmethod
    def create(cls, **fields: Any) -> "VerifiedEpisode":
        payload = {"schema_version": EPISODE_SCHEMA_VERSION, **deepcopy(fields)}
        payload["episode_hash"] = hash_payload(
            {key: value for key, value in payload.items() if key != "episode_hash"}
        )
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "VerifiedEpisode":
        payload = deepcopy(dict(raw))
        fields = {
            "schema_version", "episode_id", "round_id",
            "challenged_policy_instance_hash", "challenged_effective_policy_hash",
            "poison_id", "poison_payload_hash", "buggy_rtl_hash",
            "oracle_evidence_hash", "failure_descriptor", "blue_attempts",
            "final_outcome", "successful_patch_hash", "activated_memory_ids",
            "resource_usage", "episode_hash",
        }
        _exact_fields(payload, fields, "verified episode")
        if payload.get("schema_version") != EPISODE_SCHEMA_VERSION:
            raise MemoryValidationError("verified episode schema mismatch")
        descriptor = FailureDescriptor.from_dict(payload["failure_descriptor"])
        attempts = payload.get("blue_attempts")
        if not isinstance(attempts, list):
            raise MemoryValidationError("blue_attempts must be a list")
        outcome = str(payload.get("final_outcome") or "")
        if outcome not in FINAL_OUTCOMES:
            raise MemoryValidationError("invalid final_outcome")
        successful = _digest(
            payload.get("successful_patch_hash"), "successful_patch_hash", allow_empty=True
        )
        if (outcome == "resolved") != bool(successful):
            raise MemoryValidationError("resolved outcome must bind exactly one successful patch")
        memories = payload.get("activated_memory_ids")
        if not isinstance(memories, list) or any(
            not isinstance(item, str) or not item for item in memories
        ):
            raise MemoryValidationError("activated_memory_ids must be a string list")
        usage = payload.get("resource_usage")
        required_usage = {
            "input_tokens", "output_tokens", "llm_calls", "verifier_calls",
            "wall_time_seconds",
        }
        if not isinstance(usage, dict) or set(usage) != required_usage:
            raise MemoryValidationError("resource_usage fields mismatch")
        normalized_usage: dict[str, int | float] = {}
        for key, value in usage.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise MemoryValidationError(f"resource_usage.{key} must be non-negative")
            normalized_usage[key] = value
        body = {key: value for key, value in payload.items() if key != "episode_hash"}
        if _digest(payload.get("episode_hash"), "episode_hash") != hash_payload(body):
            raise MemoryValidationError("episode hash mismatch")
        return cls(
            episode_id=_string(payload.get("episode_id"), "episode_id"),
            round_id=_string(payload.get("round_id"), "round_id"),
            challenged_policy_instance_hash=_digest(
                payload.get("challenged_policy_instance_hash"),
                "challenged_policy_instance_hash",
            ),
            challenged_effective_policy_hash=_digest(
                payload.get("challenged_effective_policy_hash"),
                "challenged_effective_policy_hash",
            ),
            poison_id=_string(payload.get("poison_id"), "poison_id"),
            poison_payload_hash=_digest(payload.get("poison_payload_hash"), "poison_payload_hash"),
            buggy_rtl_hash=_digest(payload.get("buggy_rtl_hash"), "buggy_rtl_hash"),
            oracle_evidence_hash=_digest(
                payload.get("oracle_evidence_hash"), "oracle_evidence_hash"
            ),
            failure_descriptor=descriptor.to_dict(),
            blue_attempts=attempts,
            final_outcome=outcome,
            successful_patch_hash=successful,
            activated_memory_ids=list(memories),
            resource_usage=normalized_usage,
            episode_hash=payload["episode_hash"],
        )

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self.__dict__)


@dataclass(frozen=True)
class FailureDescriptor:
    features: dict[str, Any]
    descriptor_hash: str
    schema_version: str = DESCRIPTOR_SCHEMA_VERSION

    @classmethod
    def create(cls, features: Mapping[str, Any]) -> "FailureDescriptor":
        normalized = _validate_runtime_features(features, descriptor=True)
        payload = {"schema_version": DESCRIPTOR_SCHEMA_VERSION, **normalized}
        return cls(
            features=normalized,
            descriptor_hash=hash_payload(payload),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FailureDescriptor":
        payload = deepcopy(dict(raw))
        if payload.get("schema_version") != DESCRIPTOR_SCHEMA_VERSION:
            raise MemoryValidationError("failure descriptor schema mismatch")
        digest = _digest(payload.pop("descriptor_hash", ""), "descriptor_hash")
        payload.pop("schema_version", None)
        normalized = _validate_runtime_features(payload, descriptor=True)
        expected = hash_payload({"schema_version": DESCRIPTOR_SCHEMA_VERSION, **normalized})
        if digest != expected:
            raise MemoryValidationError("failure descriptor hash mismatch")
        return cls(normalized, digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            **deepcopy(self.features),
            "descriptor_hash": self.descriptor_hash,
        }


@dataclass(frozen=True)
class ControlMemory:
    memory_id: str
    memory_version: int
    origin_round_id: str
    source_episode_ids: list[str]
    source_episode_hashes: dict[str, str]
    created_under_policy_instance_hash: str
    created_under_effective_policy_hash: str
    trigger_predicate: dict[str, Any]
    control_delta: dict[str, Any]
    status: str
    qualification_summary: dict[str, Any]
    compatibility: dict[str, Any]
    memory_hash: str
    schema_version: str = MEMORY_SCHEMA_VERSION

    @classmethod
    def create(cls, **fields: Any) -> "ControlMemory":
        payload = {"schema_version": MEMORY_SCHEMA_VERSION, **deepcopy(fields)}
        payload["trigger_predicate"] = _validate_runtime_features(
            payload.get("trigger_predicate") or {}, descriptor=False
        )
        payload["control_delta"] = validate_control_delta(
            payload.get("control_delta") or {}
        )
        payload["memory_hash"] = hash_payload(
            {key: value for key, value in payload.items() if key != "memory_hash"}
        )
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ControlMemory":
        payload = deepcopy(dict(raw))
        fields = {
            "schema_version", "memory_id", "memory_version", "origin_round_id",
            "source_episode_ids", "source_episode_hashes",
            "created_under_policy_instance_hash", "created_under_effective_policy_hash",
            "trigger_predicate", "control_delta", "status",
            "qualification_summary", "compatibility", "memory_hash",
        }
        _exact_fields(payload, fields, "control memory")
        if payload.get("schema_version") != MEMORY_SCHEMA_VERSION:
            raise MemoryValidationError("control memory schema mismatch")
        version = _non_negative_int(payload.get("memory_version"), "memory_version")
        if version < 1:
            raise MemoryValidationError("memory_version must be positive")
        source_ids = payload.get("source_episode_ids")
        source_hashes = payload.get("source_episode_hashes")
        if (
            not isinstance(source_ids, list)
            or not source_ids
            or len(source_ids) != len(set(source_ids))
            or not isinstance(source_hashes, dict)
            or set(source_ids) != set(source_hashes)
        ):
            raise MemoryValidationError("source episode ids/hashes must bind one another")
        for episode_id, digest in source_hashes.items():
            _string(episode_id, "source episode id")
            _digest(digest, f"source_episode_hashes.{episode_id}")
        trigger = _validate_runtime_features(payload.get("trigger_predicate") or {}, descriptor=False)
        if not trigger:
            raise MemoryValidationError("trigger_predicate must not be empty")
        delta = validate_control_delta(payload.get("control_delta") or {})
        status = str(payload.get("status") or "")
        if status not in MEMORY_STATUSES:
            raise MemoryValidationError("invalid memory status")
        if status != "candidate":
            raise MemoryValidationError("immutable memory versions must originate as candidate")
        for field in ("qualification_summary", "compatibility"):
            if not isinstance(payload.get(field), dict):
                raise MemoryValidationError(f"{field} must be an object")
        body = {key: value for key, value in payload.items() if key != "memory_hash"}
        body["trigger_predicate"] = trigger
        body["control_delta"] = delta
        expected = hash_payload(body)
        if _digest(payload.get("memory_hash"), "memory_hash") != expected:
            raise MemoryValidationError("memory hash mismatch")
        return cls(
            memory_id=_string(payload.get("memory_id"), "memory_id"),
            memory_version=version,
            origin_round_id=_string(payload.get("origin_round_id"), "origin_round_id"),
            source_episode_ids=list(source_ids),
            source_episode_hashes=dict(source_hashes),
            created_under_policy_instance_hash=_digest(
                payload.get("created_under_policy_instance_hash"),
                "created_under_policy_instance_hash",
            ),
            created_under_effective_policy_hash=_digest(
                payload.get("created_under_effective_policy_hash"),
                "created_under_effective_policy_hash",
            ),
            trigger_predicate=trigger,
            control_delta=delta,
            status=status,
            qualification_summary=deepcopy(payload["qualification_summary"]),
            compatibility=deepcopy(payload["compatibility"]),
            memory_hash=payload["memory_hash"],
        )

    @property
    def effective_delta_hash(self) -> str:
        return hash_payload({"control_delta": self.control_delta})

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self.__dict__)


@dataclass(frozen=True)
class MemoryLifecycleEvent:
    memory_id: str
    memory_version: int
    memory_hash: str
    previous_status: str
    new_status: str
    effective_policy_hash: str
    reason_code: str
    evidence_hash: str
    event_hash: str
    schema_version: str = LIFECYCLE_SCHEMA_VERSION

    @classmethod
    def create(cls, **fields: Any) -> "MemoryLifecycleEvent":
        payload = {"schema_version": LIFECYCLE_SCHEMA_VERSION, **deepcopy(fields)}
        payload["event_hash"] = hash_payload(
            {key: value for key, value in payload.items() if key != "event_hash"}
        )
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "MemoryLifecycleEvent":
        payload = deepcopy(dict(raw))
        allowed = {
            "schema_version", "memory_id", "memory_version", "memory_hash",
            "previous_status", "new_status", "effective_policy_hash",
            "reason_code", "evidence_hash", "event_hash",
        }
        _exact_fields(payload, allowed, "memory lifecycle event")
        if payload.get("schema_version") != LIFECYCLE_SCHEMA_VERSION:
            raise MemoryValidationError("lifecycle schema mismatch")
        previous = str(payload.get("previous_status") or "")
        new = str(payload.get("new_status") or "")
        if previous not in MEMORY_STATUSES or new not in MEMORY_STATUSES:
            raise MemoryValidationError("invalid lifecycle status")
        body = {key: value for key, value in payload.items() if key != "event_hash"}
        if _digest(payload.get("event_hash"), "event_hash") != hash_payload(body):
            raise MemoryValidationError("lifecycle event hash mismatch")
        return cls(
            memory_id=_string(payload.get("memory_id"), "memory_id"),
            memory_version=max(1, _non_negative_int(payload.get("memory_version"), "memory_version")),
            memory_hash=_digest(payload.get("memory_hash"), "memory_hash"),
            previous_status=previous,
            new_status=new,
            effective_policy_hash=_digest(
                payload.get("effective_policy_hash"), "effective_policy_hash"
            ),
            reason_code=_string(payload.get("reason_code"), "reason_code"),
            evidence_hash=_digest(payload.get("evidence_hash"), "evidence_hash"),
            event_hash=payload["event_hash"],
        )

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self.__dict__)


@dataclass(frozen=True)
class ActiveMemoryBank:
    bank_id: str
    bank_version: int
    policy_instance_hash: str
    effective_policy_hash: str
    effective_memory_bank_hash: str
    memories: dict[str, dict[str, Any]]
    retriever_hash: str
    activation_guard_hash: str
    control_whitelist_hash: str
    bank_hash: str
    schema_version: str = BANK_SCHEMA_VERSION

    @classmethod
    def create(cls, **fields: Any) -> "ActiveMemoryBank":
        payload = {"schema_version": BANK_SCHEMA_VERSION, **deepcopy(fields)}
        memories = payload.get("memories") or {}
        payload["effective_memory_bank_hash"] = hash_payload({
            "memory_definition_hashes": sorted(
                str(binding["memory_definition_hash"])
                for binding in memories.values()
            ),
            "retriever_hash": payload.get("retriever_hash"),
            "activation_guard_hash": payload.get("activation_guard_hash"),
            "control_whitelist_hash": payload.get("control_whitelist_hash"),
            "plan_compiler_version": BANK_PLAN_COMPILER_VERSION,
            "conflict_policy_version": BANK_CONFLICT_POLICY_VERSION,
            "maximum_activation": BANK_MAXIMUM_ACTIVATION,
        })
        payload["bank_hash"] = hash_payload(
            {
                key: value for key, value in payload.items()
                if key not in {"bank_hash", "effective_policy_hash"}
            }
        )
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ActiveMemoryBank":
        payload = deepcopy(dict(raw))
        allowed = {
            "schema_version", "bank_id", "bank_version", "policy_instance_hash",
            "effective_policy_hash", "effective_memory_bank_hash",
            "memories", "retriever_hash",
            "activation_guard_hash", "control_whitelist_hash", "bank_hash",
        }
        _exact_fields(payload, allowed, "active memory bank")
        if payload.get("schema_version") != BANK_SCHEMA_VERSION:
            raise MemoryValidationError("active memory bank schema mismatch")
        memories = payload.get("memories")
        if not isinstance(memories, dict):
            raise MemoryValidationError("bank memories must be an object")
        normalized = {}
        for memory_id, binding in memories.items():
            if not isinstance(binding, dict) or set(binding) != {
                "memory_version", "memory_hash", "memory_definition_hash", "status"
            }:
                raise MemoryValidationError("invalid bank memory binding")
            if binding.get("status") not in {"bank_candidate", "active_dormant"}:
                raise MemoryValidationError(
                    "bank may contain only bank_candidate or active_dormant memories"
                )
            normalized[_string(memory_id, "memory_id")] = {
                "memory_version": max(
                    1, _non_negative_int(binding.get("memory_version"), "memory_version")
                ),
                "memory_hash": _digest(binding.get("memory_hash"), "memory_hash"),
                "memory_definition_hash": _digest(
                    binding.get("memory_definition_hash"),
                    "memory_definition_hash",
                ),
                "status": binding["status"],
            }
        definition_hashes = [
            binding["memory_definition_hash"] for binding in normalized.values()
        ]
        if len(definition_hashes) != len(set(definition_hashes)):
            raise MemoryValidationError(
                "active bank contains duplicate memory definitions"
            )
        effective_body = {
            "memory_definition_hashes": sorted(definition_hashes),
            "retriever_hash": payload.get("retriever_hash"),
            "activation_guard_hash": payload.get("activation_guard_hash"),
            "control_whitelist_hash": payload.get("control_whitelist_hash"),
            "plan_compiler_version": BANK_PLAN_COMPILER_VERSION,
            "conflict_policy_version": BANK_CONFLICT_POLICY_VERSION,
            "maximum_activation": BANK_MAXIMUM_ACTIVATION,
        }
        if _digest(
            payload.get("effective_memory_bank_hash"),
            "effective_memory_bank_hash",
        ) != hash_payload(effective_body):
            raise MemoryValidationError("effective memory bank hash mismatch")
        # effective_policy_hash is a derived promotion binding: excluding it from
        # bank identity avoids a circular hash (PolicyState binds bank_hash,
        # while the promoted policy hash is written back here).
        body = {
            key: value for key, value in payload.items()
            if key not in {"bank_hash", "effective_policy_hash"}
        }
        body["memories"] = normalized
        if _digest(payload.get("bank_hash"), "bank_hash") != hash_payload(body):
            raise MemoryValidationError("active memory bank hash mismatch")
        return cls(
            bank_id=_string(payload.get("bank_id"), "bank_id"),
            bank_version=max(1, _non_negative_int(payload.get("bank_version"), "bank_version")),
            policy_instance_hash=_digest(
                payload.get("policy_instance_hash"), "policy_instance_hash"
            ),
            effective_policy_hash=_digest(
                payload.get("effective_policy_hash"), "effective_policy_hash"
            ),
            effective_memory_bank_hash=payload["effective_memory_bank_hash"],
            memories=normalized,
            retriever_hash=_digest(payload.get("retriever_hash"), "retriever_hash"),
            activation_guard_hash=_digest(
                payload.get("activation_guard_hash"), "activation_guard_hash"
            ),
            control_whitelist_hash=_digest(
                payload.get("control_whitelist_hash"), "control_whitelist_hash"
            ),
            bank_hash=payload["bank_hash"],
        )

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self.__dict__)

    @property
    def policy_binding(self) -> dict[str, str]:
        return {
            "active_memory_bank_hash": self.bank_hash,
            "effective_memory_bank_hash": self.effective_memory_bank_hash,
            "retriever_hash": self.retriever_hash,
            "activation_guard_hash": self.activation_guard_hash,
            "memory_control_whitelist_hash": self.control_whitelist_hash,
        }


@dataclass(frozen=True)
class MemoryMatch:
    memory_id: str
    memory_version: int
    memory_hash: str
    score: float
    matched_fields: tuple[str, ...]


@dataclass(frozen=True)
class BudgetEnvelope:
    max_llm_calls: int
    max_verifier_calls: int
    max_tokens: int
    max_wall_seconds: float

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0
            for value in (
                self.max_llm_calls,
                self.max_verifier_calls,
                self.max_tokens,
                self.max_wall_seconds,
            )
        ):
            raise MemoryValidationError("budget envelope values must be positive")

    @property
    def budget_hash(self) -> str:
        return hash_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self.__dict__)


@dataclass(frozen=True)
class RuntimeContext:
    effective_policy_hash: str
    policy_instance_hash: str
    available_analyzers: tuple[str, ...]
    budget: BudgetEnvelope
    control_whitelist_hash: str

    def __post_init__(self) -> None:
        _digest(self.effective_policy_hash, "effective_policy_hash")
        _digest(self.policy_instance_hash, "policy_instance_hash")
        _digest(self.control_whitelist_hash, "control_whitelist_hash")
        if any(item not in ANALYZERS for item in self.available_analyzers):
            raise MemoryValidationError("runtime context contains unknown analyzer")


@dataclass(frozen=True)
class ReactivationDecision:
    activated_memory_ids: tuple[str, ...]
    abstained: bool
    reason_code: str
    effective_policy_hash: str
    active_bank_hash: str
    decision_hash: str

    @classmethod
    def create(
        cls,
        *,
        activated_memory_ids: list[str] | tuple[str, ...],
        abstained: bool,
        reason_code: str,
        effective_policy_hash: str,
        active_bank_hash: str,
    ) -> "ReactivationDecision":
        body = {
            "activated_memory_ids": list(activated_memory_ids),
            "abstained": abstained,
            "reason_code": reason_code,
            "effective_policy_hash": effective_policy_hash,
            "active_bank_hash": active_bank_hash,
        }
        return cls(
            activated_memory_ids=tuple(activated_memory_ids),
            abstained=abstained,
            reason_code=reason_code,
            effective_policy_hash=_digest(effective_policy_hash, "effective_policy_hash"),
            active_bank_hash=_digest(active_bank_hash, "active_bank_hash"),
            decision_hash=hash_payload(body),
        )


@dataclass(frozen=True)
class ExecutionPlan:
    effective_policy_hash: str
    active_bank_hash: str
    activated_memory_ids: tuple[str, ...]
    controls: dict[str, Any]
    memory_token_cost: int
    plan_hash: str
    schema_version: str = PLAN_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        effective_policy_hash: str,
        active_bank_hash: str,
        activated_memory_ids: list[str] | tuple[str, ...],
        controls: Mapping[str, Any],
    ) -> "ExecutionPlan":
        normalized = deepcopy(dict(controls))
        payload = {
            "schema_version": PLAN_SCHEMA_VERSION,
            "effective_policy_hash": _digest(
                effective_policy_hash, "effective_policy_hash"
            ),
            "active_bank_hash": _digest(
                active_bank_hash, "active_bank_hash", allow_empty=True
            ),
            "activated_memory_ids": list(activated_memory_ids),
            "controls": normalized,
            "memory_token_cost": 0,
        }
        return cls(
            effective_policy_hash=payload["effective_policy_hash"],
            active_bank_hash=payload["active_bank_hash"],
            activated_memory_ids=tuple(activated_memory_ids),
            controls=normalized,
            memory_token_cost=0,
            plan_hash=hash_payload(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "effective_policy_hash": self.effective_policy_hash,
            "active_bank_hash": self.active_bank_hash,
            "activated_memory_ids": list(self.activated_memory_ids),
            "controls": deepcopy(self.controls),
            "memory_token_cost": self.memory_token_cost,
            "plan_hash": self.plan_hash,
        }


@dataclass(frozen=True)
class ShadowPairedResult:
    case_id: str
    seed: int
    policy_hash: str
    memory_hash: str
    control: dict[str, Any]
    shadow: dict[str, Any]
    outcome: str
    resource_delta: dict[str, float]
    pair_hash: str
    schema_version: str = SHADOW_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self.__dict__)
