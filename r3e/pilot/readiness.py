"""Fail-closed readiness assessment for the first real-model pilot."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any, Mapping

from r3e.protocol.hashing import hash_payload


PILOT_SCHEMA = "r3e-grd8-acp7-pilot-config-v1"
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


def _main(argv: list[str] | None = None) -> int:
    """Run a secret-free readiness check without creating a provider client."""
    parser = argparse.ArgumentParser(
        description="Check GRD-8/ACP-7 pilot readiness without provider calls"
    )
    parser.add_argument(
        "--config",
        default="configs/evolution/grd8_acp7_pilot_v1.json",
        help="pilot configuration JSON",
    )
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args(argv)
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path(args.project_root) / config_path
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    result = assess_pilot_readiness(raw, project_root=args.project_root)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(_main())
