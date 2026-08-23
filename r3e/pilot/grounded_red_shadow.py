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
from r3e.providers.openai_compatible import sanitize_provider_diagnostics
from r3e.protocol.hashing import (
    atomic_write_json,
    atomic_write_jsonl,
    hash_file,
    hash_payload,
    read_json,
)
from r3e.protocol.ledger import append_ledger
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
from r3e.red.poison_payload import bind_poison_payload


RED_EVENT_SCHEMA = "r3e-grounded-red-shadow-event-v1"
RED_SUMMARY_SCHEMA = "r3e-grounded-red-shadow-summary-v1"
RED_PROVIDER_CALL_SCHEMA = "r3e-grounded-red-provider-call-v1"
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


class _GroundedRedCallAccountingProvider:
    """Record the real Red request boundary without storing prompt material."""

    def __init__(
        self,
        provider: Any,
        *,
        ledger_path: Path,
        matrix_id: str,
        matrix_hash: str,
        case_id: str,
        seed: int,
    ):
        self._provider = provider
        self._ledger_path = ledger_path
        self._identity = {
            "schema_version": RED_PROVIDER_CALL_SCHEMA,
            "matrix_id": matrix_id,
            "matrix_hash": matrix_hash,
            "case_id": case_id,
            "seed": int(seed),
            "arm_id": "grounded_red",
        }

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    def choose_target(self, **kwargs: Any) -> dict[str, Any]:
        assignment = kwargs.get("assignment")
        if not isinstance(assignment, Mapping):
            raise GroundedRedShadowViolation(
                "Grounded Red provider call is missing its assignment"
            )
        started = append_ledger(
            self._ledger_path,
            {
                **self._identity,
                "event_type": "provider_call_started",
                "assignment_id": str(assignment.get("assignment_id") or ""),
                "intent_id": str(assignment.get("intent_id") or ""),
            },
        )
        try:
            result = self._provider.choose_target(**kwargs)
        except Exception as exc:
            failure = {
                **self._identity,
                "event_type": "provider_call_failed",
                "call_ledger_index": started["ledger_index"],
                "failure_class": type(exc).__name__,
            }
            diagnostics = sanitize_provider_diagnostics(
                getattr(exc, "diagnostics", None)
            )
            if diagnostics:
                failure["provider_diagnostics"] = diagnostics
            append_ledger(self._ledger_path, failure)
            raise
        append_ledger(
            self._ledger_path,
            {
                **self._identity,
                "event_type": "provider_call_completed",
                "call_ledger_index": started["ledger_index"],
            },
        )
        return result


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


def _verify_red_resume_artifacts(
    *,
    output: Path,
    matrix: Any,
    round_id: str,
    seed: int,
    event: Mapping[str, Any],
    result_path: Path,
) -> None:
    """Reject a completed Red round if its event ledger was altered."""
    summary_path = output / "summary.json"
    try:
        summary = read_json(summary_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise GroundedRedShadowViolation(
            "completed red shadow summary is unreadable"
        ) from exc
    if not isinstance(summary, Mapping) or summary.get("summary_hash") != hash_payload({
        key: value for key, value in summary.items() if key != "summary_hash"
    }):
        raise GroundedRedShadowViolation(
            "completed red shadow summary hash mismatch"
        )
    if any(summary.get(key) != value for key, value in {
        "schema_version": RED_SUMMARY_SCHEMA,
        "matrix_id": matrix.matrix_id,
        "matrix_hash": matrix.matrix_hash,
        "round_id": round_id,
        "seed": int(seed),
        "promotion_executed": False,
        "memory_qualification_executed": False,
        "event_hash": event["event_hash"],
    }.items()):
        raise GroundedRedShadowViolation(
            "completed red shadow summary binding mismatch"
        )
    events_path = output / "events.jsonl"
    if not events_path.is_file() or summary.get("events_file_hash") != hash_file(events_path):
        raise GroundedRedShadowViolation(
            "completed red shadow event ledger hash mismatch"
        )
    try:
        rows = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise GroundedRedShadowViolation(
            "completed red shadow event ledger is invalid"
        ) from exc
    if rows != [dict(event)] or not result_path.is_file() or event.get(
        "red_result_file_hash"
    ) != hash_file(result_path):
        raise GroundedRedShadowViolation(
            "completed red shadow event binding mismatch"
        )


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
    matrix_id: str,
    matrix_hash: str,
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
    adapter.choice_provider = _GroundedRedCallAccountingProvider(
        adapter.choice_provider,
        ledger_path=workspace / "provider_calls.jsonl",
        matrix_id=matrix_id,
        matrix_hash=matrix_hash,
        case_id=case_id,
        seed=seed,
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
    # Persist the exact runner-bound poison so the integrated 1+12 admission
    # can route this Red result into every ACP arm without regenerating Red.
    # The file stays in the caller-owned ignored workspace and is validated by
    # verify_poison_payload before any Blue provider boundary is crossed.
    admitted_candidate = deepcopy(candidate)
    admitted_candidate["validity"] = validity_result["validity"]
    admitted_candidate["grounded_authority_bundle"] = verified
    admitted_candidate = bind_poison_payload(admitted_candidate)
    atomic_write_json(workspace / "candidate.json", admitted_candidate)
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
    # The integrated Grounded shadow uses the frozen portfolio-aware base
    # policy everywhere: candidate generation, formal authority, and event
    # provenance must expose the same challenged policy hash.  Reading the
    # legacy v1 snapshot here would create an event that cannot be joined to
    # its formal authority bundle.
    policy = PolicyState.from_dict(read_json(
        root / "configs/base_policy/frozen_base_policy_v3.json"
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
        if (output / "summary.json").is_file():
            _verify_red_resume_artifacts(
                output=output,
                matrix=matrix,
                round_id=round_id,
                seed=seed,
                event=event,
                result_path=result_path,
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
            matrix_id=matrix.matrix_id,
            matrix_hash=matrix.matrix_hash,
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
