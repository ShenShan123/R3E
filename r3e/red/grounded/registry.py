"""Frozen family, operator, and runtime-effect registries."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from r3e.protocol.hashing import hash_payload, read_json


FAMILY_REGISTRY_SCHEMA = "r3e-red-family-registry-v1"
OPERATOR_REGISTRY_SCHEMA = "r3e-red-operator-registry-v1"
EFFECT_REGISTRY_SCHEMA = "r3e-red-effect-registry-v1"


class GroundedRegistryViolation(RuntimeError):
    """Raised when a frozen Grounded Red registry is invalid."""


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise GroundedRegistryViolation(f"{field} must be a non-empty string")
    return value


def _string_list(value: Any, field: str, *, non_empty: bool = True) -> list[str]:
    if (
        not isinstance(value, list)
        or (non_empty and not value)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise GroundedRegistryViolation(
            f"{field} must be a unique non-empty string list"
        )
    return list(value)


def _load_registry(
    path: str | Path,
    *,
    schema: str,
    collection: str,
    item_fields: set[str],
    identity_field: str,
) -> tuple[dict[str, dict[str, Any]], str]:
    payload = read_json(path)
    if set(payload) != {
        "schema_version",
        "registry_version",
        "frozen",
        collection,
    }:
        raise GroundedRegistryViolation(f"{collection} registry fields mismatch")
    if payload["schema_version"] != schema or payload["frozen"] is not True:
        raise GroundedRegistryViolation(
            f"{collection} registry must be frozen {schema}"
        )
    if (
        not isinstance(payload["registry_version"], int)
        or isinstance(payload["registry_version"], bool)
        or payload["registry_version"] < 1
    ):
        raise GroundedRegistryViolation("registry_version must be positive")
    rows = payload[collection]
    if not isinstance(rows, list) or not rows:
        raise GroundedRegistryViolation(f"{collection} registry is empty")
    indexed: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, Mapping) or set(raw) != item_fields:
            raise GroundedRegistryViolation(
                f"{collection} definition fields mismatch"
            )
        row = deepcopy(dict(raw))
        identity = _non_empty_string(row.get(identity_field), identity_field)
        if identity in indexed:
            raise GroundedRegistryViolation(
                f"duplicate {identity_field}: {identity}"
            )
        indexed[identity] = row
    return indexed, hash_payload(payload)


@dataclass(frozen=True)
class GroundedRegistryBundle:
    families: dict[str, dict[str, Any]]
    operators: dict[str, dict[str, Any]]
    effects: dict[str, dict[str, Any]]
    family_registry_hash: str
    operator_registry_hash: str
    effect_registry_hash: str

    @property
    def registry_bundle_hash(self) -> str:
        return hash_payload({
            "family_registry_hash": self.family_registry_hash,
            "operator_registry_hash": self.operator_registry_hash,
            "effect_registry_hash": self.effect_registry_hash,
        })


def load_grounded_registries(
    *,
    family_registry: str | Path,
    operator_registry: str | Path,
    effect_registry: str | Path,
) -> GroundedRegistryBundle:
    families, family_hash = _load_registry(
        family_registry,
        schema=FAMILY_REGISTRY_SCHEMA,
        collection="families",
        identity_field="family_id",
        item_fields={
            "family_id",
            "family_group",
            "description",
            "required_context",
            "supported_oracles",
            "minimum_semantic_proof",
        },
    )
    for family in families.values():
        for field in ("family_group", "description"):
            _non_empty_string(family.get(field), field)
        for field in (
            "required_context",
            "supported_oracles",
            "minimum_semantic_proof",
        ):
            _string_list(family.get(field), field)
    operators, operator_hash = _load_registry(
        operator_registry,
        schema=OPERATOR_REGISTRY_SCHEMA,
        collection="operators",
        identity_field="operator_id",
        item_fields={
            "operator_id",
            "supported_family_ids",
            "preconditions",
            "maximum_changed_modules",
            "maximum_changed_blocks",
            "maximum_ast_edits",
            "inverse_operator",
            "executor",
        },
    )
    for operator in operators.values():
        family_ids = _string_list(
            operator.get("supported_family_ids"), "supported_family_ids"
        )
        unknown = set(family_ids) - families.keys()
        if unknown:
            raise GroundedRegistryViolation(
                f"operator references unknown families: {sorted(unknown)}"
            )
        _string_list(operator.get("preconditions"), "preconditions")
        for field in (
            "maximum_changed_modules",
            "maximum_changed_blocks",
            "maximum_ast_edits",
        ):
            value = operator.get(field)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 1
            ):
                raise GroundedRegistryViolation(f"{field} must be positive")
        _non_empty_string(operator.get("inverse_operator"), "inverse_operator")
        if operator.get("executor") not in {"ast_template", "structure_template"}:
            raise GroundedRegistryViolation("operator executor is not frozen")
    effects, effect_hash = _load_registry(
        effect_registry,
        schema=EFFECT_REGISTRY_SCHEMA,
        collection="effects",
        identity_field="effect_id",
        item_fields={
            "effect_id",
            "supported_family_ids",
            "observable_features",
        },
    )
    for effect in effects.values():
        family_ids = _string_list(
            effect.get("supported_family_ids"), "supported_family_ids"
        )
        unknown = set(family_ids) - families.keys()
        if unknown:
            raise GroundedRegistryViolation(
                f"effect references unknown families: {sorted(unknown)}"
            )
        _string_list(effect.get("observable_features"), "observable_features")
    if len(families) < 20:
        raise GroundedRegistryViolation("Grounded Red V1 requires 20+ families")
    return GroundedRegistryBundle(
        families=families,
        operators=operators,
        effects=effects,
        family_registry_hash=family_hash,
        operator_registry_hash=operator_hash,
        effect_registry_hash=effect_hash,
    )
