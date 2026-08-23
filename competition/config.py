"""Single source of truth for the competition runtime configuration.

The competition layer deliberately keeps configuration loading separate from
the research-core policy schemas.  YAML is only the human-facing input; every
service receives the validated immutable view below and does not invent its
own candidate budget, lens list, timeout, or gate names.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

try:
    import yaml
except ImportError as exc:  # pragma: no cover - setup installs PyYAML
    yaml = None
    _YAML_IMPORT_ERROR = exc
else:
    _YAML_IMPORT_ERROR = None


class ConfigError(ValueError):
    """Raised when the competition configuration is incomplete or unsafe."""


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class CompetitionConfig:
    """Validated, read-only configuration passed to all competition services."""

    repo_root: Path
    source_path: Path
    raw: Mapping[str, Any]

    @property
    def version(self) -> str:
        return str(self.raw["competition"]["version"])

    @property
    def mode(self) -> str:
        return str(self.raw["competition"]["mode"])

    @property
    def candidate_budget(self) -> int:
        return int(self.raw["competition"]["candidate_budget"])

    @property
    def run_seed(self) -> int:
        return int(self.raw["competition"]["run_seed"])

    @property
    def lenses(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.raw["repair"]["lenses"])

    @property
    def timeout_seconds(self) -> float:
        return float(self.raw["verification"]["timeout_seconds"])

    @property
    def verification_stages(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.raw["verification"]["stages"])

    @property
    def provider(self) -> Mapping[str, Any]:
        return self.raw["provider"]

    @property
    def paths(self) -> Mapping[str, Any]:
        return self.raw["paths"]

    @property
    def evolution_enabled(self) -> bool:
        return bool(self.raw["evolution"]["enabled"])

    @property
    def memory_enabled(self) -> bool:
        return bool(self.raw["memory"]["enabled"])

    def env_or(self, key: str, default: str) -> str:
        value = str(self.provider.get(key) or "")
        return os.getenv(value, default) if value else default


def _validate(raw: Mapping[str, Any], *, source_path: Path, repo_root: Path) -> CompetitionConfig:
    if not isinstance(raw, Mapping):
        raise ConfigError("competition config must be a YAML object")
    required_sections = {
        "competition", "provider", "repair", "verification", "evolution",
        "memory", "ui", "paths",
    }
    missing = required_sections - set(raw)
    if missing:
        raise ConfigError(f"competition config missing sections: {sorted(missing)}")
    sections = {name: raw[name] for name in required_sections}
    if any(not isinstance(value, Mapping) for value in sections.values()):
        raise ConfigError("all competition config sections must be objects")
    competition = sections["competition"]
    repair = sections["repair"]
    verification = sections["verification"]
    paths = sections["paths"]
    try:
        candidate_budget = int(competition.get("candidate_budget", 0))
    except (TypeError, ValueError) as exc:
        raise ConfigError("competition.candidate_budget must be an integer") from exc
    if isinstance(competition.get("candidate_budget"), bool) or candidate_budget != 3:
        raise ConfigError("M2 requires a fixed three-candidate portfolio")
    lenses = repair.get("lenses")
    if (
        not isinstance(lenses, list)
        or not lenses
        or any(not isinstance(item, str) or not item for item in lenses)
        or len(set(lenses)) != len(lenses)
    ):
        raise ConfigError("repair.lenses must be a non-empty list")
    required_stages = {
        "parse", "scope", "compile", "simulation", "oracle",
        "structural_check", "repeatability",
    }
    stages = verification.get("stages")
    expected_stages = (
        "parse", "scope", "compile", "simulation", "oracle",
        "structural_check", "repeatability",
    )
    if tuple(stages or ()) != expected_stages:
        raise ConfigError(
            "verification.stages must be exactly the ordered M2 gate set: "
            + ", ".join(sorted(required_stages))
        )
    try:
        timeout_seconds = float(verification.get("timeout_seconds", 0))
    except (TypeError, ValueError) as exc:
        raise ConfigError("verification.timeout_seconds must be positive") from exc
    if timeout_seconds <= 0:
        raise ConfigError("verification.timeout_seconds must be positive")
    try:
        run_seed = int(competition.get("run_seed", 0))
    except (TypeError, ValueError) as exc:
        raise ConfigError("competition.run_seed must be an integer") from exc
    if isinstance(competition.get("run_seed", 0), bool):
        raise ConfigError("competition.run_seed must be an integer")
    for key in ("case_root", "frozen_root", "lens_registry", "descriptor_router", "portfolio"):
        value = str(paths.get(key) or "")
        if not value or Path(value).is_absolute() or ".." in Path(value).parts:
            raise ConfigError(f"paths.{key} must be repository-relative")
    normalized = deepcopy(dict(raw))
    normalized["competition"]["candidate_budget"] = 3
    normalized["competition"]["run_seed"] = run_seed
    return CompetitionConfig(repo_root=repo_root, source_path=source_path, raw=_freeze(normalized))


def load_config(repo_root: str | Path, path: str | Path | None = None) -> CompetitionConfig:
    root = Path(repo_root).resolve()
    requested = Path(path) if path is not None else Path("competition/configs/competition.yaml")
    source = (root / requested if not requested.is_absolute() else requested).resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise ConfigError("competition config must be inside the repository") from exc
    if not source.is_file():
        raise ConfigError(f"competition config is missing: {source}")
    if yaml is None:  # pragma: no cover
        raise ConfigError("PyYAML is required to load competition.yaml") from _YAML_IMPORT_ERROR
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot parse competition config: {source}") from exc
    return _validate(raw, source_path=source, repo_root=root)
