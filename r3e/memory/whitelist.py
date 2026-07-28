"""Frozen control-memory whitelist loading and validation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from r3e.protocol.hashing import hash_payload, read_json

from .schema import ANALYZERS, CONTROL_DELTA_FIELDS


WHITELIST_SCHEMA_VERSION = "r3e-memory-control-whitelist-v1"


class MemoryWhitelistViolation(RuntimeError):
    """Raised when the formal control whitelist is changed or broadened."""


def default_whitelist() -> dict[str, Any]:
    body = {
        "schema_version": WHITELIST_SCHEMA_VERSION,
        "allowed_control_fields": sorted(CONTROL_DELTA_FIELDS),
        "allowed_analyzers": sorted(ANALYZERS),
        "forbid_prompt_payload": True,
        "forbid_authority_changes": True,
        "forbid_model_routing": True,
        "forbid_ast_rewrite": True,
    }
    return {**body, "whitelist_hash": hash_payload(body)}


def validate_whitelist(raw: dict[str, Any]) -> dict[str, Any]:
    expected_fields = {
        "schema_version", "allowed_control_fields", "allowed_analyzers",
        "forbid_prompt_payload", "forbid_authority_changes",
        "forbid_model_routing", "forbid_ast_rewrite", "whitelist_hash",
    }
    if set(raw) != expected_fields:
        raise MemoryWhitelistViolation("control whitelist fields mismatch")
    body = {key: value for key, value in raw.items() if key != "whitelist_hash"}
    if raw.get("whitelist_hash") != hash_payload(body):
        raise MemoryWhitelistViolation("control whitelist hash mismatch")
    if raw.get("schema_version") != WHITELIST_SCHEMA_VERSION:
        raise MemoryWhitelistViolation("control whitelist schema mismatch")
    if not set(raw["allowed_control_fields"]).issubset(CONTROL_DELTA_FIELDS):
        raise MemoryWhitelistViolation("whitelist contains unknown control fields")
    if not set(raw["allowed_analyzers"]).issubset(ANALYZERS):
        raise MemoryWhitelistViolation("whitelist contains unknown analyzers")
    if not all(
        raw.get(field) is True
        for field in (
            "forbid_prompt_payload",
            "forbid_authority_changes",
            "forbid_model_routing",
            "forbid_ast_rewrite",
        )
    ):
        raise MemoryWhitelistViolation("formal deny rules must remain enabled")
    return dict(raw)


def load_whitelist(path: str | Path) -> dict[str, Any]:
    return validate_whitelist(read_json(path))
