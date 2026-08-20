"""Fail-closed readiness assessment for the first real-model pilot."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
from typing import Any, Mapping

from r3e.protocol.hashing import hash_file, hash_payload


PILOT_SCHEMA = "r3e-grd8-acp7-pilot-config-v1"
SHADOW_SCHEMA = "r3e-shadow-pilot-matrix-v1"
PLACEHOLDER = "__REQUIRED__"


def assess_pilot_readiness(
    raw: Mapping[str, Any],
    *,
    project_root: str | Path,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Report blockers without reading or persisting credential values."""
    config = deepcopy(dict(raw))
    root = Path(project_root).resolve()
    env = environ if environ is not None else os.environ
    blockers: list[str] = []
    if config.get("schema_version") != PILOT_SCHEMA:
        blockers.append("pilot_schema")
    if config.get("phase") != "real_model_pilot":
        blockers.append("pilot_phase")
    authority = config.get("authority")
    required_authority = {
        "macro_round": "r3e-integrated-coevolution-config-v1",
        "red_validity": "grounded_runtime_authority_v1",
        "red_population": "r3e-red-population-schedule-v1",
        "blue_evaluation": "candidate_portfolio_v1",
        "promotion": "atomic_registry_commit",
    }
    if authority != required_authority:
        blockers.append("authority_bundle")
    bindings = config.get("experiment_bindings")
    if not isinstance(bindings, dict):
        blockers.append("experiment_bindings")
        bindings = {}
    for name in (
        "red_model_id",
        "blue_model_id",
        "target_manifest",
        "non_target_manifest",
        "promotion_thresholds",
        "red_provider_config",
        "blue_provider_config",
        "candidate_verifier_config",
    ):
        value = bindings.get(name)
        if value in {None, "", PLACEHOLDER}:
            blockers.append(name)
    seeds = bindings.get("run_seeds")
    if (
        not isinstance(seeds, list)
        or len(seeds) != 3
        or any(not isinstance(seed, int) or isinstance(seed, bool) for seed in seeds)
        or len(set(seeds)) != len(seeds)
    ):
        blockers.append("run_seeds")
    rounds = bindings.get("rounds")
    if (
        isinstance(rounds, bool)
        or not isinstance(rounds, int)
        or rounds not in {2, 3}
    ):
        blockers.append("rounds")
    designs = bindings.get("design_ids")
    if (
        not isinstance(designs, list)
        or len(designs) != 4
        or len(set(map(str, designs))) != 4
    ):
        blockers.append("design_ids")
    families = bindings.get("family_ids")
    if (
        not isinstance(families, list)
        or len(families) != 8
        or len(set(map(str, families))) != 8
    ):
        blockers.append("family_ids")
    for name in ("target_manifest", "non_target_manifest"):
        value = bindings.get(name)
        if value not in {None, "", PLACEHOLDER}:
            path = Path(str(value))
            path = path if path.is_absolute() else root / path
            if not path.is_file():
                blockers.append(f"{name}_missing")
    credential_status: dict[str, bool] = {}
    for side in ("red", "blue"):
        env_name = bindings.get(f"{side}_api_key_env")
        base_env = bindings.get(f"{side}_base_url_env")
        for kind, name in (("api_key", env_name), ("base_url", base_env)):
            key = f"{side}_{kind}_env"
            present = isinstance(name, str) and bool(name) and bool(
                env.get(name)
            )
            credential_status[key] = present
            if not present:
                blockers.append(key)
    executable_requested = config.get("executable") is True
    if config.get("executable") not in {True, False}:
        blockers.append("executable_flag")
    blockers = sorted(set(blockers))
    record = {
        "schema_version": "r3e-grd8-acp7-pilot-readiness-v1",
        "pilot_config_hash": hash_payload(config),
        "executable_requested": executable_requested,
        "credential_environment_present": credential_status,
        "blockers": blockers,
        "ready": executable_requested and not blockers,
        "claim_boundary": (
            "Readiness is configuration and authority conformance only; "
            "it is not a real-model run or empirical result."
        ),
    }
    record["readiness_hash"] = hash_payload(record)
    return record


