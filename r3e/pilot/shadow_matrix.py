"""Frozen schema and authority loader for the GRD-8/ACP-7 shadow matrix."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from r3e.blue.portfolio.schema import CandidatePortfolio
from r3e.protocol.hashing import hash_file, hash_payload, read_json


SHADOW_MATRIX_SCHEMA = "r3e-shadow-pilot-matrix-v1"
SHADOW_MATRIX_MODE = "shadow_no_promotion"
EXPECTED_ARMS = {
    "A": ("homogeneous_best_of_3", "homogeneous"),
    "B": ("fixed_mixed", "fixed_mixed"),
    "C": ("descriptor_routed", "descriptor_routed"),
    "D": ("adaptive", "adaptive"),
}
_MATRIX_FIELDS = {
    "schema_version",
    "matrix_id",
    "execution_mode",
    "model_binding",
    "target_manifest",
    "case_ids",
    "seeds",
    "smoke_subset",
    "arms",
    "controls",
    "red_shadow",
    "matrix_hash",
}
_CONTROL_FIELDS = {
    "candidate_budget",
    "call_budget_per_cell",
    "maximum_tokens_per_cell",
    "maximum_wall_seconds_per_cell",
    "evidence_bundle",
    "patch_scope",
    "verifier_id",
    "oracle_authority",
    "early_stop_mode",
    "promotion_enabled",
    "raam_execution_enabled",
}
_RED_FIELDS = {
    "enabled",
    "manifest_path",
    "manifest_file_hash",
    "case_id",
    "provider_calls_per_round",
    "maximum_input_tokens",
    "maximum_output_tokens",
    "maximum_wall_time_ms",
    "promotion_enabled",
    "memory_qualification_enabled",
}


class ShadowPilotMatrixViolation(RuntimeError):
    """Raised when a pilot matrix is mutable, incomplete, or out of scope."""


def _under(root: Path, raw: str, *, field: str) -> Path:
    path = (root / raw).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ShadowPilotMatrixViolation(
            f"{field} escapes project root"
        ) from exc
    if not path.is_file():
        raise ShadowPilotMatrixViolation(f"{field} is missing")
    return path


def _non_empty_strings(value: Any, field: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise ShadowPilotMatrixViolation(
            f"{field} must be non-empty strings"
        )
    if len(value) != len(set(value)):
        raise ShadowPilotMatrixViolation(f"{field} contains duplicates")
    return tuple(value)


def _seeds(value: Any, field: str) -> tuple[int, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
        or any(item < 0 for item in value)
    ):
        raise ShadowPilotMatrixViolation(
            f"{field} must be non-negative integers"
        )
    if len(value) != len(set(value)):
        raise ShadowPilotMatrixViolation(f"{field} contains duplicates")
    return tuple(value)


@dataclass(frozen=True)
class ShadowPilotMatrix:
    source_path: Path
    matrix_id: str
    matrix_hash: str
    model_binding: dict[str, str]
    target_manifest_path: Path
    red_target_manifest_path: Path
    red_case_id: str
    case_ids: tuple[str, ...]
    seeds: tuple[int, ...]
    smoke_case_ids: tuple[str, ...]
    smoke_seeds: tuple[int, ...]
    arms: tuple[dict[str, str], ...]
    controls: dict[str, Any]
    red_shadow: dict[str, Any]
    portfolios: dict[str, CandidatePortfolio]
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self.raw)

    def arm(self, arm_id: str) -> dict[str, str]:
        matches = [row for row in self.arms if row["arm_id"] == arm_id]
        if len(matches) != 1:
            raise ShadowPilotMatrixViolation(
                f"unknown shadow matrix arm: {arm_id}"
            )
        return deepcopy(matches[0])


def load_shadow_pilot_matrix(
    path: str | Path,
    *,
    project_root: str | Path,
) -> ShadowPilotMatrix:
    root = Path(project_root).resolve()
    source = Path(path)
    if not source.is_absolute():
        source = root / source
    source = source.resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise ShadowPilotMatrixViolation(
            "matrix path escapes project root"
        ) from exc
    payload = read_json(source)
    if not isinstance(payload, Mapping) or set(payload) != _MATRIX_FIELDS:
        raise ShadowPilotMatrixViolation("shadow matrix fields mismatch")
    raw = deepcopy(dict(payload))
    if raw["schema_version"] != SHADOW_MATRIX_SCHEMA:
        raise ShadowPilotMatrixViolation("shadow matrix schema mismatch")
    if raw["execution_mode"] != SHADOW_MATRIX_MODE:
        raise ShadowPilotMatrixViolation(
            "shadow matrix cannot enable promotion execution"
        )
    expected_hash = hash_payload({
        key: value for key, value in raw.items() if key != "matrix_hash"
    })
    if raw["matrix_hash"] != expected_hash:
        raise ShadowPilotMatrixViolation("shadow matrix hash mismatch")
    matrix_id = str(raw["matrix_id"] or "")
    if not matrix_id:
        raise ShadowPilotMatrixViolation("matrix_id must be non-empty")

    model = raw["model_binding"]
    if not isinstance(model, Mapping) or set(model) != {
        "provider_id",
        "model_id",
        "model_version",
    }:
        raise ShadowPilotMatrixViolation("model binding fields mismatch")
    model_binding = {
        key: str(model[key] or "")
        for key in ("provider_id", "model_id", "model_version")
    }
    if not all(model_binding.values()):
        raise ShadowPilotMatrixViolation("model binding is incomplete")

    manifest = raw["target_manifest"]
    if not isinstance(manifest, Mapping) or set(manifest) != {
        "path",
        "file_hash",
    }:
        raise ShadowPilotMatrixViolation("target manifest fields mismatch")
    manifest_path = _under(
        root, str(manifest["path"]), field="target manifest"
    )
    if hash_file(manifest_path) != manifest["file_hash"]:
        raise ShadowPilotMatrixViolation("target manifest hash mismatch")
    case_ids = _non_empty_strings(raw["case_ids"], "case_ids")
    seeds = _seeds(raw["seeds"], "seeds")
    if len(case_ids) != 4 or len(seeds) != 3:
        raise ShadowPilotMatrixViolation(
            "V1 matrix requires four cases and three seeds"
        )
    manifest_rows = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_case = {str(row.get("case_id") or ""): row for row in manifest_rows}
    if any(
        case_id not in by_case or by_case[case_id].get("eligible") is not True
        for case_id in case_ids
    ):
        raise ShadowPilotMatrixViolation(
            "matrix case is absent or ineligible in target manifest"
        )
    project_dirs = {
        str((by_case[case_id].get("metadata") or {}).get("project_dir") or "")
        for case_id in case_ids
    }
    if "" in project_dirs or len(project_dirs) != len(case_ids):
        raise ShadowPilotMatrixViolation(
            "matrix cases must cover four distinct designs"
        )

    smoke = raw["smoke_subset"]
    if not isinstance(smoke, Mapping) or set(smoke) != {
        "case_ids",
        "seeds",
    }:
        raise ShadowPilotMatrixViolation("smoke subset fields mismatch")
    smoke_cases = _non_empty_strings(smoke["case_ids"], "smoke case_ids")
    smoke_seeds = _seeds(smoke["seeds"], "smoke seeds")
    if not set(smoke_cases) <= set(case_ids) or not set(smoke_seeds) <= set(seeds):
        raise ShadowPilotMatrixViolation(
            "smoke subset exceeds frozen matrix"
        )

    controls = raw["controls"]
    if not isinstance(controls, Mapping) or set(controls) != _CONTROL_FIELDS:
        raise ShadowPilotMatrixViolation("matrix controls fields mismatch")
    controls = deepcopy(dict(controls))
    expected_controls = {
        "candidate_budget": 3,
        "call_budget_per_cell": 3,
        "evidence_bundle": "grounded_failure_descriptor_v1",
        "patch_scope": "local_block",
        "verifier_id": "runner-owned-public-oracle-gate",
        "oracle_authority": "public_manifest_differential_oracle",
        "early_stop_mode": "all_candidates",
        "promotion_enabled": False,
        "raam_execution_enabled": False,
    }
    if any(controls.get(key) != value for key, value in expected_controls.items()):
        raise ShadowPilotMatrixViolation(
            "matrix controls exceed shadow authority"
        )
    for key in ("maximum_tokens_per_cell", "maximum_wall_seconds_per_cell"):
        value = controls[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ShadowPilotMatrixViolation(f"{key} must be positive")

    red_shadow = raw["red_shadow"]
    if not isinstance(red_shadow, Mapping) or set(red_shadow) != _RED_FIELDS:
        raise ShadowPilotMatrixViolation("red shadow fields mismatch")
    red_shadow = deepcopy(dict(red_shadow))
    if (
        red_shadow["enabled"] is not True
        or red_shadow["provider_calls_per_round"] != 1
        or red_shadow["promotion_enabled"] is not False
        or red_shadow["memory_qualification_enabled"] is not False
    ):
        raise ShadowPilotMatrixViolation(
            "red shadow authority is not bounded"
        )
    red_manifest_path = _under(
        root,
        str(red_shadow["manifest_path"]),
        field="red shadow manifest",
    )
    if hash_file(red_manifest_path) != str(
        red_shadow["manifest_file_hash"]
    ):
        raise ShadowPilotMatrixViolation(
            "red shadow manifest hash mismatch"
        )
    red_case_id = str(red_shadow["case_id"] or "")
    if not red_case_id:
        raise ShadowPilotMatrixViolation(
            "red shadow case_id must be non-empty"
        )
    red_rows = [
        json.loads(line)
        for line in red_manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    red_matches = [
        row for row in red_rows
        if row.get("case_id") == red_case_id
        and row.get("eligible") is True
    ]
    if len(red_matches) != 1 or not all(
        red_matches[0].get(field)
        for field in (
            "grounded_testbench",
            "grounded_formal_property",
            "grounded_formal_top_module",
            "grounded_formal_depth",
        )
    ):
        raise ShadowPilotMatrixViolation(
            "red shadow manifest must contain one eligible formal case"
        )
    for key in (
        "maximum_input_tokens",
        "maximum_output_tokens",
        "maximum_wall_time_ms",
    ):
        value = red_shadow[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ShadowPilotMatrixViolation(f"red {key} must be positive")

    arms_raw = raw["arms"]
    if not isinstance(arms_raw, list) or len(arms_raw) != 4:
        raise ShadowPilotMatrixViolation("matrix requires four arms")
    arms: list[dict[str, str]] = []
    portfolios: dict[str, CandidatePortfolio] = {}
    for row in arms_raw:
        if not isinstance(row, Mapping) or set(row) != {
            "arm_id",
            "name",
            "portfolio_path",
            "portfolio_file_hash",
        }:
            raise ShadowPilotMatrixViolation("matrix arm fields mismatch")
        arm = {key: str(row[key]) for key in row}
        arm_id = arm["arm_id"]
        if arm_id not in EXPECTED_ARMS:
            raise ShadowPilotMatrixViolation("unknown matrix arm")
        expected_name, expected_mode = EXPECTED_ARMS[arm_id]
        if arm["name"] != expected_name:
            raise ShadowPilotMatrixViolation("matrix arm name mismatch")
        portfolio_path = _under(
            root, arm["portfolio_path"], field=f"arm {arm_id} portfolio"
        )
        if hash_file(portfolio_path) != arm["portfolio_file_hash"]:
            raise ShadowPilotMatrixViolation(
                f"arm {arm_id} portfolio hash mismatch"
            )
        portfolio = CandidatePortfolio.from_dict(read_json(portfolio_path))
        if (
            portfolio.mode != expected_mode
            or portfolio.candidate_budget != controls["candidate_budget"]
            or portfolio.early_stop_mode != controls["early_stop_mode"]
        ):
            raise ShadowPilotMatrixViolation(
                f"arm {arm_id} portfolio authority mismatch"
            )
        arms.append(arm)
        portfolios[arm_id] = portfolio
    if [row["arm_id"] for row in arms] != list(EXPECTED_ARMS):
        raise ShadowPilotMatrixViolation("matrix arm order must be A/B/C/D")

    return ShadowPilotMatrix(
        source_path=source,
        matrix_id=matrix_id,
        matrix_hash=raw["matrix_hash"],
        model_binding=model_binding,
        target_manifest_path=manifest_path,
        red_target_manifest_path=red_manifest_path,
        red_case_id=red_case_id,
        case_ids=case_ids,
        seeds=seeds,
        smoke_case_ids=smoke_cases,
        smoke_seeds=smoke_seeds,
        arms=tuple(arms),
        controls=controls,
        red_shadow=red_shadow,
        portfolios=portfolios,
        raw=raw,
    )
