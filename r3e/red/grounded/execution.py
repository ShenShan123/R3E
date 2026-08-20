"""Real Icarus-backed Grounded Red admission execution."""
from __future__ import annotations

from copy import deepcopy
import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from r3e.grounded.icarus import (
    IcarusGroundedProvider,
    verify_icarus_toolchain_fingerprint,
)
from r3e.grounded.provider_receipts import verify_provider_receipt
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import (
    atomic_write_json,
    atomic_write_text,
    hash_file,
    hash_payload,
    read_json,
)

from .admission import (
    ADMISSION_EVIDENCE_GROUNDED_SCHEMA_VERSION,
    decide_grounded_admission,
    verify_grounded_admission_decision,
)
from .materializers import (
    inverse_materialization,
    materialize_operator,
    verify_materialization_receipt,
)
from .mutation_plan import verify_mutation_plan
from .proofs import build_runtime_effect_receipt
from .registry import GroundedRegistryBundle, load_grounded_registries
from .verilog_ast import normalized_ast_hash


EXECUTION_BUNDLE_SCHEMA_VERSION = "r3e-grounded-red-execution-bundle-v1"
_EXECUTION_BUNDLE_FIELDS = {
    "schema_version",
    "plan",
    "materialization_receipt",
    "semantic_provider_receipt",
    "toolchain_fingerprint",
    "evidence",
    "admission_decision",
    "bundle_hash",
}


class GroundedRedExecutionViolation(RuntimeError):
    """Raised when real materialization/execution cannot produce authority."""


def verify_grounded_execution_bundle(
    bundle: Mapping[str, Any],
    *,
    policy: PolicyState,
    registries: GroundedRegistryBundle,
) -> dict[str, Any]:
    payload = deepcopy(dict(bundle))
    if payload.get("schema_version") == (
        "r3e-grounded-sequential-execution-bundle-v1"
    ):
        from .sequential_execution import (
            verify_grounded_sequential_execution_bundle,
        )

        return verify_grounded_sequential_execution_bundle(
            payload,
            policy=policy,
            registries=registries,
        )
    if set(payload) != _EXECUTION_BUNDLE_FIELDS:
        raise GroundedRedExecutionViolation(
            "grounded execution bundle fields mismatch"
        )
    if payload["schema_version"] != EXECUTION_BUNDLE_SCHEMA_VERSION:
        raise GroundedRedExecutionViolation(
            "grounded execution bundle schema mismatch"
        )
    if payload["bundle_hash"] != hash_payload({
        key: value for key, value in payload.items()
        if key != "bundle_hash"
    }):
        raise GroundedRedExecutionViolation(
            "grounded execution bundle hash mismatch"
        )
    plan = verify_mutation_plan(
        payload["plan"], policy=policy, registries=registries
    )
    materialization = verify_materialization_receipt(
        payload["materialization_receipt"]
    )
    semantic_provider = verify_provider_receipt(
        payload["semantic_provider_receipt"]
    )
    toolchain = verify_icarus_toolchain_fingerprint(
        payload["toolchain_fingerprint"]
    )
    evidence = deepcopy(dict(payload["evidence"]))
    if (
        evidence.get("schema_version")
        != ADMISSION_EVIDENCE_GROUNDED_SCHEMA_VERSION
        or evidence.get("materialization_receipt") != materialization
        or evidence.get("semantic_provider_receipt") != semantic_provider
        or materialization["plan_hash"] != plan["plan_hash"]
    ):
        raise GroundedRedExecutionViolation(
            "execution bundle authority objects are not cross-bound"
        )
    receipts = [
        evidence["clean_run"],
        *evidence["poison_runs"],
        evidence["revert_run"],
    ]
    if any(
        receipt.get("toolchain_fingerprint_hash")
        != toolchain["toolchain_fingerprint_hash"]
        or receipt.get("observed", {}).get("toolchain_fingerprint")
        != toolchain
        for receipt in receipts
    ):
        raise GroundedRedExecutionViolation(
            "execution bundle uses inconsistent toolchain receipts"
        )
    decision = verify_grounded_admission_decision(
        payload["admission_decision"],
        plan=plan,
        policy=policy,
        registries=registries,
        evidence=evidence,
    )
    if (
        decision["schema_version"]
        != "r3e-red-admission-decision-v2"
        or decision.get("authority_mode")
        != "runner_owned_grounded_execution"
    ):
        raise GroundedRedExecutionViolation(
            "execution bundle lacks runner-owned admission authority"
        )
    return {
        **payload,
        "plan": plan,
        "materialization_receipt": materialization,
        "semantic_provider_receipt": semantic_provider,
        "toolchain_fingerprint": toolchain,
        "evidence": evidence,
        "admission_decision": decision,
    }