def assess_shadow_admission_readiness(
    raw: Mapping[str, Any],
    *,
    project_root: str | Path,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Check the bounded 12+1 shadow gate without creating a client.

    This intentionally validates only the single-case/single-seed shadow
    contract. Full-pilot bindings such as non-target manifests and repeated
    seeds belong to :func:`assess_pilot_readiness` and must not block shadow
    admission prematurely.
    """
    config = deepcopy(dict(raw))
    root = Path(project_root).resolve()
    env = environ if environ is not None else os.environ
    blockers: list[str] = []
    if config.get("schema_version") != SHADOW_SCHEMA:
        blockers.append("shadow_schema")
    if config.get("execution_mode") != "shadow_no_promotion":
        blockers.append("shadow_execution_mode")
    model = config.get("model_binding")
    if not isinstance(model, Mapping) or set(model) != {
        "provider_id", "model_id", "model_version"
    } or any(not str(model.get(key) or "") for key in model):
        blockers.append("model_binding")
        model = {}

    def _under_root(value: Any, field: str) -> Path | None:
        if not isinstance(value, str) or not value:
            blockers.append(field)
            return None
        path = (root / value).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            blockers.append(f"{field}_escapes_project")
            return None
        if not path.is_file():
            blockers.append(f"{field}_missing")
            return None
        return path

    target = config.get("target_manifest")
    target_path = None
    if not isinstance(target, Mapping) or set(target) != {"path", "file_hash"}:
        blockers.append("target_manifest")
    else:
        target_path = _under_root(target.get("path"), "target_manifest")
        if target_path is not None and hash_file(target_path) != target.get("file_hash"):
            blockers.append("target_manifest_hash")

    red = config.get("red_shadow")
    if not isinstance(red, Mapping):
        blockers.append("red_shadow")
        red = {}
    red_path = _under_root(red.get("manifest_path"), "red_manifest")
    if red_path is not None and hash_file(red_path) != red.get("manifest_file_hash"):
        blockers.append("red_manifest_hash")
    smoke = config.get("smoke_subset")
    if (
        not isinstance(smoke, Mapping)
        or not isinstance(smoke.get("case_ids"), list)
        or len(smoke["case_ids"]) != 1
        or not isinstance(smoke.get("seeds"), list)
        or len(smoke["seeds"]) != 1
    ):
        blockers.append("single_case_single_seed")
    controls = config.get("controls")
    if (
        not isinstance(controls, Mapping)
        or controls.get("candidate_budget") != 3
        or controls.get("call_budget_per_cell") != 3
        or controls.get("promotion_enabled") is not False
        or controls.get("raam_execution_enabled") is not False
    ):
        blockers.append("blue_shadow_controls")
    if (
        red.get("enabled") is not True
        or red.get("provider_calls_per_round") != 1
        or red.get("promotion_enabled") is not False
        or red.get("memory_qualification_enabled") is not False
    ):
        blockers.append("red_shadow_controls")
    if target_path is not None and isinstance(smoke, Mapping):
        rows = [
            json.loads(line)
            for line in target_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        case_ids = {str(row.get("case_id") or "") for row in rows}
        if smoke.get("case_ids", [None])[0] not in case_ids:
            blockers.append("smoke_case_missing")

    selected = {
        "model": ("OPENAI_MODEL", "DEEPSEEK_MODEL", "LLM_MODEL"),
        "api_key": (
            "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "LLM_API_KEY",
            "R3E_PILOT_API_KEY",
        ),
        "base_url": (
            "OPENAI_BASE_URL", "DEEPSEEK_BASE_URL", "LLM_BASE_URL",
            "R3E_PILOT_BASE_URL",
        ),
    }
    credential_status: dict[str, bool] = {}
    for kind, names in selected.items():
        present = any(bool(env.get(name)) for name in names)
        credential_status[f"{kind}_env"] = present
        if not present:
            blockers.append(f"{kind}_env")
    tool_status = {
        name: bool(shutil.which(name)) for name in ("yosys", "iverilog", "vvp")
    }
    for name, present in tool_status.items():
        if not present:
            blockers.append(f"tool_{name}")
    blockers = sorted(set(blockers))
    record = {
        "schema_version": "r3e-real-provider-shadow-readiness-v1",
        "matrix_hash": config.get("matrix_hash"),
        "expected_provider_calls": 13,
        "credential_environment_present": credential_status,
        "formal_toolchain_present": tool_status,
        "model_binding": {
            key: str(model.get(key) or "")
            for key in ("provider_id", "model_id", "model_version")
        },
        "blockers": blockers,
        "ready": not blockers,
        "claim_boundary": (
            "Shadow readiness is configuration, toolchain, and authority "
            "conformance only; it is not a provider call or empirical result."
        ),
    }
    record["readiness_hash"] = hash_payload(record)
    return record


def _main(argv: list[str] | None = None) -> int:
    """Run a secret-free readiness check without creating a provider client."""
    parser = argparse.ArgumentParser(
        description="Check GRD-8/ACP-7 pilot readiness without provider calls"
    )
    parser.add_argument(
        "--stage", choices=("pilot", "shadow-admission"), default="pilot",
        help="readiness scope to assess",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="pilot configuration JSON",
    )
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args(argv)
    default_config = (
        "configs/evolution/grd8_acp7_pilot_v1.json"
        if args.stage == "pilot"
        else "configs/pilot/shadow_pilot_matrix_v1.json"
    )
    config_path = Path(args.config or default_config)
    if not config_path.is_absolute():
        config_path = Path(args.project_root) / config_path
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if args.stage == "pilot":
        result = assess_pilot_readiness(raw, project_root=args.project_root)
    else:
        result = assess_shadow_admission_readiness(
            raw, project_root=args.project_root
        )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(_main())
