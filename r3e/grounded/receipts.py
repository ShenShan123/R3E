"""Content-addressed receipts emitted by the authoritative command runner."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping, Sequence

from r3e.protocol.hashing import hash_payload


COMMAND_RECEIPT_SCHEMA_VERSION = "r3e-grounded-command-receipt-v1"
RECEIPT_ISSUER = "r3e-grounded-command-runner-v1"
COMMAND_PHASES = {
    "parse",
    "elaborate",
    "compile",
    "simulation",
    "clean_baseline",
    "poison_execution",
    "revert_execution",
    "formal",
    "oracle",
    "minimization",
}
RESULT_KINDS = {
    "completed",
    "timeout",
    "crash",
    "tool_error",
    "resource_limit",
    "inconclusive",
}
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_FIELDS = {
    "schema_version",
    "issuer",
    "receipt_id",
    "phase",
    "subject_hash",
    "run_context_hash",
    "toolchain_fingerprint_hash",
    "command_argv",
    "working_directory_hash",
    "exit_code",
    "timed_out",
    "crashed",
    "result_kind",
    "wall_time_ms",
    "artifact_hashes",
    "observed",
    "receipt_hash",
}


class GroundedReceiptViolation(RuntimeError):
    """Raised when a command receipt cannot be reconstructed."""


def _digest(value: Any, field: str) -> str:
    text = str(value or "")
    if not _HASH_RE.fullmatch(text):
        raise GroundedReceiptViolation(f"{field} must be an exact sha256 digest")
    return text


def build_command_receipt(
    *,
    receipt_id: str,
    phase: str,
    subject_hash: str,
    run_context_hash: str,
    toolchain_fingerprint_hash: str,
    command_argv: Sequence[str],
    working_directory_hash: str,
    exit_code: int,
    timed_out: bool,
    crashed: bool,
    result_kind: str,
    wall_time_ms: int,
    artifact_hashes: Mapping[str, str],
    observed: Mapping[str, Any],
) -> dict[str, Any]:
    payload = {
        "schema_version": COMMAND_RECEIPT_SCHEMA_VERSION,
        "issuer": RECEIPT_ISSUER,
        "receipt_id": receipt_id,
        "phase": phase,
        "subject_hash": subject_hash,
        "run_context_hash": run_context_hash,
        "toolchain_fingerprint_hash": toolchain_fingerprint_hash,
        "command_argv": list(command_argv),
        "working_directory_hash": working_directory_hash,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "crashed": crashed,
        "result_kind": result_kind,
        "wall_time_ms": wall_time_ms,
        "artifact_hashes": deepcopy(dict(artifact_hashes)),
        "observed": deepcopy(dict(observed)),
    }
    payload["receipt_hash"] = hash_payload(payload)
    return verify_command_receipt(payload)


def verify_command_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(receipt))
    if set(payload) != _FIELDS:
        raise GroundedReceiptViolation("grounded command receipt fields mismatch")
    if (
        payload["schema_version"] != COMMAND_RECEIPT_SCHEMA_VERSION
        or payload["issuer"] != RECEIPT_ISSUER
    ):
        raise GroundedReceiptViolation("grounded command receipt authority mismatch")
    body = {key: value for key, value in payload.items() if key != "receipt_hash"}
    if payload["receipt_hash"] != hash_payload(body):
        raise GroundedReceiptViolation("grounded command receipt hash mismatch")
    if not isinstance(payload["receipt_id"], str) or not payload["receipt_id"]:
        raise GroundedReceiptViolation("grounded receipt id is missing")
    if payload["phase"] not in COMMAND_PHASES:
        raise GroundedReceiptViolation("grounded command phase is unsupported")
    for field in (
        "subject_hash",
        "run_context_hash",
        "toolchain_fingerprint_hash",
        "working_directory_hash",
    ):
        _digest(payload[field], field)
    argv = payload["command_argv"]
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(item, str) or not item for item in argv)
    ):
        raise GroundedReceiptViolation("command argv must be a non-empty string list")
    exit_code = payload["exit_code"]
    wall_time = payload["wall_time_ms"]
    if (
        not isinstance(exit_code, int)
        or isinstance(exit_code, bool)
        or not isinstance(wall_time, int)
        or isinstance(wall_time, bool)
        or wall_time < 0
    ):
        raise GroundedReceiptViolation("invalid command result accounting")
    if not isinstance(payload["timed_out"], bool) or not isinstance(
        payload["crashed"], bool
    ):
        raise GroundedReceiptViolation("timeout/crash flags must be boolean")
    if payload["result_kind"] not in RESULT_KINDS:
        raise GroundedReceiptViolation("unknown command result kind")
    if payload["timed_out"] != (payload["result_kind"] == "timeout"):
        raise GroundedReceiptViolation("timeout flag/result mismatch")
    if payload["crashed"] != (payload["result_kind"] == "crash"):
        raise GroundedReceiptViolation("crash flag/result mismatch")
    if payload["result_kind"] == "completed" and exit_code != 0:
        raise GroundedReceiptViolation("completed command must exit successfully")
    artifacts = payload["artifact_hashes"]
    if not isinstance(artifacts, dict) or not artifacts:
        raise GroundedReceiptViolation("grounded receipt requires output artifacts")
    for name, digest in artifacts.items():
        if not isinstance(name, str) or not name:
            raise GroundedReceiptViolation("artifact name is invalid")
        _digest(digest, f"artifact_hashes.{name}")
    if not isinstance(payload["observed"], dict):
        raise GroundedReceiptViolation("grounded receipt observed must be an object")
    return payload
