"""Run the model-free integrated Arena/RAAM promotion scheduler.

This command is a protocol fixture.  It proves state transitions, recovery,
cross-round memory binding, and the single-promotion epoch; it is not a model
experiment and must not be reported as repair or discovery evidence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from r3e.arena.fake_adapters import DeterministicEvolutionAdapter
from r3e.arena.integrated_loop import IntegratedCoevolutionLoop
from r3e.arena.manifests import make_manifest
from r3e.memory.fake_adapter import DeterministicMemoryAdapter
from r3e.policy.registry_v2 import initialize_registry
from r3e.protocol.hashing import atomic_write_json


def run_integrated_fake_system(
    *,
    project_root: str | Path,
    workspace: str | Path,
    rounds: int = 4,
) -> list[dict]:
    if rounds < 1:
        raise ValueError("rounds must be positive")
    root = Path(project_root).resolve()
    work = Path(workspace)
    if not work.is_absolute():
        work = root / work
    work.mkdir(parents=True, exist_ok=True)
    registry = work / "registry/policy_registry.json"
    if not registry.exists():
        initialize_registry(
            root / "configs/base_policy/frozen_base_policy_v3.json",
            registry,
            base_path_record=(
                "configs/base_policy/frozen_base_policy_v3.json"
            ),
        )
    non_target = work / "non_target_manifest.json"
    if not non_target.exists():
        atomic_write_json(
            non_target,
            make_manifest(
                [
                    {
                        "case_id": "integrated_non_target_0",
                        "design": "integrated_non_target_design_0",
                    },
                    {
                        "case_id": "integrated_non_target_1",
                        "design": "integrated_non_target_design_1",
                    },
                ],
                split="non_target",
                metadata={"adapter_mode": "deterministic_fake"},
            ),
        )
    config = {
        "schema_version": "r3e-integrated-coevolution-config-v1",
        "policy_registry": str(registry),
        "memory_root": str(work / "memory"),
        "integrated_rounds_root": str(work / "integrated/rounds"),
        "arena_rounds_root": str(work / "integrated/arena"),
        "events_root": str(work / "events"),
        "decision_ledger": str(work / "decision_ledger.jsonl"),
        "macro_ledger": str(work / "integrated/macro_ledger.jsonl"),
        "code_version": "integrated-deterministic-fixture-v1",
        "promotion_lane_schedule": [
            "collect",
            "memory",
            "collect",
            "policy",
        ],
        "arena_config": {
            "policy_search_space": (
                "configs/base_policy/policy_search_space_v1.json"
            ),
            "non_target_manifest": str(non_target),
            "red_archive": str(work / "archives/residual.jsonl"),
            "covered_archive": str(work / "archives/covered.jsonl"),
            "red_rejected_archive": str(
                work / "archives/rejected.jsonl"
            ),
            "round_ledger": str(work / "arena_round_ledger.jsonl"),
            "challenge_seeds": [1, 2],
            "promotion_seeds": [11, 12],
            "split_seed": 4,
            "policy_search_seed": 5,
            "validity_authority": "legacy_adapter_evidence_v1",
        },
        "memory_config": {
            "minimum_support": 1,
            "maximum_active_memories": 1,
            "shadow_seeds": [101],
            "promotion_seeds": [201],
            "qualification_thresholds": {
                "min_helped": 1,
                "min_designs": 1,
                "max_harmed": 0,
                "max_cost_ratio": 1.5,
            },
        },
    }
    loop = IntegratedCoevolutionLoop(
        config,
        project_root=root,
        arena_adapter=DeterministicEvolutionAdapter(
            work / "adapter_workspace"
        ),
        memory_adapter=DeterministicMemoryAdapter(),
    )
    return loop.run([f"R{index:03d}" for index in range(1, rounds + 1)])


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="R3E integrated deterministic coevolution fixture"
    )
    parser.add_argument("--project-root", default=str(Path.cwd()))
    parser.add_argument(
        "--workspace", default="runtime/integrated_fake_system"
    )
    parser.add_argument("--rounds", type=int, default=4)
    args = parser.parse_args()
    summaries = run_integrated_fake_system(
        project_root=args.project_root,
        workspace=args.workspace,
        rounds=args.rounds,
    )
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
