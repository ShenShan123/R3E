"""Same-poison Grounded Red -> ACP portfolio conformance lane.

The real shadow admission historically executed the ACP matrix and the
Grounded Red call as independent children.  This module is the missing
join: an already admitted Grounded poison is supplied to every ACP arm, so
all twelve Blue calls challenge the same poison payload and FailureDescriptor.
The Red call itself remains outside this function and must be recorded in
the caller's Red ledger before invoking this lane.

No promotion or RAAM action is reachable from this module.  It is intentionally
resumable and can be used with an injected transport before a future wrapper
connects the real Red child directly.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping

from r3e.arena.portfolio import extract_grounded_failure_descriptor
from r3e.pilot.shadow_matrix import load_shadow_pilot_matrix
from r3e.pilot.shadow_runner import (
    _audit_evaluation,
    _components,
    _safe_component,
    _verify_client,
)
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import (
    atomic_write_json,
    atomic_write_jsonl,
    hash_file,
    hash_payload,
    read_json,
)
from r3e.red.poison_payload import verify_poison_payload


SAME_POISON_SCHEMA = "r3e-same-poison-grounded-acp-shadow-v1"
SAME_POISON_EVENT_SCHEMA = "r3e-same-poison-grounded-acp-cell-v1"


class SamePoisonShadowViolation(RuntimeError):
    """Raised when Red authority cannot be safely joined to ACP."""


def _case_from_poison(
    poison: Mapping[str, Any],
    *,
    project_root: Path,
) -> dict[str, Any]:
    """Build the verifier case from runner-owned Red manifest facts."""
    buggy_raw = str(poison.get("buggy_rtl") or "")
    buggy = Path(buggy_raw).resolve()
    if not buggy.is_file():
        raise SamePoisonShadowViolation("admitted poison RTL is unavailable")
    source = buggy.read_text(encoding="utf-8")
    if not source:
        raise SamePoisonShadowViolation("admitted poison RTL is empty")
    required = ("case_id", "golden_rtl", "tb_sources", "tb_output")
    if any(not poison.get(field) for field in required):
        raise SamePoisonShadowViolation("admitted poison lacks frozen case facts")
    deps = poison.get("deps") or []
    tb_sources = poison.get("tb_sources") or []
    manifest_hashes = poison.get("manifest_file_hashes")
    if not isinstance(deps, list) or not isinstance(tb_sources, list):
        raise SamePoisonShadowViolation("admitted poison case assets are invalid")
    if not isinstance(manifest_hashes, Mapping) or not manifest_hashes:
        raise SamePoisonShadowViolation("admitted poison lacks manifest asset hashes")
    # The public manifest assets are still checked against project_root by the
    # verifier.  Only the generated buggy artifact is allowed from the
    # explicitly configured external Red workspace.
    return {
        "case_id": str(poison["case_id"]),
        "buggy_rtl_path": str(buggy),
        "buggy_rtl_source": source,
        "buggy_rtl_hash": hash_payload(source),
        "golden_rtl": str(poison["golden_rtl"]),
        "deps": [str(value) for value in deps],
        "tb_sources": [str(value) for value in tb_sources],
        "tb_output": str(poison["tb_output"]),
        "top_module": str(poison.get("top_module") or ""),
        "sim_timeout": float(poison.get("sim_timeout") or 20.0),
        "manifest_file_hashes": deepcopy(dict(manifest_hashes)),
        "project_root": str(project_root),
    }


def _verify_red_binding(
    poison: Mapping[str, Any],
    *,
    policy: PolicyState,
) -> tuple[str, str, dict[str, Any]]:
    """Verify payload, formal admission and descriptor before Blue starts."""
    try:
        poison_payload_hash = verify_poison_payload(dict(poison))
    except Exception as exc:
        raise SamePoisonShadowViolation(
            "same-poison lane received an invalid poison payload"
        ) from exc
    if poison.get("challenged_policy_hash") != policy.policy_hash:
        raise SamePoisonShadowViolation(
            "same-poison lane policy binding differs from Grounded Red"
        )
    validity = poison.get("validity")
    if not isinstance(validity, Mapping) or validity.get("proven_valid") is not True:
        raise SamePoisonShadowViolation("only admitted Grounded poison may enter ACP")
    authority = poison.get("grounded_authority_bundle")
    if not isinstance(authority, Mapping):
        raise SamePoisonShadowViolation("same-poison lane requires Grounded authority")
    triplet = authority.get("formal_proof_triplet")
    if not isinstance(triplet, Mapping) or {
        name: (triplet.get(name) or {}).get("verdict")
        for name in ("clean", "poison", "revert")
    } != {"clean": "proved", "poison": "counterexample", "revert": "proved"}:
        raise SamePoisonShadowViolation("Grounded formal proof triplet is not admitted")
    try:
        descriptor = extract_grounded_failure_descriptor(poison)
    except Exception as exc:
        raise SamePoisonShadowViolation("Grounded FailureDescriptor is not bound") from exc
    return (
        str(poison.get("poison_id") or ""),
        poison_payload_hash,
        descriptor,
    )


def _cell_event(
    *,
    matrix: Any,
    arm_id: str,
    policy: PolicyState,
    poison_id: str,
    poison_payload_hash: str,
    descriptor: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    evaluation_hash: str,
) -> dict[str, Any]:
    usage = evaluation["resource_usage"]
    payload = {
        "schema_version": SAME_POISON_EVENT_SCHEMA,
        "matrix_id": matrix.matrix_id,
        "matrix_hash": matrix.matrix_hash,
        "case_id": evaluation["case_id"],
        "seed": evaluation["seed"],
        "arm_id": arm_id,
        "portfolio_hash": evaluation["portfolio_hash"],
        "evaluation_policy_hash": policy.policy_hash,
        "challenged_poison_id": poison_id,
        "challenged_poison_payload_hash": poison_payload_hash,
        "failure_descriptor_hash": descriptor["descriptor_hash"],
        "blue_evaluation_hash": evaluation_hash,
        "provider_calls": usage["provider_calls"],
        "verifier_calls": usage["verifier_calls"],
        "oracle_ok": evaluation["oracle_ok"],
    }
    payload["event_hash"] = hash_payload(payload)
    return payload


def _verify_event(event: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(event)
    expected = hash_payload({key: value for key, value in payload.items() if key != "event_hash"})
    if payload.get("schema_version") != SAME_POISON_EVENT_SCHEMA:
        raise SamePoisonShadowViolation("same-poison cell event schema mismatch")
    if payload.get("event_hash") != expected:
        raise SamePoisonShadowViolation("same-poison cell event hash mismatch")
    return payload


def run_same_poison_blue_portfolio(
    *,
    project_root: str | Path,
    workspace: str | Path,
    client: Any,
    poison: Mapping[str, Any],
    matrix_path: str | Path = "configs/pilot/shadow_pilot_matrix_v1.json",
    seed: int = 17,
    red_provider_calls: int = 1,
    resume: bool = True,
) -> dict[str, Any]:
    """Run all four ACP arms against one admitted Grounded poison.

    ``poison`` must be the exact runner-owned payload emitted by Red.  The
    function intentionally does not regenerate it, which makes a resumed
    invocation zero-call safe and leaves Red provider accounting in its own
    ledger.
    """
    root = Path(project_root).resolve()
    output = Path(workspace).resolve()
    output.mkdir(parents=True, exist_ok=True)
    matrix = load_shadow_pilot_matrix(matrix_path, project_root=root)
    _verify_client(matrix, client)
    if seed not in matrix.seeds:
        raise SamePoisonShadowViolation("seed is outside frozen shadow matrix")
    if red_provider_calls != 1:
        raise SamePoisonShadowViolation("same-poison lane requires one Red call")
    policy = PolicyState.from_dict(read_json(root / "configs/base_policy/frozen_base_policy_v3.json"))
    poison_id, poison_payload_hash, descriptor = _verify_red_binding(poison, policy=policy)
    case = _case_from_poison(poison, project_root=root)
    expected_cells = {(case["case_id"], seed, arm["arm_id"]) for arm in matrix.arms}
    summary_path = output / "summary.json"
    if summary_path.is_file():
        summary = read_json(summary_path)
        expected_hash = hash_payload({key: value for key, value in summary.items() if key != "summary_hash"})
        if summary.get("summary_hash") != expected_hash:
            raise SamePoisonShadowViolation("same-poison summary hash mismatch")
        if summary.get("schema_version") != SAME_POISON_SCHEMA:
            raise SamePoisonShadowViolation("same-poison summary schema mismatch")
        if summary.get("challenged_poison_payload_hash") != poison_payload_hash:
            raise SamePoisonShadowViolation("resumed poison payload differs")
        if (
            summary.get("matrix_id") != matrix.matrix_id
            or summary.get("matrix_hash") != matrix.matrix_hash
            or summary.get("case_id") != case["case_id"]
            or summary.get("seed") != seed
            or summary.get("red_provider_calls") != red_provider_calls
            or summary.get("expected_blue_provider_calls")
            != len(matrix.arms) * int(matrix.controls["call_budget_per_cell"])
            or summary.get("completed_cells") != len(expected_cells)
            or summary.get("call_matched") is not True
            or summary.get("promotion_executed") is not False
            or summary.get("memory_qualification_executed") is not False
        ):
            raise SamePoisonShadowViolation("completed same-poison summary is incomplete")
        event_paths = sorted((output / "cells").glob("*/cell_summary.json"))
        if len(event_paths) != len(expected_cells):
            raise SamePoisonShadowViolation("completed same-poison cells are incomplete")
        for event_path in event_paths:
            event = _verify_event(read_json(event_path))
            if (
                event.get("matrix_hash") != matrix.matrix_hash
                or event.get("case_id") != case["case_id"]
                or event.get("seed") != seed
                or event.get("challenged_poison_id") != poison_id
                or event.get("challenged_poison_payload_hash") != poison_payload_hash
                or event.get("failure_descriptor_hash") != descriptor["descriptor_hash"]
            ):
                raise SamePoisonShadowViolation("completed cell authority binding mismatch")
        return summary

    events: list[dict[str, Any]] = []
    for arm in matrix.arms:
        arm_id = str(arm["arm_id"])
        cell_dir = output / "cells" / f"arm_{_safe_component(arm_id)}"
        cell_dir.mkdir(parents=True, exist_ok=True)
        evaluation_path = cell_dir / "blue_evaluation.json"
        event_path = cell_dir / "cell_summary.json"
        executor, verifier, router, allocator, eval_policy = _components(
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
        # Grounded Red artifacts live in an ignored external workspace.  The
        # verifier accepts precisely this generated poison directory and no
        # broader path.
        verifier.allowed_case_artifact_roots = (Path(str(poison["buggy_rtl"])).resolve().parent,)
        if event_path.is_file():
            if not resume or not evaluation_path.is_file():
                raise SamePoisonShadowViolation("completed same-poison cell cannot be overwritten")
            event = _verify_event(read_json(event_path))
            if event.get("challenged_poison_payload_hash") != poison_payload_hash:
                raise SamePoisonShadowViolation("cell poison binding mismatch")
            evaluation = _audit_evaluation(
                read_json(evaluation_path),
                matrix=matrix,
                arm_id=arm_id,
                descriptor=descriptor,
                executor=executor,
                verifier=verifier,
                router=router,
                allocator=allocator,
                policy=eval_policy,
            )
            if event.get("blue_evaluation_hash") != hash_file(evaluation_path):
                raise SamePoisonShadowViolation("resumed cell evaluation hash mismatch")
        else:
            if evaluation_path.exists():
                raise SamePoisonShadowViolation("incomplete same-poison cell has an unbound evaluation")
            evaluation = executor.execute(
                policy=eval_policy,
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
                policy=eval_policy,
            )
            atomic_write_json(evaluation_path, evaluation)
            event = _cell_event(
                matrix=matrix,
                arm_id=arm_id,
                policy=eval_policy,
                poison_id=poison_id,
                poison_payload_hash=poison_payload_hash,
                descriptor=descriptor,
                evaluation=evaluation,
                evaluation_hash=hash_file(evaluation_path),
            )
            atomic_write_json(event_path, event)
        events.append(event)
        atomic_write_jsonl(output / "events.jsonl", events)

    provider_calls = sum(int(event["provider_calls"]) for event in events)
    expected_blue_calls = len(matrix.arms) * int(matrix.controls["call_budget_per_cell"])
    summary = {
        "schema_version": SAME_POISON_SCHEMA,
        "matrix_id": matrix.matrix_id,
        "matrix_hash": matrix.matrix_hash,
        "case_id": case["case_id"],
        "seed": seed,
        "red_provider_calls": red_provider_calls,
        "blue_provider_calls": provider_calls,
        "expected_blue_provider_calls": expected_blue_calls,
        "expected_total_provider_calls": red_provider_calls + expected_blue_calls,
        "completed_cells": len(events),
        "call_matched": provider_calls == expected_blue_calls,
        "challenged_poison_id": poison_id,
        "challenged_poison_payload_hash": poison_payload_hash,
        "challenged_policy_hash": str(poison["challenged_policy_hash"]),
        "failure_descriptor_hash": descriptor["descriptor_hash"],
        "formal_triplet": {name: (poison["grounded_authority_bundle"]["formal_proof_triplet"][name]["verdict"]) for name in ("clean", "poison", "revert")},
        "promotion_executed": False,
        "memory_qualification_executed": False,
        "claim_scope": "Same admitted Grounded poison routed to four ACP arms; no promotion or empirical gain claim.",
    }
    summary["summary_hash"] = hash_payload(summary)
    atomic_write_json(summary_path, summary)
    return summary


__all__ = ["SAME_POISON_SCHEMA", "SamePoisonShadowViolation", "run_same_poison_blue_portfolio"]
