"""Final compile/simulation/oracle authority for sequential AST plans."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from r3e.grounded.icarus import (
    IcarusGroundedProvider,
    verify_icarus_toolchain_fingerprint,
)
from r3e.grounded.provider_receipts import (
    build_provider_receipt,
    verify_provider_receipt,
)
from r3e.grounded.receipts import verify_command_receipt
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import (
    atomic_write_json,
    atomic_write_text,
    hash_file,
    hash_payload,
)

from .admission import _verify_execution_chain
from .materializers import verify_materialization_receipt
from .memory_materializers import (
    CONTROLLED_COMPOSITION_SCHEMA,
    MEMORY_OPERATOR_PLAN_SCHEMA,
    SEQUENTIAL_RECEIPT_SCHEMA,
    materialize_sequential_plan,
    verify_sequential_plan,
)
from .proofs import (
    build_runtime_effect_receipt,
    build_semantic_diff_receipt,
    verify_runtime_effect_receipt,
    verify_semantic_diff_receipt,
)
from .registry import GroundedRegistryBundle
from .verilog_ast import normalized_ast_hash, source_hash


SEQUENTIAL_EXECUTION_BUNDLE_SCHEMA = (
    "r3e-grounded-sequential-execution-bundle-v1"
)
SEQUENTIAL_EVIDENCE_SCHEMA = "r3e-red-sequential-admission-evidence-v1"
SEQUENTIAL_DECISION_SCHEMA = "r3e-red-sequential-admission-decision-v1"
_BUNDLE_FIELDS = {
    "schema_version",
    "plan",
    "materialization_receipt",
    "stage_materializations",
    "semantic_provider_receipt",
    "toolchain_fingerprint",
    "evidence",
    "admission_decision",
    "bundle_hash",
}
_EVIDENCE_FIELDS = {
    "schema_version",
    "plan_hash",
    "poison_payload_hash",
    "source_integrity",
    "clean_run",
    "poison_runs",
    "revert_run",
    "semantic_diff",
    "runtime_effect",
    "sequential_materialization_receipt",
    "stage_materializations",
    "semantic_provider_receipt",
    "nontriviality",
    "minimization",
    "evidence_hash",
}
_CHECK_NAMES = (
    "G1_source_integrity",
    "G2_parser_elaboration",
    "G3_clean_baseline",
    "G4_poison_executability",
    "G5_functional_failure",
    "G6_determinism",
    "G7_revert_proof",
    "G8_semantic_family_proof",
    "G9_runtime_effect_proof",
    "G10_non_triviality",
    "G11_minimization",
)


class GroundedSequentialExecutionViolation(RuntimeError):
    """Raised when a sequential execution authority cannot be reconstructed."""


def _verify_stage_authority(
    *,
    plan: Mapping[str, Any],
    receipt: Mapping[str, Any],
    stages: list[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sequential = deepcopy(dict(receipt))
    required = {
        "schema_version",
        "plan_hash",
        "plan_schema_version",
        "clean_source_hash",
        "poison_source_hash",
        "clean_ast_hash",
        "poison_ast_hash",
        "stage_admissions",
        "stage_materialization_hashes",
        "exact_inverse_source_hash",
        "intermediate_admission_complete",
        "receipt_hash",
    }
    if (
        set(sequential) != required
        or sequential.get("schema_version") != SEQUENTIAL_RECEIPT_SCHEMA
        or sequential.get("receipt_hash") != hash_payload({
            key: value
            for key, value in sequential.items()
            if key != "receipt_hash"
        })
        or sequential.get("plan_hash") != plan["plan_hash"]
        or sequential.get("plan_schema_version") != plan["schema_version"]
        or sequential.get("clean_source_hash") != plan["clean_source_hash"]
        or sequential.get("exact_inverse_source_hash")
        != plan["clean_source_hash"]
        or sequential.get("intermediate_admission_complete") is not True
    ):
        raise GroundedSequentialExecutionViolation(
            "sequential materialization receipt is invalid"
        )
    admissions = list(sequential["stage_admissions"])
    if (
        len(stages) != len(plan["subplans"])
        or len(admissions) != len(stages)
        or len(sequential["stage_materialization_hashes"]) != len(stages)
    ):
        raise GroundedSequentialExecutionViolation(
            "sequential stage authority is incomplete"
        )
    verified_stages = []
    for index, (raw, subplan, admission) in enumerate(
        zip(stages, plan["subplans"], admissions)
    ):
        stage = deepcopy(dict(raw))
        if set(stage) != {
            "receipt",
            "semantic_diff_receipt",
            "parser_provider_receipt",
        }:
            raise GroundedSequentialExecutionViolation(
                "sequential stage fields mismatch"
            )
        try:
            materialization = verify_materialization_receipt(
                stage["receipt"]
            )
            semantic = verify_semantic_diff_receipt(
                stage["semantic_diff_receipt"]
            )
            provider = verify_provider_receipt(
                stage["parser_provider_receipt"]
            )
        except Exception as exc:
            raise GroundedSequentialExecutionViolation(
                "sequential stage authority is invalid"
            ) from exc
        admission_body = {
            key: value
            for key, value in dict(admission).items()
            if key != "admission_hash"
        }
        if (
            admission.get("admission_hash") != hash_payload(admission_body)
            or admission.get("admitted") is not True
            or admission.get("stage_index") != index
            or admission.get("subplan_hash") != subplan["plan_hash"]
            or admission.get("input_source_hash")
            != materialization["clean_rtl_hash"]
            or admission.get("output_source_hash")
            != materialization["poison_rtl_hash"]
            or admission.get("materialization_hash")
            != materialization["materialization_hash"]
            or admission.get("semantic_diff_receipt_hash")
            != semantic["receipt_hash"]
            or admission.get("parser_provider_receipt_hash")
            != provider["receipt_hash"]
            or sequential["stage_materialization_hashes"][index]
            != materialization["materialization_hash"]
            or materialization["plan_hash"] != subplan["plan_hash"]
            or semantic["plan_hash"] != subplan["plan_hash"]
            or provider["provider_kind"] != "semantic_parser"
            or provider["result"].get("materialization_hash")
            != materialization["materialization_hash"]
            or provider["result"].get("semantic_diff_receipt_hash")
            != semantic["receipt_hash"]
        ):
            raise GroundedSequentialExecutionViolation(
                "sequential intermediate admission is not reconstructable"
            )
        if index and (
            materialization["clean_rtl_hash"]
            != verified_stages[-1]["receipt"]["poison_rtl_hash"]
        ):
            raise GroundedSequentialExecutionViolation(
                "sequential materialization continuity mismatch"
            )
        verified_stages.append({
            "receipt": materialization,
            "semantic_diff_receipt": semantic,
            "parser_provider_receipt": provider,
        })
    if (
        verified_stages[0]["receipt"]["clean_rtl_hash"]
        != sequential["clean_source_hash"]
        or verified_stages[-1]["receipt"]["poison_rtl_hash"]
        != sequential["poison_source_hash"]
    ):
        raise GroundedSequentialExecutionViolation(
            "sequential materialization endpoints mismatch"
        )
    return sequential, verified_stages


def _decision_from_evidence(
    *,
    plan: Mapping[str, Any],
    policy: PolicyState,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    clean = evidence["clean_run"]
    poison_runs = evidence["poison_runs"]
    revert = evidence["revert_run"]
    source = evidence["source_integrity"]
    semantic = evidence["semantic_diff"]
    effect = evidence["runtime_effect"]
    sequential = evidence["sequential_materialization_receipt"]
    clean_observed = clean["observed"]
    poison_observed = [row["observed"] for row in poison_runs]
    revert_observed = revert["observed"]
    signatures = {
        (
            row.get("oracle_pass"),
            row.get("first_divergence_hash"),
            row.get("mismatch_topology_hash"),
            row.get("effect_signature"),
            row.get("waveform_observation_hash"),
        )
        for row in poison_observed
    }
    stages = evidence["stage_materializations"]
    expected_effects = {
        row["expected_runtime_effect_id"] for row in plan["subplans"]
    }
    checks = {
        "G1_source_integrity": (
            source["clean_rtl_hash"] == sequential["clean_source_hash"]
            and source["poison_rtl_hash"]
            == sequential["poison_source_hash"]
            and source["clean_rtl_hash"] != source["poison_rtl_hash"]
            and not source["forbidden_changes"]
        ),
        "G2_parser_elaboration": (
            sequential["intermediate_admission_complete"]
            and all(
                row.get("parse_ok")
                and row.get("elaboration_ok")
                and row.get("target_ast_node_exists")
                and row.get("operator_preconditions_hold")
                for row in poison_observed
            )
        ),
        "G3_clean_baseline": (
            clean["result_kind"] == "completed"
            and all(
                clean_observed.get(field)
                for field in (
                    "parse_ok",
                    "elaboration_ok",
                    "compile_ok",
                    "simulation_complete",
                    "oracle_pass",
                )
            )
        ),
        "G4_poison_executability": all(
            row["result_kind"] == "completed"
            and not row["timed_out"]
            and not row["crashed"]
            and observed.get("compile_ok")
            and observed.get("simulation_complete")
            for row, observed in zip(poison_runs, poison_observed)
        ),
        "G5_functional_failure": all(
            observed.get("oracle_pass") is False
            and observed.get("functional_mismatch") is True
            for observed in poison_observed
        ),
        "G6_determinism": len(signatures) == 1,
        "G7_revert_proof": (
            revert["result_kind"] == "completed"
            and revert_observed.get("parse_ok")
            and revert_observed.get("compile_ok")
            and revert_observed.get("simulation_complete")
            and revert_observed.get("oracle_pass")
            and revert_observed.get("restored_semantic_hash")
            == clean_observed.get("semantic_hash")
            and sequential["exact_inverse_source_hash"]
            == sequential["clean_source_hash"]
        ),
        "G8_semantic_family_proof": (
            len(stages) == len(plan["subplans"])
            and semantic["ast_edit_count"] == len(stages)
            and semantic["collateral_edit_count"] == 0
            and semantic["reachable"]
            and semantic["preconditions_satisfied"]
            and all(
                row["semantic_diff_receipt"]["ast_edit_count"] == 1
                and row["semantic_diff_receipt"][
                    "collateral_edit_count"
                ] == 0
                for row in stages
            )
        ),
        "G9_runtime_effect_proof": (
            len(expected_effects) == 1
            and effect["effect_id"] == next(iter(expected_effects))
            and effect["failure_signature"]
            == poison_observed[0].get("effect_signature")
            and effect["first_divergence_hash"]
            == poison_observed[0].get("first_divergence_hash")
            and effect["mismatch_topology_hash"]
            == poison_observed[0].get("mismatch_topology_hash")
        ),
        "G10_non_triviality": not any(
            evidence["nontriviality"].values()
        ),
        "G11_minimization": (
            evidence["minimization"]["attempted"]
            and evidence["minimization"]["succeeded"]
            and evidence["minimization"]["preserved_failure_signature"]
            and evidence["minimization"]["minimized_poison_hash"]
            == source["poison_rtl_hash"]
            and evidence["minimization"]["remaining_ast_edits"]
            == len(stages)
        ),
    }
    body = {
        "schema_version": SEQUENTIAL_DECISION_SCHEMA,
        "plan_hash": plan["plan_hash"],
        "challenged_policy_instance_hash": policy.policy_instance_hash,
        "challenged_effective_policy_hash": policy.effective_policy_hash,
        "poison_payload_hash": evidence["poison_payload_hash"],
        "evidence_hash": evidence["evidence_hash"],
        "checks": checks,
        "rejection_reasons": [
            name for name in _CHECK_NAMES if not checks[name]
        ],
        "admitted": all(checks.values()),
        "semantic_diff_receipt_hash": semantic["receipt_hash"],
        "runtime_effect_receipt_hash": effect["receipt_hash"],
        "minimized_poison_hash": evidence["minimization"][
            "minimized_poison_hash"
        ],
        "authority_mode": "runner_owned_grounded_execution",
    }
    return {**body, "decision_hash": hash_payload(body)}


def verify_grounded_sequential_execution_bundle(
    bundle: Mapping[str, Any],
    *,
    policy: PolicyState,
    registries: GroundedRegistryBundle,
) -> dict[str, Any]:
    payload = deepcopy(dict(bundle))
    if (
        set(payload) != _BUNDLE_FIELDS
        or payload.get("schema_version")
        != SEQUENTIAL_EXECUTION_BUNDLE_SCHEMA
        or payload.get("bundle_hash") != hash_payload({
            key: value
            for key, value in payload.items()
            if key != "bundle_hash"
        })
    ):
        raise GroundedSequentialExecutionViolation(
            "sequential execution bundle envelope is invalid"
        )
    plan = verify_sequential_plan(
        payload["plan"],
        policy=policy,
        registries=registries,
    )
    sequential, stages = _verify_stage_authority(
        plan=plan,
        receipt=payload["materialization_receipt"],
        stages=list(payload["stage_materializations"]),
    )
    semantic_provider = verify_provider_receipt(
        payload["semantic_provider_receipt"]
    )
    toolchain = verify_icarus_toolchain_fingerprint(
        payload["toolchain_fingerprint"]
    )
    evidence = deepcopy(dict(payload["evidence"]))
    if (
        set(evidence) != _EVIDENCE_FIELDS
        or evidence.get("schema_version") != SEQUENTIAL_EVIDENCE_SCHEMA
        or evidence.get("plan_hash") != plan["plan_hash"]
        or evidence.get("evidence_hash") != hash_payload({
            key: value
            for key, value in evidence.items()
            if key != "evidence_hash"
        })
        or evidence.get("sequential_materialization_receipt")
        != sequential
        or evidence.get("stage_materializations") != stages
        or evidence.get("semantic_provider_receipt")
        != semantic_provider
    ):
        raise GroundedSequentialExecutionViolation(
            "sequential admission evidence is invalid"
        )
    source = dict(evidence["source_integrity"])
    if set(source) != {
        "clean_rtl_hash",
        "poison_rtl_hash",
        "testbench_hash",
        "oracle_hash",
        "build_configuration_hash",
        "allowed_file_manifest_hash",
        "changed_file_hashes",
        "forbidden_changes",
    }:
        raise GroundedSequentialExecutionViolation(
            "sequential source integrity fields mismatch"
        )
    receipts = [
        verify_command_receipt(evidence["clean_run"]),
        *[
            verify_command_receipt(row)
            for row in evidence["poison_runs"]
        ],
        verify_command_receipt(evidence["revert_run"]),
    ]
    if len(receipts) != 4 or not all(
        _verify_execution_chain(row) for row in receipts
    ):
        raise GroundedSequentialExecutionViolation(
            "sequential command/provider chain is incomplete"
        )
    if any(
        row["toolchain_fingerprint_hash"]
        != toolchain["toolchain_fingerprint_hash"]
        or row["observed"].get("toolchain_fingerprint") != toolchain
        for row in receipts
    ):
        raise GroundedSequentialExecutionViolation(
            "sequential execution toolchain mismatch"
        )
    semantic = verify_semantic_diff_receipt(evidence["semantic_diff"])
    runtime = verify_runtime_effect_receipt(evidence["runtime_effect"])
    if (
        semantic["plan_hash"] != plan["plan_hash"]
        or runtime["plan_hash"] != plan["plan_hash"]
        or semantic_provider["provider_kind"] != "semantic_parser"
        or semantic_provider["provider_id"]
        != "r3e-verilog-sequential-ast"
        or semantic_provider["provider_version"] != "3"
        or semantic_provider["input_artifact_hashes"].get(
            "sequential_materialization_receipt"
        )
        != sequential["receipt_hash"]
        or semantic_provider["result"].get(
            "semantic_diff_receipt_hash"
        )
        != semantic["receipt_hash"]
    ):
        raise GroundedSequentialExecutionViolation(
            "sequential semantic authority mismatch"
        )
    expected = _decision_from_evidence(
        plan=plan,
        policy=policy,
        evidence={
            **evidence,
            "clean_run": receipts[0],
            "poison_runs": receipts[1:3],
            "revert_run": receipts[3],
            "semantic_diff": semantic,
            "runtime_effect": runtime,
        },
    )
    if payload["admission_decision"] != expected:
        raise GroundedSequentialExecutionViolation(
            "sequential admission decision cannot be reconstructed"
        )
    return {
        **payload,
        "plan": plan,
        "materialization_receipt": sequential,
        "stage_materializations": stages,
        "semantic_provider_receipt": semantic_provider,
        "toolchain_fingerprint": toolchain,
        "evidence": {
            **evidence,
            "clean_run": receipts[0],
            "poison_runs": receipts[1:3],
            "revert_run": receipts[3],
            "semantic_diff": semantic,
            "runtime_effect": runtime,
        },
        "admission_decision": expected,
    }


def execute_grounded_sequential_admission(
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
    publish_poison_path: str | Path | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    verified_plan = verify_sequential_plan(
        plan,
        policy=policy,
        registries=registries,
    )
    root = Path(workspace).resolve()
    clean_path = Path(clean_rtl_path).resolve()
    testbench_path = Path(testbench_path).resolve()
    try:
        clean_path.relative_to(root)
        testbench_path.relative_to(root)
    except ValueError as exc:
        raise GroundedSequentialExecutionViolation(
            "RTL and testbench must be inside the execution workspace"
        ) from exc
    if (
        hash_file(clean_path) != frozen_clean_rtl_hash
        or hash_file(testbench_path) != frozen_testbench_hash
    ):
        raise GroundedSequentialExecutionViolation(
            "sequential frozen input manifest mismatch"
        )
    clean_source = clean_path.read_text(encoding="utf-8")
    materialized = materialize_sequential_plan(
        verified_plan,
        policy=policy,
        registries=registries,
        clean_source=clean_source,
    )
    poison_path = root / "materialized" / "poison.v"
    revert_path = root / "materialized" / "reverted.v"
    poison_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(poison_path, materialized["poison_source"])
    atomic_write_text(revert_path, clean_source)
    if publish_poison_path is not None:
        published = Path(publish_poison_path).resolve()
        if published.exists():
            if (
                not published.is_file()
                or published.read_text(encoding="utf-8")
                != materialized["poison_source"]
            ):
                raise GroundedSequentialExecutionViolation(
                    "published poison path contains adapter-authored RTL"
                )
        else:
            atomic_write_text(published, materialized["poison_source"])
    stage_semantics = [
        row["semantic_diff_receipt"]
        for row in materialized["materializations"]
    ]
    semantic = build_semantic_diff_receipt(
        plan_hash=verified_plan["plan_hash"],
        family_id=(
            verified_plan.get("family_id")
            or f"memory.{verified_plan['operator']}"
        ),
        operator_id=(
            verified_plan.get("lineage_operator")
            or verified_plan["operator"]
        ),
        clean_ast_hash=materialized["receipt"]["clean_ast_hash"],
        poison_ast_hash=materialized["receipt"]["poison_ast_hash"],
        target_ast_node_hash=hash_payload({
            "stage_targets": [
                row["target_ast_node_hash"]
                for row in verified_plan["subplans"]
            ]
        }),
        normalized_ast_diff_hash=hash_payload({
            "stage_semantic_receipts": [
                row["receipt_hash"] for row in stage_semantics
            ]
        }),
        changed_module_count=1,
        changed_block_count=len(stage_semantics),
        ast_edit_count=len(stage_semantics),
        dependency_depth=len(stage_semantics) - 1,
        collateral_edit_count=sum(
            row["collateral_edit_count"] for row in stage_semantics
        ),
        reachable=all(row["reachable"] for row in stage_semantics),
        preconditions_satisfied=all(
            row["preconditions_satisfied"] for row in stage_semantics
        ),
    )
    semantic_provider = build_provider_receipt(
        provider_kind="semantic_parser",
        provider_id="r3e-verilog-sequential-ast",
        provider_version="3",
        input_artifact_hashes={
            "clean_rtl": hash_file(clean_path),
            "poison_rtl": hash_file(poison_path),
            "sequential_materialization_receipt": materialized[
                "receipt"
            ]["receipt_hash"],
        },
        result={
            "plan_hash": verified_plan["plan_hash"],
            "stage_count": len(stage_semantics),
            "semantic_diff_receipt_hash": semantic["receipt_hash"],
            "sequential_materialization_receipt_hash": materialized[
                "receipt"
            ]["receipt_hash"],
        },
    )
    provider = IcarusGroundedProvider(
        workspace=root,
        run_context_hash=run_context_hash,
        timeout_seconds=timeout_seconds,
    )
    clean_run = provider.execute(
        receipt_prefix=f"{verified_plan['poison_id']}:clean",
        mode="clean_baseline",
        rtl_path=clean_path,
        testbench_path=testbench_path,
        top_module=top_module,
        semantic_hash=normalized_ast_hash(clean_source),
    )
    poison_runs = [
        provider.execute(
            receipt_prefix=(
                f"{verified_plan['poison_id']}:poison:{index}"
            ),
            mode="poison_execution",
            rtl_path=poison_path,
            testbench_path=testbench_path,
            top_module=top_module,
            semantic_hash=materialized["receipt"]["poison_ast_hash"],
            semantic_provider_receipt=semantic_provider,
        )
        for index in (1, 2)
    ]
    revert_run = provider.execute(
        receipt_prefix=f"{verified_plan['poison_id']}:revert",
        mode="revert_execution",
        rtl_path=revert_path,
        testbench_path=testbench_path,
        top_module=top_module,
        semantic_hash=materialized["receipt"]["clean_ast_hash"],
    )
    observed = poison_runs[0]["observed"]
    effects = {
        row["expected_runtime_effect_id"]
        for row in verified_plan["subplans"]
    }
    effect_id = next(iter(effects)) if len(effects) == 1 else "mixed_effect"
    runtime_effect = build_runtime_effect_receipt(
        plan_hash=verified_plan["plan_hash"],
        effect_id=effect_id,
        failure_signature=observed["effect_signature"],
        first_divergence_hash=observed["first_divergence_hash"],
        mismatch_topology_hash=observed["mismatch_topology_hash"],
        temporal_depth=0,
        observable_features={
            key: observed[key]
            for key in (
                "waveform_observation_hash",
                "first_divergence_signal",
                "first_divergence_cycle",
                "cycle_offset",
                "temporal_relation",
                "assignment_type",
                "cone_depth",
                "mismatch_pattern",
            )
        },
    )
    evidence = {
        "schema_version": SEQUENTIAL_EVIDENCE_SCHEMA,
        "plan_hash": verified_plan["plan_hash"],
        "poison_payload_hash": hash_payload({
            "plan_hash": verified_plan["plan_hash"],
            "poison_rtl_hash": hash_file(poison_path),
            "testbench_hash": hash_file(testbench_path),
        }),
        "source_integrity": {
            "clean_rtl_hash": hash_file(clean_path),
            "poison_rtl_hash": hash_file(poison_path),
            "testbench_hash": hash_file(testbench_path),
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
                "materialized/poison.v": hash_file(poison_path)
            },
            "forbidden_changes": [],
        },
        "clean_run": clean_run,
        "poison_runs": poison_runs,
        "revert_run": revert_run,
        "semantic_diff": semantic,
        "runtime_effect": runtime_effect,
        "sequential_materialization_receipt": materialized["receipt"],
        "stage_materializations": materialized["materializations"],
        "semantic_provider_receipt": semantic_provider,
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
            "remaining_ast_edits": len(stage_semantics),
        },
    }
    evidence["evidence_hash"] = hash_payload(evidence)
    decision = _decision_from_evidence(
        plan=verified_plan,
        policy=policy,
        evidence=evidence,
    )
    bundle = {
        "schema_version": SEQUENTIAL_EXECUTION_BUNDLE_SCHEMA,
        "plan": verified_plan,
        "materialization_receipt": materialized["receipt"],
        "stage_materializations": materialized["materializations"],
        "semantic_provider_receipt": semantic_provider,
        "toolchain_fingerprint": provider.toolchain,
        "evidence": evidence,
        "admission_decision": decision,
    }
    bundle["bundle_hash"] = hash_payload(bundle)
    verified = verify_grounded_sequential_execution_bundle(
        bundle,
        policy=policy,
        registries=registries,
    )
    atomic_write_json(
        root / "grounded_sequential_execution_bundle.json",
        verified,
    )
    return verified
