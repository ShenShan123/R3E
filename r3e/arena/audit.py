"""Offline reconstruction of a completed whole-policy evolution round."""
from __future__ import annotations

import argparse
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from r3e.arena.manifests import verify_manifest
from r3e.memory.episode_builder import episode_from_challenge
from r3e.memory.episode_store import EpisodeStore
from r3e.policy.promotion import (
    decide_policy_promotion,
    select_single_promotable_child,
)
from r3e.policy.registry_v2 import get_active_policy, validate_registry
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import (
    atomic_write_json,
    canonical_json,
    hash_payload,
    read_json,
    utc_now,
)
from r3e.protocol.ledger import read_ledger, writer_lock
from r3e.protocol.provenance import verify_run_context
from r3e.red.feedback_packet import FORBIDDEN_INPUT_KEYS
from r3e.red.feedback_packet import verify_red_search_context
from r3e.red.learnability import verify_learnability_result
from r3e.red.selection import select_residual_elites
from r3e.red.poison_payload import verify_poison_payload
from r3e.red.validity import validity_gate
from r3e.red.grounded.arena_validity import (
    ARENA_GROUNDED_AUTHORITY,
    verify_grounded_arena_validity,
    verify_legacy_grounded_arena_validity,
)
from r3e.red.grounded.execution import (
    verify_grounded_execution_bundle,
)
from r3e.red.grounded.formal_rejection import (
    load_formal_rejection_archive,
    verify_formal_rejection,
    verify_formal_rejection_validity,
)
from .grounded_authority import (
    GROUNDED_ARENA_COMPAT_AUTHORITY,
    GROUNDED_ARENA_AUTHORITIES,
    load_arena_grounded_registries,
    verify_arena_grounded_authority,
)


ROUND_AUDIT_SCHEMA_VERSION = "r3e-round-audit-v1"


class RoundAuditViolation(RuntimeError):
    """Raised when a completed round cannot be reconstructed exactly."""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise RoundAuditViolation(f"required round artifact is missing: {path.name}")
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _manifest(path: Path) -> dict[str, Any]:
    try:
        return verify_manifest(read_json(path))
    except Exception as exc:
        raise RoundAuditViolation(f"invalid manifest {path.name}: {exc}") from exc


