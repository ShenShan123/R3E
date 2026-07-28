"""Artifact-derived RAAM FailureDescriptor authority."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from r3e.memory.descriptor import build_failure_descriptor
from r3e.memory.schema import FailureDescriptor

from .provider_receipts import (
    build_provider_receipt,
    verify_provider_receipt,
)
from .yosys_formal import verify_formal_proof_triplet


FAILURE_DESCRIPTOR_PROVIDER_ID = "r3e-grounded-failure-descriptor"
FAILURE_DESCRIPTOR_PROVIDER_VERSION = "1"


class GroundedFailureDescriptorViolation(RuntimeError):
    """Raised when a descriptor is not derived from authority artifacts."""


def _observable_artifact_hashes(
    execution_bundle: Mapping[str, Any],
    formal_proof_triplet: Mapping[str, Any],
) -> dict[str, str]:
    bundle = dict(execution_bundle)
    evidence = dict(bundle.get("evidence") or {})
    clean = dict(evidence.get("clean_run") or {})
    poison_runs = list(evidence.get("poison_runs") or [])
    revert = dict(evidence.get("revert_run") or {})
    if len(poison_runs) != 2:
        raise GroundedFailureDescriptorViolation(
            "descriptor requires two deterministic poison runs"
        )

    def oracle_hash(run: Mapping[str, Any], label: str) -> str:
        observed = dict(run.get("observed") or {})
        receipt = dict(observed.get("oracle_provider_receipt") or {})
        value = str(receipt.get("receipt_hash") or "")
        if not value:
            raise GroundedFailureDescriptorViolation(
                f"{label} oracle receipt is missing"
            )
        return value

    semantic = dict(evidence.get("semantic_diff") or {})
    runtime = dict(evidence.get("runtime_effect") or {})
    triplet = dict(formal_proof_triplet)
    values = {
        "grounded_execution_bundle": str(
            bundle.get("bundle_hash") or ""
        ),
        "clean_oracle_receipt": oracle_hash(clean, "clean"),
        "poison_oracle_receipt_1": oracle_hash(
            poison_runs[0], "poison run 1"
        ),
        "poison_oracle_receipt_2": oracle_hash(
            poison_runs[1], "poison run 2"
        ),
        "revert_oracle_receipt": oracle_hash(revert, "revert"),
        "semantic_diff_receipt": str(
            semantic.get("receipt_hash") or ""
        ),
        "runtime_effect_receipt": str(
            runtime.get("receipt_hash") or ""
        ),
        "formal_proof_triplet": str(
            triplet.get("triplet_hash") or ""
        ),
    }
    if any(not value for value in values.values()):
        raise GroundedFailureDescriptorViolation(
            "descriptor authority artifact hash is missing"
        )
    return values


def build_grounded_failure_descriptor(
    *,
    execution_bundle: Mapping[str, Any],
    formal_proof_triplet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a descriptor without consuming family/operator/red labels."""
    triplet = verify_formal_proof_triplet(formal_proof_triplet)
    artifacts = _observable_artifact_hashes(
        execution_bundle, triplet
    )
    runtime = dict(
        (dict(execution_bundle).get("evidence") or {}).get(
            "runtime_effect"
        )
        or {}
    )
    descriptor = build_failure_descriptor({
        "oracle_stage": "grounded_simulation_and_formal",
        "sequential_context": int(
            runtime.get("temporal_depth") or 0
        ) > 0,
        "affected_roles": ["observable_output"],
        "mismatch_pattern": "reproducible_functional_mismatch",
        "first_divergence_bucket": "oracle_reported",
        "observable_artifact_hashes": artifacts,
    }).to_dict()
    receipt = build_provider_receipt(
        provider_kind="failure_descriptor",
        provider_id=FAILURE_DESCRIPTOR_PROVIDER_ID,
        provider_version=FAILURE_DESCRIPTOR_PROVIDER_VERSION,
        input_artifact_hashes=artifacts,
        result={"failure_descriptor": descriptor},
    )
    return verify_grounded_failure_descriptor(
        descriptor,
        receipt,
        execution_bundle=execution_bundle,
        formal_proof_triplet=triplet,
    )


def verify_grounded_failure_descriptor(
    descriptor: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    execution_bundle: Mapping[str, Any],
    formal_proof_triplet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    verified_descriptor = FailureDescriptor.from_dict(
        descriptor
    ).to_dict()
    verified_triplet = verify_formal_proof_triplet(
        formal_proof_triplet
    )
    verified_receipt = verify_provider_receipt(receipt)
    expected_artifacts = _observable_artifact_hashes(
        execution_bundle, verified_triplet
    )
    if (
        verified_receipt["provider_kind"]
        != "failure_descriptor"
        or verified_receipt["provider_id"]
        != FAILURE_DESCRIPTOR_PROVIDER_ID
        or verified_receipt["provider_version"]
        != FAILURE_DESCRIPTOR_PROVIDER_VERSION
        or verified_receipt["input_artifact_hashes"]
        != expected_artifacts
        or verified_receipt["result"]
        != {"failure_descriptor": verified_descriptor}
        or verified_descriptor["observable_artifact_hashes"]
        != expected_artifacts
    ):
        raise GroundedFailureDescriptorViolation(
            "failure descriptor is not bound to authority artifacts"
        )
    return deepcopy(verified_descriptor), verified_receipt
