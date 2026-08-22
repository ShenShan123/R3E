"""Resumable A/B/C/D executor for the frozen Shadow Pilot Matrix."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any, Mapping

from r3e.blue.portfolio.allocator import (
    OfflineAdaptiveAllocator,
    load_offline_allocator_state,
)
from r3e.blue.portfolio.audit import verify_blue_evaluation
from r3e.blue.portfolio.candidate_executor import CandidatePortfolioExecutor
from r3e.blue.portfolio.lens_registry import load_lens_registry
from r3e.blue.portfolio.openai_provider import OpenAICompatibleCandidateProvider
from r3e.blue.portfolio.oracle_gate_verifier import (
    PublicManifestOracleVerifier,
    public_case_from_manifest,
)
from r3e.blue.portfolio.router import load_descriptor_router
from r3e.blue.portfolio.schema import build_candidate_portfolio_binding
from r3e.pilot.grd8_acp7_smoke import (
    _baseline_descriptor,
    _client_from_environment,
)
from r3e.pilot.shadow_aggregate import (
    CELL_EVENT_SCHEMA,
    aggregate_cell_events,
    verify_cell_event,
)
from r3e.pilot.shadow_matrix import (
    ShadowPilotMatrix,
    load_shadow_pilot_matrix,
)
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


RUN_SUMMARY_SCHEMA = "r3e-shadow-pilot-run-summary-v1"


class ShadowPilotRunnerViolation(RuntimeError):
    """Raised when a shadow cell cannot be executed or resumed safely."""


SHADOW_PROVIDER_CALL_SCHEMA = "r3e-shadow-provider-call-v1"


class _ShadowCallAccountingProvider:
    """Persist call-start metadata before crossing the provider boundary.

    The ledger deliberately contains assignment identity and exception class
    labels only.  It never stores prompts, RTL, provider response text, or
    machine-specific paths.  A started row is durable even when the provider
    returns malformed/empty content or the process is interrupted, so the
    terminal failure checkpoint can report consumed calls conservatively.
    """

    def __init__(
        self,
        provider: Any,
        *,
        ledger_path: Path,
        matrix_id: str,
        matrix_hash: str,
        case_id: str,
        seed: int,
        arm_id: str,
    ):
        self._provider = provider
        self._ledger_path = ledger_path
        self._identity = {
            "schema_version": SHADOW_PROVIDER_CALL_SCHEMA,
            "matrix_id": matrix_id,
            "matrix_hash": matrix_hash,
            "case_id": case_id,
            "seed": int(seed),
            "arm_id": arm_id,
        }

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    def generate_candidate(self, **kwargs: Any) -> dict[str, Any]:
        slot = kwargs.get("slot")
        candidate_id = str(kwargs.get("candidate_id") or "")
        if not isinstance(slot, Mapping):
            raise ShadowPilotRunnerViolation(
                "provider call assignment is missing its frozen slot"
            )
        started = append_ledger(
            self._ledger_path,
            {
                **self._identity,
                "event_type": "provider_call_started",
                "candidate_id": candidate_id,
                "slot_index": int(slot["slot_index"]),
                "candidate_seed": int(slot["candidate_seed"]),
            },
        )
        try:
            result = self._provider.generate_candidate(**kwargs)
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


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", value)


def _manifest_rows(matrix: ShadowPilotMatrix) -> dict[str, dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in matrix.target_manifest_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    return {str(row.get("case_id") or ""): row for row in rows}


def _verify_client(matrix: ShadowPilotMatrix, client: Any) -> None:
    config = getattr(client, "config", None)
    identity = {
        "provider_id": str(getattr(config, "provider_id", "") or ""),
        "model_id": str(getattr(config, "model_id", "") or ""),
        "model_version": str(getattr(config, "model_version", "") or ""),
    }
    if identity != matrix.model_binding:
        raise ShadowPilotRunnerViolation(
            "provider identity does not match frozen shadow matrix"
        )


def _branch_policy(
    base: PolicyState,
    *,
    arm_id: str,
    portfolio: Any,
) -> PolicyState:
    return base.with_updates(
        policy_id=f"SHADOW_{arm_id}",
        parent_policy_id=base.policy_id,
        parent_policy_hash=base.policy_hash,
        created_round=0,
        status="candidate",
        proposal_operator="shadow_portfolio_binding",
        candidate_portfolio_binding=build_candidate_portfolio_binding(
            portfolio
        ),
    )


def _components(
    *,
    root: Path,
    cell_dir: Path,
    matrix: ShadowPilotMatrix,
    arm_id: str,
    case: Mapping[str, Any],
    seed: int,
    descriptor: Mapping[str, Any],
    client: Any,
    call_ledger_path: Path,
) -> tuple[Any, Any, Any, Any, PolicyState]:
    portfolio = matrix.portfolios[arm_id]
    registry = load_lens_registry(
        root / "configs/blue/lens_registry_v1.json",
        project_root=root,
    )
    router = None
    allocator = None
    if portfolio.mode == "descriptor_routed":
        router = load_descriptor_router(
            root / "configs/blue/descriptor_router_v1.json"
        )
    if portfolio.mode == "adaptive":
        allocator = OfflineAdaptiveAllocator(
            load_offline_allocator_state(
                root / "configs/blue/offline_allocator_state_v1.json"
            )
        )
    base = PolicyState.from_dict(read_json(
        root / "configs/base_policy/frozen_base_policy_v3.json"
    ))
    policy = _branch_policy(base, arm_id=arm_id, portfolio=portfolio)
    verifier = PublicManifestOracleVerifier(
        project_root=root,
        workspace=cell_dir / "verifier",
        run_context_hash=hash_payload({
            "matrix_hash": matrix.matrix_hash,
            "case_id": case["case_id"],
            "seed": seed,
            "arm_id": arm_id,
            "policy_hash": policy.policy_hash,
        }),
        maximum_ast_edits=64,
        timeout_seconds=float(
            matrix.controls["maximum_wall_seconds_per_cell"]
        ),
    )
    provider = OpenAICompatibleCandidateProvider(
        client,
        verifier_id=verifier.verifier_id,
        verifier_version=verifier.verifier_version,
    )
    provider = _ShadowCallAccountingProvider(
        provider,
        ledger_path=call_ledger_path,
        matrix_id=matrix.matrix_id,
        matrix_hash=matrix.matrix_hash,
        case_id=str(case["case_id"]),
        seed=seed,
        arm_id=arm_id,
    )
    executor = CandidatePortfolioExecutor(
        registry=registry,
        portfolio=portfolio,
        provider=provider,
        verifier=verifier,
        project_root=root,
        router=router,
        allocator=allocator,
    )
    return executor, verifier, router, allocator, policy


def _audit_evaluation(
    evaluation: Mapping[str, Any],
    *,
    matrix: ShadowPilotMatrix,
    arm_id: str,
    descriptor: Mapping[str, Any],
    executor: CandidatePortfolioExecutor,
    verifier: PublicManifestOracleVerifier,
    router: Any,
    allocator: Any,
    policy: PolicyState,
) -> dict[str, Any]:
    verified = verify_blue_evaluation(
        evaluation,
        policy=policy,
        registry=executor.registry,
        portfolio=matrix.portfolios[arm_id],
        provider=executor.provider,
        expected_verifier_hash=verifier.verifier_hash,
        expected_semantic_signature_provider_hash=(
            matrix.portfolios[arm_id].semantic_signature_provider_hash
        ),
        router=router,
        allocator=allocator,
        descriptor=descriptor,
    )
    calls = verified["resource_usage"]["provider_calls"]
    expected = matrix.controls["call_budget_per_cell"]
    if calls != expected:
        raise ShadowPilotRunnerViolation(
            "shadow cell is not call matched to frozen budget"
        )
    return verified


def _cell_event(
    *,
    matrix: ShadowPilotMatrix,
    arm_id: str,
    policy: PolicyState,
    evaluation: Mapping[str, Any],
    evaluation_hash: str,
) -> dict[str, Any]:
    usage = evaluation["resource_usage"]
    diversity = evaluation["portfolio_diversity_receipt"]
    selection = evaluation["selection_receipt"]
    payload = {
        "schema_version": CELL_EVENT_SCHEMA,
        "matrix_id": matrix.matrix_id,
        "matrix_hash": matrix.matrix_hash,
        "case_id": evaluation["case_id"],
        "seed": evaluation["seed"],
        "arm_id": arm_id,
        "portfolio_hash": evaluation["portfolio_hash"],
        "policy_hash": policy.policy_hash,
        "model_binding_hash": hash_payload(matrix.model_binding),
        "blue_evaluation_hash": evaluation_hash,
        "provider_calls": usage["provider_calls"],
        "verifier_calls": usage["verifier_calls"],
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "candidate_count": len(
            evaluation["candidate_generation_receipts"]
        ),
        "candidate_success_count": diversity["candidate_success_count"],
        "portfolio_success": evaluation["oracle_ok"],
        "semantic_unique_candidate_count": diversity[
            "semantic_unique_candidate_count"
        ],
        "semantic_duplicate_pairs": diversity[
            "semantic_duplicate_pairs"
        ],
        "lens_collapse": diversity["lens_collapse"],
        "selected_candidate_id": str(
            selection["selected_candidate_id"] or ""
        ),
    }
    payload["event_hash"] = hash_payload(payload)
    return verify_cell_event(payload)


def run_shadow_matrix(
    *,
    project_root: str | Path,
    workspace: str | Path,
    matrix_path: str | Path = "configs/pilot/shadow_pilot_matrix_v1.json",
    client: Any,
    smoke_only: bool = False,
    resume: bool = True,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    output = Path(workspace).resolve()
    output.mkdir(parents=True, exist_ok=True)
    matrix = load_shadow_pilot_matrix(matrix_path, project_root=root)
    _verify_client(matrix, client)
    case_ids = matrix.smoke_case_ids if smoke_only else matrix.case_ids
    seeds = matrix.smoke_seeds if smoke_only else matrix.seeds
    expected_cells = {
        (case_id, seed, arm["arm_id"])
        for case_id in case_ids
        for seed in seeds
        for arm in matrix.arms
    }
    manifest = _manifest_rows(matrix)
    events: list[dict[str, Any]] = []
    for case_id in case_ids:
        case = public_case_from_manifest(
            manifest[case_id], project_root=root
        )
        baseline_dir = output / "baselines" / _safe_component(case_id)
        descriptor = _baseline_descriptor(
            case=case, workspace=baseline_dir
        )
        atomic_write_json(baseline_dir / "failure_descriptor.json", descriptor)
        for seed in seeds:
            for arm in matrix.arms:
                arm_id = arm["arm_id"]
                cell_dir = (
                    output / "cells" / _safe_component(case_id)
                    / f"seed_{seed}" / f"arm_{arm_id}"
                )
                cell_dir.mkdir(parents=True, exist_ok=True)
                executor, verifier, router, allocator, policy = _components(
                    root=root,
                    cell_dir=cell_dir,
                    matrix=matrix,
                    arm_id=arm_id,
                    case=case,
                    seed=seed,
                    descriptor=descriptor,
                    client=client,
                    call_ledger_path=output / "provider_calls.jsonl",
                )
                evaluation_path = cell_dir / "blue_evaluation.json"
                summary_path = cell_dir / "cell_summary.json"
                if summary_path.exists():
                    if not resume or not evaluation_path.is_file():
                        raise ShadowPilotRunnerViolation(
                            "completed cell cannot be overwritten"
                        )
                    event = verify_cell_event(read_json(summary_path))
                    if (
                        event["case_id"] != case_id
                        or event["seed"] != seed
                        or event["arm_id"] != arm_id
                        or event["matrix_hash"] != matrix.matrix_hash
                        or event["blue_evaluation_hash"]
                        != hash_file(evaluation_path)
                    ):
                        raise ShadowPilotRunnerViolation(
                            "resumed cell output hash mismatch"
                        )
                    evaluation = _audit_evaluation(
                        read_json(evaluation_path),
                        matrix=matrix,
                        arm_id=arm_id,
                        descriptor=descriptor,
                        executor=executor,
                        verifier=verifier,
                        router=router,
                        allocator=allocator,
                        policy=policy,
                    )
                    if event != _cell_event(
                        matrix=matrix,
                        arm_id=arm_id,
                        policy=policy,
                        evaluation=evaluation,
                        evaluation_hash=hash_file(evaluation_path),
                    ):
                        raise ShadowPilotRunnerViolation(
                            "resumed cell summary differs from evaluation"
                        )
                else:
                    if evaluation_path.exists():
                        raise ShadowPilotRunnerViolation(
                            "incomplete cell has an unbound evaluation"
                        )
                    evaluation = executor.execute(
                        policy=policy,
                        case=deepcopy(case),
                        descriptor=descriptor,
                        run_seed=seed,
                    )
                    evaluation = _audit_evaluation(
                        evaluation,
                        matrix=matrix,
                        arm_id=arm_id,
                        descriptor=descriptor,
                        executor=executor,
                        verifier=verifier,
                        router=router,
                        allocator=allocator,
                        policy=policy,
                    )
                    atomic_write_json(evaluation_path, evaluation)
                    event = _cell_event(
                        matrix=matrix,
                        arm_id=arm_id,
                        policy=policy,
                        evaluation=evaluation,
                        evaluation_hash=hash_file(evaluation_path),
                    )
                    atomic_write_json(summary_path, event)
                events.append(event)
                atomic_write_jsonl(output / "events.jsonl", events)
    aggregate = aggregate_cell_events(
        events,
        matrix_id=matrix.matrix_id,
        matrix_hash=matrix.matrix_hash,
        expected_cells=expected_cells,
        expected_calls_per_cell=matrix.controls["call_budget_per_cell"],
    )
    atomic_write_json(output / "aggregate.json", aggregate)
    summary = {
        "schema_version": RUN_SUMMARY_SCHEMA,
        "matrix_id": matrix.matrix_id,
        "matrix_hash": matrix.matrix_hash,
        "execution_mode": "smoke" if smoke_only else "full_matrix",
        "model_binding": deepcopy(matrix.model_binding),
        "events_file_hash": hash_file(output / "events.jsonl"),
        "aggregate_hash": aggregate["aggregate_hash"],
        "completed_cells": aggregate["completed_cells"],
        "call_matched": aggregate["call_matched"],
        "promotion_executed": False,
        "raam_execution_executed": False,
        "claim_scope": aggregate["claim_scope"],
    }
    summary["summary_hash"] = hash_payload(summary)
    atomic_write_json(output / "summary.json", summary)
    return summary


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the frozen A/B/C/D Shadow Pilot Matrix"
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--workspace", default="runtime/pilots/shadow-matrix-v1"
    )
    parser.add_argument(
        "--matrix", default="configs/pilot/shadow_pilot_matrix_v1.json"
    )
    parser.add_argument("--smoke-only", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    result = run_shadow_matrix(
        project_root=args.project_root,
        workspace=args.workspace,
        matrix_path=args.matrix,
        client=_client_from_environment(),
        smoke_only=args.smoke_only,
        resume=not args.no_resume,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
