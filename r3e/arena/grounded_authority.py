"""Runner-owned Grounded Red, formal, and RAAM authority bridge."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from r3e.grounded.failure_descriptor import (
    build_grounded_failure_descriptor,
    verify_grounded_failure_descriptor,
)
from r3e.grounded.yosys_formal import (
    YosysFormalProvider,
    formal_triplet_from_assessment,
    verify_formal_proof_triplet,
)
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import (
    atomic_write_json,
    atomic_write_text,
    hash_file,
    hash_payload,
    read_json,
)
from r3e.red.grounded.arena_validity import (
    build_grounded_arena_validity,
)
from r3e.red.grounded.execution import (
    execute_grounded_icarus_admission,
    verify_grounded_execution_bundle,
)
from r3e.red.grounded.formal_rejection import (
    build_formal_rejection,
    build_formal_rejection_validity,
    verify_formal_rejection,
)
from r3e.red.grounded.registry import (
    GroundedRegistryBundle,
    load_grounded_registries,
)


GROUNDED_ARENA_AUTHORITY = "grounded_runtime_authority_v1"
GROUNDED_ARENA_COMPAT_AUTHORITY = "grounded_red_execution_v1"
GROUNDED_ARENA_AUTHORITIES = {
    GROUNDED_ARENA_AUTHORITY,
    GROUNDED_ARENA_COMPAT_AUTHORITY,
}
LEGACY_ARENA_AUTHORITY = "legacy_adapter_evidence_v1"
ARENA_VALIDITY_AUTHORITIES = {
    *GROUNDED_ARENA_AUTHORITIES,
    LEGACY_ARENA_AUTHORITY,
}
ARENA_AUTHORITY_SCHEMA_VERSION = "r3e-arena-grounded-authority-v2"
_AUTHORITY_FIELDS = {
    "schema_version",
    "execution_bundle",
    "formal_proof_triplet",
    "failure_descriptor",
    "failure_descriptor_receipt",
    "authority_hash",
}


class GroundedAuthorityIntegrationViolation(RuntimeError):
    """Raised when an arena poison cannot obtain Grounded authority."""


def load_arena_grounded_registries(
    project_root: str | Path,
    config: Mapping[str, Any],
) -> GroundedRegistryBundle:
    root = Path(project_root)
    return load_grounded_registries(
        family_registry=root / config.get(
            "grounded_family_registry",
            "configs/red/grounded_family_registry_v1.json",
        ),
        operator_registry=root / config.get(
            "grounded_operator_registry",
            "configs/red/grounded_operator_registry_v1.json",
        ),
        effect_registry=root / config.get(
            "grounded_effect_registry",
            "configs/red/grounded_effect_registry_v1.json",
        ),
    )


def _under_root(path: str | Path, root: Path, *, label: str) -> Path:
    value = Path(path).resolve()
    try:
        value.relative_to(root)
    except ValueError as exc:
        raise GroundedAuthorityIntegrationViolation(
            f"{label} escapes the project root"
        ) from exc
    if not value.is_file():
        raise GroundedAuthorityIntegrationViolation(
            f"{label} is missing"
        )
    return value


def verify_arena_grounded_authority(
    authority_bundle: Mapping[str, Any],
    *,
    policy: PolicyState,
    registries: GroundedRegistryBundle,
) -> dict[str, Any]:
    payload = deepcopy(dict(authority_bundle))
    if set(payload) != _AUTHORITY_FIELDS:
        raise GroundedAuthorityIntegrationViolation(
            "arena Grounded authority fields mismatch"
        )
    if payload["schema_version"] != ARENA_AUTHORITY_SCHEMA_VERSION:
        raise GroundedAuthorityIntegrationViolation(
            "arena Grounded authority schema mismatch"
        )
    if payload["authority_hash"] != hash_payload({
        key: value for key, value in payload.items()
        if key != "authority_hash"
    }):
        raise GroundedAuthorityIntegrationViolation(
            "arena Grounded authority hash mismatch"
        )
    execution = verify_grounded_execution_bundle(
        payload["execution_bundle"],
        policy=policy,
        registries=registries,
    )
    formal = verify_formal_proof_triplet(
        payload["formal_proof_triplet"]
    )
    descriptor, descriptor_receipt = (
        verify_grounded_failure_descriptor(
            payload["failure_descriptor"],
            payload["failure_descriptor_receipt"],
            execution_bundle=execution,
            formal_proof_triplet=formal,
        )
    )
    materialization = execution["materialization_receipt"]
    if (
        formal["clean"]["rtl_hash"]
        != materialization["clean_rtl_hash"]
        or formal["poison"]["rtl_hash"]
        != materialization["poison_rtl_hash"]
        or formal["revert"]["rtl_hash"]
        != materialization["clean_rtl_hash"]
    ):
        raise GroundedAuthorityIntegrationViolation(
            "formal proof triplet is not bound to materialized RTL"
        )
    return {
        **payload,
        "execution_bundle": execution,
        "formal_proof_triplet": formal,
        "failure_descriptor": descriptor,
        "failure_descriptor_receipt": descriptor_receipt,
    }


def _cross_bind(
    *,
    poison: Mapping[str, Any],
    policy: PolicyState,
    authority_bundle: Mapping[str, Any],
    clean_path: Path,
    poison_path: Path,
    formal_property_path: Path,
) -> None:
    execution = authority_bundle["execution_bundle"]
    formal = authority_bundle["formal_proof_triplet"]
    plan = execution["plan"]
    materialization = execution["materialization_receipt"]
    decision = execution["admission_decision"]
    if (
        poison.get("grounded_plan_hash") != plan["plan_hash"]
        or (poison.get("grounded_mutation_plan") or {}).get("plan_hash")
        != plan["plan_hash"]
        or poison.get("poison_id") != plan["plan_id"]
        or poison.get("challenged_policy_hash") != policy.policy_hash
        or plan["challenged_policy_instance_hash"]
        != policy.policy_instance_hash
        or plan["challenged_effective_policy_hash"]
        != policy.effective_policy_hash
        or materialization["clean_rtl_hash"] != hash_file(clean_path)
        or materialization["poison_rtl_hash"] != hash_file(poison_path)
        or formal["property_hash"] != hash_file(formal_property_path)
        or formal["clean"]["top_module"]
        != poison.get("grounded_formal_top_module")
        or formal["clean"]["depth"]
        != poison.get("grounded_formal_depth")
        or decision.get("authority_mode")
        != "runner_owned_grounded_execution"
    ):
        raise GroundedAuthorityIntegrationViolation(
            "arena poison is not cross-bound to Grounded authority"
        )


def _cross_bind_rejection(
    *,
    poison: Mapping[str, Any],
    policy: PolicyState,
    rejection: Mapping[str, Any],
    clean_path: Path,
    poison_path: Path,
    formal_property_path: Path,
) -> None:
    execution = rejection["execution_bundle"]
    assessment = rejection["formal_proof_assessment"]
    materialization = execution["materialization_receipt"]
    if (
        rejection["poison_payload"] != dict(poison)
        or poison.get("challenged_policy_hash") != policy.policy_hash
        or materialization["clean_rtl_hash"] != hash_file(clean_path)
        or materialization["poison_rtl_hash"] != hash_file(poison_path)
        or assessment["property_hash"] != hash_file(
            formal_property_path
        )
    ):
        raise GroundedAuthorityIntegrationViolation(
            "arena poison is not cross-bound to formal rejection"
        )


def _build_authority(
    *,
    execution_bundle: Mapping[str, Any],
    formal_proof_triplet: Mapping[str, Any],
    policy: PolicyState,
    registries: GroundedRegistryBundle,
) -> dict[str, Any]:
    execution = verify_grounded_execution_bundle(
        execution_bundle,
        policy=policy,
        registries=registries,
    )
    formal = verify_formal_proof_triplet(formal_proof_triplet)
    descriptor, descriptor_receipt = (
        build_grounded_failure_descriptor(
            execution_bundle=execution,
            formal_proof_triplet=formal,
        )
    )
    payload = {
        "schema_version": ARENA_AUTHORITY_SCHEMA_VERSION,
        "execution_bundle": execution,
        "formal_proof_triplet": formal,
        "failure_descriptor": descriptor,
        "failure_descriptor_receipt": descriptor_receipt,
    }
    payload["authority_hash"] = hash_payload(payload)
    return verify_arena_grounded_authority(
        payload,
        policy=policy,
        registries=registries,
    )


def execute_grounded_arena_validity(
    *,
    poison: Mapping[str, Any],
    policy: PolicyState,
    registries: GroundedRegistryBundle,
    project_root: str | Path,
    round_dir: str | Path,
    run_context_hash: str,
    timeout_seconds: float = 10.0,
    formal_timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Execute or resume exact Icarus, Yosys, and descriptor authority."""
    root = Path(project_root).resolve()
    clean_source = _under_root(
        str(poison.get("golden_rtl") or ""),
        root,
        label="clean RTL",
    )
    poison_source = _under_root(
        str(poison.get("buggy_rtl") or ""),
        root,
        label="poison RTL",
    )
    testbench = _under_root(
        str(poison.get("grounded_testbench") or ""),
        root,
        label="grounded testbench",
    )
    formal_property = _under_root(
        str(poison.get("grounded_formal_property") or ""),
        root,
        label="grounded formal property",
    )
    plan = poison.get("grounded_mutation_plan")
    if not isinstance(plan, Mapping):
        raise GroundedAuthorityIntegrationViolation(
            "arena poison lacks a Grounded MutationPlan"
        )
    if poison.get("grounded_plan_hash") != plan.get("plan_hash"):
        raise GroundedAuthorityIntegrationViolation(
            "arena poison Grounded plan hash mismatch"
        )
    top_module = str(poison.get("grounded_top_module") or "")
    formal_top_module = str(
        poison.get("grounded_formal_top_module") or ""
    )
    formal_depth = poison.get("grounded_formal_depth")
    if (
        not isinstance(formal_depth, int)
        or isinstance(formal_depth, bool)
        or not 1 <= formal_depth <= 1024
    ):
        raise GroundedAuthorityIntegrationViolation(
            "grounded formal depth must be between 1 and 1024"
        )
    token = hash_payload({
        "poison_payload_hash": poison.get("poison_payload_hash"),
        "plan_hash": plan.get("plan_hash"),
    }).split(":", 1)[1]
    authority_root = (
        Path(round_dir).resolve() / "grounded_authority" / token
    )
    authority_root.mkdir(parents=True, exist_ok=True)

    existing = sorted(
        authority_root.glob(
            "attempt-*/arena_grounded_authority.json"
        )
    )
    for authority_path in existing:
        authority = verify_arena_grounded_authority(
            read_json(authority_path),
            policy=policy,
            registries=registries,
        )
        _cross_bind(
            poison=poison,
            policy=policy,
            authority_bundle=authority,
            clean_path=clean_source,
            poison_path=poison_source,
            formal_property_path=formal_property,
        )
        return {
            "validity": build_grounded_arena_validity(authority),
            "grounded_authority_bundle": authority,
        }
    rejected = sorted(
        authority_root.glob("attempt-*/formal_rejection.json")
    )
    for rejection_path in rejected:
        rejection = verify_formal_rejection(
            read_json(rejection_path),
            policy=policy,
            registries=registries,
        )
        _cross_bind_rejection(
            poison=poison,
            policy=policy,
            rejection=rejection,
            clean_path=clean_source,
            poison_path=poison_source,
            formal_property_path=formal_property,
        )
        return {
            "validity": build_formal_rejection_validity(
                rejection
            ),
            "formal_rejection": rejection,
        }

    attempt_number = len(list(authority_root.glob("attempt-*"))) + 1
    workspace = authority_root / f"attempt-{attempt_number:04d}"
    workspace.mkdir(parents=True, exist_ok=False)
    staged_clean = workspace / "inputs" / "clean.v"
    staged_testbench = workspace / "inputs" / "testbench.v"
    staged_property = workspace / "inputs" / "formal_property.v"
    atomic_write_text(
        staged_clean, clean_source.read_text(encoding="utf-8")
    )
    atomic_write_text(
        staged_testbench, testbench.read_text(encoding="utf-8")
    )
    atomic_write_text(
        staged_property, formal_property.read_text(encoding="utf-8")
    )
    allowed_manifest_hash = hash_payload({
        "clean_rtl_hash": hash_file(staged_clean),
        "testbench_hash": hash_file(staged_testbench),
        "formal_property_hash": hash_file(staged_property),
        "plan_hash": plan["plan_hash"],
    })
    execution = execute_grounded_icarus_admission(
        plan=plan,
        policy=policy,
        registries=registries,
        clean_rtl_path=staged_clean,
        testbench_path=staged_testbench,
        top_module=top_module,
        workspace=workspace,
        run_context_hash=run_context_hash,
        frozen_clean_rtl_hash=hash_file(staged_clean),
        frozen_testbench_hash=hash_file(staged_testbench),
        allowed_file_manifest_hash=allowed_manifest_hash,
        timeout_seconds=timeout_seconds,
    )
    formal_provider = YosysFormalProvider(
        workspace=workspace,
        run_context_hash=run_context_hash,
        timeout_seconds=formal_timeout_seconds,
    )
    assessment = formal_provider.execute_proof_assessment(
        receipt_prefix=str(plan["plan_id"]),
        clean_rtl_path=staged_clean,
        poison_rtl_path=workspace / "materialized/poison.v",
        revert_rtl_path=workspace / "materialized/reverted.v",
        property_path=staged_property,
        top_module=formal_top_module,
        depth=formal_depth,
        frozen_clean_rtl_hash=hash_file(staged_clean),
        frozen_poison_rtl_hash=execution[
            "materialization_receipt"
        ]["poison_rtl_hash"],
        frozen_revert_rtl_hash=hash_file(staged_clean),
        frozen_property_hash=hash_file(staged_property),
    )
    atomic_write_json(
        workspace / "formal_proof_assessment.json", assessment
    )
    if not assessment["proof_satisfied"]:
        rejection = build_formal_rejection(
            round_id=Path(round_dir).resolve().name,
            poison=poison,
            execution_bundle=execution,
            formal_proof_assessment=assessment,
            policy=policy,
            registries=registries,
        )
        _cross_bind_rejection(
            poison=poison,
            policy=policy,
            rejection=rejection,
            clean_path=clean_source,
            poison_path=poison_source,
            formal_property_path=formal_property,
        )
        atomic_write_json(
            workspace / "formal_rejection.json", rejection
        )
        return {
            "validity": build_formal_rejection_validity(
                rejection
            ),
            "formal_rejection": rejection,
        }
    formal = formal_triplet_from_assessment(assessment)
    authority = _build_authority(
        execution_bundle=execution,
        formal_proof_triplet=formal,
        policy=policy,
        registries=registries,
    )
    _cross_bind(
        poison=poison,
        policy=policy,
        authority_bundle=authority,
        clean_path=clean_source,
        poison_path=poison_source,
        formal_property_path=formal_property,
    )
    atomic_write_json(
        workspace / "arena_grounded_authority.json", authority
    )
    return {
        "validity": build_grounded_arena_validity(authority),
        "grounded_authority_bundle": authority,
    }
