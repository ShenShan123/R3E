"""Fail-closed schemas and provenance for evolution-adapter outputs."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

from r3e.protocol.hashing import hash_payload


ADAPTER_CONFORMANCE_VERSION = "r3e-evolution-adapter-v1"
TOOLCHAIN_FINGERPRINT_VERSION = "r3e-adapter-toolchain-v1"
OUTPUT_SCHEMAS = {
    "generate_red": "r3e-red-candidate-v1",
    "generate_blue_candidate": "r3e-blue-candidate-provider-receipt-v1",
    "prepare_validity": "r3e-validity-evidence-v2",
    "evaluate_blue": "r3e-blue-evaluation-v1",
    "probe_learnability": "r3e-learnability-probe-v1",
    "screen_child": "r3e-child-screening-v1",
    "replay": "r3e-paired-replay-evaluation-v1",
}
OUTPUT_METADATA_FIELDS = {
    "output_schema_version",
    "model_id",
    "budget_hash",
    "verifier_hash",
    "toolchain_fingerprint_hash",
    "command_hash",
    "result_hash",
}
_TOOLCHAIN_FIELDS = {
    "schema_version",
    "adapter_id",
    "adapter_version",
    "model_id",
    "model_hash",
    "verifier_id",
    "verifier_hash",
    "runtime_id",
    "runtime_hash",
}
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class AdapterConformanceViolation(RuntimeError):
    """Raised when an adapter or one of its outputs is not reconstructable."""


def _digest(value: Any, *, field: str) -> str:
    text = str(value or "")
    if not _HASH_RE.fullmatch(text):
        raise AdapterConformanceViolation(f"{field} must be an exact sha256 digest")
    return text


def make_toolchain_fingerprint(
    *,
    adapter_id: str,
    adapter_version: str,
    model_id: str,
    model_version: str,
    verifier_id: str,
    verifier_version: str,
    runtime_id: str,
    runtime_version: str,
) -> dict[str, str]:
    """Build the exact adapter fingerprint accepted by formal rounds."""
    labels = {
        "adapter_id": adapter_id,
        "adapter_version": adapter_version,
        "model_id": model_id,
        "model_version": model_version,
        "verifier_id": verifier_id,
        "verifier_version": verifier_version,
        "runtime_id": runtime_id,
        "runtime_version": runtime_version,
    }
    if any(not isinstance(value, str) or not value for value in labels.values()):
        raise AdapterConformanceViolation("toolchain identity fields must be non-empty")
    return {
        "schema_version": TOOLCHAIN_FINGERPRINT_VERSION,
        "adapter_id": adapter_id,
        "adapter_version": adapter_version,
        "model_id": model_id,
        "model_hash": hash_payload({
            "model_id": model_id,
            "model_version": model_version,
        }),
        "verifier_id": verifier_id,
        "verifier_hash": hash_payload({
            "verifier_id": verifier_id,
            "verifier_version": verifier_version,
        }),
        "runtime_id": runtime_id,
        "runtime_hash": hash_payload({
            "runtime_id": runtime_id,
            "runtime_version": runtime_version,
        }),
    }


def validate_toolchain_fingerprint(
    fingerprint: Mapping[str, Any],
) -> dict[str, str]:
    if not isinstance(fingerprint, Mapping):
        raise AdapterConformanceViolation(
            "adapter toolchain fingerprint must be an object"
        )
    payload = deepcopy(dict(fingerprint))
    if set(payload) != _TOOLCHAIN_FIELDS:
        raise AdapterConformanceViolation("adapter toolchain fingerprint fields mismatch")
    if payload.get("schema_version") != TOOLCHAIN_FINGERPRINT_VERSION:
        raise AdapterConformanceViolation("adapter toolchain fingerprint schema mismatch")
    for field in (
        "adapter_id",
        "adapter_version",
        "model_id",
        "verifier_id",
        "runtime_id",
    ):
        if not isinstance(payload.get(field), str) or not payload[field]:
            raise AdapterConformanceViolation(
                f"adapter toolchain {field} must be non-empty"
            )
    for field in ("model_hash", "verifier_hash", "runtime_hash"):
        _digest(payload.get(field), field=field)
    return payload


def bind_adapter_output(
    payload: Mapping[str, Any],
    operation: str,
    toolchain_fingerprint: Mapping[str, Any],
    *,
    model_id: str | None = None,
    budget_hash: str | None = None,
    verifier_hash: str | None = None,
    command_hash: str | None = None,
) -> dict[str, Any]:
    """Bind one method result to its operation and immutable provenance."""
    if operation not in OUTPUT_SCHEMAS:
        raise AdapterConformanceViolation(f"unknown adapter operation: {operation}")
    fingerprint = validate_toolchain_fingerprint(toolchain_fingerprint)
    result = {
        key: deepcopy(value)
        for key, value in dict(payload).items()
        if key not in OUTPUT_METADATA_FIELDS
    }
    bound_model = str(
        model_id
        or payload.get("model_id")
        or fingerprint["model_id"]
    )
    if not bound_model:
        raise AdapterConformanceViolation("adapter output model_id is required")
    bound_budget = str(
        budget_hash
        or payload.get("budget_hash")
        or hash_payload({
            "adapter_id": fingerprint["adapter_id"],
            "operation": operation,
            "budget": "frozen-default",
        })
    )
    bound_verifier = str(
        verifier_hash
        or payload.get("verifier_hash")
        or fingerprint["verifier_hash"]
    )
    bound_command = str(
        command_hash
        or payload.get("command_hash")
        or hash_payload({
            "adapter_id": fingerprint["adapter_id"],
            "operation": operation,
            "command": "deterministic-interface",
        })
    )
    result.update({
        "output_schema_version": OUTPUT_SCHEMAS[operation],
        "model_id": bound_model,
        "budget_hash": _digest(bound_budget, field="budget_hash"),
        "verifier_hash": _digest(bound_verifier, field="verifier_hash"),
        "toolchain_fingerprint_hash": hash_payload(fingerprint),
        "command_hash": _digest(bound_command, field="command_hash"),
    })
    result["result_hash"] = hash_payload(result)
    return result


def strip_adapter_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return only the operation-specific payload after conformance checking."""
    return {
        key: deepcopy(value)
        for key, value in payload.items()
        if key not in OUTPUT_METADATA_FIELDS
    }


