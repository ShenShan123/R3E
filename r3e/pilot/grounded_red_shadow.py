"""One bounded Grounded Red round under Shadow Pilot authority."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping

from r3e.arena.grounded_authority import (
    execute_grounded_arena_validity,
    verify_arena_grounded_authority,
)
from r3e.arena.real_grounded_adapter import RealGroundedArenaAdapter
from r3e.pilot.grd8_acp7_smoke import (
    _client_from_environment,
    _real_population_config,
)
from r3e.pilot.shadow_matrix import load_shadow_pilot_matrix
from r3e.pilot.shadow_runner import _verify_client
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import (
    atomic_write_json,
    atomic_write_jsonl,
    hash_file,
    hash_payload,
    read_json,
)
from r3e.red.grounded.coverage import freeze_coverage_state
from r3e.red.grounded.population_scheduler import (
    build_population_schedule,
    verify_population_candidates,
)
from r3e.red.grounded.proposal_planner import (
    build_grounded_proposal_plan,
    verify_proposal_candidates,
)
from r3e.red.grounded.registry import load_grounded_registries


RED_EVENT_SCHEMA = "r3e-grounded-red-shadow-event-v1"
RED_SUMMARY_SCHEMA = "r3e-grounded-red-shadow-summary-v1"
_EVENT_FIELDS = {
    "schema_version",
    "matrix_id",
    "matrix_hash",
    "round_id",
    "seed",
    "policy_hash",
    "model_binding_hash",
    "red_result_file_hash",
    "provider_calls",
    "admitted",
    "formal_triplet",
    "authority_hash",
    "failure_descriptor_hash",
    "promotion_executed",
    "memory_qualification_executed",
    "event_hash",
}


class GroundedRedShadowViolation(RuntimeError):
    """Raised when the bounded red shadow authority cannot be rebuilt."""


def verify_red_shadow_event(raw: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(raw))
    if set(payload) != _EVENT_FIELDS:
        raise GroundedRedShadowViolation("red shadow event fields mismatch")
    if payload["schema_version"] != RED_EVENT_SCHEMA:
        raise GroundedRedShadowViolation("red shadow event schema mismatch")
    event_hash = payload.pop("event_hash")
    if event_hash != hash_payload(payload):
        raise GroundedRedShadowViolation("red shadow event hash mismatch")
    if payload["provider_calls"] != 1:
        raise GroundedRedShadowViolation("red shadow call budget mismatch")
    if (
        payload["promotion_executed"] is not False
        or payload["memory_qualification_executed"] is not False
    ):
        raise GroundedRedShadowViolation(
            "red shadow cannot promote or qualify memory"
        )
    triplet = payload["formal_triplet"]
    if not isinstance(triplet, Mapping) or set(triplet) != {
        "clean", "poison", "revert"
    }:
        raise GroundedRedShadowViolation("formal triplet fields mismatch")
    payload["event_hash"] = event_hash
    return payload


def _audit_result(
    *, root: Path, workspace: Path, result: Mapping[str, Any]
) -> dict[str, Any]:
    payload = deepcopy(dict(result))
    policy = PolicyState.from_dict(read_json(
        root / "configs/base_policy/frozen_base_policy_v3.json"
    ))
    registries = load_grounded_registries(
        family_registry=root / "configs/red/grounded_family_registry_v1.json",
        operator_registry=root / "configs/red/grounded_operator_registry_v1.json",
        effect_registry=root / "configs/red/grounded_effect_registry_v1.json",
    )
    authority = verify_arena_grounded_authority(
        read_json(workspace / "authority.json"),
        policy=policy,
        registries=registries,
    )
    proposal = read_json(workspace / "proposal.json")
    schedule = read_json(workspace / "population_schedule.json")
    choice = read_json(workspace / "model_choice.json")
    triplet = {
        name: authority["formal_proof_triplet"][name]["verdict"]
        for name in ("clean", "poison", "revert")
    }
    expected = {
        "proposal_plan_hash": proposal["plan_hash"],
        "population_schedule_hash": schedule["schedule_hash"],
        "assignment_hash": schedule["assignments"][0]["assignment_hash"],
        "choice_hash": choice["choice_hash"],
        "authority_hash": authority["authority_hash"],
        "formal_triplet": triplet,
        "failure_descriptor_hash": authority[
            "failure_descriptor"
        ]["descriptor_hash"],
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise GroundedRedShadowViolation(
            "red shadow result authority mismatch"
        )
    admitted = bool(payload.get("admitted"))
    if admitted != (
        triplet == {
            "clean": "proved",
            "poison": "counterexample",
            "revert": "proved",
        }
    ):
        raise GroundedRedShadowViolation("formal admission mismatch")
    return payload


def _run_manifest_grounded(
    *,
    root: Path,
    workspace: Path,
    client: Any,
    seed: int,
    round_id: str,
    manifest_path: Path,
    case_id: str,
    red_budget: Mapping[str, Any],
) -> dict[str, Any]:
    """Run the one-call Red shadow against a frozen formal manifest row.

    This deliberately shares the manifest-backed adapter used by the
    integrated Arena lane.  The provider can select only a runner-enumerated
    parser node; all materialization and formal admission stay local.
    """
    policy = PolicyState.from_dict(read_json(
        root / "configs/base_policy/frozen_base_policy_v3.json"
    ))
    registries = load_grounded_registries(
        family_registry=root / "configs/red/grounded_family_registry_v1.json",
        operator_registry=root / "configs/red/grounded_operator_registry_v1.json",
        effect_registry=root / "configs/red/grounded_effect_registry_v1.json",
    )
    proposal = build_grounded_proposal_plan(
        policy=policy,
        registries=registries,
        coverage_state=freeze_coverage_state([]),
        archive_view=[],
        budget=1,
        family_quota=1,
        archive_quota=0,
        maximum_difficulty_band="D3",
        memory_capability=None,
        allow_composition=False,
    )
    selected_intent = next(
        row for row in proposal["candidate_intents"]
        if row["intent_id"] == proposal["selected_intent_ids"][0]
    )
    adapter = RealGroundedArenaAdapter(
        project_root=root,
        manifest_path=manifest_path,
        client=client,
        # Provider-facing and formal command artifacts belong to the caller's
        # ignored/external workspace, never to the checked-out repository.
        artifact_root=workspace / "round" / "artifacts",
        case_id=case_id,
        round_id=round_id,
    )
    population_config = _real_population_config(
        adapter.choice_provider,
        maximum_input_tokens=int(red_budget["maximum_input_tokens"]),
        maximum_output_tokens=int(red_budget["maximum_output_tokens"]),
        maximum_wall_time_ms=int(red_budget["maximum_wall_time_ms"]),
    )
    schedule = build_population_schedule(
        policy=policy,
        proposal_plan=proposal,
        config=population_config,
    )
    red_context = {
        "grounded_proposal_plan": proposal,
        "grounded_population_schedule": schedule,
    }
    candidates = list(adapter.generate_red(
        policy,
        {"red_seed": int(seed)},
        red_context,
    ))
    if len(candidates) != 1:
        raise GroundedRedShadowViolation(
            "manifest shadow must produce exactly one candidate"
        )
    candidate = candidates[0]
    verify_proposal_candidates(proposal, candidates, policy=policy)
    verify_population_candidates(schedule, candidates, policy=policy)
    choice = candidate.get("grounded_choice_receipt")
    if not isinstance(choice, Mapping):
        raise GroundedRedShadowViolation(
            "manifest shadow is missing the provider choice receipt"
        )
    validity_result = execute_grounded_arena_validity(
        poison=candidate,
        policy=policy,
        registries=registries,
        project_root=root,
        round_dir=workspace / "round",
        run_context_hash=hash_payload({
            "pilot": "real-integrated-shadow",
            "round_id": round_id,
            "seed": int(seed),
            "choice_hash": choice["choice_hash"],
        }),
    )
    authority = validity_result.get("grounded_authority_bundle")
    if not isinstance(authority, Mapping):
        raise GroundedRedShadowViolation(
            "manifest shadow candidate was routed to formal rejection"
        )
    verified = verify_arena_grounded_authority(
        authority,
        policy=policy,
        registries=registries,
    )
    atomic_write_json(workspace / "proposal.json", proposal)
    atomic_write_json(workspace / "population_schedule.json", schedule)
    atomic_write_json(workspace / "model_choice.json", choice)
    atomic_write_json(workspace / "authority.json", verified)
    triplet = verified["formal_proof_triplet"]
    proven_valid = validity_result["validity"]["proven_valid"]
    return {
        "status": "passed" if proven_valid else "rejected",
        "proposal_plan_hash": proposal["plan_hash"],
        "population_schedule_hash": schedule["schedule_hash"],
        "assignment_hash": schedule["assignments"][0]["assignment_hash"],
        "choice_hash": choice["choice_hash"],
        "selected_node_ordinal": choice["selected_node_ordinal"],
        "provider_usage": {
            "input_tokens": choice["input_tokens"],
            "output_tokens": choice["output_tokens"],
            "wall_time_ms": choice["wall_time_ms"],
        },
        "authority_hash": verified["authority_hash"],
        "admitted": proven_valid,
        "formal_triplet": {
            name: triplet[name]["verdict"]
            for name in ("clean", "poison", "revert")
        },
        "failure_descriptor_hash": verified[
            "failure_descriptor"
        ]["descriptor_hash"],
        "manifest_case_id": candidate["case_id"],
    }


def run_grounded_red_shadow(
    *,
    project_root: str | Path,
    workspace: str | Path,
    client: Any,
    seed: int,
    matrix_path: str | Path = "configs/pilot/shadow_pilot_matrix_v1.json",
    resume: bool = True,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    output = Path(workspace).resolve()
    output.mkdir(parents=True, exist_ok=True)
    matrix = load_shadow_pilot_matrix(matrix_path, project_root=root)
    _verify_client(matrix, client)
    if seed not in matrix.seeds:
        raise GroundedRedShadowViolation("seed is outside frozen matrix")
    policy = PolicyState.from_dict(read_json(
        root / "configs/base_policy/frozen_base_policy_v1.json"
    ))
    round_id = f"{matrix.matrix_id}-red-seed-{seed}"
    result_path = output / "red_result.json"
    event_path = output / "event.json"
    if event_path.exists():
        if not resume or not result_path.is_file():
            raise GroundedRedShadowViolation(
                "completed red shadow round cannot be overwritten"
            )
        event = verify_red_shadow_event(read_json(event_path))
        if (
            event["matrix_hash"] != matrix.matrix_hash
            or event["round_id"] != round_id
            or event["seed"] != seed
            or event["red_result_file_hash"] != hash_file(result_path)
        ):
            raise GroundedRedShadowViolation(
                "resumed red shadow output hash mismatch"
            )
        result = _audit_result(
            root=root, workspace=output / "execution", result=read_json(result_path)
        )
    else:
        if result_path.exists():
            raise GroundedRedShadowViolation(
                "incomplete red shadow has an unbound result"
            )
        result = _run_manifest_grounded(
            root=root,
            workspace=output / "execution",
            client=client,
            seed=seed,
            round_id=round_id,
            manifest_path=matrix.red_target_manifest_path,
            case_id=matrix.red_case_id,
            red_budget=matrix.red_shadow,
        )
        result = _audit_result(
            root=root, workspace=output / "execution", result=result
        )
        atomic_write_json(result_path, result)
        event = {
            "schema_version": RED_EVENT_SCHEMA,
            "matrix_id": matrix.matrix_id,
            "matrix_hash": matrix.matrix_hash,
            "round_id": round_id,
            "seed": seed,
            "policy_hash": policy.policy_hash,
            "model_binding_hash": hash_payload(matrix.model_binding),
            "red_result_file_hash": hash_file(result_path),
            "provider_calls": matrix.red_shadow["provider_calls_per_round"],
            "admitted": result["admitted"],
            "formal_triplet": deepcopy(result["formal_triplet"]),
            "authority_hash": result["authority_hash"],
            "failure_descriptor_hash": result["failure_descriptor_hash"],
            "promotion_executed": False,
            "memory_qualification_executed": False,
        }
        event["event_hash"] = hash_payload(event)
        event = verify_red_shadow_event(event)
        atomic_write_json(event_path, event)
    atomic_write_jsonl(output / "events.jsonl", [event])
    summary = {
        "schema_version": RED_SUMMARY_SCHEMA,
        "matrix_id": matrix.matrix_id,
        "matrix_hash": matrix.matrix_hash,
        "round_id": round_id,
        "seed": seed,
        "status": "admitted" if result["admitted"] else "rejected",
        "event_hash": event["event_hash"],
        "events_file_hash": hash_file(output / "events.jsonl"),
        "promotion_executed": False,
        "memory_qualification_executed": False,
        "claim_scope": (
            "One Grounded Red shadow round only; no multi-round or "
            "comparative empirical claim."
        ),
    }
    summary["summary_hash"] = hash_payload(summary)
    atomic_write_json(output / "summary.json", summary)
    return summary


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one bounded Grounded Red shadow round"
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--workspace", default="runtime/pilots/grounded-red-shadow-v1"
    )
    parser.add_argument(
        "--matrix", default="configs/pilot/shadow_pilot_matrix_v1.json"
    )
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    result = run_grounded_red_shadow(
        project_root=args.project_root,
        workspace=args.workspace,
        client=_client_from_environment(),
        seed=args.seed,
        matrix_path=args.matrix,
        resume=not args.no_resume,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
