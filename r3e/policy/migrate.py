"""Fail-closed migration from legacy skills/registries to Policy Registry V2.

Migration never converts a manual skill or an old promoted artifact into an
active evolution-history node. Legacy skills become a hash-bound frozen B0
asset; the V2 registry starts with B0 as its sole policy.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from r3e.protocol.hashing import (
    atomic_write_json,
    hash_file,
    hash_payload,
    read_json,
    utc_now,
)

from .registry_v2 import initialize_registry
from .schema import PolicyState


class MigrationViolation(RuntimeError):
    """Raised when legacy input cannot be migrated without granting authority."""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise MigrationViolation(f"legacy skills file missing: {path}")
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise MigrationViolation("legacy skills file is empty")
    for index, row in enumerate(rows):
        if not row.get("skill_id") or not isinstance(row.get("action_policy"), dict):
            raise MigrationViolation(f"malformed legacy skill at row {index}")
    return rows


def _legacy_registry_summary(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return []
    payload = read_json(path)
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise MigrationViolation("legacy registry artifacts missing")
    return [
        {
            "artifact_id": str(row.get("artifact_id") or ""),
            "old_status": str(row.get("runtime_status") or row.get("status") or ""),
            "disposition": "excluded_no_v2_promotion_evidence",
        }
        for row in artifacts
    ]


def migrate_legacy(
    *,
    legacy_skills_path: str | Path,
    base_template_path: str | Path,
    base_output_path: str | Path,
    registry_path: str | Path,
    report_path: str | Path,
    ledger_path: str | Path | None = None,
    legacy_registry_path: str | Path | None = None,
) -> dict[str, Any]:
    legacy_skills = Path(legacy_skills_path)
    base_template = Path(base_template_path)
    base_output = Path(base_output_path)
    registry = Path(registry_path)
    report_target = Path(report_path)
    targets = (base_output, registry, report_target)
    existing = [str(path) for path in targets if path.exists()]
    if existing:
        raise MigrationViolation(f"migration refuses to overwrite outputs: {existing}")

    rows = _read_jsonl(legacy_skills)
    template_raw = read_json(base_template)
    # Validate the source template before modifying its frozen asset set.
    PolicyState.from_dict(template_raw)

    frozen_copy = base_output.parent / "legacy" / "manual_skills.json"
    if frozen_copy.exists():
        if hash_file(frozen_copy) != hash_file(legacy_skills):
            raise MigrationViolation("existing frozen manual skill asset differs")
    else:
        frozen_copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_skills, frozen_copy)

    migrated = dict(template_raw)
    assets = dict(migrated.get("frozen_assets") or {})
    asset_key = str(frozen_copy.relative_to(base_output.parent))
    assets[asset_key] = hash_file(frozen_copy)
    migrated["frozen_assets"] = assets
    migrated["base_policy_hash"] = hash_payload({
        "schema_version": migrated["schema_version"],
        "configuration": migrated["configuration"],
        "budgets": migrated["budgets"],
        "frozen_assets": assets,
    })
    migrated["policy_id"] = "B0"
    migrated["parent_policy_id"] = ""
    migrated["parent_policy_hash"] = ""
    migrated["created_round"] = 0
    migrated["created_from_residual_manifest_hash"] = ""
    migrated["status"] = "active"
    migrated["validation_manifest_hash"] = ""
    migrated["promotion_decision_hash"] = ""
    migrated["rollback_policy_id"] = ""
    migrated["rollback_registry_hash"] = ""
    migrated["proposal_operator"] = "legacy_freeze_migration"
    migrated_policy = PolicyState.from_dict(migrated)
    atomic_write_json(base_output, migrated_policy.to_dict())

    created_registry = initialize_registry(
        base_output,
        registry,
        base_path_record=str(base_output),
        ledger_path=ledger_path,
    )
    if set(created_registry["policies"]) != {"B0"}:
        raise MigrationViolation("migration unexpectedly created evolution history")

    legacy_artifacts = _legacy_registry_summary(
        Path(legacy_registry_path) if legacy_registry_path else None
    )
    report = {
        "schema_version": "r3e-policy-migration-v1",
        "source_kind": "legacy",
        "target_kind": "registry-v2",
        "created_at": utc_now(),
        "legacy_skills_file_hash": hash_file(legacy_skills),
        "frozen_asset_path": str(frozen_copy),
        "frozen_asset_hash": hash_file(frozen_copy),
        "manual_skills": [
            {
                "skill_id": str(row["skill_id"]),
                "old_status": str(row.get("status") or ""),
                "disposition": "frozen_base_asset_no_promotion_authority",
            }
            for row in rows
        ],
        "legacy_registry_artifacts": legacy_artifacts,
        "active_policy_id": "B0",
        "active_policy_hash": migrated_policy.policy_hash,
        "registry_hash": created_registry["registry_hash"],
        "automatic_promotions_migrated": 0,
    }
    report["migration_report_hash"] = hash_payload(report)
    atomic_write_json(report_target, report)
    return report


def _main() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy R³E authority to Registry V2")
    parser.add_argument("--from", dest="source_kind", choices=["legacy"], required=True)
    parser.add_argument("--to", dest="target_kind", choices=["registry-v2"], required=True)
    parser.add_argument("--legacy-skills", default="configs/skills.json")
    parser.add_argument(
        "--base-template", default="configs/base_policy/frozen_base_policy_v1.json"
    )
    parser.add_argument(
        "--base-out", default="runtime/migration/frozen_base_policy_migrated.json"
    )
    parser.add_argument(
        "--registry", default="runtime/registry/policy_registry.json"
    )
    parser.add_argument(
        "--report", default="runtime/migration/legacy_to_registry_v2.json"
    )
    parser.add_argument("--ledger", default="runtime/registry/decision_ledger.jsonl")
    parser.add_argument("--legacy-registry")
    args = parser.parse_args()
    report = migrate_legacy(
        legacy_skills_path=args.legacy_skills,
        base_template_path=args.base_template,
        base_output_path=args.base_out,
        registry_path=args.registry,
        report_path=args.report,
        ledger_path=args.ledger,
        legacy_registry_path=args.legacy_registry,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
