"""Strict, privacy-safe aggregation for Shadow Pilot Matrix cells."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping

from r3e.protocol.hashing import hash_payload


CELL_EVENT_SCHEMA = "r3e-shadow-pilot-cell-event-v1"
AGGREGATE_SCHEMA = "r3e-shadow-pilot-aggregate-v1"
_CELL_FIELDS = {
    "schema_version",
    "matrix_id",
    "matrix_hash",
    "case_id",
    "seed",
    "arm_id",
    "portfolio_hash",
    "policy_hash",
    "model_binding_hash",
    "blue_evaluation_hash",
    "provider_calls",
    "verifier_calls",
    "input_tokens",
    "output_tokens",
    "candidate_count",
    "candidate_success_count",
    "portfolio_success",
    "semantic_unique_candidate_count",
    "semantic_duplicate_pairs",
    "lens_collapse",
    "selected_candidate_id",
    "event_hash",
}


class ShadowPilotAggregateViolation(RuntimeError):
    """Raised when matrix events are incomplete, duplicated, or tampered."""


def verify_cell_event(raw: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(raw))
    if set(payload) != _CELL_FIELDS:
        raise ShadowPilotAggregateViolation("shadow cell event fields mismatch")
    if payload["schema_version"] != CELL_EVENT_SCHEMA:
        raise ShadowPilotAggregateViolation("shadow cell event schema mismatch")
    event_hash = payload.pop("event_hash")
    if event_hash != hash_payload(payload):
        raise ShadowPilotAggregateViolation("shadow cell event hash mismatch")
    for field in (
        "matrix_id", "matrix_hash", "case_id", "arm_id",
        "portfolio_hash", "policy_hash", "model_binding_hash",
        "blue_evaluation_hash",
    ):
        if not isinstance(payload[field], str) or not payload[field]:
            raise ShadowPilotAggregateViolation(f"{field} must be non-empty")
    for field in (
        "seed", "provider_calls", "verifier_calls", "input_tokens",
        "output_tokens", "candidate_count", "candidate_success_count",
        "semantic_unique_candidate_count", "semantic_duplicate_pairs",
    ):
        value = payload[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ShadowPilotAggregateViolation(f"{field} must be non-negative")
    for field in ("portfolio_success", "lens_collapse"):
        if not isinstance(payload[field], bool):
            raise ShadowPilotAggregateViolation(f"{field} must be boolean")
    selected = payload["selected_candidate_id"]
    if not isinstance(selected, str):
        raise ShadowPilotAggregateViolation(
            "selected_candidate_id must be a string"
        )
    payload["event_hash"] = event_hash
    return payload


def load_cell_events(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.is_file():
        raise ShadowPilotAggregateViolation("shadow event log is missing")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        source.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ShadowPilotAggregateViolation(
                f"invalid JSONL at line {line_number}"
            ) from exc
        if not isinstance(raw, Mapping):
            raise ShadowPilotAggregateViolation("cell event must be an object")
        rows.append(verify_cell_event(raw))
    return rows


def aggregate_cell_events(
    rows: list[Mapping[str, Any]],
    *,
    matrix_id: str,
    matrix_hash: str,
    expected_cells: set[tuple[str, int, str]],
    expected_calls_per_cell: int,
) -> dict[str, Any]:
    verified = [verify_cell_event(row) for row in rows]
    keys = [
        (row["case_id"], row["seed"], row["arm_id"])
        for row in verified
    ]
    if len(keys) != len(set(keys)):
        raise ShadowPilotAggregateViolation("duplicate shadow matrix cell")
    if set(keys) != expected_cells:
        raise ShadowPilotAggregateViolation("shadow matrix cell set mismatch")
    if any(
        row["matrix_id"] != matrix_id
        or row["matrix_hash"] != matrix_hash
        for row in verified
    ):
        raise ShadowPilotAggregateViolation("matrix authority mismatch")
    arms: dict[str, dict[str, Any]] = {}
    for arm_id in sorted({row["arm_id"] for row in verified}):
        arm_rows = [row for row in verified if row["arm_id"] == arm_id]
        portfolio_hashes = {row["portfolio_hash"] for row in arm_rows}
        if len(portfolio_hashes) != 1:
            raise ShadowPilotAggregateViolation("arm portfolio hash drift")
        arms[arm_id] = {
            "portfolio_hash": next(iter(portfolio_hashes)),
            "cells": len(arm_rows),
            "provider_calls": sum(row["provider_calls"] for row in arm_rows),
            "verifier_calls": sum(row["verifier_calls"] for row in arm_rows),
            "input_tokens": sum(row["input_tokens"] for row in arm_rows),
            "output_tokens": sum(row["output_tokens"] for row in arm_rows),
            "candidates": sum(row["candidate_count"] for row in arm_rows),
            "candidate_successes": sum(
                row["candidate_success_count"] for row in arm_rows
            ),
            "portfolio_successes": sum(
                row["portfolio_success"] for row in arm_rows
            ),
            "semantic_unique_candidates": sum(
                row["semantic_unique_candidate_count"] for row in arm_rows
            ),
            "semantic_duplicate_pairs": sum(
                row["semantic_duplicate_pairs"] for row in arm_rows
            ),
            "lens_collapse_cells": sum(
                row["lens_collapse"] for row in arm_rows
            ),
        }
    cell_counts = {value["cells"] for value in arms.values()}
    call_matched = (
        len(cell_counts) == 1
        and all(
            row["provider_calls"] == expected_calls_per_cell
            and row["verifier_calls"] == expected_calls_per_cell
            and row["candidate_count"] == expected_calls_per_cell
            for row in verified
        )
    )
    token_totals = {
        arm_id: value["input_tokens"] + value["output_tokens"]
        for arm_id, value in arms.items()
    }
    payload = {
        "schema_version": AGGREGATE_SCHEMA,
        "matrix_id": matrix_id,
        "matrix_hash": matrix_hash,
        "completed_cells": len(verified),
        "expected_cells": len(expected_cells),
        "call_matched": call_matched,
        "token_matched": len(set(token_totals.values())) <= 1,
        "claim_scope": (
            "Shadow execution statistics only; no promotion, causal, "
            "or comparative effectiveness claim."
        ),
        "arms": arms,
        "cell_event_hashes": sorted(row["event_hash"] for row in verified),
    }
    payload["aggregate_hash"] = hash_payload(payload)
    return payload