def adapter_output_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(payload.get(key))
        for key in sorted(OUTPUT_METADATA_FIELDS)
    }


def _require_fields(
    payload: Mapping[str, Any],
    required: set[str],
    *,
    operation: str,
) -> None:
    missing = required - payload.keys()
    if missing:
        raise AdapterConformanceViolation(
            f"{operation} output missing fields: {sorted(missing)}"
        )


def _validate_operation_payload(operation: str, payload: Mapping[str, Any]) -> None:
    if operation == "generate_blue_candidate":
        _require_fields(
            payload,
            {
                "candidate_id",
                "slot_index",
                "lens_id",
                "lens_hash",
                "candidate_seed",
                "prompt_hash",
                "current_case_evidence_hash",
                "raw_response_hash",
                "patch_payload",
                "patch_payload_hash",
                "input_tokens",
                "output_tokens",
            },
            operation=operation,
        )
        forbidden = {
            "selected_candidate_id",
            "winner",
            "rank",
            "score",
            "oracle_ok",
            "parse_ok",
            "scope_ok",
            "compile_ok",
            "formal_ok",
            "verification_hash",
            "selection_hash",
        }
        leaked = forbidden & payload.keys()
        if leaked:
            raise AdapterConformanceViolation(
                "blue candidate provider attempted an authority decision: "
                f"{sorted(leaked)}"
            )
        for field in ("candidate_id", "lens_id"):
            if not isinstance(payload.get(field), str) or not payload[field]:
                raise AdapterConformanceViolation(
                    f"{operation}.{field} must be non-empty"
                )
        for field in (
            "lens_hash",
            "prompt_hash",
            "current_case_evidence_hash",
            "raw_response_hash",
            "patch_payload_hash",
        ):
            _digest(payload.get(field), field=f"{operation}.{field}")
        for field in (
            "slot_index",
            "candidate_seed",
            "input_tokens",
            "output_tokens",
        ):
            value = payload.get(field)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise AdapterConformanceViolation(
                    f"{operation}.{field} must be a non-negative integer"
                )
        patch_payload = payload.get("patch_payload")
        if not isinstance(patch_payload, Mapping):
            raise AdapterConformanceViolation(
                "generate_blue_candidate.patch_payload must be an object"
            )
        if payload["patch_payload_hash"] != hash_payload(patch_payload):
            raise AdapterConformanceViolation(
                "generate_blue_candidate patch payload hash mismatch"
            )
    elif operation == "generate_red":
        _require_fields(
            payload,
            {
                "poison_id",
                "golden_rtl",
                "buggy_rtl",
                "challenged_policy_hash",
                "family",
                "effect",
                "affected_role",
                "edit_scope",
                "normalized_diff_hash",
            },
            operation=operation,
        )
        for field in (
            "poison_id",
            "golden_rtl",
            "buggy_rtl",
            "family",
            "effect",
            "affected_role",
            "edit_scope",
        ):
            if not isinstance(payload.get(field), str) or not payload[field]:
                raise AdapterConformanceViolation(
                    f"{operation}.{field} must be non-empty"
                )
        _digest(payload.get("challenged_policy_hash"), field="challenged_policy_hash")
        _digest(payload.get("normalized_diff_hash"), field="normalized_diff_hash")
    elif operation == "prepare_validity":
        _require_fields(
            payload,
            {
                "poison_id",
                "challenged_policy_hash",
                "poison_payload_hash",
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
                "toolchain_fingerprint_hash",
                "command_hash",
            },
            operation=operation,
        )
        _digest(payload.get("poison_payload_hash"), field="poison_payload_hash")
        for field in (
            "golden_compile_ok",
            "golden_oracle_ok",
            "buggy_compile_ok",
            "buggy_functional_fail",
            "output_complete",
            "revert_oracle_ok",
            "fresh_output",
        ):
            if not isinstance(payload.get(field), bool):
                raise AdapterConformanceViolation(
                    f"{operation}.{field} must be boolean"
                )
    elif operation in {"evaluate_blue", "replay"}:
        _require_fields(
            payload,
            {"policy_hash", "seed", "oracle_ok"},
            operation=operation,
        )
        _digest(payload.get("policy_hash"), field="policy_hash")
        if not isinstance(payload.get("seed"), int) or isinstance(
            payload.get("seed"), bool
        ):
            raise AdapterConformanceViolation(f"{operation}.seed must be an integer")
        if not isinstance(payload.get("oracle_ok"), bool):
            raise AdapterConformanceViolation(
                f"{operation}.oracle_ok must be boolean"
            )
        if operation == "replay" and (
            not isinstance(payload.get("cost"), (int, float))
            or isinstance(payload.get("cost"), bool)
            or float(payload["cost"]) < 0.0
        ):
            raise AdapterConformanceViolation("replay.cost must be non-negative")
    elif operation == "probe_learnability":
        _require_fields(
            payload,
            {
                "label",
                "challenged_policy_hash",
                "teacher_mode",
                "teacher_budget",
                "attempts",
                "successes",
                "budget_exhausted",
                "evidence",
            },
            operation=operation,
        )
        _digest(payload.get("challenged_policy_hash"), field="challenged_policy_hash")
    elif operation == "screen_child":
        _require_fields(payload, {"survive"}, operation=operation)
        if not isinstance(payload.get("survive"), bool):
            raise AdapterConformanceViolation("screen_child.survive must be boolean")


