"""Append-only archive records for admitted poisons rejected by formal proof."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any, Mapping

from r3e.grounded.yosys_formal import (
    verify_formal_proof_assessment,
)
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import canonical_json, hash_payload, utc_now
from r3e.protocol.ledger import writer_lock
from r3e.red.poison_payload import verify_poison_payload

from .execution import (
    execution_materialization_bounds,
    verify_grounded_execution_bundle,
)
from .registry import GroundedRegistryBundle


FORMAL_REJECTION_SCHEMA_VERSION = "r3e-grounded-formal-rejection-v1"
FORMAL_REJECTION_VALIDITY_AUTHORITY = (
    "runner_owned_formal_rejection"
)
_REJECTION_FIELDS = {
    "schema_version",
    "round_id",
    "policy_state",
    "poison_payload",
    "execution_bundle",
    "formal_proof_assessment",
    "rejection_reasons",
    "rejected_at",
    "rejection_hash",
}
_VALIDITY_FIELDS = {
    "proven_valid",
    "checks",
    "rejection_reasons",
    "evidence",
    "result_hash",
}


class GroundedFormalRejectionViolation(RuntimeError):
    """Raised when a formal rejection is incomplete or not cross-bound."""


def _verify_rejection_bindings(
    payload: Mapping[str, Any],
    *,
    policy: PolicyState,
    registries: GroundedRegistryBundle,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    poison = deepcopy(dict(payload["poison_payload"]))
    verify_poison_payload(poison)
    execution = verify_grounded_execution_bundle(
        payload["execution_bundle"],
        policy=policy,
        registries=registries,
    )
    assessment = verify_formal_proof_assessment(
        payload["formal_proof_assessment"]
    )
    plan = execution["plan"]
    bounds = execution_materialization_bounds(execution)
    decision = execution["admission_decision"]
    plan_payload = (
        poison.get("grounded_sequential_plan")
        or poison.get("grounded_mutation_plan")
        or {}
    )
    if (
        not decision["admitted"]
        or assessment["proof_satisfied"]
        or any(
            assessment[phase]["command_receipt"]["result_kind"]
            != "completed"
            for phase in ("clean", "poison", "revert")
        )
        or poison.get("poison_id")
        != (plan.get("plan_id") or plan.get("poison_id"))
        or poison.get("grounded_plan_hash") != plan["plan_hash"]
        or plan_payload.get("plan_hash") != plan["plan_hash"]
        or poison.get("challenged_policy_hash") != policy.policy_hash
        or (
            (
                plan.get("challenged_policy_hash")
                != policy.policy_hash
            )
            if plan.get("schema_version") in {
                "r3e-parser-memory-operator-plan-v1",
                "r3e-controlled-composition-plan-v1",
            }
            else plan.get("challenged_policy_instance_hash")
            != policy.policy_instance_hash
        )
        or plan["challenged_effective_policy_hash"]
        != policy.effective_policy_hash
        or assessment["clean"]["rtl_hash"] != bounds["clean_rtl_hash"]
        or assessment["poison"]["rtl_hash"] != bounds["poison_rtl_hash"]
        or assessment["revert"]["rtl_hash"] != bounds["revert_rtl_hash"]
        or assessment["clean"]["top_module"]
        != poison.get("grounded_formal_top_module")
        or assessment["clean"]["depth"]
        != poison.get("grounded_formal_depth")
    ):
        raise GroundedFormalRejectionViolation(
            "formal rejection authority objects are not cross-bound"
        )
    return poison, execution, assessment


def build_formal_rejection(
    *,
    round_id: str,
    poison: Mapping[str, Any],
    execution_bundle: Mapping[str, Any],
    formal_proof_assessment: Mapping[str, Any],
    policy: PolicyState,
    registries: GroundedRegistryBundle,
) -> dict[str, Any]:
    if not str(round_id):
        raise GroundedFormalRejectionViolation(
            "formal rejection requires a round id"
        )
    payload = {
        "schema_version": FORMAL_REJECTION_SCHEMA_VERSION,
        "round_id": str(round_id),
        "policy_state": policy.to_dict(),
        "poison_payload": deepcopy(dict(poison)),
        "execution_bundle": deepcopy(dict(execution_bundle)),
        "formal_proof_assessment": deepcopy(
            dict(formal_proof_assessment)
        ),
        "rejection_reasons": list(
            formal_proof_assessment.get("rejection_reasons") or []
        ),
        "rejected_at": utc_now(),
    }
    payload["rejection_hash"] = hash_payload(payload)
    return verify_formal_rejection(
        payload,
        policy=policy,
        registries=registries,
    )


def verify_formal_rejection(
    record: Mapping[str, Any],
    *,
    policy: PolicyState | None,
    registries: GroundedRegistryBundle,
) -> dict[str, Any]:
    payload = deepcopy(dict(record))
    if set(payload) != _REJECTION_FIELDS:
        raise GroundedFormalRejectionViolation(
            "formal rejection fields mismatch"
        )
    if payload["schema_version"] != FORMAL_REJECTION_SCHEMA_VERSION:
        raise GroundedFormalRejectionViolation(
            "formal rejection schema mismatch"
        )
    if not isinstance(payload["round_id"], str) or not payload["round_id"]:
        raise GroundedFormalRejectionViolation(
            "formal rejection round id is invalid"
        )
    if (
        not isinstance(payload["rejected_at"], str)
        or not payload["rejected_at"]
        or payload["rejection_hash"] != hash_payload({
            key: value for key, value in payload.items()
            if key != "rejection_hash"
        })
    ):
        raise GroundedFormalRejectionViolation(
            "formal rejection envelope is invalid"
        )
    embedded_policy = PolicyState.from_dict(payload["policy_state"])
    if policy is not None and (
        policy.policy_instance_hash
        != embedded_policy.policy_instance_hash
    ):
        raise GroundedFormalRejectionViolation(
            "formal rejection policy snapshot mismatch"
        )
    poison, execution, assessment = _verify_rejection_bindings(
        payload,
        policy=embedded_policy,
        registries=registries,
    )
    if payload["rejection_reasons"] != assessment[
        "rejection_reasons"
    ]:
        raise GroundedFormalRejectionViolation(
            "formal rejection reasons mismatch"
        )
    return {
        **payload,
        "poison_payload": poison,
        "execution_bundle": execution,
        "formal_proof_assessment": assessment,
    }


def build_formal_rejection_validity(
    rejection: Mapping[str, Any],
) -> dict[str, Any]:
    assessment = rejection["formal_proof_assessment"]
    checks = {
        "F1_clean_proof": assessment["clean"]["verdict"] == "proved",
        "F2_poison_counterexample": (
            assessment["poison"]["verdict"] == "counterexample"
        ),
        "F3_revert_proof": assessment["revert"]["verdict"] == "proved",
    }
    record = {
        "proven_valid": False,
        "checks": checks,
        "rejection_reasons": list(rejection["rejection_reasons"]),
        "evidence": {
            "authority_mode": FORMAL_REJECTION_VALIDITY_AUTHORITY,
            "rejection_hash": rejection["rejection_hash"],
            "execution_bundle_hash": rejection["execution_bundle"][
                "bundle_hash"
            ],
            "formal_assessment_hash": assessment["assessment_hash"],
            "plan_hash": rejection["execution_bundle"]["plan"][
                "plan_hash"
            ],
        },
    }
    record["result_hash"] = hash_payload(record)
    return verify_formal_rejection_validity(record, rejection)


def verify_formal_rejection_validity(
    record: Mapping[str, Any],
    rejection: Mapping[str, Any],
) -> dict[str, Any]:
    payload = deepcopy(dict(record))
    if set(payload) != _VALIDITY_FIELDS:
        raise GroundedFormalRejectionViolation(
            "formal rejection validity fields mismatch"
        )
    expected = {
        "proven_valid": False,
        "checks": {
            "F1_clean_proof": (
                rejection["formal_proof_assessment"]["clean"]["verdict"]
                == "proved"
            ),
            "F2_poison_counterexample": (
                rejection["formal_proof_assessment"]["poison"]["verdict"]
                == "counterexample"
            ),
            "F3_revert_proof": (
                rejection["formal_proof_assessment"]["revert"]["verdict"]
                == "proved"
            ),
        },
        "rejection_reasons": list(rejection["rejection_reasons"]),
        "evidence": {
            "authority_mode": FORMAL_REJECTION_VALIDITY_AUTHORITY,
            "rejection_hash": rejection["rejection_hash"],
            "execution_bundle_hash": rejection["execution_bundle"][
                "bundle_hash"
            ],
            "formal_assessment_hash": rejection[
                "formal_proof_assessment"
            ]["assessment_hash"],
            "plan_hash": rejection["execution_bundle"]["plan"][
                "plan_hash"
            ],
        },
    }
    expected["result_hash"] = hash_payload(expected)
    if payload != expected:
        raise GroundedFormalRejectionViolation(
            "formal rejection validity cannot be reconstructed"
        )
    return payload


def load_formal_rejection_archive(
    path: str | Path,
    *,
    registries: GroundedRegistryBundle,
) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows = [
        json.loads(line)
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [
        verify_formal_rejection(
            row,
            policy=None,
            registries=registries,
        )
        for row in rows
    ]


def append_formal_rejection(
    path: str | Path,
    record: Mapping[str, Any],
    *,
    policy: PolicyState,
    registries: GroundedRegistryBundle,
) -> dict[str, Any]:
    target = Path(path)
    verified = verify_formal_rejection(
        record,
        policy=policy,
        registries=registries,
    )
    with writer_lock(target.with_suffix(target.suffix + ".lock")):
        existing = load_formal_rejection_archive(
            target,
            registries=registries,
        )
        duplicate = next(
            (
                row for row in existing
                if row["rejection_hash"] == verified["rejection_hash"]
            ),
            None,
        )
        if duplicate is not None:
            return duplicate
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as stream:
            stream.write(canonical_json(verified) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return verified
