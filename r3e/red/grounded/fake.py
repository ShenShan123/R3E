"""Deterministic, model-free Grounded Red protocol fixtures.

The fixtures exercise authority reconstruction only.  They are not parser,
simulator, formal, oracle, or empirical red-discovery evidence.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from r3e.grounded.receipts import build_command_receipt
from r3e.protocol.hashing import hash_payload

from .admission import ADMISSION_EVIDENCE_SCHEMA_VERSION
from .proofs import (
    build_runtime_effect_receipt,
    build_semantic_diff_receipt,
)


def _h(label: str) -> str:
    return hash_payload({"deterministic_grounded_red_fixture": label})


def build_deterministic_evidence(
    plan: Mapping[str, Any],
    *,
    run_context_hash: str | None = None,
    toolchain_fingerprint_hash: str | None = None,
) -> dict[str, Any]:
    """Build a clean-pass/poison-fail/revert-pass synthetic authority graph."""
    value = deepcopy(dict(plan))
    context_hash = run_context_hash or _h("run-context")
    toolchain_hash = toolchain_fingerprint_hash or _h("toolchain")
    clean_hash = _h(f"{value['plan_id']}:clean-rtl")
    poison_hash = _h(f"{value['plan_id']}:poison-rtl")
    clean_semantic_hash = _h(f"{value['plan_id']}:clean-semantic")
    first_divergence_hash = _h(f"{value['plan_id']}:first-divergence")
    mismatch_topology_hash = _h(f"{value['plan_id']}:mismatch-topology")
    failure_signature = f"fixture:{value['expected_runtime_effect_id']}"

    def receipt(
        *,
        receipt_id: str,
        phase: str,
        subject_hash: str,
        observed: dict[str, Any],
    ) -> dict[str, Any]:
        return build_command_receipt(
            receipt_id=receipt_id,
            phase=phase,
            subject_hash=subject_hash,
            run_context_hash=context_hash,
            toolchain_fingerprint_hash=toolchain_hash,
            command_argv=["deterministic-grounded-red-fixture", phase],
            working_directory_hash=_h("working-directory"),
            exit_code=0,
            timed_out=False,
            crashed=False,
            result_kind="completed",
            wall_time_ms=1,
            artifact_hashes={
                "stdout": _h(f"{receipt_id}:stdout"),
                "result": _h(f"{receipt_id}:result"),
            },
            observed=observed,
        )

    clean_run = receipt(
        receipt_id=f"{value['plan_id']}:clean",
        phase="clean_baseline",
        subject_hash=clean_hash,
        observed={
            "parse_ok": True,
            "elaboration_ok": True,
            "compile_ok": True,
            "simulation_complete": True,
            "oracle_pass": True,
            "semantic_hash": clean_semantic_hash,
        },
    )
    poison_observed = {
        "parse_ok": True,
        "elaboration_ok": True,
        "target_ast_node_exists": True,
        "operator_preconditions_hold": True,
        "compile_ok": True,
        "simulation_complete": True,
        "oracle_pass": False,
        "functional_mismatch": True,
        "first_divergence_hash": first_divergence_hash,
        "mismatch_topology_hash": mismatch_topology_hash,
        "effect_signature": failure_signature,
    }
    poison_runs = [
        receipt(
            receipt_id=f"{value['plan_id']}:poison:{index}",
            phase="poison_execution",
            subject_hash=poison_hash,
            observed=poison_observed,
        )
        for index in (1, 2)
    ]
    revert_run = receipt(
        receipt_id=f"{value['plan_id']}:revert",
        phase="revert_execution",
        subject_hash=poison_hash,
        observed={
            "parse_ok": True,
            "compile_ok": True,
            "simulation_complete": True,
            "oracle_pass": True,
            "restored_semantic_hash": clean_semantic_hash,
        },
    )
    semantic = build_semantic_diff_receipt(
        plan_hash=value["plan_hash"],
        family_id=value["family_id"],
        operator_id=value["operator_id"],
        clean_ast_hash=clean_semantic_hash,
        poison_ast_hash=_h(f"{value['plan_id']}:poison-semantic"),
        target_ast_node_hash=value["target_ast_node_hash"],
        normalized_ast_diff_hash=_h(f"{value['plan_id']}:normalized-diff"),
        changed_module_count=1,
        changed_block_count=1,
        ast_edit_count=1,
        dependency_depth=int(
            value["difficulty_target"]["dependency_depth_delta"]
        ),
        collateral_edit_count=0,
        reachable=True,
        preconditions_satisfied=True,
    )
    effect = build_runtime_effect_receipt(
        plan_hash=value["plan_hash"],
        effect_id=value["expected_runtime_effect_id"],
        failure_signature=failure_signature,
        first_divergence_hash=first_divergence_hash,
        mismatch_topology_hash=mismatch_topology_hash,
        temporal_depth=int(
            value["difficulty_target"]["temporal_depth_delta"]
        ),
        observable_features={
            "fixture": True,
            "first_divergence_hash": first_divergence_hash,
        },
    )
    payload = {
        "schema_version": ADMISSION_EVIDENCE_SCHEMA_VERSION,
        "plan_hash": value["plan_hash"],
        "poison_payload_hash": _h(f"{value['plan_id']}:poison-payload"),
        "source_integrity": {
            "clean_rtl_hash": clean_hash,
            "poison_rtl_hash": poison_hash,
            "testbench_hash": _h("testbench"),
            "oracle_hash": _h("oracle"),
            "build_configuration_hash": _h("build-configuration"),
            "allowed_file_manifest_hash": _h("allowed-file-manifest"),
            "changed_file_hashes": {"rtl/top.v": poison_hash},
            "forbidden_changes": [],
        },
        "clean_run": clean_run,
        "poison_runs": poison_runs,
        "revert_run": revert_run,
        "semantic_diff": semantic,
        "runtime_effect": effect,
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
            "minimized_poison_hash": poison_hash,
            "preserved_failure_signature": True,
            "remaining_ast_edits": 1,
        },
    }
    payload["evidence_hash"] = hash_payload(payload)
    return payload