class AdapterConformanceGate:
    """Validate an adapter and every method result before runner admission."""

    def __init__(self, adapter: Any):
        fingerprints = getattr(adapter, "conformance_fingerprints", None)
        if fingerprints is None:
            fingerprint = getattr(adapter, "toolchain_fingerprint", None)
            fingerprints = [fingerprint] if fingerprint is not None else []
        if (
            not isinstance(fingerprints, (list, tuple))
            or not fingerprints
        ):
            raise AdapterConformanceViolation(
                "adapter must declare at least one toolchain fingerprint"
            )
        validated = [
            validate_toolchain_fingerprint(fingerprint)
            for fingerprint in fingerprints
        ]
        self.toolchains_by_hash = {
            hash_payload(fingerprint): fingerprint for fingerprint in validated
        }
        self.allowed_toolchain_hashes = {
            hash_payload(fingerprint) for fingerprint in validated
        }

    def validate(
        self,
        operation: str,
        raw: Mapping[str, Any],
    ) -> dict[str, Any]:
        if operation not in OUTPUT_SCHEMAS:
            raise AdapterConformanceViolation(
                f"unknown adapter operation: {operation}"
            )
        if not isinstance(raw, Mapping):
            raise AdapterConformanceViolation(
                f"{operation} output must be an object"
            )
        payload = deepcopy(dict(raw))
        _require_fields(payload, OUTPUT_METADATA_FIELDS, operation=operation)
        if payload.get("output_schema_version") != OUTPUT_SCHEMAS[operation]:
            raise AdapterConformanceViolation(
                f"{operation} output schema mismatch"
            )
        if not isinstance(payload.get("model_id"), str) or not payload["model_id"]:
            raise AdapterConformanceViolation(f"{operation}.model_id is required")
        for field in (
            "budget_hash",
            "verifier_hash",
            "toolchain_fingerprint_hash",
            "command_hash",
            "result_hash",
        ):
            _digest(payload.get(field), field=f"{operation}.{field}")
        if (
            payload["toolchain_fingerprint_hash"]
            not in self.allowed_toolchain_hashes
        ):
            raise AdapterConformanceViolation(
                f"{operation} output uses an undeclared toolchain fingerprint"
            )
        toolchain = self.toolchains_by_hash[
            payload["toolchain_fingerprint_hash"]
        ]
        if payload["model_id"] != toolchain["model_id"]:
            raise AdapterConformanceViolation(
                f"{operation} model does not match its declared toolchain"
            )
        if payload["verifier_hash"] != toolchain["verifier_hash"]:
            raise AdapterConformanceViolation(
                f"{operation} verifier does not match its declared toolchain"
            )
        body = {
            key: value for key, value in payload.items() if key != "result_hash"
        }
        if payload["result_hash"] != hash_payload(body):
            raise AdapterConformanceViolation(
                f"{operation} result hash mismatch"
            )
        _validate_operation_payload(operation, payload)
        return payload