def reconstruct_round(round_dir: str | Path) -> dict[str, Any]:
    root = Path(round_dir)
    project_root = root.parents[2]
    context = verify_run_context(read_json(root / "toolchain.json"))
    config = read_json(root / "round_config.json")
    if context["round_config_hash"] != hash_payload(config):
        raise RoundAuditViolation("round config does not match frozen run context")
    parent = PolicyState.from_dict(read_json(root / "active_parent.json"))
    if parent.policy_hash != context["active_policy_hash"]:
        raise RoundAuditViolation("active parent does not match frozen run context")
    validity_authority = str(
        config.get(
            "validity_authority",
            "legacy_adapter_evidence_v1",
        )
    )
    validity_rows = _read_jsonl(root / "validity_results.jsonl")
    formal_rejections_path = root / "formal_rejections.jsonl"
    formal_rejections = (
        _read_jsonl(formal_rejections_path)
        if formal_rejections_path.is_file()
        else []
    )
    if validity_authority in GROUNDED_ARENA_AUTHORITIES:
        registries = load_arena_grounded_registries(
            project_root, config
        )
        reconstructed_rejections = []
        for row in validity_rows:
            try:
                verify_poison_payload(row)
                if row.get("formal_rejection"):
                    rejection = verify_formal_rejection(
                        row["formal_rejection"],
                        policy=parent,
                        registries=registries,
                    )
                    if rejection["poison_payload"] != {
                        key: value for key, value in row.items()
                        if key not in {
                            "validity",
                            "formal_rejection",
                        }
                    }:
                        raise RoundAuditViolation(
                            "formal rejection poison payload mismatch"
                        )
                    validity = verify_formal_rejection_validity(
                        row.get("validity") or {},
                        rejection,
                    )
                    bundle = rejection["execution_bundle"]
                    reconstructed_rejections.append(rejection)
                elif row.get("grounded_authority_bundle"):
                    authority = verify_arena_grounded_authority(
                        row["grounded_authority_bundle"],
                        policy=parent,
                        registries=registries,
                    )
                    bundle = authority["execution_bundle"]
                    validity = verify_grounded_arena_validity(
                        row.get("validity") or {},
                        authority_bundle=authority,
                    )
                elif (
                    validity_authority
                    == GROUNDED_ARENA_COMPAT_AUTHORITY
                ):
                    bundle = verify_grounded_execution_bundle(
                        row.get("grounded_execution_bundle") or {},
                        policy=parent,
                        registries=registries,
                    )
                    validity = (
                        verify_legacy_grounded_arena_validity(
                            row.get("validity") or {},
                            execution_bundle=bundle,
                        )
                    )
                else:
                    raise RoundAuditViolation(
                        "Grounded Runtime authority bundle is missing"
                    )
            except Exception as exc:
                raise RoundAuditViolation(
                    "Grounded validity row cannot be reconstructed"
                ) from exc
            if (
                row.get("grounded_plan_hash")
                != bundle["plan"]["plan_hash"]
                or row.get("poison_id") != bundle["plan"]["plan_id"]
            ):
                raise RoundAuditViolation(
                    "Grounded validity row is not cross-bound"
                )
            if (
                row.get("formal_rejection") is None
                and validity["evidence"]["authority_mode"]
                != ARENA_GROUNDED_AUTHORITY
            ):
                raise RoundAuditViolation(
                    "Grounded validity authority mode mismatch"
                )
        if reconstructed_rejections != formal_rejections:
            raise RoundAuditViolation(
                "round formal rejection ledger mismatch"
            )
        rejected_archive = load_formal_rejection_archive(
            project_root
            / config.get(
                "red_rejected_archive",
                "runtime/archives/red_rejected_archive.jsonl",
            ),
            registries=registries,
        )
        archived_hashes = {
            row["rejection_hash"] for row in rejected_archive
        }
        if any(
            row["rejection_hash"] not in archived_hashes
            for row in formal_rejections
        ):
            raise RoundAuditViolation(
                "formal rejection is missing from rejected archive"
            )
    else:
        for row in validity_rows:
            verify_poison_payload(row)
            rebuilt = validity_gate(row)
            expected = {
                "proven_valid": rebuilt.proven_valid,
                "checks": rebuilt.checks,
                "rejection_reasons": rebuilt.rejection_reasons,
                "evidence": rebuilt.evidence,
                "result_hash": rebuilt.result_hash,
            }
            if row.get("validity") != expected:
                raise RoundAuditViolation(
                    "legacy validity result cannot be reconstructed"
                )
    challenge_rows = _read_jsonl(
        root / "blue_challenge_results.jsonl"
    )
    rejected_poison_ids = {
        row["poison_payload"]["poison_id"]
        for row in formal_rejections
    }
    if rejected_poison_ids.intersection({
        str(row.get("poison_id") or "") for row in challenge_rows
    }):
        raise RoundAuditViolation(
            "formal-rejected poison reached blue challenge"
        )
    try:
        expected_episodes = [
            episode_from_challenge(
                row,
                policy=parent,
                round_id=context["round_id"],
            )
            for row in challenge_rows
        ]
    except Exception as exc:
        raise RoundAuditViolation(
            "verified episodes cannot be reconstructed"
        ) from exc
    episode_manifest = read_json(root / "verified_episodes.json")
    expected_episode_manifest = {
        "schema_version": "r3e-round-episode-manifest-v1",
        "round_id": context["round_id"],
        "challenged_policy_hash": parent.policy_hash,
        "episode_ids": [
            episode.episode_id for episode in expected_episodes
        ],
        "episode_hashes": [
            episode.episode_hash for episode in expected_episodes
        ],
    }
    expected_episode_manifest["manifest_hash"] = hash_payload(
        expected_episode_manifest
    )
    if episode_manifest != expected_episode_manifest:
        raise RoundAuditViolation(
            "verified episode manifest cannot be reconstructed"
        )
    episode_store = EpisodeStore(
        project_root
        / config.get("memory_root", "runtime/memory")
        / "episodes"
    )
    try:
        for expected_episode in expected_episodes:
            if (
                episode_store.get(
                    expected_episode.episode_id
                ).to_dict()
                != expected_episode.to_dict()
            ):
                raise RoundAuditViolation(
                    "stored verified episode differs from authority"
                )
    except Exception as exc:
        if isinstance(exc, RoundAuditViolation):
            raise
        raise RoundAuditViolation(
            "stored verified episode cannot be audited"
        ) from exc
    red_context = verify_red_search_context(
        read_json(root / "red_search_context.json")
    )
    if red_context["challenged_policy_hash"] != parent.policy_hash:
        raise RoundAuditViolation("red search context policy binding mismatch")
    registry_before = validate_registry(read_json(root / "registry_before.json"))
    if registry_before["registry_hash"] != context["registry_hash_before"]:
        raise RoundAuditViolation("registry_before does not match frozen run context")
    residual = _manifest(root / "residual_manifest.json")
    archive_updates = _read_jsonl(root / "archive_updates.jsonl")
    accumulated = _read_jsonl(root / "accumulated_residuals.jsonl")
    accumulation = read_json(root / "residual_accumulation.json")
    covered_updates = _read_jsonl(root / "covered_archive_updates.jsonl")
    archive_exclusions = _read_jsonl(root / "archive_exclusions.jsonl")
    selection = read_json(root / "residual_selection.json")
    if selection.get("schema_version") != "r3e-residual-selection-v2":
        raise RoundAuditViolation("residual selection schema mismatch")
    selection_body = {
        key: value for key, value in selection.items() if key != "selection_hash"
    }
    if selection.get("selection_hash") != hash_payload(selection_body):
        raise RoundAuditViolation("residual selection hash mismatch")
    accumulation_body = {
        key: value for key, value in accumulation.items()
        if key != "accumulation_hash"
    }
    if (
        accumulation.get("schema_version") != "r3e-residual-accumulation-v1"
        or accumulation.get("accumulation_hash") != hash_payload(accumulation_body)
        or accumulation.get("accumulated_rows_hash") != hash_payload(accumulated)
        or accumulation.get("current_round_updates_hash") != hash_payload(archive_updates)
        or int(accumulation.get("row_count", -1)) != len(accumulated)
    ):
        raise RoundAuditViolation("residual accumulation hash mismatch")
    for row in accumulated:
        row_body = {
            key: value for key, value in row.items()
            if key != "archive_entry_hash"
        }
        if row.get("archive_entry_hash") != hash_payload(row_body):
            raise RoundAuditViolation("accumulated archive entry hash mismatch")
    if accumulation.get("archive_entry_hashes") != [
        str(row.get("archive_entry_hash") or "") for row in accumulated
    ]:
        raise RoundAuditViolation("residual accumulation entry list mismatch")
    if accumulation.get("included_round_ids") != sorted({
        str(row.get("discovered_round") or "")
        for row in accumulated if row.get("discovered_round")
    }):
        raise RoundAuditViolation("residual accumulation round list mismatch")
    if any(
        row.get("challenged_policy_hash") != parent.policy_hash
        for row in accumulated
    ):
        raise RoundAuditViolation("residual accumulation mixed policy hashes")
    if (
        selection.get("current_round_updates_hash") != hash_payload(archive_updates)
        or selection.get("accumulated_rows_hash") != hash_payload(accumulated)
        or selection.get("accumulation_hash") != accumulation["accumulation_hash"]
    ):
        raise RoundAuditViolation("residual selection accumulation mismatch")
    if selection.get("challenged_policy_hash") != parent.policy_hash:
        raise RoundAuditViolation("residual selection policy binding mismatch")
    selected_ids = {str(row.get("poison_id") or "") for row in residual["rows"]}
    recorded_ids = {str(value) for value in selection.get("selected_poison_ids") or []}
    expected_order = [
        str(row.get("poison_id") or "")
        for row in select_residual_elites(accumulated)
    ]
    if (
        selected_ids != recorded_ids
        or len(selected_ids) != int(selection.get("selected_count", -1))
        or list(selection.get("selected_poison_ids") or []) != expected_order
    ):
        raise RoundAuditViolation("residual manifest does not match elite selection")
    if residual.get("metadata", {}).get("selection_hash") != selection["selection_hash"]:
        raise RoundAuditViolation("residual manifest selection hash mismatch")
    if (
        residual.get("metadata", {}).get("accumulation_hash")
        != accumulation["accumulation_hash"]
    ):
        raise RoundAuditViolation("residual manifest accumulation hash mismatch")
    adaptation = _manifest(root / "adaptation_manifest.json")
    target = _manifest(root / "target_manifest.json")
    non_target = _manifest(root / "non_target_manifest.json")
    promotion = _manifest(root / "promotion_manifest.json")
    if adaptation["source_residual_manifest_hash"] != residual["manifest_hash"]:
        raise RoundAuditViolation("adaptation manifest source mismatch")
    if target["source_residual_manifest_hash"] != residual["manifest_hash"]:
        raise RoundAuditViolation("target manifest source mismatch")
    adaptation_designs = {
        str(row.get("design") or row.get("design_id") or "")
        for row in adaptation["rows"]
    }
    target_designs = {
        str(row.get("design") or row.get("design_id") or "")
        for row in target["rows"]
    }
    if adaptation_designs & target_designs:
        raise RoundAuditViolation("adaptation/target design leakage")
    for row in adaptation["rows"]:
        leaked = FORBIDDEN_INPUT_KEYS & row.keys()
        if leaked:
            raise RoundAuditViolation(
                f"adaptation row contains hidden fields: {sorted(leaked)}"
            )
    if (
        promotion.get("metadata", {}).get("non_target_manifest_hash")
        != non_target["manifest_hash"]
    ):
        raise RoundAuditViolation("promotion/non-target manifest binding mismatch")
    if promotion["source_residual_manifest_hash"] != target["manifest_hash"]:
        raise RoundAuditViolation("promotion/target manifest binding mismatch")

    children = {
        child.policy_id: child
        for child in (
            PolicyState.from_dict(row)
            for row in _read_jsonl(root / "child_policies.jsonl")
        )
    }
    for child in children.values():
        if child.parent_policy_hash != parent.policy_hash:
            raise RoundAuditViolation("child is not bound to audited parent")
        if child.created_from_residual_manifest_hash != residual["manifest_hash"]:
            raise RoundAuditViolation("child residual-manifest binding mismatch")

    replay_rows = _read_jsonl(root / "paired_validation.jsonl")
    decisions = _read_jsonl(root / "promotion_decisions.jsonl")
    promoted_candidates = {
        str(row.get("policy_id") or "") for row in promotion["rows"]
    }
    decided_candidates = {
        str(row.get("candidate_policy_id") or "") for row in decisions
    }
    if promoted_candidates != decided_candidates:
        raise RoundAuditViolation("promotion candidates and decisions differ")
    reconstructed = []
    for decision in decisions:
        candidate_id = str(decision.get("candidate_policy_id") or "")
        child = children.get(candidate_id)
        if child is None:
            raise RoundAuditViolation(f"decision candidate is missing: {candidate_id}")
        rows = [
            row for row in replay_rows
            if row.get("candidate_policy_id") == candidate_id
        ]
        provenance = decision.get("provenance") or {}
        expected_provenance = {
            "round_id": context["round_id"],
            "residual_manifest_hash": residual["manifest_hash"],
            "adaptation_manifest_hash": adaptation["manifest_hash"],
            "target_manifest_hash": target["manifest_hash"],
            "non_target_manifest_hash": non_target["manifest_hash"],
            "paired_result_hash": hash_payload(rows),
            "code_commit_sha": context["code_version"],
            "run_context_hash": context["run_context_hash"],
            "toolchain_fingerprint_hash": context["toolchain_fingerprint_hash"],
            "toolchain_fingerprint": context["toolchain_fingerprint"],
        }
        if provenance != expected_provenance:
            raise RoundAuditViolation(
                f"promotion provenance mismatch: {candidate_id}"
            )
        if decision.get("validation_manifest_hash") != promotion["manifest_hash"]:
            raise RoundAuditViolation(
                f"promotion validation manifest mismatch: {candidate_id}"
            )
        rebuilt = decide_policy_promotion(
            parent,
            child,
            rows,
            validation_manifest_hash=str(decision["validation_manifest_hash"]),
            thresholds=deepcopy(decision.get("thresholds") or {}),
            provenance=deepcopy(provenance),
        )
        if rebuilt != decision:
            raise RoundAuditViolation(
                f"promotion decision cannot be reconstructed: {candidate_id}"
            )
        reconstructed.append(rebuilt)
    winner = read_json(root / "winner.json")
    rebuilt_winner = select_single_promotable_child(reconstructed) or {}
    if winner != rebuilt_winner:
        raise RoundAuditViolation("recorded winner does not match reconstructed decisions")

    learnability_rows = _read_jsonl(root / "learnability_results.jsonl")
    learnability_by_id = {}
    for row in learnability_rows:
        verified = verify_learnability_result(row)
        poison_id = verified["poison_id"]
        if poison_id in learnability_by_id:
            raise RoundAuditViolation("duplicate learnability result")
        learnability_by_id[poison_id] = verified
    for row in archive_updates:
        evidence = row.get("learnability")
        poison_id = str(row.get("poison_id") or "")
        if evidence != learnability_by_id.get(poison_id):
            raise RoundAuditViolation("residual learnability evidence mismatch")

    registry_after = validate_registry(read_json(root / "registry_after.json"))
    active_after = get_active_policy(registry_after)
    if winner:
        if (
            active_after.policy_id != winner.get("candidate_policy_id")
            or active_after.policy_hash != winner.get("candidate_policy_hash")
        ):
            raise RoundAuditViolation("registry active policy is not the audited winner")
    elif active_after.policy_hash != parent.policy_hash:
        raise RoundAuditViolation("registry changed active policy without a winner")
    renewed = read_json(root / "renewed_challenge_binding.json")
    if renewed.get("challenged_policy_hash") != active_after.policy_hash:
        raise RoundAuditViolation("renewed challenge is not bound to final active policy")
    record = {
        "schema_version": ROUND_AUDIT_SCHEMA_VERSION,
        "round_id": context["round_id"],
        "code_version": context["code_version"],
        "run_context_hash": context["run_context_hash"],
        "toolchain_fingerprint_hash": context["toolchain_fingerprint_hash"],
        "red_search_context_hash": red_context["context_hash"],
        "parent_policy_id": parent.policy_id,
        "parent_policy_hash": parent.policy_hash,
        "active_policy_id": active_after.policy_id,
        "active_policy_hash": active_after.policy_hash,
        "registry_hash_before": registry_before["registry_hash"],
        "registry_hash_after": registry_after["registry_hash"],
        "residual_manifest_hash": residual["manifest_hash"],
        "residual_selection_hash": selection["selection_hash"],
        "residual_archive_updates_hash": hash_payload(archive_updates),
        "validity_authority": validity_authority,
        "validity_results_hash": hash_payload(validity_rows),
        "blue_challenge_results_hash": hash_payload(
            challenge_rows
        ),
        "verified_episode_manifest_hash": episode_manifest[
            "manifest_hash"
        ],
        "covered_archive_updates_hash": hash_payload(covered_updates),
        "archive_exclusions_hash": hash_payload(archive_exclusions),
        "learnability_results_hash": hash_payload(learnability_rows),
        "adaptation_manifest_hash": adaptation["manifest_hash"],
        "target_manifest_hash": target["manifest_hash"],
        "non_target_manifest_hash": non_target["manifest_hash"],
        "promotion_manifest_hash": promotion["manifest_hash"],
        "paired_result_hash": hash_payload(replay_rows),
        "decisions_hash": hash_payload(decisions),
        "winner_decision_hash": str(winner.get("decision_hash") or ""),
        "promoted": active_after.policy_hash != parent.policy_hash,
        "promotion_eligible": target["row_count"] > 0,
        "defer_reason": (
            "" if target["row_count"] > 0 else "insufficient_residual_designs"
        ),
    }
    if formal_rejections_path.is_file():
        record.update({
            "formal_rejection_count": len(formal_rejections),
            "formal_rejections_hash": hash_payload(
                formal_rejections
            ),
        })
    record["audit_record_hash"] = hash_payload(record)
    return record


