"""Formal 1+12 Grounded Red -> ACP shadow admission.

Unlike the historical Shadow Pilot wrapper, this lane does not execute an
independent ACP matrix beside Red.  It runs one manifest-bound Grounded Red
call, persists the admitted candidate in the external execution workspace,
and routes that exact payload through all four ACP arms (three slots each).
Promotion and RAAM qualification remain unreachable.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from r3e.pilot.grd8_acp7_smoke import _client_from_environment
from r3e.pilot.grounded_red_shadow import run_grounded_red_shadow
from r3e.pilot.same_poison_shadow import run_same_poison_blue_portfolio
from r3e.pilot.shadow_matrix import load_shadow_pilot_matrix
from r3e.protocol.hashing import atomic_write_json, hash_payload, read_json


INTEGRATED_SHADOW_SCHEMA = "r3e-integrated-grounded-red-acp-shadow-v1"
INTEGRATED_EVENT_SCHEMA = "r3e-integrated-grounded-red-acp-event-v1"


class IntegratedShadowAdmissionViolation(RuntimeError):
    """Raised when the formal 1+12 admission cannot be reconstructed."""


def _verify_hash(payload: Any, field: str, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get(field) != hash_payload({
        key: value for key, value in payload.items() if key != field
    }):
        raise IntegratedShadowAdmissionViolation(f"{label} hash mismatch")
    return payload


def run_integrated_shadow_admission(
    *,
    project_root: str | Path,
    workspace: str | Path,
    client: Any,
    matrix_path: str | Path = "configs/pilot/shadow_pilot_matrix_v1.json",
    resume: bool = True,
) -> dict[str, Any]:
    """Run one Red provider call followed by twelve same-poison ACP calls."""
    root = Path(project_root).resolve()
    output = Path(workspace).resolve()
    output.mkdir(parents=True, exist_ok=True)
    matrix = load_shadow_pilot_matrix(matrix_path, project_root=root)
    if len(matrix.smoke_case_ids) != 1 or len(matrix.smoke_seeds) != 1:
        raise IntegratedShadowAdmissionViolation(
            "integrated shadow admission requires one smoke case and seed"
        )
    expected_blue = len(matrix.arms) * int(matrix.controls["call_budget_per_cell"])
    expected_red = int(matrix.red_shadow["provider_calls_per_round"])
    if expected_red != 1 or expected_blue != 12:
        raise IntegratedShadowAdmissionViolation(
            "integrated shadow admission requires a frozen 1+12 budget"
        )
    summary_path = output / "summary.json"
    if summary_path.is_file():
        summary = _verify_hash(read_json(summary_path), "summary_hash", "integrated summary")
        expected = {
            "schema_version": INTEGRATED_SHADOW_SCHEMA,
            "matrix_id": matrix.matrix_id,
            "matrix_hash": matrix.matrix_hash,
            "expected_red_provider_calls": expected_red,
            "expected_blue_provider_calls": expected_blue,
            "expected_total_provider_calls": expected_red + expected_blue,
            "promotion_executed": False,
            "memory_qualification_executed": False,
            "resume_additional_calls": 0,
        }
        if any(summary.get(key) != value for key, value in expected.items()):
            raise IntegratedShadowAdmissionViolation(
                "completed integrated shadow summary drifted"
            )

    red = run_grounded_red_shadow(
        project_root=root,
        workspace=output / "grounded_red",
        client=client,
        seed=matrix.smoke_seeds[0],
        matrix_path=matrix_path,
        resume=resume,
    )
    candidate_path = output / "grounded_red" / "execution" / "candidate.json"
    if not candidate_path.is_file():
        raise IntegratedShadowAdmissionViolation(
            "Grounded Red did not persist its admitted candidate"
        )
    try:
        candidate = read_json(candidate_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise IntegratedShadowAdmissionViolation(
            "Grounded Red candidate is unreadable"
        ) from exc
    if not isinstance(candidate, dict):
        raise IntegratedShadowAdmissionViolation(
            "Grounded Red candidate is not an object"
        )
    if red.get("status") != "admitted":
        raise IntegratedShadowAdmissionViolation(
            "integrated shadow requires an admitted Grounded Red candidate"
        )
    blue = run_same_poison_blue_portfolio(
        project_root=root,
        workspace=output / "same_poison_blue",
        client=client,
        poison=candidate,
        matrix_path=matrix_path,
        seed=matrix.smoke_seeds[0],
        red_provider_calls=expected_red,
        resume=resume,
    )
    if blue.get("blue_provider_calls") != expected_blue:
        raise IntegratedShadowAdmissionViolation(
            "integrated shadow Blue calls are not call-matched"
        )
    if blue.get("challenged_poison_id") != candidate.get("poison_id"):
        raise IntegratedShadowAdmissionViolation(
            "integrated shadow poison identity was not preserved"
        )
    event = {
        "schema_version": INTEGRATED_EVENT_SCHEMA,
        "matrix_id": matrix.matrix_id,
        "matrix_hash": matrix.matrix_hash,
        "red_summary_hash": red["summary_hash"],
        "blue_summary_hash": blue["summary_hash"],
        "challenged_poison_id": blue["challenged_poison_id"],
        "challenged_poison_payload_hash": blue[
            "challenged_poison_payload_hash"
        ],
        "challenged_policy_hash": blue["challenged_policy_hash"],
        "failure_descriptor_hash": blue["failure_descriptor_hash"],
        "provider_calls": expected_red + expected_blue,
        "promotion_executed": False,
        "memory_qualification_executed": False,
    }
    event["event_hash"] = hash_payload(event)
    atomic_write_json(output / "event.json", event)
    summary = {
        "schema_version": INTEGRATED_SHADOW_SCHEMA,
        "matrix_id": matrix.matrix_id,
        "matrix_hash": matrix.matrix_hash,
        "red_summary_hash": red["summary_hash"],
        "blue_summary_hash": blue["summary_hash"],
        "challenged_poison_id": blue["challenged_poison_id"],
        "challenged_poison_payload_hash": blue[
            "challenged_poison_payload_hash"
        ],
        "challenged_policy_hash": blue["challenged_policy_hash"],
        "failure_descriptor_hash": blue["failure_descriptor_hash"],
        "formal_triplet": blue["formal_triplet"],
        "expected_red_provider_calls": expected_red,
        "expected_blue_provider_calls": expected_blue,
        "expected_total_provider_calls": expected_red + expected_blue,
        "provider_calls": event["provider_calls"],
        "call_matched": event["provider_calls"] == expected_red + expected_blue,
        "promotion_executed": False,
        "memory_qualification_executed": False,
        "resume_additional_calls": 0,
        "event_hash": event["event_hash"],
        "claim_scope": (
            "One admitted Grounded poison routed to four ACP arms; no policy "
            "promotion, RAAM qualification, or empirical gain claim."
        ),
    }
    summary["summary_hash"] = hash_payload(summary)
    if summary_path.is_file():
        existing = _verify_hash(read_json(summary_path), "summary_hash", "integrated summary")
        if existing != summary:
            raise IntegratedShadowAdmissionViolation(
                "resumed integrated shadow summary differs from reconstruction"
            )
        return existing
    atomic_write_json(summary_path, summary)
    return summary


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the formal 1+12 same-poison shadow admission"
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--workspace", default="runtime/pilots/integrated-shadow-admission-v1"
    )
    parser.add_argument(
        "--matrix", default="configs/pilot/shadow_pilot_matrix_v1.json"
    )
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    result = run_integrated_shadow_admission(
        project_root=args.project_root,
        workspace=args.workspace,
        client=_client_from_environment(),
        matrix_path=args.matrix,
        resume=not args.no_resume,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
