"""Validated immutable policy-state schema."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Mapping

from r3e.protocol.hashing import hash_payload


POLICY_SCHEMA_VERSION = "r3e-policy-v2"
EVIDENCE_MODES = {"raw", "hybrid"}
REPAIR_LOOPS = {"one-shot", "critique-revise"}
CANDIDATE_SELECTION = {"first_verified", "critic_ranked", "verifier_guided"}
PATCH_SCOPES = {"expression", "local_block"}
POLICY_STATUSES = {
    "candidate", "active", "superseded", "rejected", "provisional",
    "retired", "rolled_back", "audit_failed",
}
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_POLICY_FIELDS = {
    "policy_id",
    "schema_version",
    "parent_policy_id",
    "parent_policy_hash",
    "base_policy_hash",
    "created_round",
    "created_from_residual_manifest_hash",
    "configuration",
    "budgets",
    "status",
    "configuration_hash",
    "policy_hash",
    "validation_manifest_hash",
    "promotion_decision_hash",
    "rollback_policy_id",
    "frozen_assets",
    "proposal_operator",
    "rollback_registry_hash",
    "memory_binding",
}
_MEMORY_BINDING_FIELDS = {
    "active_memory_bank_hash",
    "retriever_hash",
    "activation_guard_hash",
    "memory_control_whitelist_hash",
}


class PolicyValidationError(ValueError):
    """Raised when policy state is incomplete or outside the frozen schema."""


def _require_string(payload: Mapping[str, Any], key: str, *, allow_empty: bool = False) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or (not value and not allow_empty):
        raise PolicyValidationError(f"{key} must be a non-empty string")
    return value


@dataclass(frozen=True)
class PolicyState:
    policy_id: str
    schema_version: str
    parent_policy_id: str
    parent_policy_hash: str
    base_policy_hash: str
    created_round: int
    created_from_residual_manifest_hash: str
    configuration: dict[str, Any]
    budgets: dict[str, int]
    status: str
    validation_manifest_hash: str = ""
    promotion_decision_hash: str = ""
    rollback_policy_id: str = ""
    frozen_assets: dict[str, str] | None = None
    proposal_operator: str = ""
    rollback_registry_hash: str = ""
    memory_binding: dict[str, str] | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PolicyState":
        payload = deepcopy(dict(raw))
        unknown = set(payload) - _POLICY_FIELDS
        if unknown:
            raise PolicyValidationError(f"undeclared policy fields: {sorted(unknown)}")
        schema = _require_string(payload, "schema_version")
        if schema != POLICY_SCHEMA_VERSION:
            raise PolicyValidationError(f"unsupported policy schema: {schema}")
        policy_id = _require_string(payload, "policy_id")
        parent_id = _require_string(payload, "parent_policy_id", allow_empty=True)
        parent_hash = _require_string(payload, "parent_policy_hash", allow_empty=True)
        base_hash = _require_string(payload, "base_policy_hash")
        residual_hash = _require_string(
            payload, "created_from_residual_manifest_hash", allow_empty=True
        )
        try:
            created_round = int(payload.get("created_round"))
        except (TypeError, ValueError) as exc:
            raise PolicyValidationError("created_round must be an integer") from exc
        if created_round < 0:
            raise PolicyValidationError("created_round must be non-negative")
        configuration = deepcopy(payload.get("configuration"))
        budgets = deepcopy(payload.get("budgets"))
        if not isinstance(configuration, dict):
            raise PolicyValidationError("configuration must be an object")
        if not isinstance(budgets, dict):
            raise PolicyValidationError("budgets must be an object")
        cls._validate_configuration(configuration)
        cls._validate_budgets(budgets)
        status = _require_string(payload, "status")
        if status not in POLICY_STATUSES:
            raise PolicyValidationError(f"invalid policy status: {status}")
        if policy_id != "B0" and (not parent_id or not parent_hash):
            raise PolicyValidationError("non-base policy must bind parent id and hash")
        for key, value in (
            ("base_policy_hash", base_hash),
            ("parent_policy_hash", parent_hash),
            ("created_from_residual_manifest_hash", residual_hash),
            ("rollback_registry_hash", str(payload.get("rollback_registry_hash") or "")),
        ):
            if value and not _HASH_RE.fullmatch(value):
                raise PolicyValidationError(f"{key} must be a sha256-prefixed digest")
        frozen_assets = deepcopy(payload.get("frozen_assets") or {})
        if not isinstance(frozen_assets, dict):
            raise PolicyValidationError("frozen_assets must be an object")
        if not frozen_assets:
            raise PolicyValidationError("every policy must bind frozen prompt assets")
        if any(
            not isinstance(path, str)
            or not path
            or not isinstance(digest, str)
            or not _HASH_RE.fullmatch(digest)
            for path, digest in frozen_assets.items()
        ):
            raise PolicyValidationError("invalid frozen asset binding")
        if policy_id == "B0":
            expected_base_hash = hash_payload({
                "schema_version": schema,
                "configuration": configuration,
                "budgets": {key: int(value) for key, value in budgets.items()},
                "frozen_assets": frozen_assets,
            })
            if base_hash != expected_base_hash:
                raise PolicyValidationError("base_policy_hash does not bind frozen base content")
        memory_binding = deepcopy(payload.get("memory_binding") or {})
        if not isinstance(memory_binding, dict):
            raise PolicyValidationError("memory_binding must be an object")
        if memory_binding and set(memory_binding) != _MEMORY_BINDING_FIELDS:
            raise PolicyValidationError("memory_binding fields mismatch")
        if any(
            not isinstance(digest, str) or not _HASH_RE.fullmatch(digest)
            for digest in memory_binding.values()
        ):
            raise PolicyValidationError("memory_binding contains an invalid hash")
        return cls(
            policy_id=policy_id,
            schema_version=schema,
            parent_policy_id=parent_id,
            parent_policy_hash=parent_hash,
            base_policy_hash=base_hash,
            created_round=created_round,
            created_from_residual_manifest_hash=residual_hash,
            configuration=configuration,
            budgets={key: int(value) for key, value in budgets.items()},
            status=status,
            validation_manifest_hash=str(payload.get("validation_manifest_hash") or ""),
            promotion_decision_hash=str(payload.get("promotion_decision_hash") or ""),
            rollback_policy_id=str(payload.get("rollback_policy_id") or ""),
            frozen_assets=frozen_assets,
            proposal_operator=str(payload.get("proposal_operator") or ""),
            rollback_registry_hash=str(payload.get("rollback_registry_hash") or ""),
            memory_binding=memory_binding,
        )

    @staticmethod
    def _validate_configuration(configuration: Mapping[str, Any]) -> None:
        required = {
            "evidence_mode", "evidence_k", "n_candidates", "repair_loop",
            "prompt_lens_id", "candidate_selection", "model_route_id",
            "blue_population", "patch_scope", "verifier_order",
        }
        missing = required - configuration.keys()
        if missing:
            raise PolicyValidationError(f"configuration missing: {sorted(missing)}")
        unknown = set(configuration) - required
        if unknown:
            raise PolicyValidationError(
                f"undeclared configuration fields: {sorted(unknown)}"
            )
        if configuration["evidence_mode"] not in EVIDENCE_MODES:
            raise PolicyValidationError("invalid evidence_mode")
        if int(configuration["evidence_k"]) not in {1, 3, 6}:
            raise PolicyValidationError("evidence_k must be one of 1, 3, 6")
        if int(configuration["n_candidates"]) not in {1, 3, 6}:
            raise PolicyValidationError("n_candidates must be one of 1, 3, 6")
        if configuration["repair_loop"] not in REPAIR_LOOPS:
            raise PolicyValidationError("invalid repair_loop")
        if configuration["candidate_selection"] not in CANDIDATE_SELECTION:
            raise PolicyValidationError("invalid candidate_selection")
        if configuration["patch_scope"] not in PATCH_SCOPES:
            raise PolicyValidationError("invalid patch_scope")
        if int(configuration["blue_population"]) < 1:
            raise PolicyValidationError("blue_population must be positive")
        verifier_order = configuration["verifier_order"]
        if (
            not isinstance(verifier_order, list)
            or not verifier_order
            or any(item not in {"simulation", "formal"} for item in verifier_order)
        ):
            raise PolicyValidationError("verifier_order must contain simulation/formal")
        for key in ("prompt_lens_id", "model_route_id"):
            if not isinstance(configuration[key], str) or not configuration[key]:
                raise PolicyValidationError(f"{key} must be a non-empty string")

    @staticmethod
    def _validate_budgets(budgets: Mapping[str, Any]) -> None:
        required = {
            "max_llm_calls_per_case",
            "max_wall_seconds_per_case",
            "max_tokens_per_case",
        }
        missing = required - budgets.keys()
        if missing:
            raise PolicyValidationError(f"budgets missing: {sorted(missing)}")
        unknown = set(budgets) - required
        if unknown:
            raise PolicyValidationError(f"undeclared budget fields: {sorted(unknown)}")
        for key in required:
            try:
                value = int(budgets[key])
            except (TypeError, ValueError) as exc:
                raise PolicyValidationError(f"{key} must be an integer") from exc
            if value <= 0:
                raise PolicyValidationError(f"{key} must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "schema_version": self.schema_version,
            "parent_policy_id": self.parent_policy_id,
            "parent_policy_hash": self.parent_policy_hash,
            "base_policy_hash": self.base_policy_hash,
            "created_round": self.created_round,
            "created_from_residual_manifest_hash": self.created_from_residual_manifest_hash,
            "configuration": deepcopy(self.configuration),
            "budgets": deepcopy(self.budgets),
            "status": self.status,
            "configuration_hash": self.configuration_hash,
            "policy_hash": self.policy_hash,
            "validation_manifest_hash": self.validation_manifest_hash,
            "promotion_decision_hash": self.promotion_decision_hash,
            "rollback_policy_id": self.rollback_policy_id,
            "frozen_assets": deepcopy(self.frozen_assets or {}),
            "proposal_operator": self.proposal_operator,
            "rollback_registry_hash": self.rollback_registry_hash,
            "memory_binding": deepcopy(self.memory_binding or {}),
        }

    @property
    def configuration_hash(self) -> str:
        return hash_payload({"configuration": self.configuration, "budgets": self.budgets})

    @property
    def policy_hash(self) -> str:
        immutable = {
            "policy_id": self.policy_id,
            "schema_version": self.schema_version,
            "parent_policy_id": self.parent_policy_id,
            "parent_policy_hash": self.parent_policy_hash,
            "base_policy_hash": self.base_policy_hash,
            "created_round": self.created_round,
            "created_from_residual_manifest_hash": self.created_from_residual_manifest_hash,
            "configuration": self.configuration,
            "budgets": self.budgets,
            "rollback_policy_id": self.rollback_policy_id,
            "frozen_assets": self.frozen_assets or {},
            "proposal_operator": self.proposal_operator,
        }
        if self.memory_binding:
            immutable["memory_binding"] = self.memory_binding
        return hash_payload(immutable)

    @property
    def policy_instance_hash(self) -> str:
        """Lineage identity of this exact policy version."""
        return self.policy_hash

    @property
    def effective_policy_hash(self) -> str:
        """Behavioral identity, excluding round/id/lineage bookkeeping."""
        return hash_payload({
            "schema_version": self.schema_version,
            "base_policy_hash": self.base_policy_hash,
            "configuration": self.configuration,
            "budgets": self.budgets,
            "frozen_assets": self.frozen_assets or {},
            "memory_binding": self.memory_binding or {},
        })

    def with_updates(self, **updates: Any) -> "PolicyState":
        payload = self.to_dict()
        payload.update(updates)
        payload.pop("configuration_hash", None)
        payload.pop("policy_hash", None)
        return PolicyState.from_dict(payload)
