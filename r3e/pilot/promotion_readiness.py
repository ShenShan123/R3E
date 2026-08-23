"""Read-only gate from a completed real shadow to policy rehearsal.

The gate never constructs a provider client and never mutates a registry.  It
only accepts a fully reconstructed 12+1 shadow admission plus an explicit,
one-seed/two-round policy-lane binding.  This keeps a terminal provider
failure, an incomplete shadow, or a deterministic-only artifact from being
used accidentally as promotion evidence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from r3e.protocol.hashing import hash_file, hash_payload, read_json
from r3e.red.poison_payload import verify_poison_payload


READINESS_SCHEMA = "r3e-policy-promotion-readiness-v1"
BINDING_SCHEMA = "r3e-real-policy-promotion-rehearsal-binding-v1"
SHADOW_ADMISSION_SCHEMA = "r3e-real-provider-shadow-admission-v1"
BLUE_SUMMARY_SCHEMA = "r3e-shadow-pilot-run-summary-v1"
RED_SUMMARY_SCHEMA = "r3e-grounded-red-shadow-summary-v1"
RED_EVENT_SCHEMA = "r3e-grounded-red-shadow-event-v1"
INTEGRATED_SHADOW_SCHEMA = "r3e-integrated-grounded-red-acp-shadow-v1"
INTEGRATED_EVENT_SCHEMA = "r3e-integrated-grounded-red-acp-event-v1"


class PromotionReadinessViolation(RuntimeError):
    """Raised only by the CLI when the supplied readiness input is malformed."""


def _hash_matches(payload: Any, field: str) -> bool:
    return (
        isinstance(payload, Mapping)
        and isinstance(payload.get(field), str)
        and payload[field]
        == hash_payload({key: value for key, value in payload.items() if key != field})
    )


def _read(path: Path) -> Any | None:
    try:
        return read_json(path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _under_roots(roots: tuple[Path, ...], raw: Any) -> Path | None:
    if not isinstance(raw, str) or not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = roots[0] / path
    path = path.resolve()
    if not any(
        _is_relative_to(path, root)
        for root in roots
    ):
        return None
    return path if path.is_file() else None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _under_root(root: Path, raw: Any) -> Path | None:
    return _under_roots((root,), raw)


def _manifest_binding(
    binding: Mapping[str, Any],
    *,
    root: Path,
    field: str,
    blockers: list[str],
) -> tuple[Path | None, list[dict[str, Any]]]:
    raw = binding.get(field)
    if not isinstance(raw, Mapping) or set(raw) != {"path", "file_hash"}:
        blockers.append(f"{field}_binding")
        return None, []
    path = _under_root(root, raw.get("path"))
    if path is None:
        blockers.append(f"{field}_missing_or_outside_root")
        return None, []
    if hash_file(path) != raw.get("file_hash"):
        blockers.append(f"{field}_hash")
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("manifest row is not an object")
                rows.append(value)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        blockers.append(f"{field}_schema")
    return path, rows


def _verify_shadow(
    workspace: Path,
    *,
    blockers: list[str],
) -> dict[str, Any]:
    if (workspace / "terminal_failure.json").is_file():
        # A terminal checkpoint is authoritative even if stale success files
        # are also present; never let mixed terminal/success artifacts enter
        # a policy-promotion evidence lane.
        blockers.append("shadow_terminal_failure")
    summary = _read(workspace / "summary.json")
    if summary is None:
        if not (workspace / "terminal_failure.json").is_file():
            blockers.append("shadow_summary_missing")
        return {}
    if not _hash_matches(summary, "summary_hash"):
        blockers.append("shadow_summary_hash")
    expected = {
        "schema_version": SHADOW_ADMISSION_SCHEMA,
        "execution_mode": "smoke",
        "expected_blue_provider_calls": 12,
        "expected_red_provider_calls": 1,
        "expected_total_provider_calls": 13,
        "blue_completed_cells": 4,
        "blue_call_matched": True,
        "red_status": "admitted",
        "promotion_executed": False,
        "memory_qualification_executed": False,
        "resume_additional_calls": 0,
    }
    for key, value in expected.items():
        if summary.get(key) != value:
            blockers.append(f"shadow_{key}")

    blue = _read(workspace / "blue_matrix" / "summary.json")
    aggregate = _read(workspace / "blue_matrix" / "aggregate.json")
    if blue is None:
        blockers.append("blue_summary_missing")
    else:
        if (
            blue.get("matrix_id") != summary.get("matrix_id")
            or blue.get("matrix_hash") != summary.get("matrix_hash")
        ):
            blockers.append("blue_matrix_binding")
        if not _hash_matches(blue, "summary_hash"):
            blockers.append("blue_summary_hash")
        for key, value in {
            "schema_version": BLUE_SUMMARY_SCHEMA,
            "execution_mode": "smoke",
            "completed_cells": 4,
            "call_matched": True,
            "promotion_executed": False,
            "raam_execution_executed": False,
        }.items():
            if blue.get(key) != value:
                blockers.append(f"blue_{key}")
        if summary.get("blue_summary_hash") != blue.get("summary_hash"):
            blockers.append("blue_summary_binding")
    if aggregate is None:
        blockers.append("blue_aggregate_missing")
    else:
        if (
            aggregate.get("matrix_id") != summary.get("matrix_id")
            or aggregate.get("matrix_hash") != summary.get("matrix_hash")
        ):
            blockers.append("blue_aggregate_binding")
        if not _hash_matches(aggregate, "aggregate_hash"):
            blockers.append("blue_aggregate_hash")
        if (
            aggregate.get("completed_cells") != 4
            or aggregate.get("expected_cells") != 4
            or aggregate.get("call_matched") is not True
        ):
            blockers.append("blue_aggregate_shape")
        if blue is not None and blue.get("aggregate_hash") != aggregate.get(
            "aggregate_hash"
        ):
            blockers.append("blue_aggregate_hash_binding")
    blue_events_path = workspace / "blue_matrix" / "events.jsonl"
    if blue is None or not blue_events_path.is_file():
        blockers.append("blue_events_missing")
    elif blue.get("events_file_hash") != hash_file(blue_events_path):
        blockers.append("blue_events_hash_binding")
        arms = aggregate.get("arms")
        if not isinstance(arms, Mapping) or sum(
            int(row.get("provider_calls", -1))
            for row in arms.values()
            if isinstance(row, Mapping)
        ) != 12:
            blockers.append("blue_provider_call_total")

    red = _read(workspace / "grounded_red" / "summary.json")
    event = _read(workspace / "grounded_red" / "event.json")
    result = _read(workspace / "grounded_red" / "red_result.json")
    if red is None:
        blockers.append("red_summary_missing")
    else:
        if (
            red.get("matrix_id") != summary.get("matrix_id")
            or red.get("matrix_hash") != summary.get("matrix_hash")
        ):
            blockers.append("red_matrix_binding")
        if not _hash_matches(red, "summary_hash"):
            blockers.append("red_summary_hash")
        for key, value in {
            "schema_version": RED_SUMMARY_SCHEMA,
            "status": "admitted",
            "promotion_executed": False,
            "memory_qualification_executed": False,
        }.items():
            if red.get(key) != value:
                blockers.append(f"red_{key}")
        if summary.get("red_summary_hash") != red.get("summary_hash"):
            blockers.append("red_summary_binding")
    if result is None:
        blockers.append("red_result_missing")
    else:
        triplet = result.get("formal_triplet")
        if result.get("admitted") is not True or triplet != {
            "clean": "proved",
            "poison": "counterexample",
            "revert": "proved",
        }:
            blockers.append("red_formal_triplet")
    if event is None:
        blockers.append("red_event_missing")
    else:
        if (
            event.get("matrix_id") != summary.get("matrix_id")
            or event.get("matrix_hash") != summary.get("matrix_hash")
            or (red is not None and event.get("event_hash") != red.get("event_hash"))
        ):
            blockers.append("red_event_binding")
        if not _hash_matches(event, "event_hash"):
            blockers.append("red_event_hash")
        if event.get("schema_version") != RED_EVENT_SCHEMA:
            blockers.append("red_event_schema")
        if event.get("provider_calls") != 1:
            blockers.append("red_provider_call_total")
        if event.get("admitted") is not True:
            blockers.append("red_event_admission")
        if event.get("formal_triplet") != {
            "clean": "proved",
            "poison": "counterexample",
            "revert": "proved",
        }:
            blockers.append("red_event_formal_triplet")
        result_path = workspace / "grounded_red" / "red_result.json"
        if result_path.is_file() and event.get("red_result_file_hash") != hash_file(result_path):
            blockers.append("red_result_hash_binding")
    events_path = workspace / "grounded_red" / "events.jsonl"
    if red is None or not events_path.is_file():
        blockers.append("red_events_missing")
    elif red.get("events_file_hash") != hash_file(events_path):
        blockers.append("red_events_hash_binding")
    return dict(summary)


def _verify_integrated_shadow(
    workspace: Path,
    *,
    blockers: list[str],
) -> dict[str, Any]:
    """Verify the formal 1+12 same-poison shadow workspace read-only."""
    summary = _read(workspace / "summary.json")
    if summary is None:
        blockers.append("integrated_shadow_summary_missing")
        return {}
    if not _hash_matches(summary, "summary_hash"):
        blockers.append("integrated_shadow_summary_hash")
    expected = {
        "schema_version": INTEGRATED_SHADOW_SCHEMA,
        "expected_red_provider_calls": 1,
        "expected_blue_provider_calls": 12,
        "expected_total_provider_calls": 13,
        "provider_calls": 13,
        "call_matched": True,
        "promotion_executed": False,
        "memory_qualification_executed": False,
        "resume_additional_calls": 0,
    }
    for key, value in expected.items():
        if summary.get(key) != value:
            blockers.append(f"integrated_shadow_{key}")
    event = _read(workspace / "event.json")
    if event is None:
        blockers.append("integrated_shadow_event_missing")
    else:
        if not _hash_matches(event, "event_hash"):
            blockers.append("integrated_shadow_event_hash")
        for key, value in {
            "schema_version": INTEGRATED_EVENT_SCHEMA,
            "provider_calls": 13,
            "promotion_executed": False,
            "memory_qualification_executed": False,
        }.items():
            if event.get(key) != value:
                blockers.append(f"integrated_shadow_event_{key}")
        if summary.get("event_hash") != event.get("event_hash"):
            blockers.append("integrated_shadow_event_binding")
        for field in (
            "challenged_poison_id",
            "challenged_poison_payload_hash",
            "challenged_policy_hash",
            "failure_descriptor_hash",
        ):
            if summary.get(field) != event.get(field):
                blockers.append(f"integrated_shadow_{field}_binding")

    red = _read(workspace / "grounded_red" / "summary.json")
    red_result = _read(workspace / "grounded_red" / "red_result.json")
    candidate = _read(workspace / "grounded_red" / "execution" / "candidate.json")
    if red is None:
        blockers.append("integrated_shadow_red_summary_missing")
    elif (
        red.get("status") != "admitted"
        or red.get("promotion_executed") is not False
        or red.get("memory_qualification_executed") is not False
        or not _hash_matches(red, "summary_hash")
    ):
        blockers.append("integrated_shadow_red_admission")
    if red_result is None:
        blockers.append("integrated_shadow_red_result_missing")
    else:
        if red_result.get("formal_triplet") != {
            "clean": "proved",
            "poison": "counterexample",
            "revert": "proved",
        } or red_result.get("admitted") is not True:
            blockers.append("integrated_shadow_red_formal_triplet")
    if candidate is None:
        blockers.append("integrated_shadow_candidate_missing")
    else:
        try:
            candidate_hash = verify_poison_payload(candidate)
        except Exception:
            blockers.append("integrated_shadow_candidate_payload")
        else:
            if candidate_hash != summary.get("challenged_poison_payload_hash"):
                blockers.append("integrated_shadow_candidate_binding")
            if candidate.get("poison_id") != summary.get("challenged_poison_id"):
                blockers.append("integrated_shadow_candidate_identity")

    blue = _read(workspace / "same_poison_blue" / "summary.json")
    if blue is None:
        blockers.append("integrated_shadow_blue_summary_missing")
    else:
        if not _hash_matches(blue, "summary_hash"):
            blockers.append("integrated_shadow_blue_summary_hash")
        for key, value in {
            "blue_provider_calls": 12,
            "expected_blue_provider_calls": 12,
            "completed_cells": 4,
            "call_matched": True,
            "promotion_executed": False,
            "memory_qualification_executed": False,
        }.items():
            if blue.get(key) != value:
                blockers.append(f"integrated_shadow_blue_{key}")
        if summary.get("blue_summary_hash") != blue.get("summary_hash"):
            blockers.append("integrated_shadow_blue_binding")
        for field in (
            "challenged_poison_id",
            "challenged_poison_payload_hash",
            "challenged_policy_hash",
            "failure_descriptor_hash",
        ):
            if summary.get(field) != blue.get(field):
                blockers.append(f"integrated_shadow_blue_{field}_binding")
        if blue.get("formal_triplet") != {
            "clean": "proved",
            "poison": "counterexample",
            "revert": "proved",
        }:
            blockers.append("integrated_shadow_blue_formal_triplet")
    archive_kind = summary.get("archive_kind")
    if archive_kind not in {"residual", "covered"}:
        blockers.append("integrated_shadow_archive_kind")
    else:
        archive_path = workspace / "archives" / f"{archive_kind}.jsonl"
        if not archive_path.is_file():
            blockers.append("integrated_shadow_archive_missing")
        elif summary.get("archive_path_hash") != hash_file(archive_path):
            blockers.append("integrated_shadow_archive_hash")
    episode_manifest_path = workspace / "verified_episodes.json"
    episode_manifest = _read(episode_manifest_path)
    if episode_manifest is None:
        blockers.append("integrated_shadow_episode_manifest_missing")
    else:
        if not _hash_matches(episode_manifest, "manifest_hash"):
            blockers.append("integrated_shadow_episode_manifest_hash")
        if summary.get("verified_episode_manifest_hash") != episode_manifest.get(
            "manifest_hash"
        ):
            blockers.append("integrated_shadow_episode_manifest_binding")
        if not episode_manifest.get("episode_ids"):
            blockers.append("integrated_shadow_episode_manifest_empty")
    renewed_path = workspace / "renewed_challenge_binding.json"
    renewed = _read(renewed_path)
    if renewed is None:
        blockers.append("integrated_shadow_renewed_challenge_missing")
    else:
        if not _hash_matches(renewed, "binding_hash"):
            blockers.append("integrated_shadow_renewed_challenge_hash")
        for field in (
            "poison_payload_hash",
            "failure_descriptor_hash",
            "verified_episode_manifest_hash",
            "archive_entry_hash",
        ):
            if not renewed.get(field):
                blockers.append(f"integrated_shadow_renewed_{field}")
        if renewed.get("challenged_policy_hash") != summary.get(
            "challenged_policy_hash"
        ):
            blockers.append("integrated_shadow_renewed_policy_binding")
        if renewed.get("poison_id") != summary.get("challenged_poison_id"):
            blockers.append("integrated_shadow_renewed_poison_binding")
        if renewed.get("poison_payload_hash") != summary.get(
            "challenged_poison_payload_hash"
        ):
            blockers.append("integrated_shadow_renewed_payload_binding")
        registry_path = workspace / "policy_registry.json"
        if not registry_path.is_file():
            blockers.append("integrated_shadow_registry_missing")
        else:
            registry_file_hash = hash_file(registry_path)
            if registry_file_hash != summary.get("registry_hash_before"):
                blockers.append("integrated_shadow_registry_before_hash")
            if registry_file_hash != summary.get("registry_hash_after"):
                blockers.append("integrated_shadow_registry_after_hash")
            if renewed.get("registry_hash_before") != registry_file_hash:
                blockers.append("integrated_shadow_renewed_registry_binding")
    blue_events = workspace / "same_poison_blue" / "events.jsonl"
    if not blue_events.is_file():
        blockers.append("integrated_shadow_blue_events_missing")
    return dict(summary)


def _verify_binding(
    raw: Any,
    *,
    root: Path,
    workspace: Path,
    blockers: list[str],
) -> None:
    if not isinstance(raw, Mapping) or raw.get("schema_version") != BINDING_SCHEMA:
        blockers.append("rehearsal_binding_schema")
        return
    if raw.get("execution_mode") != "real_provider":
        blockers.append("rehearsal_execution_mode")
    if raw.get("round_ids") != ["R000", "R001"]:
        blockers.append("rehearsal_round_ids")
    if not isinstance(raw.get("challenge_seeds"), list) or len(raw["challenge_seeds"]) != 1:
        blockers.append("rehearsal_challenge_seed")
    if not isinstance(raw.get("promotion_seeds"), list) or len(raw["promotion_seeds"]) != 1:
        blockers.append("rehearsal_promotion_seed")
    for key, value in {
        "policy_promotion_enabled": True,
        "memory_promotion_enabled": False,
        "memory_qualification_enabled": False,
        "at_most_one_registry_commit": True,
    }.items():
        if raw.get(key) != value:
            blockers.append(f"rehearsal_{key}")
    model = raw.get("model_binding")
    if not isinstance(model, Mapping) or any(not str(model.get(key) or "") for key in (
        "provider_id", "model_id", "model_version"
    )):
        blockers.append("rehearsal_model_binding")
    budget = raw.get("budget_binding")
    if not isinstance(budget, Mapping) or any(
        isinstance(budget.get(key), bool)
        or not isinstance(budget.get(key), (int, float))
        or budget.get(key) <= 0
        for key in (
            "maximum_llm_calls_per_case",
            "maximum_input_tokens_per_case",
            "maximum_output_tokens_per_case",
            "maximum_wall_time_ms_per_case",
        )
    ):
        blockers.append("rehearsal_budget_binding")
    if not isinstance(raw.get("toolchain_fingerprint_hash"), str) or not raw[
        "toolchain_fingerprint_hash"
    ].startswith("sha256:"):
        blockers.append("rehearsal_toolchain_binding")
    thresholds = raw.get("promotion_thresholds")
    if not isinstance(thresholds, Mapping) or not thresholds:
        blockers.append("rehearsal_promotion_thresholds")

    _, target_rows = _manifest_binding(
        raw, root=root, field="target_manifest", blockers=blockers
    )
    _, non_target_rows = _manifest_binding(
        raw, root=root, field="non_target_manifest", blockers=blockers
    )
    target_designs = {
        str(row.get("design") or row.get("design_id") or "")
        for row in target_rows
    }
    non_target_designs = {
        str(row.get("design") or row.get("design_id") or "")
        for row in non_target_rows
    }
    if len(target_designs - {""}) < 2:
        blockers.append("target_residual_designs")
    if not non_target_rows or not non_target_designs - {""}:
        blockers.append("non_target_manifest_rows")
    if target_designs & non_target_designs:
        blockers.append("target_non_target_overlap")

    snapshots = raw.get("registry_snapshots")
    if not isinstance(snapshots, Mapping) or set(snapshots) != {"before", "after"}:
        blockers.append("registry_snapshot_binding")
    else:
        resolved: dict[str, str] = {}
        for label in ("before", "after"):
            item = snapshots.get(label)
            raw_path = item.get("path") if isinstance(item, Mapping) else None
            path = _under_root(root, raw_path) or _under_root(workspace, raw_path)
            if path is None or not isinstance(item, Mapping) or hash_file(path) != item.get("file_hash"):
                blockers.append(f"registry_{label}_snapshot")
            else:
                resolved[label] = hash_file(path)
        if len(resolved) == 2 and resolved["before"] != resolved["after"]:
            blockers.append("registry_changed")


def assess_policy_promotion_readiness(
    *,
    shadow_workspace: str | Path,
    rehearsal_binding: Mapping[str, Any],
    project_root: str | Path,
) -> dict[str, Any]:
    """Return a privacy-safe, read-only policy-promotion readiness record."""
    root = Path(project_root).resolve()
    workspace = Path(shadow_workspace).resolve()
    blockers: list[str] = []
    if isinstance(rehearsal_binding, Mapping) and (
        rehearsal_binding.get("shadow_mode") == "integrated_same_poison"
        or rehearsal_binding.get("require_same_poison") is True
    ):
        summary = _verify_integrated_shadow(workspace, blockers=blockers)
    else:
        summary = _verify_shadow(workspace, blockers=blockers)
    _verify_binding(
        rehearsal_binding,
        root=root,
        workspace=workspace,
        blockers=blockers,
    )
    evidence = {
        "shadow_summary_hash": str(summary.get("summary_hash") or ""),
        "shadow_matrix_hash": str(summary.get("matrix_hash") or ""),
        "binding_hash": hash_payload(dict(rehearsal_binding))
        if isinstance(rehearsal_binding, Mapping)
        else "",
    }
    record = {
        "schema_version": READINESS_SCHEMA,
        "evidence": evidence,
        "blockers": sorted(set(blockers)),
        "ready": not blockers,
        "claim_boundary": (
            "Readiness is a fail-closed authority check only; it performs no "
            "provider call and is not policy-promotion or empirical evidence."
        ),
    }
    record["readiness_hash"] = hash_payload(record)
    return record


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check real shadow to policy-rehearsal readiness"
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--shadow-workspace", required=True)
    parser.add_argument("--binding", required=True)
    args = parser.parse_args(argv)
    binding_path = Path(args.binding)
    if not binding_path.is_absolute():
        binding_path = Path(args.project_root) / binding_path
    binding = _read(binding_path)
    if binding is None:
        raise PromotionReadinessViolation("rehearsal binding is unreadable")
    result = assess_policy_promotion_readiness(
        shadow_workspace=args.shadow_workspace,
        rehearsal_binding=binding,
        project_root=args.project_root,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(_main())
