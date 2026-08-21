"""Fail-closed entrypoint for resumable real-provider shadow admission.

The original shadow admission runner persists successful child checkpoints.
This wrapper adds a terminal failure checkpoint around provider/conformance
exceptions so a failed provider attempt cannot be replayed accidentally.
Failure records contain only public authority metadata and exception class
labels; prompts, RTL, paths, credentials, and provider response text are never
copied into the record.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from r3e.pilot.grd8_acp7_smoke import _client_from_environment
from r3e.pilot.shadow_admission import (
    ShadowAdmissionViolation,
    run_shadow_admission,
)
from r3e.pilot.shadow_matrix import load_shadow_pilot_matrix
from r3e.protocol.hashing import atomic_write_json, hash_payload, read_json
from r3e.protocol.ledger import read_ledger


FAILURE_SCHEMA = "r3e-shadow-admission-terminal-failure-v1"


def _expected_calls(matrix_path: str | Path, *, project_root: Path) -> tuple[int, int]:
    matrix = load_shadow_pilot_matrix(matrix_path, project_root=project_root)
    blue = (
        len(matrix.smoke_case_ids)
        * len(matrix.smoke_seeds)
        * len(matrix.arms)
        * int(matrix.controls["call_budget_per_cell"])
    )
    return blue, int(matrix.red_shadow["provider_calls_per_round"])


def _completed_blue_calls(output: Path) -> int:
    ledger = output / "blue_matrix" / "provider_calls.jsonl"
    if ledger.is_file():
        try:
            return sum(
                row.get("event_type") == "provider_call_started"
                for row in read_ledger(ledger)
            )
        except (OSError, TypeError, ValueError):
            pass
    aggregate = output / "blue_matrix" / "aggregate.json"
    if not aggregate.is_file():
        return 0
    try:
        payload = read_json(aggregate)
        return sum(
            int(row.get("provider_calls", 0))
            for row in (payload.get("arms") or {}).values()
            if isinstance(row, dict)
        )
    except (OSError, TypeError, ValueError):
        return 0


def _failure_payload(
    *,
    output: Path,
    matrix_id: str,
    matrix_hash: str,
    expected_blue_calls: int,
    expected_red_calls: int,
    error: Exception,
) -> dict[str, Any]:
    blue_summary = output / "blue_matrix" / "summary.json"
    red_summary = output / "grounded_red" / "summary.json"
    red_attempted = blue_summary.is_file() and not red_summary.is_file()
    stage = "grounded_red" if red_attempted else "blue_matrix"
    calls: int | str
    if red_attempted:
        # The Red adapter has exactly one scheduled provider request. A
        # response that fails a runner-owned wall-time/schema gate still
        # consumes that request and must never be retried on resume.
        calls = expected_red_calls
    else:
        calls = _completed_blue_calls(output)
    body = {
        "schema_version": FAILURE_SCHEMA,
        "matrix_id": matrix_id,
        "matrix_hash": matrix_hash,
        "execution_mode": "smoke",
        "failed_stage": stage,
        "failure_class": type(error).__name__,
        "provider_calls_consumed": calls,
        "expected_blue_provider_calls": expected_blue_calls,
        "expected_red_provider_calls": expected_red_calls,
        "expected_total_provider_calls": expected_blue_calls + expected_red_calls,
        "promotion_executed": False,
        "memory_qualification_executed": False,
        "terminal": True,
        "claim_scope": (
            "Terminal shadow failure checkpoint only; no provider gain, "
            "policy transition, RAAM qualification, or empirical claim."
        ),
    }
    body["failure_hash"] = hash_payload(body)
    return body


def _verify_failure(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ShadowAdmissionViolation("terminal shadow failure is not an object")
    expected = hash_payload({
        key: value for key, value in payload.items() if key != "failure_hash"
    })
    if payload.get("schema_version") != FAILURE_SCHEMA:
        raise ShadowAdmissionViolation("terminal shadow failure schema mismatch")
    if payload.get("failure_hash") != expected:
        raise ShadowAdmissionViolation("terminal shadow failure hash mismatch")
    if payload.get("terminal") is not True:
        raise ShadowAdmissionViolation("terminal shadow failure is not terminal")
    if (
        payload.get("promotion_executed") is not False
        or payload.get("memory_qualification_executed") is not False
    ):
        raise ShadowAdmissionViolation(
            "terminal shadow failure has invalid evolution controls"
        )
    return payload


def run_safe_shadow_admission(
    *,
    project_root: str | Path,
    workspace: str | Path,
    client: Any,
    matrix_path: str | Path = "configs/pilot/shadow_pilot_matrix_v1.json",
    resume: bool = True,
) -> dict[str, Any]:
    """Run shadow admission and persist a terminal checkpoint on failure."""
    root = Path(project_root).resolve()
    output = Path(workspace).resolve()
    output.mkdir(parents=True, exist_ok=True)
    failure_path = output / "terminal_failure.json"
    if failure_path.exists():
        failure = _verify_failure(read_json(failure_path))
        raise ShadowAdmissionViolation(
            "terminal shadow failure already recorded; start a new workspace "
            f"for another authorized batch ({failure['failed_stage']})"
        )
    matrix = load_shadow_pilot_matrix(matrix_path, project_root=root)
    expected_blue, expected_red = _expected_calls(
        matrix_path, project_root=root
    )
    try:
        return run_shadow_admission(
            project_root=root,
            workspace=output,
            client=client,
            matrix_path=matrix_path,
            smoke_only=True,
            resume=resume,
        )
    except Exception as exc:
        failure = _failure_payload(
            output=output,
            matrix_id=matrix.matrix_id,
            matrix_hash=matrix.matrix_hash,
            expected_blue_calls=expected_blue,
            expected_red_calls=expected_red,
            error=exc,
        )
        atomic_write_json(failure_path, failure)
        raise ShadowAdmissionViolation(
            "shadow admission failed and was terminally checkpointed; "
            "resume is disabled for this workspace"
        ) from exc


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Run fail-closed real-provider shadow admission"
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--workspace", default="runtime/pilots/shadow-admission-real-v1"
    )
    parser.add_argument(
        "--matrix", default="configs/pilot/shadow_pilot_matrix_v1.json"
    )
    args = parser.parse_args()
    result = run_safe_shadow_admission(
        project_root=args.project_root,
        workspace=args.workspace,
        client=_client_from_environment(),
        matrix_path=args.matrix,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
