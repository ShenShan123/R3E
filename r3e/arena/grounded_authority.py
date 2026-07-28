"""Runner-owned bridge from Grounded Red execution to arena validity."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import (
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
from r3e.red.grounded.registry import (
    GroundedRegistryBundle,
    load_grounded_registries,
)


GROUNDED_ARENA_AUTHORITY = "grounded_red_execution_v1"
LEGACY_ARENA_AUTHORITY = "legacy_adapter_evidence_v1"
ARENA_VALIDITY_AUTHORITIES = {
    GROUNDED_ARENA_AUTHORITY,
    LEGACY_ARENA_AUTHORITY,
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


def _cross_bind(
    *,
    poison: Mapping[str, Any],
    policy: PolicyState,
    bundle: Mapping[str, Any],
    clean_path: Path,
    poison_path: Path,
) -> None:
    plan = bundle["plan"]
    materialization = bundle["materialization_receipt"]
    decision = bundle["admission_decision"]
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
        or decision.get("authority_mode")
        != "runner_owned_grounded_execution"
    ):
        raise GroundedAuthorityIntegrationViolation(
            "arena poison is not cross-bound to Grounded execution"
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
) -> dict[str, Any]:
    """Execute or resume one exact Grounded Red authority bundle."""
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
    token = hash_payload({
        "poison_payload_hash": poison.get("poison_payload_hash"),
        "plan_hash": plan.get("plan_hash"),
    }).split(":", 1)[1]
    authority_root = Path(round_dir).resolve() / "grounded_authority" / token
    authority_root.mkdir(parents=True, exist_ok=True)

    existing = sorted(
        authority_root.glob(
            "attempt-*/grounded_execution_bundle.json"
        )
    )
    for bundle_path in existing:
        bundle = verify_grounded_execution_bundle(
            read_json(bundle_path),
            policy=policy,
            registries=registries,
        )
        _cross_bind(
            poison=poison,
            policy=policy,
            bundle=bundle,
            clean_path=clean_source,
            poison_path=poison_source,
        )
        return {
            "validity": build_grounded_arena_validity(bundle),
            "grounded_execution_bundle": bundle,
        }

    attempt_number = len(list(authority_root.glob("attempt-*"))) + 1
    workspace = authority_root / f"attempt-{attempt_number:04d}"
    workspace.mkdir(parents=True, exist_ok=False)
    staged_clean = workspace / "inputs" / "clean.v"
    staged_testbench = workspace / "inputs" / "testbench.v"
    atomic_write_text(
        staged_clean, clean_source.read_text(encoding="utf-8")
    )
    atomic_write_text(
        staged_testbench, testbench.read_text(encoding="utf-8")
    )
    allowed_manifest_hash = hash_payload({
        "clean_rtl_hash": hash_file(staged_clean),
        "testbench_hash": hash_file(staged_testbench),
        "plan_hash": plan["plan_hash"],
    })
    bundle = execute_grounded_icarus_admission(
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
    _cross_bind(
        poison=poison,
        policy=policy,
        bundle=bundle,
        clean_path=clean_source,
        poison_path=poison_source,
    )
    return {
        "validity": build_grounded_arena_validity(bundle),
        "grounded_execution_bundle": bundle,
    }
