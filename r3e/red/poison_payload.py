"""Immutable poison payload binding across red, validity and blue stages."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from r3e.protocol.hashing import hash_payload


POISON_PAYLOAD_SCHEMA_VERSION = "r3e-poison-payload-v1"
_ADAPTER_METADATA = {
    "output_schema_version",
    "model_id",
    "budget_hash",
    "verifier_hash",
    "toolchain_fingerprint_hash",
    "command_hash",
    "result_hash",
}
_DOWNSTREAM_FIELDS = {
    "formal_status",
    "golden_compile_ok",
    "golden_oracle_ok",
    "buggy_compile_ok",
    "buggy_functional_fail",
    "output_complete",
    "revert_oracle_ok",
    "fresh_output",
    "oracle_result_hash",
    "counterexample_hash",
    "validity",
    "challenged_policy_id",
    "repair_attempts",
    "repair_successes",
    "hardness",
    "hardness_class",
    "blue_results",
    "challenge_budget_hash",
    "challenge_result_hash",
    "validity_adapter_output",
    "grounded_execution_bundle",
    "grounded_authority_bundle",
    "formal_rejection",
}


class PoisonPayloadViolation(RuntimeError):
    """Raised when a downstream stage changes a generated poison."""


def poison_payload_body(poison: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in poison.items()
        if key not in _ADAPTER_METADATA
        and key not in _DOWNSTREAM_FIELDS
        and key not in {"poison_payload_hash", "poison_payload_schema_version"}
    }


def bind_poison_payload(poison: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(poison))
    if "poison_payload_hash" in result:
        verify_poison_payload(result)
        return result
    result["poison_payload_schema_version"] = POISON_PAYLOAD_SCHEMA_VERSION
    result["poison_payload_hash"] = hash_payload(poison_payload_body(result))
    return result


def verify_poison_payload(poison: Mapping[str, Any]) -> str:
    if poison.get("poison_payload_schema_version") != POISON_PAYLOAD_SCHEMA_VERSION:
        raise PoisonPayloadViolation("poison payload schema mismatch")
    expected = hash_payload(poison_payload_body(poison))
    if poison.get("poison_payload_hash") != expected:
        raise PoisonPayloadViolation("poison payload hash mismatch")
    return expected
