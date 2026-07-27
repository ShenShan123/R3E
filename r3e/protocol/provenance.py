"""Frozen per-round provenance binding code, policy, registry, and toolchain."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .hashing import hash_payload, utc_now


RUN_CONTEXT_SCHEMA_VERSION = "r3e-round-provenance-v1"


class RunContextViolation(RuntimeError):
    """Raised when frozen round provenance cannot be reconstructed."""


def build_run_context(
    *,
    round_id: str,
    code_version: str,
    round_config: Mapping[str, Any],
    registry_hash: str,
    active_policy_id: str,
    active_policy_hash: str,
    toolchain_fingerprint: Mapping[str, Any],
) -> dict[str, Any]:
    if not round_id or not code_version:
        raise RunContextViolation("round_id and code_version are required")
    if not registry_hash or not active_policy_hash:
        raise RunContextViolation("registry and active policy hashes are required")
    toolchain = deepcopy(dict(toolchain_fingerprint))
    payload = {
        "schema_version": RUN_CONTEXT_SCHEMA_VERSION,
        "round_id": round_id,
        "code_version": code_version,
        "round_config_hash": hash_payload(round_config),
        "registry_hash_before": registry_hash,
        "active_policy_id": active_policy_id,
        "active_policy_hash": active_policy_hash,
        "toolchain_fingerprint": toolchain,
        "toolchain_fingerprint_hash": hash_payload(toolchain),
        "created_at": utc_now(),
    }
    payload["run_context_hash"] = hash_payload({
        key: value
        for key, value in payload.items()
        if key not in {"created_at", "run_context_hash"}
    })
    return payload


def verify_run_context(context: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(context))
    allowed = {
        "schema_version",
        "round_id",
        "code_version",
        "round_config_hash",
        "registry_hash_before",
        "active_policy_id",
        "active_policy_hash",
        "toolchain_fingerprint",
        "toolchain_fingerprint_hash",
        "created_at",
        "run_context_hash",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise RunContextViolation(f"undeclared run context fields: {sorted(unknown)}")
    if payload.get("schema_version") != RUN_CONTEXT_SCHEMA_VERSION:
        raise RunContextViolation("run context schema mismatch")
    toolchain = payload.get("toolchain_fingerprint")
    if not isinstance(toolchain, dict):
        raise RunContextViolation("toolchain fingerprint must be an object")
    if payload.get("toolchain_fingerprint_hash") != hash_payload(toolchain):
        raise RunContextViolation("toolchain fingerprint hash mismatch")
    expected = hash_payload({
        key: value
        for key, value in payload.items()
        if key not in {"created_at", "run_context_hash"}
    })
    if payload.get("run_context_hash") != expected:
        raise RunContextViolation("run context hash mismatch")
    return payload
