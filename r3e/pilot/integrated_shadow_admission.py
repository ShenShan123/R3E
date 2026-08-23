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
from r3e.memory.episode_builder import episode_from_challenge
from r3e.memory.episode_store import EpisodeStore
from r3e.policy.schema import PolicyState
from r3e.red.archive import update_archive
from r3e.protocol.hashing import hash_file
from r3e.protocol.hashing import atomic_write_json, hash_payload, read_json


INTEGRATED_SHADOW_SCHEMA = "r3e-integrated-grounded-red-acp-shadow-v1"
INTEGRATED_EVENT_SCHEMA = "r3e-integrated-grounded-red-acp-event-v1"


class IntegratedShadowAdmissionViolation(RuntimeError):
    """Raised when the formal 1+12 admission cannot be reconstructed."""


def _aggregate_blue_results(
    *,
    output: Path,
    poison: dict[str, Any],
    blue: dict[str, Any],
) -> dict[str, Any]:
    """Build one challenge attempt while retaining all four arm receipts."""
    cells = sorted(
        (output / "same_poison_blue" / "cells").glob(
            "*/blue_evaluation.json"
        )
    )
    if len(cells) != 4:
        raise IntegratedShadowAdmissionViolation(
            "same-poison Blue result set must contain four arm evaluations"
        )
    evaluations = [read_json(path) for path in cells]
    successes = [row for row in evaluations if row.get("oracle_ok")]
    resource_usage = {
        field: sum(
            float((row.get("resource_usage") or {}).get(field, 0))
            for row in evaluations
        )
        for field in (
            "input_tokens",
            "output_tokens",
            "llm_calls",
            "verifier_calls",
            "wall_time_seconds",
        )
    }
    for field in ("input_tokens", "output_tokens", "llm_calls", "verifier_calls"):
        resource_usage[field] = int(resource_usage[field])
    aggregate = {
        "schema_version": "r3e-same-poison-blue-aggregate-v1",
        "case_id": poison["case_id"],
        "seed": int(blue["seed"]),
        "arm_count": len(evaluations),
        "arm_results": evaluations,
        "oracle_ok": bool(successes),
        "successful_patch_hash": (
            str(successes[0].get("successful_patch_hash") or "")
            if successes else ""
        ),
        "activated_memory_ids": sorted({
            memory_id
            for row in evaluations
            for memory_id in row.get("activated_memory_ids", [])
        }),
        "resource_usage": resource_usage,
    }
    aggregate["aggregate_hash"] = hash_payload(aggregate)
    return aggregate


def _persist_episode_and_archive(
    *,
    output: Path,
    policy: Any,
    candidate: dict[str, Any],
    blue: dict[str, Any],
) -> dict[str, Any]:
    aggregate = _aggregate_blue_results(
        output=output,
        poison=candidate,
        blue=blue,
    )
    challenge = dict(candidate)
    challenge["blue_results"] = [aggregate]
    challenge["challenge_result_hash"] = hash_payload({
        key: value for key, value in challenge.items()
        if key != "challenge_result_hash"
    })
    episode = episode_from_challenge(
        challenge,
        policy=policy,
        round_id="integrated-shadow-R000",
    )
    store = EpisodeStore(output / "memory" / "episodes")
    store.append_episode(episode)
    episode_manifest = {
        "schema_version": "r3e-round-episode-manifest-v1",
        "round_id": "integrated-shadow-R000",
        "challenged_policy_hash": policy.policy_hash,
        "episode_ids": [episode.episode_id],
        "episode_hashes": [episode.episode_hash],
    }
    episode_manifest["manifest_hash"] = hash_payload(episode_manifest)
    atomic_write_json(output / "verified_episodes.json", episode_manifest)

    archive_payload = dict(challenge)
    if aggregate["oracle_ok"]:
        archive_kind = "covered"
        archive_payload["hardness_class"] = "covered"
    else:
        archive_kind = "residual"
        archive_payload["hardness_class"] = "hard_residual"
        archive_payload["learnability"] = {
            "label": "reachable",
            "evidence": {
                "source": "same_poison_shadow_conformance",
                "provider_calls": 0,
            },
        }
    archive_path = output / "archives" / f"{archive_kind}.jsonl"
    archived = update_archive(
        archive_path,
        archive_payload,
        archive_kind=archive_kind,
    )
    return {
        "challenge_result_hash": challenge["challenge_result_hash"],
        "archive_kind": archive_kind,
        "archive_entry_hash": archived["archive_entry_hash"],
        "verified_episode_manifest_hash": episode_manifest["manifest_hash"],
        "episode_hash": episode.episode_hash,
        "archive_path_hash": hash_file(archive_path),
    }


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
    policy = PolicyState.from_dict(
        read_json(root / "configs/base_policy/frozen_base_policy_v3.json")
    )
    downstream = _persist_episode_and_archive(
        output=output,
        policy=policy,
        candidate=candidate,
        blue=blue,
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
        "challenge_result_hash": downstream["challenge_result_hash"],
        "archive_kind": downstream["archive_kind"],
        "archive_entry_hash": downstream["archive_entry_hash"],
        "verified_episode_manifest_hash": downstream[
            "verified_episode_manifest_hash"
        ],
        "archive_path_hash": downstream["archive_path_hash"],
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
        "challenge_result_hash": downstream["challenge_result_hash"],
        "archive_kind": downstream["archive_kind"],
        "archive_entry_hash": downstream["archive_entry_hash"],
        "verified_episode_manifest_hash": downstream[
            "verified_episode_manifest_hash"
        ],
        "episode_hash": downstream["episode_hash"],
        "archive_path_hash": downstream["archive_path_hash"],
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
