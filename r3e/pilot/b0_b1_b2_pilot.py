"""Deterministic multi-repetition B0 -> B1 -> B2 pilot scaffold.

The pilot freezes the engineering shape of the first real experiment without
claiming provider quality or policy gain.  Each repetition owns an isolated
registry, target/non-target/held-out manifests, three macro rounds and a
rollback probe.  The runner remains the sole authority for promotion and
renewed-challenge binding.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any, Mapping

from r3e.arena.fake_adapters import DeterministicEvolutionAdapter
from r3e.arena.integrated_loop import (
    IntegratedCoevolutionLoop,
    verify_integrated_round,
)
from r3e.arena.manifests import freeze_manifest, make_manifest, verify_manifest
from r3e.memory.fake_adapter import DeterministicMemoryAdapter
from r3e.policy.registry_v2 import (
    get_active_policy,
    initialize_registry,
    load_registry,
    rollback_policy,
)
from r3e.protocol.hashing import atomic_write_json, hash_payload, read_json


PILOT_SCHEMA = "r3e-b0-b1-b2-pilot-v1"
CONFIG_SCHEMA = "r3e-b0-b1-b2-pilot-config-v1"


class B012PilotViolation(RuntimeError):
    """Raised when the frozen deterministic pilot cannot be reconstructed."""


def _under_root(path: str | Path, root: Path, *, label: str) -> Path:
    value = Path(path)
    if not value.is_absolute():
        value = root / value
    value = value.resolve()
    try:
        value.relative_to(root)
    except ValueError as exc:
        raise B012PilotViolation(f"{label} escapes project root") from exc
    return value


def load_b012_config(
    path: str | Path,
    *,
    project_root: str | Path,
) -> dict[str, Any]:
    """Load and validate the frozen experiment-shape contract."""
    root = Path(project_root).resolve()
    source = _under_root(path, root, label="pilot config")
    payload = read_json(source)
    if not isinstance(payload, Mapping):
        raise B012PilotViolation("pilot config is not an object")
    value = dict(payload)
    if value.get("schema_version") != CONFIG_SCHEMA:
        raise B012PilotViolation("pilot config schema mismatch")
    expected_hash = hash_payload({
        key: item for key, item in value.items() if key != "config_hash"
    })
    if value.get("config_hash") != expected_hash:
        raise B012PilotViolation("pilot config hash mismatch")
    if value.get("execution_mode") != "deterministic_fake":
        raise B012PilotViolation("B0->B1->B2 pilot must be deterministic_fake")
    if value.get("round_ids") != ["R000", "R001", "R002"]:
        raise B012PilotViolation("pilot requires exactly R000, R001 and R002")
    if value.get("promotion_lane_schedule") != [
        "collect",
        "policy",
        "policy",
    ]:
        raise B012PilotViolation("pilot lane schedule must be collect->policy->policy")
    repetitions = value.get("repetitions")
    if not isinstance(repetitions, list) or len(repetitions) != 3:
        raise B012PilotViolation("pilot requires exactly three repetitions")
    repetition_ids = [str(row.get("repetition_id") or "") for row in repetitions]
    if repetition_ids != ["rep_000", "rep_001", "rep_002"]:
        raise B012PilotViolation("repetition ids are not frozen")
    challenge_seeds = []
    for row in repetitions:
        if not isinstance(row, Mapping):
            raise B012PilotViolation("repetition binding is malformed")
        challenge = int(row.get("challenge_seed") or 0)
        promotion = int(row.get("promotion_seed") or 0)
        if challenge < 1 or challenge != promotion:
            raise B012PilotViolation("each repetition must freeze one shared seed")
        challenge_seeds.append(challenge)
        for key in ("non_target_design_id", "held_out_design_id"):
            if not str(row.get(key) or ""):
                raise B012PilotViolation(f"missing repetition {key}")
    if len(set(challenge_seeds)) != len(challenge_seeds):
        raise B012PilotViolation("repetition seeds must be distinct")
    if len(value.get("design_ids") or []) != 4:
        raise B012PilotViolation("pilot must freeze four target designs")
    if len(value.get("family_ids") or []) != 8:
        raise B012PilotViolation("pilot must freeze eight family ids")
    split = value.get("split_contract")
    if not isinstance(split, Mapping) or any(
        split.get(key) != expected
        for key, expected in {
            "target_split": "target",
            "non_target_split": "non_target",
            "held_out_split": "held_out",
            "design_disjoint": True,
            "non_target_count": 1,
            "held_out_count": 1,
            "target_must_be_nonempty": True,
        }.items()
    ):
        raise B012PilotViolation("target/non-target/held-out split contract changed")
    fixed = value.get("fixed_binding")
    if not isinstance(fixed, Mapping) or fixed.get("model_id") != (
        "deterministic-fake-model"
    ):
        raise B012PilotViolation("fixed fake model binding is missing")
    lane = value.get("policy_lane")
    if not isinstance(lane, Mapping) or any(
        lane.get(key) != expected
        for key, expected in {
            "policy_promotion_enabled": True,
            "memory_promotion_enabled": False,
            "memory_qualification_enabled": False,
            "at_most_one_registry_commit_per_round": True,
        }.items()
    ):
        raise B012PilotViolation("pilot policy lane is not policy-only")
    rollback = value.get("rollback_contract")
    if not isinstance(rollback, Mapping) or any(
        rollback.get(key) != expected
        for key, expected in {
            "exact_parent_snapshot": True,
            "probe_depth": 2,
            "stale_child_rejected": True,
        }.items()
    ):
        raise B012PilotViolation("exact rollback contract is not frozen")
    return value


def _freeze_split(
    path: Path,
    *,
    split: str,
    design_id: str,
    repetition_id: str,
) -> dict[str, Any]:
    manifest = make_manifest(
        [{
            "case_id": f"{repetition_id}_{split}",
            "design": design_id,
            "repetition_id": repetition_id,
        }],
        split=split,
        metadata={
            "adapter_mode": "deterministic_fake",
            "repetition_id": repetition_id,
        },
    )
    return freeze_manifest(path, manifest)


def _build_loop_config(
    *,
    root: Path,
    work: Path,
    frozen: Mapping[str, Any],
    registry: Path,
    non_target: Path,
    repetition: Mapping[str, Any],
) -> dict[str, Any]:
    fixed = dict(frozen["fixed_binding"])
    return {
        "schema_version": "r3e-integrated-coevolution-config-v1",
        "policy_registry": str(registry),
        "memory_root": str(work / "memory"),
        "integrated_rounds_root": str(work / "integrated/rounds"),
        "arena_rounds_root": str(work / "integrated/arena"),
        "events_root": str(work / "events"),
        "decision_ledger": str(work / "decision_ledger.jsonl"),
        "macro_ledger": str(work / "integrated/macro_ledger.jsonl"),
        "code_version": "b0-b1-b2-deterministic-pilot-v1",
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
            "challenge_seeds": [int(repetition["challenge_seed"])],
            "promotion_seeds": [int(repetition["promotion_seed"])],
            "split_seed": 4,
            "policy_search_seed": 5,
            "promotion_thresholds": dict(frozen["promotion_thresholds"]),
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
        "pilot_binding": {
            "repetition_id": repetition["repetition_id"],
            "challenge_seed": int(repetition["challenge_seed"]),
            "promotion_seed": int(repetition["promotion_seed"]),
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


def _designs(manifest: Mapping[str, Any]) -> set[str]:
    return {
        str(row.get("design") or row.get("design_id") or "")
        for row in manifest["rows"]
    }


def _rollback_probe(
    *,
    registry_path: Path,
    work: Path,
    expected_final_hash: str,
) -> dict[str, Any]:
    result_path = work / "rollback_probe.json"
    if result_path.exists():
        result = read_json(result_path)
        expected_result_hash = hash_payload({
            key: value for key, value in result.items()
            if key != "result_hash"
        })
        if (
            result.get("result_hash") != expected_result_hash
            or result.get("final_policy_hash") != expected_final_hash
            or result.get("probe_depth") != 2
            or result.get("exact_parent_snapshot") is not True
            or result.get("restored_to_base") is not True
        ):
            raise B012PilotViolation("rollback probe final hash changed")
        return result
    probe_root = work / "rollback_probe_state"
    probe_root.mkdir(parents=True, exist_ok=True)
    probe_registry = probe_root / "policy_registry.json"
    shutil.copy2(registry_path, probe_registry)
    versions = registry_path.parent / f".{registry_path.name}.versions"
    if versions.is_dir():
        shutil.copytree(versions, probe_root / versions.name)
    current = load_registry(probe_registry)
    if get_active_policy(current).policy_hash != expected_final_hash:
        raise B012PilotViolation("rollback probe started from stale policy")
    first = rollback_policy(
        probe_registry,
        expected_active_policy_hash=expected_final_hash,
        reason="deterministic-b012-rollback-probe",
    )
    first_active = get_active_policy(first)
    second = rollback_policy(
        probe_registry,
        expected_active_policy_hash=first_active.policy_hash,
        reason="deterministic-b012-rollback-probe",
    )
    second_active = get_active_policy(second)
    result = {
        "probe_depth": 2,
        "final_policy_hash": expected_final_hash,
        "first_restored_policy_id": first_active.policy_id,
        "first_restored_policy_hash": first_active.policy_hash,
        "first_parent_policy_hash": first_active.parent_policy_hash,
        "second_restored_policy_id": second_active.policy_id,
        "second_restored_policy_hash": second_active.policy_hash,
        "first_registry_hash": first["registry_hash"],
        "second_registry_hash": second["registry_hash"],
        "exact_parent_snapshot": (
            first_active.parent_policy_hash == second_active.policy_hash
        ),
        "restored_to_base": second_active.policy_id == "B0",
    }
    if not result["restored_to_base"]:
        raise B012PilotViolation("two-step rollback did not restore B0")
    result["result_hash"] = hash_payload(result)
    atomic_write_json(result_path, result)
    return result


def _run_repetition(
    *,
    root: Path,
    work: Path,
    frozen: Mapping[str, Any],
    repetition: Mapping[str, Any],
) -> dict[str, Any]:
    repetition_id = str(repetition["repetition_id"])
    registry = work / "registry/policy_registry.json"
    if not registry.exists():
        initialize_registry(
            root / "configs/base_policy/frozen_base_policy_v3.json",
            registry,
            base_path_record="configs/base_policy/frozen_base_policy_v3.json",
        )
    non_target_path = work / "splits/non_target_manifest.json"
    held_out_path = work / "splits/held_out_manifest.json"
    non_target = _freeze_split(
        non_target_path,
        split="non_target",
        design_id=str(repetition["non_target_design_id"]),
        repetition_id=repetition_id,
    )
    held_out = _freeze_split(
        held_out_path,
        split="held_out",
        design_id=str(repetition["held_out_design_id"]),
        repetition_id=repetition_id,
    )
    loop_config = _build_loop_config(
        root=root,
        work=work,
        frozen=frozen,
        registry=registry,
        non_target=non_target_path,
        repetition=repetition,
    )
    adapter = DeterministicEvolutionAdapter(work / "adapter")
    fixed = frozen["fixed_binding"]
    if hash_payload(adapter.toolchain_fingerprint) != fixed[
        "toolchain_fingerprint_hash"
    ]:
        raise B012PilotViolation("deterministic adapter toolchain hash changed")
    if adapter.toolchain_fingerprint["model_id"] != fixed["model_id"]:
        raise B012PilotViolation("deterministic adapter model binding changed")
    loop = IntegratedCoevolutionLoop(
        loop_config,
        project_root=root,
        arena_adapter=adapter,
        memory_adapter=DeterministicMemoryAdapter(),
    )
    summaries = loop.run(frozen["round_ids"])
    if len(summaries) != 3:
        raise B012PilotViolation("repetition did not complete three rounds")
    expected_lanes = list(frozen["promotion_lane_schedule"])
    expected_promotions = [0, 1, 1]
    previous_active = ""
    for index, summary in enumerate(summaries):
        if summary["promotion_lane"] != expected_lanes[index]:
            raise B012PilotViolation("round lane differs from frozen schedule")
        if summary["promotion_count"] != expected_promotions[index]:
            raise B012PilotViolation("round promotion count differs from B0/B1/B2 contract")
        if index and summary["parent_policy_hash"] != previous_active:
            raise B012PilotViolation("renewed round challenges a stale parent")
        if index and summary["active_policy_hash"] == summary["parent_policy_hash"]:
            raise B012PilotViolation("policy lane did not produce a child")
        if summary["memory_promoted"] or not summary["arena_promoted"] and index:
            raise B012PilotViolation("policy-only lane acquired memory authority")
        previous_active = summary["active_policy_hash"]

    r0 = verify_manifest(read_json(
        work / "integrated/arena/R000/residual_manifest.json"
    ))
    r1 = verify_manifest(read_json(
        work / "integrated/arena/R001/target_manifest.json"
    ))
    r2 = verify_manifest(read_json(
        work / "integrated/arena/R002/target_manifest.json"
    ))
    if r0["row_count"] < int(frozen["residual_requirement"]["minimum_rows"]):
        raise B012PilotViolation("B0 residual manifest is too small")
    residual_designs = sorted(_designs(r0))
    if len(set(residual_designs)) < int(
        frozen["residual_requirement"]["minimum_designs"]
    ):
        raise B012PilotViolation("B0 residuals do not cover two designs")
    target_designs = sorted(_designs(r1) | _designs(r2))
    if not target_designs:
        raise B012PilotViolation("renewed target manifests are empty")
    non_target_designs = _designs(non_target)
    held_out_designs = _designs(held_out)
    if non_target["split"] != "non_target" or held_out["split"] != "held_out":
        raise B012PilotViolation("split labels are not frozen")
    if non_target_designs & held_out_designs:
        raise B012PilotViolation("non-target and held-out designs overlap")
    if (set(residual_designs) | set(target_designs)) & (
        non_target_designs | held_out_designs
    ):
        raise B012PilotViolation("target and non-target/held-out designs overlap")
    observed_families = sorted({
        str(row.get("family") or "")
        for manifest in (r0, r1, r2)
        for row in manifest["rows"]
        if str(row.get("family") or "")
    })
    if not set(observed_families).issubset(set(frozen["family_ids"])):
        raise B012PilotViolation("adapter emitted a family outside frozen ontology")
    audits = [
        verify_integrated_round(work / f"integrated/rounds/{round_id}")
        for round_id in frozen["round_ids"]
    ]
    registry_after = load_registry(registry)
    final_hash = get_active_policy(registry_after).policy_hash
    rollback = _rollback_probe(
        registry_path=registry,
        work=work,
        expected_final_hash=final_hash,
    )
    result = {
        "schema_version": "r3e-b0-b1-b2-repetition-v1",
        "repetition_id": repetition_id,
        "challenge_seed": int(repetition["challenge_seed"]),
        "promotion_seed": int(repetition["promotion_seed"]),
        "round_ids": list(frozen["round_ids"]),
        "promotion_counts": [int(row["promotion_count"]) for row in summaries],
        "parent_policy_hash": summaries[0]["parent_policy_hash"],
        "b1_policy_hash": summaries[1]["active_policy_hash"],
        "b2_policy_hash": summaries[2]["active_policy_hash"],
        "residual_manifest_hash": r0["manifest_hash"],
        "residual_designs": residual_designs,
        "target_manifest_hashes": [r1["manifest_hash"], r2["manifest_hash"]],
        "target_designs": target_designs,
        "non_target_manifest_hash": non_target["manifest_hash"],
        "held_out_manifest_hash": held_out["manifest_hash"],
        "observed_families": observed_families,
        "audit_record_hashes": [row["audit_record_hash"] for row in audits],
        "rollback": rollback,
        "direction_consistent": (
            summaries[0]["promotion_count"] == 0
            and summaries[1]["promotion_count"] == 1
            and summaries[2]["promotion_count"] == 1
            and summaries[1]["active_policy_hash"]
            != summaries[1]["parent_policy_hash"]
            and summaries[2]["active_policy_hash"]
            != summaries[2]["parent_policy_hash"]
        ),
        "claim_scope": frozen["claim_boundary"],
    }
    if not result["direction_consistent"]:
        raise B012PilotViolation("repetition direction is inconsistent")
    result["result_hash"] = hash_payload(result)
    summary_path = work / "summary.json"
    if summary_path.exists():
        existing = read_json(summary_path)
        if existing != result:
            raise B012PilotViolation("resumed repetition differs from frozen result")
    else:
        atomic_write_json(summary_path, result)
    return result


def run_b0_b1_b2_pilot(
    *,
    project_root: str | Path,
    workspace: str | Path,
    config_path: str | Path = "configs/evolution/b0_b1_b2_pilot_v1.json",
    resume: bool = True,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    work = Path(workspace)
    if not work.is_absolute():
        work = root / work
    work = work.resolve()
    frozen = load_b012_config(config_path, project_root=root)
    summary_path = work / "summary.json"
    if summary_path.exists() and not resume:
        raise B012PilotViolation("completed pilot cannot be overwritten without resume")
    work.mkdir(parents=True, exist_ok=True)
    results = []
    for repetition in frozen["repetitions"]:
        repetition_work = work / "repetitions" / str(repetition["repetition_id"])
        results.append(_run_repetition(
            root=root,
            work=repetition_work,
            frozen=frozen,
            repetition=repetition,
        ))
    if not all(row["direction_consistent"] for row in results):
        raise B012PilotViolation("not all repetitions have consistent direction")
    summary = {
        "schema_version": PILOT_SCHEMA,
        "config_hash": frozen["config_hash"],
        "round_ids": list(frozen["round_ids"]),
        "promotion_lane_schedule": list(frozen["promotion_lane_schedule"]),
        "repetition_ids": [row["repetition_id"] for row in results],
        "repetition_result_hashes": [row["result_hash"] for row in results],
        "repetitions": len(results),
        "design_count": len(frozen["design_ids"]),
        "family_count": len(frozen["family_ids"]),
        "promotion_counts_by_repetition": [
            row["promotion_counts"] for row in results
        ],
        "direction_consistent": True,
        "policy_promotion_enabled": True,
        "memory_promotion_enabled": False,
        "memory_qualification_enabled": False,
        "claim_scope": frozen["claim_boundary"],
    }
    summary["summary_hash"] = hash_payload(summary)
    if summary_path.exists():
        existing = read_json(summary_path)
        if existing != summary:
            raise B012PilotViolation("resumed pilot summary differs from frozen result")
    else:
        atomic_write_json(summary_path, summary)
    return summary


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the deterministic B0->B1->B2 pilot scaffold"
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--workspace", default="runtime/pilots/b0-b1-b2-v1"
    )
    parser.add_argument(
        "--config", default="configs/evolution/b0_b1_b2_pilot_v1.json"
    )
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    result = run_b0_b1_b2_pilot(
        project_root=args.project_root,
        workspace=args.workspace,
        config_path=args.config,
        resume=not args.no_resume,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
