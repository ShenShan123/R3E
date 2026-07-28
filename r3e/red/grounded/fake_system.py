"""Deterministic Grounded Red authority-system fixture.

This CLI validates protocol/state behavior without invoking a parser, EDA tool,
oracle service, model, or experiment.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import atomic_write_json, hash_payload, read_json

from .admission import decide_grounded_admission
from .archives import GroundedRedArchive
from .capability_packet import build_grounded_red_capability_packet
from .coverage import freeze_coverage_state
from .difficulty import build_difficulty_profile
from .fake import build_deterministic_evidence
from .lineage import build_lineage
from .mutation_plan import build_mutation_plan
from .registry import load_grounded_registries


def run_fake_grounded_red(
    workspace: str | Path,
    *,
    rounds: int = 2,
) -> dict[str, object]:
    if rounds < 1:
        raise ValueError("rounds must be positive")
    project_root = Path(__file__).resolve().parents[3]
    target = Path(workspace)
    target.mkdir(parents=True, exist_ok=True)
    policy = PolicyState.from_dict(
        read_json(
            project_root / "configs/base_policy/frozen_base_policy_v1.json"
        )
    )
    registries = load_grounded_registries(
        family_registry=project_root
        / "configs/red/grounded_family_registry_v1.json",
        operator_registry=project_root
        / "configs/red/grounded_operator_registry_v1.json",
        effect_registry=project_root
        / "configs/red/grounded_effect_registry_v1.json",
    )
    archive = GroundedRedArchive(
        target / "archives",
        require_grounded_execution=False,
    )
    cells = []
    admitted = []
    for index in range(1, rounds + 1):
        round_id = f"GRD{index:03d}"
        plan = build_mutation_plan(
            plan_id=f"MP_{round_id}_001",
            policy=policy,
            registries=registries,
            target_design=f"fixture_design_{index}",
            target_module="fixture_top",
            target_ast_node_hash=hash_payload({
                "round_id": round_id,
                "node": "comparison",
            }),
            family_id="combinational.comparator_boundary",
            operator_id="replace_comparator",
            expected_runtime_effect_id="wrong_combinational_value",
            preconditions={"comparison_expression": True},
            scope_limits={
                "maximum_changed_modules": 1,
                "maximum_changed_blocks": 1,
                "maximum_ast_edits": 1,
            },
            difficulty_target={
                "difficulty_band": "D0",
                "dependency_depth_delta": 0,
                "temporal_depth_delta": 0,
            },
        )
        evidence = build_deterministic_evidence(plan)
        decision = decide_grounded_admission(
            plan=plan,
            policy=policy,
            registries=registries,
            evidence=evidence,
        )
        profile = build_difficulty_profile(
            current_blue_failure_rate=1.0,
            semantic_diff_receipt=evidence["semantic_diff"],
            runtime_effect_receipt=evidence["runtime_effect"],
            activation_rarity=0.5,
            repair_locality="expression",
            candidate_ambiguity=1,
            composition_depth=1,
        )
        poison_id = f"P_{round_id}_001"
        lineage = build_lineage(
            poison_id=poison_id,
            parent_poison_ids=[],
            lineage_operator="fresh",
            source_family_ids=[plan["family_id"]],
            target_family_ids=[plan["family_id"]],
            difficulty_delta={"temporal_depth": 0, "dependency_depth": 0},
            semantic_diff_receipt_hash=evidence["semantic_diff"][
                "receipt_hash"
            ],
        )
        archive.add(
            kind="valid",
            poison_id=poison_id,
            archived_round_id=round_id,
            policy=policy,
            registries=registries,
            plan=plan,
            evidence=evidence,
            admission_decision=decision,
            difficulty_profile=profile,
            lineage=lineage,
        )
        archive.add(
            kind="residual",
            poison_id=poison_id,
            archived_round_id=round_id,
            policy=policy,
            registries=registries,
            plan=plan,
            evidence=evidence,
            admission_decision=decision,
            difficulty_profile=profile,
            lineage=lineage,
        )
        cells.append({
            "family_id": plan["family_id"],
            "design_id": plan["target_design"],
            "rtl_role": "control",
            "temporal_context": "combinational",
            "difficulty_band": profile["difficulty_band"],
            "effective_blue_policy_hash": policy.effective_policy_hash,
            "effective_memory_bank_hash": "",
            "proposals": 1,
            "admitted": 1,
            "covered": 0,
            "residual": 1,
            "rejected": 0,
            "last_round_id": round_id,
        })
        admitted.append({
            "round_id": round_id,
            "poison_id": poison_id,
            "plan_hash": plan["plan_hash"],
            "evidence_hash": evidence["evidence_hash"],
            "decision_hash": decision["decision_hash"],
        })
    coverage = freeze_coverage_state(cells)
    capability = build_grounded_red_capability_packet(
        policy,
        coverage_state=coverage,
    )
    summary = {
        "schema_version": "r3e-grounded-red-fake-system-v1",
        "round_count": rounds,
        "policy_instance_hash": policy.policy_instance_hash,
        "effective_policy_hash": policy.effective_policy_hash,
        "registry_bundle_hash": registries.registry_bundle_hash,
        "coverage_hash": coverage["coverage_hash"],
        "capability_packet_hash": capability["packet_hash"],
        "valid_archive_count": len(archive.load("valid")),
        "residual_archive_count": len(archive.load("residual")),
        "admitted": admitted,
    }
    summary["summary_hash"] = hash_payload(summary)
    atomic_write_json(target / "coverage.json", coverage)
    atomic_write_json(target / "capability_packet.json", capability)
    atomic_write_json(target / "summary.json", summary)
    return summary


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--rounds", type=int, default=2)
    args = parser.parse_args()
    print(json.dumps(
        run_fake_grounded_red(args.workspace, rounds=args.rounds),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ))


if __name__ == "__main__":
    _main()
