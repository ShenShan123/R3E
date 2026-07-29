"""Strict, reconstructable schemas for candidate portfolio execution."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Mapping

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload


LENS_DEFINITION_SCHEMA = "r3e-blue-lens-definition-v1"
LENS_REGISTRY_SCHEMA = "r3e-blue-lens-registry-v1"
PORTFOLIO_SCHEMA = "r3e-candidate-portfolio-v1"
ALLOCATION_PLAN_SCHEMA = "r3e-candidate-allocation-plan-v1"

PORTFOLIO_MODES = {
    "homogeneous",
    "fixed_mixed",
    "descriptor_routed",
    "adaptive",
}
EARLY_STOP_MODES = {"all_candidates", "first_verified"}
DUPLICATE_POLICIES = {"measure_only", "reject_duplicate"}
ALLOWED_EVIDENCE_FIELDS = {
    "sequential_context",
    "temporal_relation",
    "cycle_offset_bucket",
    "assignment_type",
    "cone_depth_bucket",
    "mismatch_pattern",
    "first_divergence_bucket",
    "first_divergence_signal",
    "affected_roles",
    "observable_artifact_hashes",
}
FORBIDDEN_LENS_FIELDS = {
    "history_patch",
    "source_episode",
    "source_episodes",
    "red_family",
    "mutation_family",
    "mutation_operator",
    "reference_fix",
    "reference_patch",
    "free_text_memory",
}
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


class PortfolioValidationError(ValueError):
    """Raised when an ACP protocol object is incomplete or unbound."""


def _digest(value: Any, field: str) -> str:
    text = str(value or "")
    if not _HASH_RE.fullmatch(text):
        raise PortfolioValidationError(f"{field} must be an exact sha256 digest")
    return text


def _identifier(value: Any, field: str) -> str:
    text = str(value or "")
    if not _ID_RE.fullmatch(text):
        raise PortfolioValidationError(f"{field} must be a stable identifier")
    return text


def _exact_fields(raw: Mapping[str, Any], expected: set[str], name: str) -> None:
    if set(raw) != expected:
        raise PortfolioValidationError(f"{name} fields mismatch")


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise PortfolioValidationError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise PortfolioValidationError(f"{field} must be an integer") from exc
    if result < 0:
        raise PortfolioValidationError(f"{field} must be non-negative")
    return result


@dataclass(frozen=True)
class LensDefinition:
    lens_id: str
    prompt_asset_path: str
    prompt_asset_hash: str
    lens_group: str
    orthogonality_group: str
    allowed_evidence_fields: tuple[str, ...]
    forbid_history: bool = True
    forbid_red_truth: bool = True

    @classmethod
    def create(
        cls,
        *,
        lens_id: str,
        prompt_asset_path: str,
        prompt_asset_hash: str,
        lens_group: str,
        orthogonality_group: str,
        allowed_evidence_fields: list[str] | tuple[str, ...],
    ) -> "LensDefinition":
        value = cls(
            lens_id=_identifier(lens_id, "lens_id"),
            prompt_asset_path=str(prompt_asset_path),
            prompt_asset_hash=_digest(prompt_asset_hash, "prompt_asset_hash"),
            lens_group=_identifier(lens_group, "lens_group"),
            orthogonality_group=_identifier(
                orthogonality_group, "orthogonality_group"
            ),
            allowed_evidence_fields=tuple(sorted(set(allowed_evidence_fields))),
        )
        value._validate()
        return value

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "LensDefinition":
        payload = deepcopy(dict(raw))
        _exact_fields(
            payload,
            {
                "schema_version",
                "lens_id",
                "prompt_asset_path",
                "prompt_asset_hash",
                "lens_group",
                "orthogonality_group",
                "allowed_evidence_fields",
                "forbid_history",
                "forbid_red_truth",
                "definition_hash",
            },
            "lens definition",
        )
        if payload["schema_version"] != LENS_DEFINITION_SCHEMA:
            raise PortfolioValidationError("lens definition schema mismatch")
        fields = payload["allowed_evidence_fields"]
        if not isinstance(fields, list):
            raise PortfolioValidationError("allowed_evidence_fields must be a list")
        value = cls.create(
            lens_id=payload["lens_id"],
            prompt_asset_path=payload["prompt_asset_path"],
            prompt_asset_hash=payload["prompt_asset_hash"],
            lens_group=payload["lens_group"],
            orthogonality_group=payload["orthogonality_group"],
            allowed_evidence_fields=fields,
        )
        if payload["forbid_history"] is not True or payload["forbid_red_truth"] is not True:
            raise PortfolioValidationError("lens safety flags must be true")
        if payload["definition_hash"] != value.definition_hash:
            raise PortfolioValidationError("lens definition hash mismatch")
        return value

    def _validate(self) -> None:
        path = self.prompt_asset_path
        if (
            not path
            or path.startswith("/")
            or "\\" in path
            or ".." in path.split("/")
        ):
            raise PortfolioValidationError(
                "prompt_asset_path must be a repository-relative path"
            )
        fields = set(self.allowed_evidence_fields)
        if not fields or not fields <= ALLOWED_EVIDENCE_FIELDS:
            raise PortfolioValidationError(
                "lens allowed evidence fields exceed the public descriptor"
            )
        if fields & FORBIDDEN_LENS_FIELDS:
            raise PortfolioValidationError("lens exposes private or red-truth fields")
        if not self.forbid_history or not self.forbid_red_truth:
            raise PortfolioValidationError("lens safety flags must be true")

    @property
    def definition_hash(self) -> str:
        return hash_payload(self._body())

    def _body(self) -> dict[str, Any]:
        return {
            "schema_version": LENS_DEFINITION_SCHEMA,
            "lens_id": self.lens_id,
            "prompt_asset_path": self.prompt_asset_path,
            "prompt_asset_hash": self.prompt_asset_hash,
            "lens_group": self.lens_group,
            "orthogonality_group": self.orthogonality_group,
            "allowed_evidence_fields": list(self.allowed_evidence_fields),
            "forbid_history": True,
            "forbid_red_truth": True,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._body()
        payload["definition_hash"] = self.definition_hash
        return payload


@dataclass(frozen=True)
class LensRegistry:
    registry_id: str
    lenses: dict[str, LensDefinition]

    @classmethod
    def create(
        cls, *, registry_id: str, lenses: list[LensDefinition]
    ) -> "LensRegistry":
        by_id = {lens.lens_id: lens for lens in lenses}
        if len(by_id) != len(lenses) or not by_id:
            raise PortfolioValidationError("lens registry IDs must be unique")
        return cls(
            registry_id=_identifier(registry_id, "registry_id"),
            lenses={key: by_id[key] for key in sorted(by_id)},
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "LensRegistry":
        payload = deepcopy(dict(raw))
        _exact_fields(
            payload,
            {"schema_version", "registry_id", "lenses", "registry_hash"},
            "lens registry",
        )
        if payload["schema_version"] != LENS_REGISTRY_SCHEMA:
            raise PortfolioValidationError("lens registry schema mismatch")
        if not isinstance(payload["lenses"], Mapping):
            raise PortfolioValidationError("lens registry lenses must be an object")
        lenses = []
        for key, definition in payload["lenses"].items():
            lens = LensDefinition.from_dict(definition)
            if key != lens.lens_id:
                raise PortfolioValidationError("lens registry key/id mismatch")
            lenses.append(lens)
        value = cls.create(registry_id=payload["registry_id"], lenses=lenses)
        if payload["registry_hash"] != value.registry_hash:
            raise PortfolioValidationError("lens registry hash mismatch")
        return value

    @property
    def registry_hash(self) -> str:
        return hash_payload(self._body())

    def _body(self) -> dict[str, Any]:
        return {
            "schema_version": LENS_REGISTRY_SCHEMA,
            "registry_id": self.registry_id,
            "lenses": {
                key: self.lenses[key].to_dict() for key in sorted(self.lenses)
            },
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._body()
        payload["registry_hash"] = self.registry_hash
        return payload


@dataclass(frozen=True)
class CandidatePortfolio:
    portfolio_id: str
    mode: str
    candidate_budget: int
    lens_registry_hash: str
    router_hash: str
    allocator_hash: str
    selector_hash: str
    semantic_signature_provider_hash: str
    duplicate_policy: str
    early_stop_mode: str
    lens_ids: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        portfolio_id: str,
        mode: str,
        candidate_budget: int,
        lens_registry_hash: str,
        router_hash: str,
        allocator_hash: str,
        selector_hash: str,
        semantic_signature_provider_hash: str,
        duplicate_policy: str,
        early_stop_mode: str,
        lens_ids: list[str] | tuple[str, ...],
    ) -> "CandidatePortfolio":
        budget = _non_negative_int(candidate_budget, "candidate_budget")
        if budget < 1:
            raise PortfolioValidationError("candidate_budget must be positive")
        normalized_lenses = tuple(
            _identifier(value, "portfolio lens_id") for value in lens_ids
        )
        if mode in {"descriptor_routed", "adaptive"} and (
            len(normalized_lenses) < budget
            or len(set(normalized_lenses)) != len(normalized_lenses)
        ):
            raise PortfolioValidationError(
                "descriptor-routed portfolio requires a distinct lens pool"
            )
        if mode not in {"descriptor_routed", "adaptive"} and (
            len(normalized_lenses) != budget
        ):
            raise PortfolioValidationError(
                "portfolio slot count must equal candidate budget"
            )
        if mode not in PORTFOLIO_MODES:
            raise PortfolioValidationError("unsupported portfolio mode")
        if mode == "homogeneous" and len(set(normalized_lenses)) != 1:
            raise PortfolioValidationError(
                "homogeneous portfolio must use exactly one lens"
            )
        if duplicate_policy not in DUPLICATE_POLICIES:
            raise PortfolioValidationError("unsupported duplicate policy")
        if early_stop_mode not in EARLY_STOP_MODES:
            raise PortfolioValidationError("unsupported early stop mode")
        return cls(
            portfolio_id=_identifier(portfolio_id, "portfolio_id"),
            mode=mode,
            candidate_budget=budget,
            lens_registry_hash=_digest(
                lens_registry_hash, "lens_registry_hash"
            ),
            router_hash=_digest(router_hash, "router_hash"),
            allocator_hash=_digest(allocator_hash, "allocator_hash"),
            selector_hash=_digest(selector_hash, "selector_hash"),
            semantic_signature_provider_hash=_digest(
                semantic_signature_provider_hash,
                "semantic_signature_provider_hash",
            ),
            duplicate_policy=duplicate_policy,
            early_stop_mode=early_stop_mode,
            lens_ids=normalized_lenses,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CandidatePortfolio":
        payload = deepcopy(dict(raw))
        _exact_fields(
            payload,
            {
                "schema_version",
                "portfolio_id",
                "mode",
                "candidate_budget",
                "lens_registry_hash",
                "router_hash",
                "allocator_hash",
                "selector_hash",
                "semantic_signature_provider_hash",
                "duplicate_policy",
                "early_stop_mode",
                "lens_ids",
                "effective_portfolio_hash",
            },
            "candidate portfolio",
        )
        if payload["schema_version"] != PORTFOLIO_SCHEMA:
            raise PortfolioValidationError("candidate portfolio schema mismatch")
        if not isinstance(payload["lens_ids"], list):
            raise PortfolioValidationError("portfolio lens_ids must be a list")
        value = cls.create(
            **{
                key: payload[key]
                for key in (
                    "portfolio_id",
                    "mode",
                    "candidate_budget",
                    "lens_registry_hash",
                    "router_hash",
                    "allocator_hash",
                    "selector_hash",
                    "semantic_signature_provider_hash",
                    "duplicate_policy",
                    "early_stop_mode",
                    "lens_ids",
                )
            }
        )
        if payload["effective_portfolio_hash"] != value.effective_portfolio_hash:
            raise PortfolioValidationError("candidate portfolio hash mismatch")
        return value

    @property
    def portfolio_hash(self) -> str:
        """Instance identity including the stable portfolio ID."""
        return hash_payload(self._body())

    @property
    def effective_portfolio_hash(self) -> str:
        """Behavioral identity, excluding the portfolio bookkeeping ID."""
        return hash_payload({
            key: value
            for key, value in self._body().items()
            if key != "portfolio_id"
        })

    def _body(self) -> dict[str, Any]:
        return {
            "schema_version": PORTFOLIO_SCHEMA,
            "portfolio_id": self.portfolio_id,
            "mode": self.mode,
            "candidate_budget": self.candidate_budget,
            "lens_registry_hash": self.lens_registry_hash,
            "router_hash": self.router_hash,
            "allocator_hash": self.allocator_hash,
            "selector_hash": self.selector_hash,
            "semantic_signature_provider_hash": (
                self.semantic_signature_provider_hash
            ),
            "duplicate_policy": self.duplicate_policy,
            "early_stop_mode": self.early_stop_mode,
            "lens_ids": list(self.lens_ids),
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._body()
        payload["effective_portfolio_hash"] = self.effective_portfolio_hash
        return payload


@dataclass(frozen=True)
class AllocationPlan:
    effective_policy_hash: str
    descriptor_hash: str
    portfolio_hash: str
    run_seed: int
    candidate_budget: int
    slots: tuple[dict[str, Any], ...]
    allocation_reason: dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        effective_policy_hash: str,
        descriptor_hash: str,
        portfolio: CandidatePortfolio,
        registry: LensRegistry,
        run_seed: int,
        revision_budget: int = 0,
        router_receipt: Mapping[str, Any] | None = None,
        allocator_receipt: Mapping[str, Any] | None = None,
        portfolio_control_receipt: Mapping[str, Any] | None = None,
    ) -> "AllocationPlan":
        if portfolio.lens_registry_hash != registry.registry_hash:
            raise PortfolioValidationError(
                "portfolio is bound to a different lens registry"
            )
        if any(lens_id not in registry.lenses for lens_id in portfolio.lens_ids):
            raise PortfolioValidationError("portfolio contains an unknown lens")
        selected_lenses = portfolio.lens_ids
        router_receipt_hash = portfolio.router_hash
        allocator_receipt_hash = portfolio.allocator_hash
        portfolio_control_receipt_hash = hash_payload({
            "portfolio_control": "policy_default",
        })
        if (
            portfolio.mode == "descriptor_routed"
            and not portfolio_control_receipt
        ):
            if not isinstance(router_receipt, Mapping):
                raise PortfolioValidationError(
                    "descriptor-routed allocation requires router receipt"
                )
            receipt = deepcopy(dict(router_receipt))
            if set(receipt) != {
                "schema_version",
                "router_hash",
                "descriptor_hash",
                "specialist_scores",
                "matched_rule_ids",
                "primary_lens_id",
                "orthogonal_lens_id",
                "allocated_lens_ids",
                "receipt_hash",
            }:
                raise PortfolioValidationError(
                    "descriptor router receipt fields mismatch"
                )
            if (
                receipt["schema_version"]
                != "r3e-descriptor-router-receipt-v1"
                or receipt["router_hash"] != portfolio.router_hash
                or receipt["descriptor_hash"] != descriptor_hash
                or receipt["receipt_hash"] != hash_payload({
                    key: value
                    for key, value in receipt.items()
                    if key != "receipt_hash"
                })
            ):
                raise PortfolioValidationError(
                    "descriptor router receipt authority mismatch"
                )
            allocated = receipt["allocated_lens_ids"]
            if (
                not isinstance(allocated, list)
                or len(allocated) != portfolio.candidate_budget
                or len(set(allocated)) != len(allocated)
                or not set(allocated) <= set(portfolio.lens_ids)
            ):
                raise PortfolioValidationError(
                    "descriptor router allocation exceeds portfolio"
                )
            selected_lenses = tuple(str(value) for value in allocated)
            router_receipt_hash = receipt["receipt_hash"]
        elif router_receipt:
            raise PortfolioValidationError(
                "static portfolio cannot consume descriptor router receipt"
            )
        if portfolio_control_receipt:
            if router_receipt or allocator_receipt:
                raise PortfolioValidationError(
                    "RAAM template cannot consume router/allocator receipt"
                )
            receipt = deepcopy(dict(portfolio_control_receipt))
            if set(receipt) != {
                "schema_version",
                "effective_policy_hash",
                "execution_plan_hash",
                "portfolio_template_registry_hash",
                "portfolio_template_id",
                "portfolio_template_hash",
                "allocated_lens_ids",
                "candidate_budget",
                "specialist_slot_budget",
                "diversity_retry_budget",
                "portfolio_early_stop",
                "receipt_hash",
            }:
                raise PortfolioValidationError(
                    "portfolio control receipt fields mismatch"
                )
            if (
                receipt["schema_version"]
                != "r3e-portfolio-control-receipt-v1"
                or receipt["effective_policy_hash"] != effective_policy_hash
                or receipt["receipt_hash"] != hash_payload({
                    key: value for key, value in receipt.items()
                    if key != "receipt_hash"
                })
                or receipt["candidate_budget"] != portfolio.candidate_budget
                or receipt["diversity_retry_budget"] != 0
                or receipt["portfolio_early_stop"] != "all_candidates"
            ):
                raise PortfolioValidationError(
                    "portfolio control receipt authority mismatch"
                )
            allocated = receipt["allocated_lens_ids"]
            if (
                not isinstance(allocated, list)
                or len(allocated) != portfolio.candidate_budget
                or not set(allocated) <= set(portfolio.lens_ids)
            ):
                raise PortfolioValidationError(
                    "portfolio control allocation exceeds portfolio"
                )
            selected_lenses = tuple(str(value) for value in allocated)
            portfolio_control_receipt_hash = receipt["receipt_hash"]
        elif portfolio.mode == "adaptive":
            if not isinstance(allocator_receipt, Mapping):
                raise PortfolioValidationError(
                    "adaptive allocation requires allocator receipt"
                )
            receipt = deepcopy(dict(allocator_receipt))
            if (
                receipt.get("schema_version")
                != "r3e-offline-allocation-receipt-v1"
                or receipt.get("allocator_state_hash")
                != portfolio.allocator_hash
                or receipt.get("descriptor_hash") != descriptor_hash
                or receipt.get("receipt_hash") != hash_payload({
                    key: value for key, value in receipt.items()
                    if key != "receipt_hash"
                })
            ):
                raise PortfolioValidationError(
                    "offline allocator receipt authority mismatch"
                )
            allocated = receipt.get("allocated_lens_ids")
            if (
                not isinstance(allocated, list)
                or len(allocated) != portfolio.candidate_budget
                or not set(allocated) <= set(portfolio.lens_ids)
            ):
                raise PortfolioValidationError(
                    "offline allocator allocation exceeds portfolio"
                )
            selected_lenses = tuple(str(value) for value in allocated)
            allocator_receipt_hash = receipt["receipt_hash"]
        elif allocator_receipt:
            raise PortfolioValidationError(
                "non-adaptive portfolio cannot consume allocator receipt"
            )
        seed = _non_negative_int(run_seed, "run_seed")
        revision = _non_negative_int(revision_budget, "revision_budget")
        slots = []
        for index, lens_id in enumerate(selected_lenses):
            lens = registry.lenses[lens_id]
            seed_payload = {
                "run_seed": seed,
                "effective_policy_hash": _digest(
                    effective_policy_hash, "effective_policy_hash"
                ),
                "descriptor_hash": _digest(descriptor_hash, "descriptor_hash"),
                "lens_id": lens_id,
                "slot_index": index,
            }
            seed_digest = hash_payload(seed_payload).split(":", 1)[1]
            candidate_seed = int(seed_digest[:16], 16) % (2**31 - 1)
            slots.append({
                "slot_index": index,
                "lens_id": lens_id,
                "lens_hash": lens.definition_hash,
                "candidate_seed": candidate_seed,
                "revision_budget": revision,
            })
        reason = {
            "mode": portfolio.mode,
            "router_receipt_hash": router_receipt_hash,
            "allocator_receipt_hash": allocator_receipt_hash,
            "portfolio_control_receipt_hash": (
                portfolio_control_receipt_hash
            ),
        }
        return cls(
            effective_policy_hash=_digest(
                effective_policy_hash, "effective_policy_hash"
            ),
            descriptor_hash=_digest(descriptor_hash, "descriptor_hash"),
            portfolio_hash=portfolio.effective_portfolio_hash,
            run_seed=seed,
            candidate_budget=portfolio.candidate_budget,
            slots=tuple(slots),
            allocation_reason=reason,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AllocationPlan":
        payload = deepcopy(dict(raw))
        _exact_fields(
            payload,
            {
                "schema_version",
                "effective_policy_hash",
                "descriptor_hash",
                "portfolio_hash",
                "run_seed",
                "candidate_budget",
                "slots",
                "allocation_reason",
                "plan_hash",
            },
            "allocation plan",
        )
        if payload["schema_version"] != ALLOCATION_PLAN_SCHEMA:
            raise PortfolioValidationError("allocation plan schema mismatch")
        budget = _non_negative_int(payload["candidate_budget"], "candidate_budget")
        if not isinstance(payload["slots"], list) or len(payload["slots"]) != budget:
            raise PortfolioValidationError("allocation plan slot count mismatch")
        expected_slot_fields = {
            "slot_index",
            "lens_id",
            "lens_hash",
            "candidate_seed",
            "revision_budget",
        }
        slots = []
        for expected_index, raw_slot in enumerate(payload["slots"]):
            if not isinstance(raw_slot, Mapping):
                raise PortfolioValidationError("allocation slot must be an object")
            slot = deepcopy(dict(raw_slot))
            _exact_fields(slot, expected_slot_fields, "allocation slot")
            if _non_negative_int(slot["slot_index"], "slot_index") != expected_index:
                raise PortfolioValidationError("allocation slots must be contiguous")
            _identifier(slot["lens_id"], "slot lens_id")
            _digest(slot["lens_hash"], "slot lens_hash")
            _non_negative_int(slot["candidate_seed"], "candidate_seed")
            _non_negative_int(slot["revision_budget"], "revision_budget")
            slots.append(slot)
        reason = payload["allocation_reason"]
        if not isinstance(reason, Mapping) or set(reason) != {
            "mode",
            "router_receipt_hash",
            "allocator_receipt_hash",
            "portfolio_control_receipt_hash",
        }:
            raise PortfolioValidationError("allocation reason fields mismatch")
        if reason["mode"] not in PORTFOLIO_MODES:
            raise PortfolioValidationError("allocation reason mode mismatch")
        _digest(reason["router_receipt_hash"], "router_receipt_hash")
        _digest(
            reason["allocator_receipt_hash"],
            "allocator_receipt_hash",
        )
        _digest(
            reason["portfolio_control_receipt_hash"],
            "portfolio_control_receipt_hash",
        )
        value = cls(
            effective_policy_hash=_digest(
                payload["effective_policy_hash"], "effective_policy_hash"
            ),
            descriptor_hash=_digest(payload["descriptor_hash"], "descriptor_hash"),
            portfolio_hash=_digest(payload["portfolio_hash"], "portfolio_hash"),
            run_seed=_non_negative_int(payload["run_seed"], "run_seed"),
            candidate_budget=budget,
            slots=tuple(slots),
            allocation_reason=deepcopy(dict(reason)),
        )
        if payload["plan_hash"] != value.plan_hash:
            raise PortfolioValidationError("allocation plan hash mismatch")
        return value

    @property
    def plan_hash(self) -> str:
        return hash_payload(self._body())

    def _body(self) -> dict[str, Any]:
        return {
            "schema_version": ALLOCATION_PLAN_SCHEMA,
            "effective_policy_hash": self.effective_policy_hash,
            "descriptor_hash": self.descriptor_hash,
            "portfolio_hash": self.portfolio_hash,
            "run_seed": self.run_seed,
            "candidate_budget": self.candidate_budget,
            "slots": [deepcopy(slot) for slot in self.slots],
            "allocation_reason": deepcopy(self.allocation_reason),
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._body()
        payload["plan_hash"] = self.plan_hash
        return payload


def build_implicit_homogeneous_portfolio(
    policy: PolicyState, registry: LensRegistry
) -> CandidatePortfolio:
    """Map a Policy V2 candidate count/lens to an explicit frozen portfolio."""
    lens_id = str(policy.configuration["prompt_lens_id"])
    if lens_id not in registry.lenses:
        raise PortfolioValidationError("policy prompt lens is absent from registry")
    candidate_budget = int(policy.configuration["n_candidates"])
    if candidate_budget > int(policy.budgets["max_llm_calls_per_case"]):
        raise PortfolioValidationError("policy candidates exceed its call budget")
    selection = str(policy.configuration["candidate_selection"])
    selector_ids = {
        "first_verified": "first_verified_v1",
        "verifier_guided": "oracle_then_minimality_v1",
    }
    if selection not in selector_ids:
        raise PortfolioValidationError(
            "Policy V2 critic-ranked selection is forbidden in formal ACP"
        )
    selector_id = selector_ids[selection]
    return CandidatePortfolio.create(
        portfolio_id=(
            f"implicit-{lens_id}-{candidate_budget}-"
            f"{selector_id}"
        ),
        mode="homogeneous",
        candidate_budget=candidate_budget,
        lens_registry_hash=registry.registry_hash,
        router_hash=hash_payload({"router": "implicit-homogeneous-v1"}),
        allocator_hash=hash_payload({"allocator": "fixed-policy-v2"}),
        selector_hash=hash_payload({"selector": selector_id}),
        semantic_signature_provider_hash=hash_payload({
            "provider": "measure-only-v1",
        }),
        duplicate_policy="measure_only",
        early_stop_mode="all_candidates",
        lens_ids=[lens_id] * candidate_budget,
    )


def build_candidate_portfolio_binding(
    portfolio: CandidatePortfolio,
) -> dict[str, str]:
    return {
        "portfolio_hash": portfolio.portfolio_hash,
        "effective_portfolio_hash": portfolio.effective_portfolio_hash,
        "lens_registry_hash": portfolio.lens_registry_hash,
        "router_hash": portfolio.router_hash,
        "allocator_hash": portfolio.allocator_hash,
        "selector_hash": portfolio.selector_hash,
        "semantic_signature_provider_hash": (
            portfolio.semantic_signature_provider_hash
        ),
    }