def freeze_round_audit(round_dir: str | Path) -> dict[str, Any]:
    root = Path(round_dir)
    target = root / "round_audit.json"
    rebuilt = reconstruct_round(root)
    if target.exists():
        existing = read_json(target)
        if existing != rebuilt:
            raise RoundAuditViolation("frozen round audit differs from reconstruction")
        return existing
    atomic_write_json(target, rebuilt)
    return rebuilt


def append_round_ledger(path: str | Path, audit: dict[str, Any]) -> dict[str, Any]:
    """Idempotently append one completed round to a hash-chain ledger."""
    target = Path(path)
    with writer_lock(target.with_suffix(target.suffix + ".lock")):
        existing = read_ledger(target)
        same_round = [
            row for row in existing
            if row.get("round_id") == audit.get("round_id")
        ]
        if same_round:
            if (
                len(same_round) != 1
                or same_round[0].get("audit_record_hash")
                != audit.get("audit_record_hash")
            ):
                raise RoundAuditViolation("round ledger contains a conflicting round")
            return same_round[0]
        payload = {
            "operation": "round_complete",
            "round_id": audit["round_id"],
            "audit_record_hash": audit["audit_record_hash"],
            "parent_policy_hash": audit["parent_policy_hash"],
            "active_policy_hash": audit["active_policy_hash"],
            "registry_hash_before": audit["registry_hash_before"],
            "registry_hash_after": audit["registry_hash_after"],
            "timestamp": utc_now(),
            "ledger_index": len(existing),
            "previous_entry_hash": (
                existing[-1]["ledger_entry_hash"] if existing else ""
            ),
        }
        payload["ledger_entry_hash"] = hash_payload(payload)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as stream:
            stream.write(canonical_json(payload) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return payload


def verify_frozen_round(round_dir: str | Path) -> dict[str, Any]:
    target = Path(round_dir) / "round_audit.json"
    if not target.is_file():
        raise RoundAuditViolation("frozen round audit is missing")
    existing = read_json(target)
    rebuilt = reconstruct_round(round_dir)
    if existing != rebuilt:
        raise RoundAuditViolation("round artifacts differ from frozen audit")
    return rebuilt


def _main() -> None:
    parser = argparse.ArgumentParser(description="Reconstruct and audit an R³E round")
    parser.add_argument("--round-dir", required=True)
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()
    result = (
        freeze_round_audit(args.round_dir)
        if args.freeze
        else verify_frozen_round(args.round_dir)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
