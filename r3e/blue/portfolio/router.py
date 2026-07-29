"""Deterministic, descriptor-only lens routing for ACP-2."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping

from r3e.memory.schema import (
    DESCRIPTOR_SCHEMA_VERSION,
    FailureDescriptor,
)
from r3e.protocol.hashing import hash_payload, read_json

from .schema import CandidatePortfolio, LensRegistry


DESCRIPTOR_ROUTER_SCHEMA = "r3e-descriptor-router-v1"
ROUTER_RECEIPT_SCHEMA = "r3e-descriptor-router-receipt-v1"
ROUTER_RULE_OPERATORS = {"equals", "one_of", "greater_than"}
ROUTER_FEATURE_FIELDS = {
    "sequential_context",
    "temporal_relation",
    "cycle_offset_bucket",
    "assignment_type",
    "cone_depth_bucket",
    "mismatch_pattern",
    "first_divergence_bucket",
}
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


class DescriptorRouterViolation(RuntimeError):
    """Raised when descriptor routing is incomplete or non-reconstructable."""


def _identifier(value: Any, field: str) -> str:
    text = str(value or "")
    if not _ID_RE.fullmatch(text):
        raise DescriptorRouterViolation(f"{field} must be a stable identifier")
    return text


def _exact_fields(
    raw: Mapping[str, Any],
    fields: set[str],
    name: str,
) -> None:
    if set(raw) != fields:
        raise DescriptorRouterViolation(f"{name} fields mismatch")


def _positive_score(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise DescriptorRouterViolation(f"{field} must be a positive integer")
    try:
        score = int(value)
    except (TypeError, ValueError) as exc:
        raise DescriptorRouterViolation(
            f"{field} must be a positive integer"
        ) from exc
    if score <= 0:
        raise DescriptorRouterViolation(f"{field} must be positive")
    return score


@dataclass(frozen=True)
class DescriptorRouter:
    router_id: str
    generic_lens_id: str
    specialist_lens_ids: tuple[str, ...]
    tie_break_order: tuple[str, ...]
    orthogonal_priority: dict[str, tuple[str, ...]]
    score_rules: tuple[dict[str, Any], ...]
    descriptor_schema_version: str = DESCRIPTOR_SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        *,
        router_id: str,
        descriptor_schema_version: str,
        generic_lens_id: str,
        specialist_lens_ids: list[str] | tuple[str, ...],
        tie_break_order: list[str] | tuple[str, ...],
        orthogonal_priority: Mapping[str, list[str] | tuple[str, ...]],
        score_rules: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    ) -> "DescriptorRouter":
        if descriptor_schema_version != DESCRIPTOR_SCHEMA_VERSION:
            raise DescriptorRouterViolation(
                "descriptor router schema authority mismatch"
            )
        generic = _identifier(generic_lens_id, "generic_lens_id")
        specialists = tuple(
            _identifier(value, "specialist_lens_id")
            for value in specialist_lens_ids
        )
        if (
            len(specialists) != 3
            or len(set(specialists)) != 3
            or generic in specialists
        ):
            raise DescriptorRouterViolation(
                "ACP-2 requires three distinct specialist lenses"
            )
        tie_break = tuple(
            _identifier(value, "tie_break lens") for value in tie_break_order
        )
        if set(tie_break) != set(specialists) or len(tie_break) != 3:
            raise DescriptorRouterViolation(
                "tie_break_order must be a specialist permutation"
            )
        if set(orthogonal_priority) != set(specialists):
            raise DescriptorRouterViolation(
                "orthogonal_priority specialist keys mismatch"
            )
        normalized_orthogonal: dict[str, tuple[str, ...]] = {}
        for primary in specialists:
            priorities = tuple(
                _identifier(value, "orthogonal lens")
                for value in orthogonal_priority[primary]
            )
            if (
                len(priorities) != 2
                or set(priorities) != (set(specialists) - {primary})
            ):
                raise DescriptorRouterViolation(
                    "orthogonal_priority must rank the other specialists"
                )
            normalized_orthogonal[primary] = priorities
        if not isinstance(score_rules, (list, tuple)) or not score_rules:
            raise DescriptorRouterViolation("descriptor score rules are required")
        normalized_rules = []
        rule_ids = set()
        for raw_rule in score_rules:
            if not isinstance(raw_rule, Mapping):
                raise DescriptorRouterViolation("score rule must be an object")
            rule = deepcopy(dict(raw_rule))
            _exact_fields(
                rule,
                {"rule_id", "field", "operator", "value", "scores"},
                "descriptor score rule",
            )
            rule_id = _identifier(rule["rule_id"], "rule_id")
            if rule_id in rule_ids:
                raise DescriptorRouterViolation("score rule IDs must be unique")
            rule_ids.add(rule_id)
            field = str(rule["field"])
            if field not in ROUTER_FEATURE_FIELDS:
                raise DescriptorRouterViolation(
                    "router rule field exceeds Grounded descriptor authority"
                )
            operator = str(rule["operator"])
            if operator not in ROUTER_RULE_OPERATORS:
                raise DescriptorRouterViolation(
                    "unsupported descriptor rule operator"
                )
            value = deepcopy(rule["value"])
            if operator == "one_of" and (
                not isinstance(value, list) or not value
            ):
                raise DescriptorRouterViolation(
                    "one_of descriptor rule requires a non-empty list"
                )
            scores = rule["scores"]
            if not isinstance(scores, Mapping) or not scores:
                raise DescriptorRouterViolation(
                    "descriptor score rule requires specialist scores"
                )
            if not set(scores) <= set(specialists):
                raise DescriptorRouterViolation(
                    "descriptor rule scores an unknown specialist"
                )
            normalized_rules.append({
                "rule_id": rule_id,
                "field": field,
                "operator": operator,
                "value": value,
                "scores": {
                    lens_id: _positive_score(
                        scores[lens_id],
                        f"{rule_id}.{lens_id}",
                    )
                    for lens_id in tie_break
                    if lens_id in scores
                },
            })
        return cls(
            router_id=_identifier(router_id, "router_id"),
            descriptor_schema_version=descriptor_schema_version,
            generic_lens_id=generic,
            specialist_lens_ids=specialists,
            tie_break_order=tie_break,
            orthogonal_priority=normalized_orthogonal,
            score_rules=tuple(normalized_rules),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DescriptorRouter":
        payload = deepcopy(dict(raw))
        _exact_fields(
            payload,
            {
                "schema_version",
                "router_id",
                "descriptor_schema_version",
                "generic_lens_id",
                "specialist_lens_ids",
                "tie_break_order",
                "orthogonal_priority",
                "score_rules",
                "router_hash",
            },
            "descriptor router",
        )
        if payload["schema_version"] != DESCRIPTOR_ROUTER_SCHEMA:
            raise DescriptorRouterViolation("descriptor router schema mismatch")
        value = cls.create(
            router_id=payload["router_id"],
            descriptor_schema_version=payload[
                "descriptor_schema_version"
            ],
            generic_lens_id=payload["generic_lens_id"],
            specialist_lens_ids=payload["specialist_lens_ids"],
            tie_break_order=payload["tie_break_order"],
            orthogonal_priority=payload["orthogonal_priority"],
            score_rules=payload["score_rules"],
        )
        if payload["router_hash"] != value.router_hash:
            raise DescriptorRouterViolation("descriptor router hash mismatch")
        return value

    def _body(self) -> dict[str, Any]:
        return {
            "schema_version": DESCRIPTOR_ROUTER_SCHEMA,
            "router_id": self.router_id,
            "descriptor_schema_version": self.descriptor_schema_version,
            "generic_lens_id": self.generic_lens_id,
            "specialist_lens_ids": list(self.specialist_lens_ids),
            "tie_break_order": list(self.tie_break_order),
            "orthogonal_priority": {
                lens_id: list(self.orthogonal_priority[lens_id])
                for lens_id in self.specialist_lens_ids
            },
            "score_rules": [
                deepcopy(rule) for rule in self.score_rules
            ],
        }

    @property
    def router_hash(self) -> str:
        return hash_payload(self._body())

    def to_dict(self) -> dict[str, Any]:
        payload = self._body()
        payload["router_hash"] = self.router_hash
        return payload

    @staticmethod
    def _matches(rule: Mapping[str, Any], value: Any) -> bool:
        operator = rule["operator"]
        expected = rule["value"]
        if operator == "equals":
            return value == expected
        if operator == "one_of":
            return value in expected
        if operator == "greater_than":
            return (
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and not isinstance(expected, bool)
                and isinstance(expected, (int, float))
                and value > expected
            )
        raise DescriptorRouterViolation(
            "unreachable descriptor rule operator"
        )

    def _validate_authority(
        self,
        portfolio: CandidatePortfolio,
        registry: LensRegistry,
    ) -> None:
        if portfolio.mode != "descriptor_routed":
            raise DescriptorRouterViolation(
                "descriptor router requires descriptor_routed portfolio"
            )
        if portfolio.candidate_budget != 3:
            raise DescriptorRouterViolation(
                "ACP-2 descriptor router requires exactly three slots"
            )
        if portfolio.router_hash != self.router_hash:
            raise DescriptorRouterViolation(
                "portfolio is bound to a different descriptor router"
            )
        if portfolio.lens_registry_hash != registry.registry_hash:
            raise DescriptorRouterViolation(
                "descriptor router lens registry binding mismatch"
            )
        required = {
            self.generic_lens_id,
            *self.specialist_lens_ids,
        }
        if (
            not required <= set(portfolio.lens_ids)
            or not required <= set(registry.lenses)
        ):
            raise DescriptorRouterViolation(
                "descriptor portfolio lacks an authorized router lens"
            )

    def route(
        self,
        descriptor: FailureDescriptor | Mapping[str, Any],
        *,
        portfolio: CandidatePortfolio,
        registry: LensRegistry,
    ) -> dict[str, Any]:
        self._validate_authority(portfolio, registry)
        failure = (
            descriptor
            if isinstance(descriptor, FailureDescriptor)
            else FailureDescriptor.from_dict(descriptor)
        )
        scores = {lens_id: 0 for lens_id in self.tie_break_order}
        matched_rule_ids = []
        for rule in self.score_rules:
            if self._matches(
                rule,
                failure.features.get(rule["field"]),
            ):
                matched_rule_ids.append(rule["rule_id"])
                for lens_id, score in rule["scores"].items():
                    scores[lens_id] += int(score)
        primary = min(
            self.tie_break_order,
            key=lambda lens_id: (
                -scores[lens_id],
                self.tie_break_order.index(lens_id),
            ),
        )
        orthogonal = self.orthogonal_priority[primary][0]
        allocated = [self.generic_lens_id, primary, orthogonal]
        payload = {
            "schema_version": ROUTER_RECEIPT_SCHEMA,
            "router_hash": self.router_hash,
            "descriptor_hash": failure.descriptor_hash,
            "specialist_scores": {
                lens_id: scores[lens_id]
                for lens_id in self.tie_break_order
            },
            "matched_rule_ids": matched_rule_ids,
            "primary_lens_id": primary,
            "orthogonal_lens_id": orthogonal,
            "allocated_lens_ids": allocated,
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
        _exact_fields(
            payload,
            {
                "schema_version",
                "router_hash",
                "descriptor_hash",
                "specialist_scores",
                "matched_rule_ids",
                "primary_lens_id",
                "orthogonal_lens_id",
                "allocated_lens_ids",
                "receipt_hash",
            },
            "descriptor router receipt",
        )
        expected = self.route(
            descriptor,
            portfolio=portfolio,
            registry=registry,
        )
        if payload != expected:
            raise DescriptorRouterViolation(
                "descriptor router receipt is not reconstructable"
            )
        return payload


def load_descriptor_router(path: str | Path) -> DescriptorRouter:
    return DescriptorRouter.from_dict(read_json(path))
