"""Immutable, hash-bound manifests and design-disjoint splits."""
from __future__ import annotations

import random
from copy import deepcopy
from pathlib import Path
from typing import Any

from r3e.protocol.hashing import atomic_write_json, hash_payload, read_json, utc_now


MANIFEST_SCHEMA_VERSION = "r3e-frozen-manifest-v1"


class ManifestViolation(RuntimeError):
    """Raised when a frozen manifest changes or leaks a design split."""


def _canonical_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (deepcopy(row) for row in rows),
        key=lambda row: (
            str(row.get("design") or row.get("design_id") or ""),
            str(row.get("case_id") or row.get("poison_id") or ""),
            int(row.get("seed") or 0),
        ),
    )


def make_manifest(
    rows: list[dict[str, Any]],
    *,
    split: str,
    source_manifest_hash: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    canonical = _canonical_rows(rows)
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "split": split,
        "source_residual_manifest_hash": source_manifest_hash,
        "rows": canonical,
        "row_count": len(canonical),
        "metadata": deepcopy(metadata or {}),
        "frozen_at": utc_now(),
    }
    # Timestamps are audit metadata, not manifest membership.
    payload["manifest_hash"] = hash_payload({
        key: value for key, value in payload.items() if key not in {"frozen_at", "manifest_hash"}
    })
    return payload


def verify_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ManifestViolation("manifest schema mismatch")
    rows = manifest.get("rows")
    if not isinstance(rows, list) or manifest.get("row_count") != len(rows):
        raise ManifestViolation("manifest row count mismatch")
    expected = hash_payload({
        key: value for key, value in manifest.items() if key not in {"frozen_at", "manifest_hash"}
    })
    if manifest.get("manifest_hash") != expected:
        raise ManifestViolation("manifest hash mismatch")
    if rows != _canonical_rows(rows):
        raise ManifestViolation("manifest rows are not canonical")
    return manifest


def freeze_manifest(path: str | Path, manifest: dict[str, Any]) -> dict[str, Any]:
    target = Path(path)
    verify_manifest(manifest)
    if target.exists():
        existing = verify_manifest(read_json(target))
        if existing["manifest_hash"] != manifest["manifest_hash"]:
            raise ManifestViolation(f"refusing to overwrite frozen manifest: {target}")
        return existing
    atomic_write_json(target, manifest)
    return verify_manifest(read_json(target))


def grouped_split(
    residual_manifest: dict[str, Any],
    *,
    adaptation_fraction: float = 0.5,
    seed: int = 0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    verify_manifest(residual_manifest)
    if not 0.0 < adaptation_fraction < 1.0:
        raise ManifestViolation("adaptation_fraction must be between zero and one")
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in residual_manifest["rows"]:
        design = str(row.get("design") or row.get("design_id") or "")
        if not design:
            raise ManifestViolation("residual row missing design")
        groups.setdefault(design, []).append(row)
    if len(groups) < 2:
        raise ManifestViolation("design-disjoint split requires at least two designs")
    designs = sorted(groups)
    random.Random(seed).shuffle(designs)
    cut = min(len(designs) - 1, max(1, round(len(designs) * adaptation_fraction)))
    adaptation_designs = set(designs[:cut])
    adaptation_rows = [
        row for design in designs if design in adaptation_designs for row in groups[design]
    ]
    target_rows = [
        row for design in designs if design not in adaptation_designs for row in groups[design]
    ]
    source = residual_manifest["manifest_hash"]
    adaptation = make_manifest(
        adaptation_rows,
        split="adaptation",
        source_manifest_hash=source,
        metadata={"group_key": "design", "seed": seed},
    )
    target = make_manifest(
        target_rows,
        split="target",
        source_manifest_hash=source,
        metadata={"group_key": "design", "seed": seed},
    )
    left = {
        str(row.get("design") or row.get("design_id")) for row in adaptation["rows"]
    }
    right = {str(row.get("design") or row.get("design_id")) for row in target["rows"]}
    if left & right:
        raise ManifestViolation("grouped split leaked designs")
    return adaptation, target