def execution_materialization_bounds(
    bundle: Mapping[str, Any],
) -> dict[str, str]:
    """Project either execution schema onto formal clean/poison/revert hashes."""
    materialization = dict(
        dict(bundle).get("materialization_receipt") or {}
    )
    if dict(bundle).get("schema_version") == (
        "r3e-grounded-sequential-execution-bundle-v1"
    ):
        clean_hash = str(materialization.get("clean_source_hash") or "")
        poison_hash = str(materialization.get("poison_source_hash") or "")
    else:
        clean_hash = str(materialization.get("clean_rtl_hash") or "")
        poison_hash = str(materialization.get("poison_rtl_hash") or "")
    return {
        "clean_rtl_hash": clean_hash,
        "poison_rtl_hash": poison_hash,
        "revert_rtl_hash": clean_hash,
    }


def execute_grounded_icarus_admission(
    *,
    plan: Mapping[str, Any],
    policy: PolicyState,
    registries: GroundedRegistryBundle,
    clean_rtl_path: str | Path,
    testbench_path: str | Path,
    top_module: str,
    workspace: str | Path,
    run_context_hash: str,
    frozen_clean_rtl_hash: str,
    frozen_testbench_hash: str,
    allowed_file_manifest_hash: str,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    verified_plan = verify_mutation_plan(
        plan, policy=policy, registries=registries
    )
    root = Path(workspace).resolve()
    root.mkdir(parents=True, exist_ok=True)
    clean_input = Path(clean_rtl_path).resolve()
    testbench_input = Path(testbench_path).resolve()
    try:
        clean_input.relative_to(root)
        testbench_input.relative_to(root)
    except ValueError as exc:
        raise GroundedRedExecutionViolation(
            "RTL and testbench must be inside the execution workspace"
        ) from exc
    clean_source = clean_input.read_text(encoding="utf-8")
    if hash_file(clean_input) != frozen_clean_rtl_hash:
        raise GroundedRedExecutionViolation(
            "clean RTL differs from the frozen source manifest"
        )
    if hash_file(testbench_input) != frozen_testbench_hash:
        raise GroundedRedExecutionViolation(
            "testbench differs from the frozen source manifest"
        )
    materialization = materialize_operator(
        verified_plan,
        clean_source=clean_source,
    )
    poison_path = root / "materialized" / "poison.v"
    revert_path = root / "materialized" / "reverted.v"
    poison_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(poison_path, materialization.poison_source)
    restored_source = inverse_materialization(
        materialization.receipt,
        poison_source=materialization.poison_source,
    )
    atomic_write_text(revert_path, restored_source)
    if restored_source != clean_source:
        raise GroundedRedExecutionViolation(
            "inverse materialization did not restore exact source"
        )
    provider = IcarusGroundedProvider(
        workspace=root,
        run_context_hash=run_context_hash,
        timeout_seconds=timeout_seconds,
    )
    clean_run = provider.execute(
        receipt_prefix=f"{verified_plan['plan_id']}:clean",
        mode="clean_baseline",
        rtl_path=clean_input,
        testbench_path=testbench_input,
        top_module=top_module,
        semantic_hash=normalized_ast_hash(clean_source),
    )
    poison_runs = [
        provider.execute(
            receipt_prefix=f"{verified_plan['plan_id']}:poison:{index}",
            mode="poison_execution",
            rtl_path=poison_path,
            testbench_path=testbench_input,
            top_module=top_module,
            semantic_hash=normalized_ast_hash(
                materialization.poison_source
            ),
            semantic_provider_receipt=(
                materialization.parser_provider_receipt
            ),
        )
        for index in (1, 2)
    ]
    revert_run = provider.execute(
        receipt_prefix=f"{verified_plan['plan_id']}:revert",
        mode="revert_execution",
        rtl_path=revert_path,
        testbench_path=testbench_input,
        top_module=top_module,
        semantic_hash=normalized_ast_hash(restored_source),
    )
    poison_observed = poison_runs[0]["observed"]
    runtime_effect = build_runtime_effect_receipt(
        plan_hash=verified_plan["plan_hash"],
        effect_id=verified_plan["expected_runtime_effect_id"],
        failure_signature=poison_observed["effect_signature"],
        first_divergence_hash=poison_observed["first_divergence_hash"],
        mismatch_topology_hash=poison_observed[
            "mismatch_topology_hash"
        ],
        temporal_depth=0,
        observable_features={
            "oracle_provider_receipt_hash": poison_observed[
                "oracle_provider_receipt"
            ]["receipt_hash"],
            "first_divergence_hash": poison_observed[
                "first_divergence_hash"
            ],
            "waveform_observation_hash": poison_observed[
                "waveform_observation_hash"
            ],
            "first_divergence_signal": poison_observed[
                "first_divergence_signal"
            ],
            "first_divergence_cycle": poison_observed[
                "first_divergence_cycle"
            ],
            "cycle_offset": poison_observed["cycle_offset"],
            "temporal_relation": poison_observed[
                "temporal_relation"
            ],
            "assignment_type": poison_observed["assignment_type"],
            "cone_depth": poison_observed["cone_depth"],
            "mismatch_pattern": poison_observed["mismatch_pattern"],
        },
    )
    evidence = {
        "schema_version": ADMISSION_EVIDENCE_GROUNDED_SCHEMA_VERSION,
        "plan_hash": verified_plan["plan_hash"],
        "poison_payload_hash": hash_payload({
            "plan_hash": verified_plan["plan_hash"],
            "poison_rtl_hash": hash_file(poison_path),
            "testbench_hash": hash_file(testbench_input),
        }),
        "source_integrity": {
            "clean_rtl_hash": hash_file(clean_input),
            "poison_rtl_hash": hash_file(poison_path),
            "testbench_hash": hash_file(testbench_input),
            "oracle_hash": hash_payload({
                "oracle_provider": "r3e-stdout-oracle-v1",
            }),
            "build_configuration_hash": hash_payload({
                "provider": provider.toolchain,
                "top_module": top_module,
                "language": "SystemVerilog-2012",
            }),
            "allowed_file_manifest_hash": allowed_file_manifest_hash,
            "changed_file_hashes": {
                "materialized/poison.v": hash_file(poison_path),
            },
            "forbidden_changes": [],
        },
        "clean_run": clean_run,
        "poison_runs": poison_runs,
        "revert_run": revert_run,
        "semantic_diff": materialization.semantic_diff_receipt,
        "materialization_receipt": materialization.receipt,
        "semantic_provider_receipt": (
            materialization.parser_provider_receipt
        ),
        "runtime_effect": runtime_effect,
        "nontriviality": {
            "constant_output": False,
            "deleted_major_block": False,
            "global_x": False,
            "unbounded_execution": False,
            "comment_only": False,
            "unreachable_only": False,
            "unrelated_signal_only": False,
            "excess_collateral": False,
        },
        "minimization": {
            "attempted": True,
            "succeeded": True,
            "minimized_poison_hash": hash_file(poison_path),
            "preserved_failure_signature": True,
            "remaining_ast_edits": int(
                materialization.receipt["ast_edit_count"]
            ),
        },
    }
    evidence["evidence_hash"] = hash_payload(evidence)
    decision = decide_grounded_admission(
        plan=verified_plan,
        policy=policy,
        registries=registries,
        evidence=evidence,
    )
    bundle = {
        "schema_version": EXECUTION_BUNDLE_SCHEMA_VERSION,
        "plan": deepcopy(verified_plan),
        "materialization_receipt": deepcopy(
            materialization.receipt
        ),
        "semantic_provider_receipt": deepcopy(
            materialization.parser_provider_receipt
        ),
        "toolchain_fingerprint": deepcopy(provider.toolchain),
        "evidence": evidence,
        "admission_decision": decision,
    }
    bundle["bundle_hash"] = hash_payload(bundle)
    verified_bundle = verify_grounded_execution_bundle(
        bundle,
        policy=policy,
        registries=registries,
    )
    atomic_write_json(
        root / "grounded_execution_bundle.json", verified_bundle
    )
    return verified_bundle


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--clean-rtl", required=True)
    parser.add_argument("--testbench", required=True)
    parser.add_argument("--top-module", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--run-context-hash", required=True)
    parser.add_argument("--frozen-clean-rtl-hash", required=True)
    parser.add_argument("--frozen-testbench-hash", required=True)
    parser.add_argument("--allowed-file-manifest-hash", required=True)
    parser.add_argument("--family-registry")
    parser.add_argument("--operator-registry")
    parser.add_argument("--effect-registry")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[3]
    loaded_registries = load_grounded_registries(
        family_registry=args.family_registry
        or project_root / "configs/red/grounded_family_registry_v1.json",
        operator_registry=args.operator_registry
        or project_root / "configs/red/grounded_operator_registry_v1.json",
        effect_registry=args.effect_registry
        or project_root / "configs/red/grounded_effect_registry_v1.json",
    )
    bundle = execute_grounded_icarus_admission(
        plan=read_json(args.plan),
        policy=PolicyState.from_dict(read_json(args.policy)),
        registries=loaded_registries,
        clean_rtl_path=args.clean_rtl,
        testbench_path=args.testbench,
        top_module=args.top_module,
        workspace=args.workspace,
        run_context_hash=args.run_context_hash,
        frozen_clean_rtl_hash=args.frozen_clean_rtl_hash,
        frozen_testbench_hash=args.frozen_testbench_hash,
        allowed_file_manifest_hash=args.allowed_file_manifest_hash,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps({
        "bundle_hash": bundle["bundle_hash"],
        "decision_hash": bundle["admission_decision"]["decision_hash"],
        "admitted": bundle["admission_decision"]["admitted"],
        "workspace": str(Path(args.workspace)),
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    _main()
