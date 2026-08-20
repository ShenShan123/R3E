from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from r3e.arena.fake_adapters import (
    DeterministicEvolutionAdapter,
    FailureInjectionAdapter,
)
from r3e.arena.integrated_loop import (
    IntegratedCoevolutionLoop,
    IntegratedLoopViolation,
    verify_integrated_round,
)
from r3e.arena.integrated_fake_system import (
    run_integrated_fake_system,
)
from r3e.arena.manifests import make_manifest
from r3e.memory.bank_store import ActiveBankStore
from r3e.memory.episode_store import EpisodeStore
from r3e.memory.fake_adapter import DeterministicMemoryAdapter
from r3e.memory.memory_store import MemoryStore
from r3e.policy.registry_v2 import (
    get_active_policy,
    initialize_registry,
    load_registry,
)
from r3e.protocol.hashing import atomic_write_json


ROOT = Path(__file__).resolve().parents[1]


def _workspace(tmp_path):
    registry = tmp_path / "runtime/registry.json"
    initialize_registry(
        ROOT / "configs/base_policy/frozen_base_policy_v3.json",
        registry,
    )
    non_target = tmp_path / "non_target.json"
    atomic_write_json(
        non_target,
        make_manifest(
            [
                {"case_id": "n0", "design": "non_target_0"},
                {"case_id": "n1", "design": "non_target_1"},
            ],
            split="non_target",
        ),
    )
    config = {
        "schema_version": "r3e-integrated-coevolution-config-v1",
        "policy_registry": str(registry),
        "memory_root": str(tmp_path / "runtime/memory"),
        "integrated_rounds_root": str(
            tmp_path / "runtime/integrated/rounds"
        ),
        "arena_rounds_root": str(
            tmp_path / "runtime/integrated/arena"
        ),
        "events_root": str(tmp_path / "runtime/events"),
        "decision_ledger": str(
            tmp_path / "runtime/decision_ledger.jsonl"
        ),
        "macro_ledger": str(
            tmp_path / "runtime/integrated/macro_ledger.jsonl"
        ),
        "code_version": "integrated-deterministic-test",
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
            "red_archive": str(
                tmp_path / "runtime/archives/residual.jsonl"
            ),
            "covered_archive": str(
                tmp_path / "runtime/archives/covered.jsonl"
            ),
            "red_rejected_archive": str(
                tmp_path / "runtime/archives/rejected.jsonl"
            ),
            "round_ledger": str(
                tmp_path / "runtime/arena_round_ledger.jsonl"
            ),
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
    return config, registry


def _loop(tmp_path, config, *, adapter=None):
    return IntegratedCoevolutionLoop(
        config,
        project_root=ROOT,
        arena_adapter=(
            adapter
            or DeterministicEvolutionAdapter(tmp_path / "adapter")
        ),
        memory_adapter=DeterministicMemoryAdapter(),
    )


def test_integrated_loop_serializes_memory_and_policy_promotions(tmp_path):
    config, registry = _workspace(tmp_path)
    loop = _loop(tmp_path, config)
    first = loop.run_round(round_id="R001", round_index=0)
    assert first["promotion_count"] == 0
    assert first["parent_policy_hash"] == first["active_policy_hash"]
    assert len(EpisodeStore(
        tmp_path / "runtime/memory/episodes"
    ).audit()) == 4

    second = loop.run_round(
        round_id="R002", round_index=1, previous_round_id="R001"
    )
    assert second["promotion_count"] == 1
    assert second["memory_promoted"] is True
    assert second["arena_promoted"] is False
    assert second["post_memory_policy_hash"] == second[
        "active_policy_hash"
    ]
    active_memory = get_active_policy(load_registry(registry))
    assert active_memory.memory_binding
    memories = MemoryStore(
        tmp_path / "runtime/memory/library",
        episode_store=EpisodeStore(
            tmp_path / "runtime/memory/episodes"
        ),
    )
    bank = ActiveBankStore(
        tmp_path / "runtime/memory/active_banks",
        memory_store=memories,
    ).load_for_policy(active_memory)
    assert len(bank.memories) == 1

    third = loop.run_round(
        round_id="R003", round_index=2, previous_round_id="R002"
    )
    assert third["promotion_count"] == 0
    assert third["memory_promoted"] is False
    assert third["arena_promoted"] is False
    assert third["parent_policy_hash"] == second[
        "active_policy_hash"
    ]
    assert third["active_policy_hash"] == third[
        "parent_policy_hash"
    ]
    red_context = json.loads((
        tmp_path
        / "runtime/integrated/arena/R003/red_search_context.json"
    ).read_text())
    assert red_context["memory_capability"][
        "challenged_policy_hash"
    ] == third["active_policy_hash"]
    assert red_context["memory_capability"][
        "effective_memory_bank_hash"
    ] == active_memory.memory_binding[
        "effective_memory_bank_hash"
    ]

    fourth = loop.run_round(
        round_id="R004", round_index=3, previous_round_id="R003"
    )
    assert fourth["promotion_count"] == 1
    assert fourth["memory_promoted"] is False
    assert fourth["arena_promoted"] is True
    assert fourth["parent_policy_hash"] == third[
        "active_policy_hash"
    ]
    assert fourth["active_policy_hash"] != fourth[
        "parent_policy_hash"
    ]
    for round_id in ("R001", "R002", "R003", "R004"):
        audit = verify_integrated_round(
            tmp_path / f"runtime/integrated/rounds/{round_id}"
        )
        assert audit["promotion_count"] <= 1


def test_integrated_loop_resumes_after_memory_commit_before_arena(
    tmp_path,
):
    config, registry = _workspace(tmp_path)
    _loop(tmp_path, config).run_round(
        round_id="R001", round_index=0
    )
    failing = FailureInjectionAdapter(
        DeterministicEvolutionAdapter(tmp_path / "adapter"),
        fail_method="generate_red",
    )
    with pytest.raises(RuntimeError, match="injected failure"):
        _loop(tmp_path, config, adapter=failing).run_round(
            round_id="R002",
            round_index=1,
            previous_round_id="R001",
        )
    promoted_after_failure = get_active_policy(
        load_registry(registry)
    )
    resumed = _loop(tmp_path, config).run_round(
        round_id="R002",
        round_index=1,
        previous_round_id="R001",
    )
    assert resumed["promotion_count"] == 1
    assert resumed["memory_promoted"] is True
    assert resumed["active_policy_hash"] == (
        promoted_after_failure.policy_hash
    )

    round_dir = tmp_path / "runtime/integrated/rounds/R002"
    tampered = deepcopy(json.loads(
        (round_dir / "memory_epoch_summary.json").read_text()
    ))
    tampered["promoted"] = False
    atomic_write_json(round_dir / "memory_epoch_summary.json", tampered)
    with pytest.raises(IntegratedLoopViolation):
        verify_integrated_round(round_dir)


def test_registry_initialization_preserves_policy_v3_authority(tmp_path):
    registry = tmp_path / "registry.json"
    initialize_registry(
        ROOT / "configs/base_policy/frozen_base_policy_v3.json",
        registry,
    )
    active = get_active_policy(load_registry(registry))
    assert active.schema_version == "r3e-policy-v3"
    assert active.candidate_portfolio_binding


def test_integrated_fake_system_runs_four_rounds_idempotently(tmp_path):
    workspace = tmp_path / "integrated-system"
    first = run_integrated_fake_system(
        project_root=ROOT,
        workspace=workspace,
        rounds=4,
    )
    second = run_integrated_fake_system(
        project_root=ROOT,
        workspace=workspace,
        rounds=4,
    )
    assert second == first
    assert [row["promotion_lane"] for row in first] == [
        "collect",
        "memory",
        "collect",
        "policy",
    ]
    assert [row["promotion_count"] for row in first] == [0, 1, 0, 1]
