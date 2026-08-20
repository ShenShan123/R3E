"""Call-matched GRD-8/ACP-7 shadow admission orchestration.

The admission lane deliberately runs no policy or memory promotion.  It
combines the frozen A/B/C/D matrix with one manifest-backed Grounded Red
shadow call so a caller can authorize and audit the complete 12+1 budget as a
single resumable unit.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from r3e.pilot.grd8_acp7_smoke import _client_from_environment
from r3e.pilot.grounded_red_shadow import run_grounded_red_shadow
from r3e.pilot.shadow_matrix import load_shadow_pilot_matrix
from r3e.pilot.shadow_runner import _safe_component, run_shadow_matrix
from r3e.protocol.hashing import atomic_write_json, hash_payload, read_json


SHADOW_ADMISSION_SCHEMA = "r3e-real-provider-shadow-admission-v1"


class ShadowAdmissionViolation(RuntimeError):
    """Raised when the combined shadow budget or authority is invalid."""


def _verify_completed_children(
    *,
    output: Path,
    matrix: Any,
    smoke_only: bool,
    summary: dict[str, Any],
    expected_blue_calls: int,
    expected_red_calls: int,
) -> None:
    """Fail closed before resume can recreate a supposedly completed call.

    A wrapper summary is written only after both children complete. If that
    summary remains but a child artifact is removed or replaced, treating the
    missing cell as an ordinary interruption could issue provider calls twice.
    Require the complete child shape before entering either child runner.
    """
    mode = "smoke" if smoke_only else "full_matrix"
    blue_dir = output / "blue_matrix"
    red_dir = output / "grounded_red"
    blue_summary_path = blue_dir / "summary.json"
    red_summary_path = red_dir / "summary.json"
    if not blue_summary_path.is_file() or not red_summary_path.is_file():
        raise ShadowAdmissionViolation(
            "completed shadow admission is missing child summary artifacts"
        )
    blue = read_json(blue_summary_path)
    red = read_json(red_summary_path)
    for payload, field, label in (
        (blue, "summary_hash", "blue child summary"),
        (red, "summary_hash", "red child summary"),
    ):
        if not isinstance(payload, dict) or payload.get(field) != hash_payload({
            key: value for key, value in payload.items() if key != field
        }):
            raise ShadowAdmissionViolation(f"{label} hash mismatch")
    expected_cells = (
        len(matrix.smoke_case_ids if smoke_only else matrix.case_ids)
        * len(matrix.smoke_seeds if smoke_only else matrix.seeds)
        * len(matrix.arms)
    )
    if (
        blue.get("schema_version") != "r3e-shadow-pilot-run-summary-v1"
        or blue.get("matrix_id") != matrix.matrix_id
        or blue.get("matrix_hash") != matrix.matrix_hash
        or blue.get("execution_mode") != mode
        or blue.get("completed_cells") != expected_cells
        or blue.get("call_matched") is not True
        or blue.get("promotion_executed") is not False
        or blue.get("raam_execution_executed") is not False
        or blue.get("summary_hash") != summary.get("blue_summary_hash")
        or not (blue_dir / "aggregate.json").is_file()
        or not (blue_dir / "events.jsonl").is_file()
    ):
        raise ShadowAdmissionViolation(
            "completed shadow admission blue artifacts are incomplete"
        )
    case_ids = matrix.smoke_case_ids if smoke_only else matrix.case_ids
    seeds = matrix.smoke_seeds if smoke_only else matrix.seeds
    for case_id in case_ids:
        for seed in seeds:
            for arm in matrix.arms:
                cell = (
                    blue_dir / "cells" / _safe_component(case_id)
                    / f"seed_{seed}" / f"arm_{arm['arm_id']}"
                )
                if not (
                    (cell / "cell_summary.json").is_file()
                    and (cell / "blue_evaluation.json").is_file()
                ):
                    raise ShadowAdmissionViolation(
                        "completed shadow admission has an incomplete blue cell"
                    )
    red_seed = (matrix.smoke_seeds if smoke_only else matrix.seeds)[0]
    if (
        red.get("schema_version") != "r3e-grounded-red-shadow-summary-v1"
        or red.get("matrix_id") != matrix.matrix_id
        or red.get("matrix_hash") != matrix.matrix_hash
        or red.get("round_id")
        != f"{matrix.matrix_id}-red-seed-{red_seed}"
        or red.get("promotion_executed") is not False
        or red.get("memory_qualification_executed") is not False
        or red.get("summary_hash") != summary.get("red_summary_hash")
        or not (red_dir / "red_result.json").is_file()
        or not (red_dir / "event.json").is_file()
        or not (red_dir / "events.jsonl").is_file()
    ):
        raise ShadowAdmissionViolation(
            "completed shadow admission red artifacts are incomplete"
        )
    if (
        summary.get("expected_blue_provider_calls") != expected_blue_calls
        or summary.get("expected_red_provider_calls") != expected_red_calls
    ):
        raise ShadowAdmissionViolation(
            "completed shadow admission child call budget drifted"
        )


def _verify_frozen_summary(
    summary: Any,
    *,
    matrix: Any,
    execution_mode: str,
    expected_blue_calls: int,
    expected_red_calls: int,
) -> dict[str, Any]:
    """Validate a completed wrapper summary before any provider call."""
    if not isinstance(summary, dict):
        raise ShadowAdmissionViolation("shadow admission summary is not an object")
    expected_hash = hash_payload({
        key: value for key, value in summary.items() if key != "summary_hash"
    })
    if summary.get("summary_hash") != expected_hash:
        raise ShadowAdmissionViolation("shadow admission summary hash mismatch")
    expected = {
        "schema_version": SHADOW_ADMISSION_SCHEMA,
        "matrix_id": matrix.matrix_id,
        "matrix_hash": matrix.matrix_hash,
        "execution_mode": execution_mode,
        "expected_blue_provider_calls": expected_blue_calls,
        "expected_red_provider_calls": expected_red_calls,
        "expected_total_provider_calls": expected_blue_calls + expected_red_calls,
        "promotion_executed": False,
        "memory_qualification_executed": False,
        "resume_additional_calls": 0,
    }
    if any(summary.get(key) != value for key, value in expected.items()):
        raise ShadowAdmissionViolation(
            "shadow admission summary does not match frozen execution"
        )
    return summary


def run_shadow_admission(
    *,
    project_root: str | Path,
    workspace: str | Path,
    client: Any,
    matrix_path: str | Path = "configs/pilot/shadow_pilot_matrix_v1.json",
    smoke_only: bool = True,
    resume: bool = True,
) -> dict[str, Any]:
    """Run the bounded 12+1 shadow admission and return an auditable summary."""
    root = Path(project_root).resolve()
    output = Path(workspace).resolve()
    output.mkdir(parents=True, exist_ok=True)
    matrix = load_shadow_pilot_matrix(matrix_path, project_root=root)
    case_count = len(matrix.smoke_case_ids if smoke_only else matrix.case_ids)
    seed_count = len(matrix.smoke_seeds if smoke_only else matrix.seeds)
    expected_blue_calls = (
        case_count
        * seed_count
        * len(matrix.arms)
        * int(matrix.controls["call_budget_per_cell"])
    )
    expected_red_calls = int(matrix.red_shadow["provider_calls_per_round"])
    summary_path = output / "summary.json"
    if summary_path.exists():
        if not resume:
            raise ShadowAdmissionViolation(
                "completed shadow admission cannot be overwritten"
            )
        existing_summary = _verify_frozen_summary(
            read_json(summary_path),
            matrix=matrix,
            execution_mode="smoke" if smoke_only else "full_matrix",
            expected_blue_calls=expected_blue_calls,
            expected_red_calls=expected_red_calls,
        )
        _verify_completed_children(
            output=output,
            matrix=matrix,
            smoke_only=smoke_only,
            summary=existing_summary,
            expected_blue_calls=expected_blue_calls,
            expected_red_calls=expected_red_calls,
        )
    blue = run_shadow_matrix(
        project_root=root,
        workspace=output / "blue_matrix",
        matrix_path=matrix_path,
        client=client,
        smoke_only=smoke_only,
        resume=resume,
    )
    red_seed = (matrix.smoke_seeds if smoke_only else matrix.seeds)[0]
    red = run_grounded_red_shadow(
        project_root=root,
        workspace=output / "grounded_red",
        client=client,
        seed=red_seed,
        matrix_path=matrix_path,
        resume=resume,
    )
    if blue.get("promotion_executed") is not False:
        raise ShadowAdmissionViolation("ACP shadow unexpectedly promoted")
    if blue.get("raam_execution_executed") is not False:
        raise ShadowAdmissionViolation("ACP shadow unexpectedly ran RAAM")
    if red.get("promotion_executed") is not False:
        raise ShadowAdmissionViolation("Red shadow unexpectedly promoted")
    if red.get("memory_qualification_executed") is not False:
        raise ShadowAdmissionViolation(
            "Red shadow unexpectedly qualified memory"
        )
    summary = {
        "schema_version": SHADOW_ADMISSION_SCHEMA,
        "matrix_id": matrix.matrix_id,
        "matrix_hash": matrix.matrix_hash,
        "execution_mode": "smoke" if smoke_only else "full_matrix",
        "blue_summary_hash": blue["summary_hash"],
        "red_summary_hash": red["summary_hash"],
        "blue_completed_cells": blue["completed_cells"],
        "blue_call_matched": blue["call_matched"],
        "red_status": red["status"],
        "expected_blue_provider_calls": expected_blue_calls,
        "expected_red_provider_calls": expected_red_calls,
        "expected_total_provider_calls": (
            expected_blue_calls + expected_red_calls
        ),
        "promotion_executed": False,
        "memory_qualification_executed": False,
        "resume_additional_calls": 0,
        "claim_scope": (
            "Call-matched shadow admission only; no real-provider gain, "
            "policy transition, RAAM qualification, or multi-round claim."
        ),
    }
    summary["summary_hash"] = hash_payload(summary)
    if summary_path.exists():
        existing = _verify_frozen_summary(
            read_json(summary_path),
            matrix=matrix,
            execution_mode="smoke" if smoke_only else "full_matrix",
            expected_blue_calls=expected_blue_calls,
            expected_red_calls=expected_red_calls,
        )
        if existing != summary:
            raise ShadowAdmissionViolation(
                "resumed shadow admission summary differs from reconstruction"
            )
        return existing
    atomic_write_json(summary_path, summary)
    return summary


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the bounded GRD-8/ACP-7 12+1 shadow admission"
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--workspace", default="runtime/pilots/shadow-admission-v1"
    )
    parser.add_argument(
        "--matrix", default="configs/pilot/shadow_pilot_matrix_v1.json"
    )
    parser.add_argument("--full-matrix", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    result = run_shadow_admission(
        project_root=args.project_root,
        workspace=args.workspace,
        matrix_path=args.matrix,
        client=_client_from_environment(),
        smoke_only=not args.full_matrix,
        resume=not args.no_resume,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
