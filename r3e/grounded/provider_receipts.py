"""Hash-bound receipts from runner-owned artifact parsers/providers."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any, Mapping

from r3e.protocol.hashing import hash_file, hash_payload


PROVIDER_RECEIPT_SCHEMA_VERSION = "r3e-grounded-provider-receipt-v1"
PROVIDER_KINDS = {
    "oracle_parser",
    "semantic_parser",
    "failure_descriptor",
    "formal_parser",
}
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class GroundedProviderViolation(RuntimeError):
    """Raised when a provider receipt cannot be reconstructed."""


def provider_implementation_hash(
    *,
    provider_kind: str,
    provider_id: str,
    provider_version: str,
) -> str:
    """Bind a frozen provider ID to the exact local parser implementation."""
    grounded_root = Path(__file__).resolve().parent
    red_grounded_root = grounded_root.parent / "red" / "grounded"
    implementations = {
        ("oracle_parser", "r3e-stdout-oracle", "1"): [
            grounded_root / "icarus.py",
        ],
        ("semantic_parser", "r3e-verilog-token-ast", "1"): [
            red_grounded_root / "verilog_ast.py",
            red_grounded_root / "materializers.py",
        ],
        ("semantic_parser", "r3e-verilog-operator-ast", "2"): [
            red_grounded_root / "verilog_ast.py",
            red_grounded_root / "operator_ast.py",
            red_grounded_root / "materializers.py",
        ],
        ("formal_parser", "r3e-yosys-sat-formal", "1"): [
            grounded_root / "yosys_formal.py",
        ],
    }
    paths = implementations.get((provider_kind, provider_id, provider_version))
    if not paths or any(not path.is_file() for path in paths):
        raise GroundedProviderViolation(
            "provider implementation is not in the frozen allowlist"
        )
    return hash_payload({
        "provider_kind": provider_kind,
        "provider_id": provider_id,
        "provider_version": provider_version,
        "source_hashes": {
            path.name: hash_file(path) for path in sorted(paths)
        },
    })


def build_provider_receipt(
    *,
    provider_kind: str,
    provider_id: str,
    provider_version: str,
    input_artifact_hashes: Mapping[str, str],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    implementation_hash = provider_implementation_hash(
        provider_kind=provider_kind,
        provider_id=provider_id,
        provider_version=provider_version,
    )
    payload = {
        "schema_version": PROVIDER_RECEIPT_SCHEMA_VERSION,
        "provider_kind": provider_kind,
        "provider_id": provider_id,
        "provider_version": provider_version,
        "provider_hash": hash_payload({
            "provider_kind": provider_kind,
            "provider_id": provider_id,
            "provider_version": provider_version,
            "provider_implementation_hash": implementation_hash,
        }),
        "provider_implementation_hash": implementation_hash,
        "input_artifact_hashes": deepcopy(dict(input_artifact_hashes)),
        "result": deepcopy(dict(result)),
    }
    payload["receipt_hash"] = hash_payload(payload)
    return verify_provider_receipt(payload)


def verify_provider_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(receipt))
    required = {
        "schema_version",
        "provider_kind",
        "provider_id",
        "provider_version",
        "provider_hash",
        "provider_implementation_hash",
        "input_artifact_hashes",
        "result",
        "receipt_hash",
    }
    if set(payload) != required:
        raise GroundedProviderViolation("provider receipt fields mismatch")
    if payload["schema_version"] != PROVIDER_RECEIPT_SCHEMA_VERSION:
        raise GroundedProviderViolation("provider receipt schema mismatch")
    if payload["provider_kind"] not in PROVIDER_KINDS:
        raise GroundedProviderViolation("provider kind is unsupported")
    for field in ("provider_id", "provider_version"):
        if not isinstance(payload[field], str) or not payload[field]:
            raise GroundedProviderViolation(f"{field} must be non-empty")
    implementation_hash = provider_implementation_hash(
        provider_kind=payload["provider_kind"],
        provider_id=payload["provider_id"],
        provider_version=payload["provider_version"],
    )
    if payload["provider_implementation_hash"] != implementation_hash:
        raise GroundedProviderViolation(
            "provider implementation hash mismatch"
        )
    expected_provider_hash = hash_payload({
        "provider_kind": payload["provider_kind"],
        "provider_id": payload["provider_id"],
        "provider_version": payload["provider_version"],
        "provider_implementation_hash": implementation_hash,
    })
    if payload["provider_hash"] != expected_provider_hash:
        raise GroundedProviderViolation("provider implementation hash mismatch")
    artifacts = payload["input_artifact_hashes"]
    if not isinstance(artifacts, dict) or not artifacts:
        raise GroundedProviderViolation("provider input artifacts are missing")
    for name, digest in artifacts.items():
        if (
            not isinstance(name, str)
            or not name
            or not _HASH_RE.fullmatch(str(digest or ""))
        ):
            raise GroundedProviderViolation("provider artifact hash is invalid")
    if not isinstance(payload["result"], dict):
        raise GroundedProviderViolation("provider result must be an object")
    if payload["receipt_hash"] != hash_payload({
        key: value for key, value in payload.items() if key != "receipt_hash"
    }):
        raise GroundedProviderViolation("provider receipt hash mismatch")
    return payload
