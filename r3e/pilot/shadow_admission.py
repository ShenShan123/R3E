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
from r3e.pilot.shadow_runner import run_shadow_matrix
from r3e.protocol.hashing import atomic_write_json, hash_payload


SHADOW_ADMISSION_SCHEMA = "r3e-real-provider-shadow-admission-v1"


class ShadowAdmissionViolation(RuntimeError):
    """Raised when the combined shadow budget or authority is invalid."""


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
    atomic_write_json(output / "summary.json", summary)
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
