"""Run model-free multi-round system validation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from r3e.arena.fake_adapters import DeterministicEvolutionAdapter
from r3e.arena.manifests import freeze_manifest, make_manifest
from r3e.arena.runner import EvolutionRoundRunner
from r3e.policy.registry_v2 import initialize_registry


def run_fake_system(
    *,
    project_root: str | Path,
    workspace: str | Path,
    start_round: int,
    rounds: int,
) -> list[dict]:
    root = Path(project_root).resolve()
    work = Path(workspace)
    if not work.is_absolute():
        work = root / work
    work.mkdir(parents=True, exist_ok=True)
    registry = work / "registry/policy_registry.json"
    if not registry.exists():
        initialize_registry(
            root / "configs/base_policy/frozen_base_policy_v1.json",
            registry,
            base_path_record="configs/base_policy/frozen_base_policy_v1.json",
        )
    non_target_path = work / "non_target_manifest.json"
    freeze_manifest(
        non_target_path,
        make_manifest(
            [
                {"case_id": "fake_non_target_0", "design": "fake_non_target_design_0"},
                {"case_id": "fake_non_target_1", "design": "fake_non_target_design_1"},
            ],
            split="non_target",
            metadata={"adapter_mode": "deterministic_fake"},
        ),
    )
    config = {
        "policy_registry": str(registry),
        "policy_search_space": "configs/base_policy/policy_search_space_v1.json",
        "non_target_manifest": str(non_target_path),
        "rounds_root": str(work / "rounds"),
        "decision_ledger": str(work / "decision_ledger.jsonl"),
        "red_archive": str(work / "red_archive.jsonl"),
        "covered_archive": str(work / "covered_archive.jsonl"),
        "round_ledger": str(work / "round_ledger.jsonl"),
        "events_root": str(work / "events"),
        "challenge_seeds": [1, 2, 3],
        "promotion_seeds": [11, 12, 13],
        "split_seed": 4,
        "policy_search_seed": 5,
        "code_version": "deterministic-fake-system",
        "adapter_mode": "deterministic_fake",
    }
    adapter = DeterministicEvolutionAdapter(work / "adapter_workspace")
    summaries = []
    for number in range(start_round, start_round + rounds):
        summaries.append(EvolutionRoundRunner(
            config,
            round_id=f"R{number:03d}",
            adapter=adapter,
            project_root=root,
        ).run())
    return summaries


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="R³E deterministic fake-adapter system validation"
    )
    parser.add_argument("--project-root", default=str(Path.cwd()))
    parser.add_argument("--workspace", default="runtime/fake_system")
    parser.add_argument("--start-round", type=int, default=1)
    parser.add_argument("--rounds", type=int, default=2)
    args = parser.parse_args()
    rows = run_fake_system(
        project_root=args.project_root,
        workspace=args.workspace,
        start_round=args.start_round,
        rounds=args.rounds,
    )
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
