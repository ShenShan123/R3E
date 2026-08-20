"""Deterministic policy-only promotion rehearsal.

This is the engineering bridge between shadow admission and a future
authorized real-provider transition.  It freezes two rounds, one seed, an
independent non-target manifest, and a single policy promotion lane while
keeping RAAM promotion disabled.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from r3e.arena.fake_adapters import DeterministicEvolutionAdapter
from r3e.arena.integrated_loop import (
    IntegratedCoevolutionLoop,
    verify_integrated_round,
)
from r3e.arena.manifests import make_manifest, verify_manifest
from r3e.memory.fake_adapter import DeterministicMemoryAdapter
from r3e.policy.registry_v2 import (
    get_active_policy,
    initialize_registry,
    load_registry,
)
from r3e.protocol.hashing import (
    atomic_write_json,
    hash_payload,
    read_json,
)


REHEARSAL_SCHEMA = "r3e-policy-promotion-rehearsal-v1"


class PolicyPromotionRehearsalViolation(RuntimeError):
    """Raised when the frozen policy-only rehearsal cannot be rebuilt."""


def load_rehearsal_config(
    path: str | Path,
    *,
    project_root: str | Path,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    source = Path(path)
    if not source.is_absolute():
        source = root / source
    source = source.resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise PolicyPromotionRehearsalViolation(
            "rehearsal config escapes project root"
        ) from exc
    payload = read_json(source)
    if not isinstance(payload, Mapping):
        raise PolicyPromotionRehearsalViolation("rehearsal config is not an object")
    payload = dict(payload)
    if payload.get("schema_version") != (
        "r3e-policy-promotion-rehearsal-config-v1"
    ):
        raise PolicyPromotionRehearsalViolation("rehearsal config schema mismatch")
    expected_hash = hash_payload({
        key: value for key, value in payload.items() if key != "config_hash"
    })
    if payload.get("config_hash") != expected_hash:
        raise PolicyPromotionRehearsalViolation("rehearsal config hash mismatch")
    if payload.get("execution_mode") != "deterministic_fake":
        raise PolicyPromotionRehearsalViolation(
            "rehearsal must use the deterministic fake adapter"
        )
    if payload.get("round_ids") != ["R000", "R001"]:
        raise PolicyPromotionRehearsalViolation(
            "rehearsal requires exactly R000 and R001"
        )
    if payload.get("promotion_lane_schedule") != ["collect", "policy"]:
        raise PolicyPromotionRehearsalViolation(
            "rehearsal lane schedule must be collect then policy"
        )
    fixed = payload.get("fixed_binding")
    if not isinstance(fixed, Mapping) or fixed.get("challenge_seeds") != [17]:
        raise PolicyPromotionRehearsalViolation(
            "rehearsal must freeze one challenge seed"
        )
    lane = payload.get("policy_lane")
    if not isinstance(lane, Mapping) or any(
        lane.get(key) != value for key, value in {
            "policy_promotion_enabled": True,
            "memory_promotion_enabled": False,
            "memory_qualification_enabled": False,
            "at_most_one_registry_commit": True,
        }.items()
    ):
        raise PolicyPromotionRehearsalViolation(
            "rehearsal policy lane exceeds policy-only authority"
        )
    return payload


def _non_target_manifest(path: Path) -> dict[str, Any]:
    expected = make_manifest(
        [{
            "case_id": "policy_rehearsal_non_target_0",
            "design": "policy_rehearsal_non_target_design_0",
        }],
        split="non_target",
        metadata={"adapter_mode": "deterministic_fake"},
    )
    if path.exists():
        existing = verify_manifest(read_json(path))
        if (
            existing["manifest_hash"] != expected["manifest_hash"]
            or existing["rows"] != expected["rows"]
            or existing["split"] != expected["split"]
        ):
            raise PolicyPromotionRehearsalViolation(
                "non-target manifest changed after rehearsal initialization"
            )
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, expected)
    return expected


def _build_loop_config(
    *,
    root: Path,
    work: Path,
    frozen: Mapping[str, Any],
    registry: Path,
    non_target: Path,
) -> dict[str, Any]:
    fixed = dict(frozen["fixed_binding"])
    thresholds = dict(frozen["promotion_thresholds"])
    return {
        "schema_version": "r3e-integrated-coevolution-config-v1",
        "policy_registry": str(registry),
        "memory_root": str(work / "memory"),
        "integrated_rounds_root": str(work / "integrated/rounds"),
        "arena_rounds_root": str(work / "integrated/arena"),
        "events_root": str(work / "events"),
        "decision_ledger": str(work / "decision_ledger.jsonl"),
        "macro_ledger": str(work / "integrated/macro_ledger.jsonl"),
        "code_version": "policy-promotion-rehearsal-v1",
        "promotion_lane_schedule": list(frozen["promotion_lane_schedule"]),
        "arena_config": {
            "policy_search_space": str(
                root / "configs/base_policy/policy_search_space_v1.json"
            ),
            "non_target_manifest": str(non_target),
            "red_archive": str(work / "archives/residual.jsonl"),
            "covered_archive": str(work / "archives/covered.jsonl"),
            "red_rejected_archive": str(work / "archives/rejected.jsonl"),
            "round_ledger": str(work / "arena_round_ledger.jsonl"),
            "challenge_seeds": list(fixed["challenge_seeds"]),
            "promotion_seeds": list(fixed["promotion_seeds"]),
            "split_seed": 4,
            "policy_search_seed": 5,
            "promotion_thresholds": thresholds,
            "validity_authority": "legacy_adapter_evidence_v1",
        },
        "memory_config": {
            "minimum_support": 1,
            "maximum_active_memories": 1,
            "shadow_seeds": [101],
            "promotion_seeds": [201],
            "qualification_thresholds": {
                "min_helped": 1,
                "min_designs": 1,
                "max_harmed": 0,
                "max_cost_ratio": 1.5,
            },
        },
        "rehearsal_binding": {
            "model_id": fixed["model_id"],
            "model_version": fixed["model_version"],
            "toolchain_fingerprint_hash": fixed[
                "toolchain_fingerprint_hash"
            ],
            "maximum_llm_calls_per_case": fixed[
                "maximum_llm_calls_per_case"
            ],
            "maximum_input_tokens_per_case": fixed[
                "maximum_input_tokens_per_case"
            ],
            "maximum_output_tokens_per_case": fixed[
                "maximum_output_tokens_per_case"
            ],
            "maximum_wall_time_ms_per_case": fixed[
                "maximum_wall_time_ms_per_case"
            ],
        },
    }


def run_policy_promotion_rehearsal(
    *,
    project_root: str | Path,
    workspace: str | Path,
    config_path: str | Path = (
        "configs/evolution/policy_promotion_rehearsal_v1.json"
    ),
    resume: bool = True,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    work = Path(workspace)
    if not work.is_absolute():
        work = root / work
    work = work.resolve()
    frozen = load_rehearsal_config(config_path, project_root=root)
    summary_path = work / "summary.json"
    if summary_path.exists() and not resume:
        raise PolicyPromotionRehearsalViolation(
            "completed rehearsal cannot be overwritten without resume"
        )
    work.mkdir(parents=True, exist_ok=True)
    registry = work / "registry/policy_registry.json"
    if not registry.exists():
        initialize_registry(
            root / "configs/base_policy/frozen_base_policy_v3.json",
            registry,
            base_path_record="configs/base_policy/frozen_base_policy_v3.json",
        )
    non_target_path = work / "non_target_manifest.json"
    non_target = _non_target_manifest(non_target_path)
    loop_config = _build_loop_config(
        root=root,
        work=work,
        frozen=frozen,
        registry=registry,
        non_target=non_target_path,
    )
    adapter = DeterministicEvolutionAdapter(work / "adapter")
    fixed_binding = frozen["fixed_binding"]
    if hash_payload(adapter.toolchain_fingerprint) != fixed_binding[
        "toolchain_fingerprint_hash"
    ]:
        raise PolicyPromotionRehearsalViolation(
            "deterministic rehearsal toolchain differs from frozen binding"
        )
    if adapter.toolchain_fingerprint["model_id"] != fixed_binding["model_id"]:
        raise PolicyPromotionRehearsalViolation(
            "deterministic rehearsal model differs from frozen binding"
        )
    loop = IntegratedCoevolutionLoop(
        loop_config,
        project_root=root,
        arena_adapter=adapter,
        memory_adapter=DeterministicMemoryAdapter(),
    )
    summaries = loop.run(frozen["round_ids"])
    if len(summaries) != 2:
        raise PolicyPromotionRehearsalViolation(
            "rehearsal did not complete exactly two rounds"
        )
    first, second = summaries
    if (
        first["promotion_lane"] != "collect"
        or first["promotion_count"] != 0
        or second["promotion_lane"] != "policy"
        or second["promotion_count"] != 1
        or second["memory_promoted"] is not False
        or second["arena_promoted"] is not True
    ):
        raise PolicyPromotionRehearsalViolation(
            "rehearsal did not produce one policy-only promotion"
        )
    if second["parent_policy_hash"] != first["active_policy_hash"]:
        raise PolicyPromotionRehearsalViolation(
            "R001 does not challenge the R000 active policy"
        )
    if second["active_policy_hash"] == second["parent_policy_hash"]:
        raise PolicyPromotionRehearsalViolation(
            "policy rehearsal did not produce a child policy"
        )
    r0_dir = work / "integrated/arena/R000"
    r1_dir = work / "integrated/arena/R001"
    r0_residual = verify_manifest(read_json(r0_dir / "residual_manifest.json"))
    r1_target = verify_manifest(read_json(r1_dir / "target_manifest.json"))
    non_target = verify_manifest(read_json(non_target_path))
    residual_designs = sorted({
        str(row.get("design") or row.get("design_id") or "")
        for row in r0_residual["rows"]
    })
    target_designs = sorted({
        str(row.get("design") or row.get("design_id") or "")
        for row in r1_target["rows"]
    })
    if len(residual_designs) < int(
        frozen["residual_requirement"]["minimum_designs"]
    ) or r0_residual["row_count"] < int(
        frozen["residual_requirement"]["minimum_rows"]
    ):
        raise PolicyPromotionRehearsalViolation(
            "rehearsal residual manifest lacks two designs"
        )
    if r1_target["row_count"] < int(
        frozen["target_requirement"]["minimum_rows"]
    ):
        raise PolicyPromotionRehearsalViolation(
            "rehearsal target manifest is empty"
        )
    if len(non_target["rows"]) != int(
        frozen["non_target_requirement"]["independent_design_count"]
    ):
        raise PolicyPromotionRehearsalViolation(
            "rehearsal non-target manifest size mismatch"
        )
    if set(residual_designs) & {
        str(row.get("design") or row.get("design_id") or "")
        for row in non_target["rows"]
    }:
        raise PolicyPromotionRehearsalViolation(
            "rehearsal target/non-target designs overlap"
        )
    audits = [
        verify_integrated_round(work / f"integrated/rounds/{round_id}")
        for round_id in frozen["round_ids"]
    ]
    registry_before = load_registry(work / "integrated/rounds/R000/registry_before.json")
    registry_after = load_registry(registry)
    active = get_active_policy(registry_after)
    if active.policy_hash != second["active_policy_hash"]:
        raise PolicyPromotionRehearsalViolation(
            "registry active policy differs from R001 summary"
        )
    summary = {
        "schema_version": REHEARSAL_SCHEMA,
        "config_hash": frozen["config_hash"],
        "round_ids": list(frozen["round_ids"]),
        "round_summaries_hash": hash_payload(summaries),
        "promotion_lane_schedule": list(frozen["promotion_lane_schedule"]),
        "residual_manifest_hash": r0_residual["manifest_hash"],
        "residual_designs": residual_designs,
        "target_manifest_hash": r1_target["manifest_hash"],
        "target_designs": target_designs,
        "non_target_manifest_hash": non_target["manifest_hash"],
        "registry_hash_before": registry_before["registry_hash"],
        "registry_hash_after": registry_after["registry_hash"],
        "parent_policy_hash": second["parent_policy_hash"],
        "child_policy_hash": second["active_policy_hash"],
        "promotion_count": 1,
        "policy_promotion_enabled": True,
        "memory_promotion_enabled": False,
        "memory_qualification_enabled": False,
        "renewed_challenge_hash": second["renewed_challenge_hash"],
        "audit_record_hashes": [audit["audit_record_hash"] for audit in audits],
        "claim_scope": frozen["claim_boundary"],
    }
    summary["summary_hash"] = hash_payload(summary)
    if summary_path.exists():
        existing = read_json(summary_path)
        if existing != summary:
            raise PolicyPromotionRehearsalViolation(
                "resumed rehearsal summary differs from the frozen result"
            )
    else:
        atomic_write_json(summary_path, summary)
    return summary


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the deterministic policy-only promotion rehearsal"
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--workspace", default="runtime/pilots/policy-promotion-rehearsal-v1"
    )
    parser.add_argument(
        "--config", default="configs/evolution/policy_promotion_rehearsal_v1.json"
    )
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    result = run_policy_promotion_rehearsal(
        project_root=args.project_root,
        workspace=args.workspace,
        config_path=args.config,
        resume=not args.no_resume,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
