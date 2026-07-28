"""Runner-owned G1-G11 Grounded Red admission authority."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

from r3e.grounded.receipts import verify_command_receipt
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload

from .mutation_plan import verify_mutation_plan
from .proofs import (
    verify_runtime_effect_receipt,
    verify_semantic_diff_receipt,
)
from .registry import GroundedRegistryBundle


ADMISSION_EVIDENCE_SCHEMA_VERSION = "r3e-red-admission-evidence-v1"
ADMISSION_DECISION_SCHEMA_VERSION = "r3e-red-admission-decision-v1"
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_NONTRIVIALITY_FLAGS = {
    "constant_output",
    "deleted_major_block",
    "global_x",
    "unbounded_execution",
    "comment_only",
    "unreachable_only",
    "unrelated_signal_only",
    "excess_collateral",
}


class GroundedAdmissionViolation(RuntimeError):
    """Raised when an admission authority object cannot be reconstructed."""


def _digest(value: Any, field: str) -> str:
    text = str(value or "")
    if not _HASH_RE.fullmatch(text):
        raise GroundedAdmissionViolation(
            f"{field} must be an exact sha256 digest"
        )
    return text


def _verify_source_integrity(value: Mapping[str, Any]) -> dict[str, Any]:
    source = deepcopy(dict(value))
    required = {
        "clean_rtl_hash",
        "poison_rtl_hash",
        "testbench_hash",
        "oracle_hash",
        "build_configuration_hash",
        "allowed_file_manifest_hash",
        "changed_file_hashes",
        "forbidden_changes",
    }
    if set(source) != required:
        raise GroundedAdmissionViolation("source integrity fields mismatch")
    for field in required - {"changed_file_hashes", "forbidden_changes"}:
        _digest(source[field], field)
    if source["clean_rtl_hash"] == source["poison_rtl_hash"]:
        raise GroundedAdmissionViolation("poison RTL is identical to clean RTL")
    changes = source["changed_file_hashes"]
    if not isinstance(changes, dict) or not changes:
        raise GroundedAdmissionViolation("source integrity has no allowed change")
    for path, digest in changes.items():
        if not isinstance(path, str) or not path:
            raise GroundedAdmissionViolation("changed file path is invalid")
        _digest(digest, f"changed_file_hashes.{path}")
    forbidden = source["forbidden_changes"]
    if not isinstance(forbidden, list) or any(
        not isinstance(item, str) or not item for item in forbidden
    ):
        raise GroundedAdmissionViolation("forbidden_changes must be a string list")
    return source


def _observed(receipt: dict[str, Any], required: set[str]) -> dict[str, Any]:
    observed = receipt["observed"]
    missing = required - observed.keys()
    if missing:
        raise GroundedAdmissionViolation(
            f"{receipt['phase']} receipt missing observations: {sorted(missing)}"
        )
    return observed


def _verify_evidence(
    evidence: Mapping[str, Any],
    *,
    plan: dict[str, Any],
) -> dict[str, Any]:
    payload = deepcopy(dict(evidence))
    required = {
        "schema_version",
        "plan_hash",
        "poison_payload_hash",
        "source_integrity",
        "clean_run",
        "poison_runs",
        "revert_run",
        "semantic_diff",
        "runtime_effect",
        "nontriviality",
        "minimization",
        "evidence_hash",
    }
    if set(payload) != required:
        raise GroundedAdmissionViolation("grounded admission evidence fields mismatch")
    if payload["schema_version"] != ADMISSION_EVIDENCE_SCHEMA_VERSION:
        raise GroundedAdmissionViolation("grounded admission evidence schema mismatch")
    if payload["plan_hash"] != plan["plan_hash"]:
        raise GroundedAdmissionViolation("admission evidence plan mismatch")
    _digest(payload["poison_payload_hash"], "poison_payload_hash")
    body = {key: value for key, value in payload.items() if key != "evidence_hash"}
    if payload["evidence_hash"] != hash_payload(body):
        raise GroundedAdmissionViolation("grounded admission evidence hash mismatch")
    source = _verify_source_integrity(payload["source_integrity"])
    clean = verify_command_receipt(payload["clean_run"])
    poison_runs = [
        verify_command_receipt(row) for row in payload["poison_runs"]
    ]
    if len(poison_runs) < 2:
        raise GroundedAdmissionViolation(
            "determinism gate requires two poison executions"
        )
    revert = verify_command_receipt(payload["revert_run"])
    if clean["phase"] != "clean_baseline" or any(
        row["phase"] != "poison_execution" for row in poison_runs
    ) or revert["phase"] != "revert_execution":
        raise GroundedAdmissionViolation("grounded receipt phase mismatch")
    receipts = [clean, *poison_runs, revert]
    if len({row["receipt_id"] for row in receipts}) != len(receipts):
        raise GroundedAdmissionViolation("grounded receipt ids must be unique")
    if (
        len({row["run_context_hash"] for row in receipts}) != 1
        or len({row["toolchain_fingerprint_hash"] for row in receipts}) != 1
    ):
        raise GroundedAdmissionViolation(
            "grounded receipts use inconsistent run context or toolchain"
        )
    if clean["subject_hash"] != source["clean_rtl_hash"] or any(
        row["subject_hash"] != source["poison_rtl_hash"]
        for row in poison_runs
    ):
        raise GroundedAdmissionViolation("execution receipt/source binding mismatch")
    semantic = verify_semantic_diff_receipt(payload["semantic_diff"])
    effect = verify_runtime_effect_receipt(payload["runtime_effect"])
    if semantic["plan_hash"] != plan["plan_hash"] or effect["plan_hash"] != plan[
        "plan_hash"
    ]:
        raise GroundedAdmissionViolation("proof receipt plan mismatch")
    nontriviality = payload["nontriviality"]
    if (
        not isinstance(nontriviality, dict)
        or set(nontriviality) != _NONTRIVIALITY_FLAGS
        or any(not isinstance(value, bool) for value in nontriviality.values())
    ):
        raise GroundedAdmissionViolation("nontriviality proof fields mismatch")
    minimization = payload["minimization"]
    if not isinstance(minimization, dict) or set(minimization) != {
        "attempted",
        "succeeded",
        "minimized_poison_hash",
        "preserved_failure_signature",
        "remaining_ast_edits",
    }:
        raise GroundedAdmissionViolation("minimization proof fields mismatch")
    for field in ("attempted", "succeeded", "preserved_failure_signature"):
        if not isinstance(minimization[field], bool):
            raise GroundedAdmissionViolation(
                f"minimization {field} must be boolean"
            )
    _digest(minimization["minimized_poison_hash"], "minimized_poison_hash")
    if (
        not isinstance(minimization["remaining_ast_edits"], int)
        or isinstance(minimization["remaining_ast_edits"], bool)
        or minimization["remaining_ast_edits"] < 1
    ):
        raise GroundedAdmissionViolation(
            "remaining_ast_edits must be positive"
        )
    return {
        **payload,
        "source_integrity": source,
        "clean_run": clean,
        "poison_runs": poison_runs,
        "revert_run": revert,
        "semantic_diff": semantic,
        "runtime_effect": effect,
    }


def decide_grounded_admission(
    *,
    plan: Mapping[str, Any],
    policy: PolicyState,
    registries: GroundedRegistryBundle,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    verified_plan = verify_mutation_plan(
        plan, policy=policy, registries=registries
    )
    verified = _verify_evidence(evidence, plan=verified_plan)
    source = verified["source_integrity"]
    clean = verified["clean_run"]
    poison_runs = verified["poison_runs"]
    revert = verified["revert_run"]
    semantic = verified["semantic_diff"]
    effect = verified["runtime_effect"]
    clean_observed = _observed(
        clean,
        {
            "parse_ok",
            "elaboration_ok",
            "compile_ok",
            "simulation_complete",
            "oracle_pass",
            "semantic_hash",
        },
    )
    poison_observed = [
        _observed(
            row,
            {
                "parse_ok",
                "elaboration_ok",
                "target_ast_node_exists",
                "operator_preconditions_hold",
                "compile_ok",
                "simulation_complete",
                "oracle_pass",
                "functional_mismatch",
                "first_divergence_hash",
                "mismatch_topology_hash",
                "effect_signature",
            },
        )
        for row in poison_runs
    ]
    revert_observed = _observed(
        revert,
        {
            "parse_ok",
            "compile_ok",
            "simulation_complete",
            "oracle_pass",
            "restored_semantic_hash",
        },
    )
    signatures = {
        (
            row["oracle_pass"],
            row["first_divergence_hash"],
            row["mismatch_topology_hash"],
            row["effect_signature"],
        )
        for row in poison_observed
    }
    scope = verified_plan["scope_limits"]
    minimization = verified["minimization"]
    checks = {
        "G1_source_integrity": (
            not source["forbidden_changes"]
            and source["poison_rtl_hash"]
            in source["changed_file_hashes"].values()
        ),
        "G2_parser_elaboration": (
            all(
                row["parse_ok"]
                and row["elaboration_ok"]
                and row["target_ast_node_exists"]
                and row["operator_preconditions_hold"]
                for row in poison_observed
            )
            and semantic["preconditions_satisfied"]
        ),
        "G3_clean_baseline": (
            clean["result_kind"] == "completed"
            and clean_observed["parse_ok"]
            and clean_observed["elaboration_ok"]
            and clean_observed["compile_ok"]
            and clean_observed["simulation_complete"]
            and clean_observed["oracle_pass"]
        ),
        "G4_poison_executability": all(
            row["result_kind"] == "completed"
            and not row["timed_out"]
            and not row["crashed"]
            and observed["compile_ok"]
            and observed["simulation_complete"]
            for row, observed in zip(poison_runs, poison_observed)
        ),
        "G5_functional_failure": all(
            not row["oracle_pass"] and row["functional_mismatch"]
            for row in poison_observed
        ),
        "G6_determinism": len(signatures) == 1,
        "G7_revert_proof": (
            revert["result_kind"] == "completed"
            and revert_observed["parse_ok"]
            and revert_observed["compile_ok"]
            and revert_observed["simulation_complete"]
            and revert_observed["oracle_pass"]
            and revert_observed["restored_semantic_hash"]
            == clean_observed["semantic_hash"]
        ),
        "G8_semantic_family_proof": (
            semantic["family_id"] == verified_plan["family_id"]
            and semantic["operator_id"] == verified_plan["operator_id"]
            and semantic["target_ast_node_hash"]
            == verified_plan["target_ast_node_hash"]
            and semantic["changed_module_count"]
            <= scope["maximum_changed_modules"]
            and semantic["changed_block_count"]
            <= scope["maximum_changed_blocks"]
            and semantic["ast_edit_count"] <= scope["maximum_ast_edits"]
            and semantic["collateral_edit_count"] == 0
            and semantic["reachable"]
        ),
        "G9_runtime_effect_proof": (
            effect["effect_id"]
            == verified_plan["expected_runtime_effect_id"]
            and effect["failure_signature"]
            == poison_observed[0]["effect_signature"]
            and effect["first_divergence_hash"]
            == poison_observed[0]["first_divergence_hash"]
            and effect["mismatch_topology_hash"]
            == poison_observed[0]["mismatch_topology_hash"]
        ),
        "G10_non_triviality": not any(verified["nontriviality"].values()),
        "G11_minimization": (
            minimization["attempted"]
            and minimization["succeeded"]
            and minimization["preserved_failure_signature"]
            and minimization["minimized_poison_hash"]
            == source["poison_rtl_hash"]
            and minimization["remaining_ast_edits"]
            <= semantic["ast_edit_count"]
        ),
    }
    decision = {
        "schema_version": ADMISSION_DECISION_SCHEMA_VERSION,
        "plan_hash": verified_plan["plan_hash"],
        "challenged_policy_instance_hash": policy.policy_instance_hash,
        "challenged_effective_policy_hash": policy.effective_policy_hash,
        "poison_payload_hash": verified["poison_payload_hash"],
        "evidence_hash": verified["evidence_hash"],
        "checks": checks,
        "rejection_reasons": [
            name for name, passed in checks.items() if not passed
        ],
        "admitted": all(checks.values()),
        "semantic_diff_receipt_hash": semantic["receipt_hash"],
        "runtime_effect_receipt_hash": effect["receipt_hash"],
        "minimized_poison_hash": minimization["minimized_poison_hash"],
    }
    decision["decision_hash"] = hash_payload(decision)
    return decision


def verify_grounded_admission_decision(
    decision: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    policy: PolicyState,
    registries: GroundedRegistryBundle,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    recorded = deepcopy(dict(decision))
    required = {
        "schema_version",
        "plan_hash",
        "challenged_policy_instance_hash",
        "challenged_effective_policy_hash",
        "poison_payload_hash",
        "evidence_hash",
        "checks",
        "rejection_reasons",
        "admitted",
        "semantic_diff_receipt_hash",
        "runtime_effect_receipt_hash",
        "minimized_poison_hash",
        "decision_hash",
    }
    if set(recorded) != required:
        raise GroundedAdmissionViolation("grounded admission decision fields mismatch")
    if recorded["schema_version"] != ADMISSION_DECISION_SCHEMA_VERSION:
        raise GroundedAdmissionViolation("grounded admission decision schema mismatch")
    if recorded["decision_hash"] != hash_payload({
        key: value for key, value in recorded.items() if key != "decision_hash"
    }):
        raise GroundedAdmissionViolation("grounded admission decision hash mismatch")
    reconstructed = decide_grounded_admission(
        plan=plan,
        policy=policy,
        registries=registries,
        evidence=evidence,
    )
    if recorded != reconstructed:
        raise GroundedAdmissionViolation(
            "grounded admission decision is not reconstructable"
        )
    return recorded
