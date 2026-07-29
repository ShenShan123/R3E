"""Load a lens registry and verify every frozen prompt asset."""
from __future__ import annotations

from pathlib import Path

from r3e.protocol.hashing import hash_file, read_json

from .schema import LensRegistry, PortfolioValidationError


def load_lens_registry(
    registry_path: str | Path,
    *,
    project_root: str | Path,
) -> LensRegistry:
    root = Path(project_root).resolve()
    registry = LensRegistry.from_dict(read_json(registry_path))
    for lens in registry.lenses.values():
        asset = (root / lens.prompt_asset_path).resolve()
        try:
            asset.relative_to(root)
        except ValueError as exc:
            raise PortfolioValidationError(
                "prompt asset escapes the project root"
            ) from exc
        if not asset.is_file():
            raise PortfolioValidationError(
                f"prompt asset is missing: {lens.prompt_asset_path}"
            )
        if hash_file(asset) != lens.prompt_asset_hash:
            raise PortfolioValidationError(
                f"prompt asset hash mismatch: {lens.lens_id}"
            )
    return registry
