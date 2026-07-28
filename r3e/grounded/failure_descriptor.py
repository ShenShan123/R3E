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

    def oracle_receipt(
        run: Mapping[str, Any], label: str
    ) -> dict[str, Any]:
        observed = dict(run.get("observed") or {})
        try:
            receipt = verify_provider_receipt(
                observed.get("oracle_provider_receipt") or {}
            )
        except Exception as exc:
            raise GroundedFailureDescriptorViolation(
                f"{label} oracle receipt is invalid"
            ) from exc
        return receipt

    clean_oracle = oracle_receipt(clean, "clean")
    poison_oracles = [
        oracle_receipt(poison_runs[0], "poison run 1"),
        oracle_receipt(poison_runs[1], "poison run 2"),
    ]
    revert_oracle = oracle_receipt(revert, "revert")

    semantic = dict(evidence.get("semantic_diff") or {})
    runtime = dict(evidence.get("runtime_effect") or {})
    triplet = dict(formal_proof_triplet)
    values = {
        "grounded_execution_bundle": str(
            bundle.get("bundle_hash") or ""
        ),
        "clean_oracle_receipt": clean_oracle["receipt_hash"],
        "poison_oracle_receipt_1": poison_oracles[0]["receipt_hash"],
        "poison_oracle_receipt_2": poison_oracles[1]["receipt_hash"],
        "revert_oracle_receipt": revert_oracle["receipt_hash"],
        "waveform_observation": str(
            poison_oracles[0]["result"].get(
                "waveform_observation_hash"
            )
            or ""
        ),
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


def _waveform_observation(
    execution_bundle: Mapping[str, Any],
) -> dict[str, Any]:
    evidence = dict(
        dict(execution_bundle).get("evidence") or {}
    )
    poison_runs = list(evidence.get("poison_runs") or [])
    if len(poison_runs) != 2:
        raise GroundedFailureDescriptorViolation(
            "descriptor requires two poison observations"
        )
    results = []
    for index, run in enumerate(poison_runs, start=1):
        observed = dict(dict(run).get("observed") or {})
        receipt = verify_provider_receipt(
            observed.get("oracle_provider_receipt") or {}
        )
        result = dict(receipt["result"])
        required = {
            "waveform_observation_complete",
            "waveform_observation_hash",
            "first_divergence_signal",
            "first_divergence_cycle",
            "cycle_offset",
            "temporal_relation",
            "assignment_type",
            "cone_depth",
            "mismatch_pattern",
        }
        if (
            not result.get("waveform_observation_complete")
            or required - result.keys()
        ):
            raise GroundedFailureDescriptorViolation(
                f"poison run {index} lacks waveform observation"
            )
        results.append({
            key: result[key] for key in sorted(required)
        })
    if results[0] != results[1]:
        raise GroundedFailureDescriptorViolation(
            "poison waveform observations are not deterministic"
        )
    result = results[0]
    for field in (
        "first_divergence_cycle",
        "cycle_offset",
        "cone_depth",
    ):
        value = result[field]
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
        ):
            raise GroundedFailureDescriptorViolation(
                f"waveform {field} is invalid"
            )
    for field in (
        "first_divergence_signal",
        "temporal_relation",
        "assignment_type",
        "mismatch_pattern",
    ):
        if not isinstance(result[field], str) or not result[field]:
            raise GroundedFailureDescriptorViolation(
                f"waveform {field} is invalid"
            )
    return result


def _depth_bucket(depth: int) -> str:
    if depth == 0:
        return "depth_0"
    if depth == 1:
        return "depth_1"
    if depth <= 3:
        return "depth_2_3"
    return "depth_4_plus"


def _cycle_bucket(cycle: int) -> str:
    if cycle == 0:
        return "cycle_0"
    if cycle == 1:
        return "cycle_1"
    if cycle <= 3:
        return "cycle_2_3"
    if cycle <= 7:
        return "cycle_4_7"
    return "cycle_8_plus"


def _descriptor_from_artifacts(
    execution_bundle: Mapping[str, Any],
    artifacts: Mapping[str, str],
) -> dict[str, Any]:
    waveform = _waveform_observation(execution_bundle)
    sequential = (
        waveform["assignment_type"]
        not in {"combinational", "continuous"}
        or waveform["temporal_relation"]
        not in {"same_cycle", "combinational"}
        or waveform["cycle_offset"] > 0
    )
    return build_failure_descriptor({
        "oracle_stage": "grounded_simulation_and_formal",
        "sequential_context": sequential,
        "temporal_relation": waveform["temporal_relation"],
        "cycle_offset_bucket": waveform["cycle_offset"],
        "affected_roles": ["observable_output"],
        "assignment_type": waveform["assignment_type"],
        "cone_depth_bucket": _depth_bucket(
            waveform["cone_depth"]
        ),
        "mismatch_pattern": waveform["mismatch_pattern"],
        "first_divergence_bucket": _cycle_bucket(
            waveform["first_divergence_cycle"]
        ),
        "first_divergence_signal": waveform[
            "first_divergence_signal"
        ],
        "observable_artifact_hashes": dict(artifacts),
    }).to_dict()


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
    descriptor = _descriptor_from_artifacts(
        execution_bundle, artifacts
    )
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
    expected_descriptor = _descriptor_from_artifacts(
        execution_bundle, expected_artifacts
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
        or verified_descriptor != expected_descriptor
        or verified_descriptor["observable_artifact_hashes"]
        != expected_artifacts
    ):
        raise GroundedFailureDescriptorViolation(
            "failure descriptor is not bound to authority artifacts"
        )
    return deepcopy(verified_descriptor), verified_receipt
