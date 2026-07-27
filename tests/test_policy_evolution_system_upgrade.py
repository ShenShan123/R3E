from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from r3e.arena.fake_adapters import (
    DeterministicEvolutionAdapter,
    FakeBlueAdapter,
    FakeRedAdapter,
    FailureInjectionAdapter,
)
from r3e.arena.audit import RoundAuditViolation, verify_frozen_round
from r3e.arena.manifests import make_manifest
from r3e.arena.round_state import RoundState, RoundStateViolation
from r3e.arena.runner import EvolutionRoundRunner, RoundRunnerViolation, run_round
from r3e.policy.migrate import migrate_legacy
from r3e.policy.registry_v2 import (
    RegistryViolation,
    get_active_policy,
    initialize_registry,
    load_registry,
    promote_policy,
    register_candidate,
    registry_hash,
    rollback_policy,
    validate_registry,
)
from r3e.policy.schema import PolicyState
from r3e.policy.search import propose_children
from r3e.policy.runtime import PolicyRuntime, PolicyRuntimeViolation
from r3e.protocol.events import EventLogger, EventViolation, read_events
from r3e.protocol.hashing import atomic_write_json, hash_payload
from r3e.protocol.ledger import read_ledger
from r3e.protocol.provenance import RunContextViolation, verify_run_context
from r3e.red.archive import ArchiveViolation, load_archive, update_archive
from r3e.red.feedback_packet import (
    CapabilityPacketViolation,
    build_capability_packet,
    build_red_search_context,
)
from r3e.red.generator import generate_poison
from r3e.red.lineage import validate_lineage_graph
from r3e.red.learnability import (
    LearnabilityViolation,
    validate_learnability_result,
    verify_learnability_result,
)
from r3e.red.selection import materialize_elites, pareto_frontier, select_residual_elites
from semantic_repair_bench import functional_repair


ROOT = Path(__file__).resolve().parents[1]


def _init(tmp_path: Path) -> tuple[Path, PolicyState]:
    registry = tmp_path / "policy_registry.json"
    initialize_registry(
        ROOT / "configs/base_policy/frozen_base_policy_v1.json",
        registry,
    )
    return registry, get_active_policy(load_registry(registry))


def _children(parent: PolicyState, count: int = 2) -> list[PolicyState]:
    adaptation = make_manifest(
        [
            {"case_id": "a", "design": "d0"},
            {"case_id": "b", "design": "d1"},
        ],
        split="adaptation",
    )
    space = json.loads(
        (ROOT / "configs/base_policy/policy_search_space_v1.json").read_text()
    )
    return propose_children(
        parent, adaptation, space, round_id="R900", seed=17
    )[:count]


def _strong_decision(parent: PolicyState, child: PolicyState) -> dict:
    from r3e.policy.promotion import decide_policy_promotion

    rows = []
    for split, cases in (
        ("target", [("t0", "td0"), ("t1", "td1")]),
        ("non_target", [("n0", "nd0"), ("n1", "nd1")]),
    ):
        for case_id, design in cases:
            for arm in ("parent", "candidate"):
                rows.append({
                    "case_id": case_id,
                    "design": design,
                    "seed": 1,
                    "split": split,
                    "arm": arm,
                    "oracle_ok": arm == "candidate" or split == "non_target",
                    "model_id": "fake",
                    "budget_hash": "fake",
                    "verifier_hash": "fake",
                    "cost": 1.0,
                })
    return decide_policy_promotion(
        parent,
        child,
        rows,
        validation_manifest_hash="sha256:validation",
        provenance={
            "round_id": "R900",
            "residual_manifest_hash": "sha256:" + "1" * 64,
            "adaptation_manifest_hash": "sha256:" + "2" * 64,
            "target_manifest_hash": "sha256:" + "3" * 64,
            "non_target_manifest_hash": "sha256:" + "4" * 64,
            "paired_result_hash": hash_payload(rows),
            "code_commit_sha": "test-version",
            "toolchain_fingerprint_hash": "sha256:" + "5" * 64,
            "run_context_hash": "sha256:" + "6" * 64,
            "toolchain_fingerprint": {"adapter": "test"},
        },
    )


def _project(tmp_path: Path) -> tuple[dict, Path]:
    (tmp_path / "configs").mkdir()
    (tmp_path / "runtime/registry").mkdir(parents=True)
    base = json.loads(
        (ROOT / "configs/base_policy/frozen_base_policy_v1.json").read_text()
    )
    search = json.loads(
        (ROOT / "configs/base_policy/policy_search_space_v1.json").read_text()
    )
    atomic_write_json(tmp_path / "configs/base.json", base)
    atomic_write_json(tmp_path / "configs/search.json", search)
    registry = tmp_path / "runtime/registry/policy_registry.json"
    initialize_registry(tmp_path / "configs/base.json", registry)
    atomic_write_json(
        tmp_path / "non_target.json",
        make_manifest(
            [
                {"case_id": "n0", "design": "non_target_0"},
                {"case_id": "n1", "design": "non_target_1"},
            ],
            split="non_target",
        ),
    )
    config = {
        "policy_registry": "runtime/registry/policy_registry.json",
        "policy_search_space": "configs/search.json",
        "non_target_manifest": "non_target.json",
        "challenge_seeds": [1, 2, 3],
        "promotion_seeds": [11, 12, 13],
        "split_seed": 4,
        "policy_search_seed": 5,
        "code_version": "test-version",
    }
    return config, registry


def _archive_poison(**updates):
    row = {
        "poison_id": "p0",
        "challenged_policy_hash": "sha256:" + "a" * 64,
        "family": "constant_error",
        "effect": "wrong_value",
        "affected_role": "control",
        "edit_scope": "expression",
        "composition_depth": 1,
        "sequential_depth": 0,
        "first_divergence_cycle_bucket": "combinational",
        "failure_signature": "y:0",
        "normalized_diff_hash": "diff0",
        "hardness": 1.0,
        "learnability": "reachable",
        "validity": {
            "proven_valid": True,
            "evidence": {"formal_status": "PROVEN_NON_EQUIV"},
        },
    }
    row.update(updates)
    return row


def test_registry_rejects_dual_active_and_manual_authority(tmp_path):
    registry, parent = _init(tmp_path)
    child = _children(parent, 1)[0].with_updates(status="active")
    payload = load_registry(registry)
    payload["policies"][child.policy_id] = {
        "status": "active",
        "policy_hash": child.policy_hash,
        "policy": child.to_dict(),
    }
    payload["registry_hash"] = registry_hash(payload)
    with pytest.raises(RegistryViolation, match="exactly one active"):
        validate_registry(payload)

    payload = load_registry(registry)
    payload["legacy_manual_skills"] = [{"skill_id": "manual"}]
    payload["registry_hash"] = registry_hash(payload)
    with pytest.raises(RegistryViolation, match="manual/legacy"):
        validate_registry(payload)


def test_promotion_without_reconstructable_provenance_is_rejected():
    from r3e.policy.promotion import decide_policy_promotion

    parent = PolicyState.from_dict(
        json.loads(
            (ROOT / "configs/base_policy/frozen_base_policy_v1.json").read_text()
        )
    )
    child = _children(parent, 1)[0]
    complete = _strong_decision(parent, child)
    rows = [
        {
            "case_id": case_id,
            "design": design,
            "seed": 1,
            "split": split,
            "arm": arm,
            "oracle_ok": arm == "candidate" or split == "non_target",
            "model_id": "fake",
            "budget_hash": "fake",
            "verifier_hash": "fake",
            "cost": 1.0,
        }
        for split, cases in (
            ("target", [("t0", "td0"), ("t1", "td1")]),
            ("non_target", [("n0", "nd0"), ("n1", "nd1")]),
        )
        for case_id, design in cases
        for arm in ("parent", "candidate")
    ]
    decision = decide_policy_promotion(
        parent,
        child,
        rows,
        validation_manifest_hash=complete["validation_manifest_hash"],
    )
    assert not decision["promote"]
    assert decision["checks"]["provenance_complete"] is False


def test_registry_tamper_and_missing_rollback_version_fail_closed(tmp_path):
    registry, parent = _init(tmp_path)
    raw = json.loads(registry.read_text())
    raw["active_policy_id"] = "tampered"
    registry.write_text(json.dumps(raw))
    with pytest.raises(RegistryViolation, match="registry hash mismatch"):
        load_registry(registry)

    registry.unlink()
    registry, parent = _init(tmp_path)
    child = _children(parent, 1)[0]
    register_candidate(registry, child)
    promoted = promote_policy(registry, child.policy_id, _strong_decision(parent, child))
    snapshot = (
        registry.parent
        / f".{registry.name}.versions"
        / f"{get_active_policy(promoted).rollback_registry_hash.replace(':', '_')}.json"
    )
    snapshot.unlink()
    with pytest.raises(RegistryViolation, match="version is missing"):
        rollback_policy(registry)


def test_registry_serializes_concurrent_writers(tmp_path):
    registry, parent = _init(tmp_path)
    children = _children(parent, 4)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda child: register_candidate(registry, child), children))
    loaded = load_registry(registry)
    assert all(child.policy_id in loaded["policies"] for child in children)
    assert get_active_policy(loaded).policy_id == "B0"


def test_round_state_detects_artifact_hash_mismatch_and_repeat(tmp_path):
    state = RoundState(tmp_path / "state.json", round_id="R001")
    state.initialize({"config": 1})
    state.complete_stage("INIT", stage_input={"a": 1}, stage_output={"b": 2})
    state.verify_stage("INIT", stage_input={"a": 1}, stage_output={"b": 2})
    with pytest.raises(RoundStateViolation, match="output hash mismatch"):
        state.verify_stage("INIT", stage_output={"b": 3})
    with pytest.raises(RoundStateViolation, match="expected stage LOAD_ACTIVE_POLICY"):
        state.complete_stage("INIT", stage_input={}, stage_output={})


def test_fake_adapter_multi_round_switches_policy_and_packets(tmp_path):
    config, registry = _project(tmp_path)
    adapter = DeterministicEvolutionAdapter(tmp_path / "runtime/fake")
    first = EvolutionRoundRunner(
        config, round_id="R001", adapter=adapter, project_root=tmp_path
    ).run()
    second = EvolutionRoundRunner(
        config, round_id="R002", adapter=adapter, project_root=tmp_path
    ).run()
    assert first["promoted"] and second["promoted"]
    assert first["active_policy_hash"] == second["parent_policy_hash"]
    assert second["active_policy_hash"] != first["active_policy_hash"]
    first_red = [
        json.loads(line)
        for line in (tmp_path / "runtime/rounds/R001/red_candidates.jsonl").read_text().splitlines()
    ]
    second_red = [
        json.loads(line)
        for line in (tmp_path / "runtime/rounds/R002/red_candidates.jsonl").read_text().splitlines()
    ]
    assert first_red[0]["capability_packet_hash"] != second_red[0]["capability_packet_hash"]
    second_context = json.loads(
        (tmp_path / "runtime/rounds/R002/red_search_context.json").read_text()
    )
    assert second_context["residual_count"] >= 4
    assert second_context["challenged_policy_hash"] == first["active_policy_hash"]
    assert get_active_policy(load_registry(registry)).policy_hash == second["active_policy_hash"]


def test_stable_run_round_accepts_separate_red_and_blue_adapters(tmp_path):
    config, registry = _project(tmp_path)
    summary = run_round(
        registry,
        FakeRedAdapter(tmp_path / "runtime/fake"),
        FakeBlueAdapter(),
        {
            **config,
            "round_id": "R001",
            "project_root": str(tmp_path),
        },
    )
    assert summary["promoted"]


def test_failure_injection_resumes_without_rewriting_stages(tmp_path):
    config, _registry = _project(tmp_path)
    adapter = FailureInjectionAdapter(
        DeterministicEvolutionAdapter(tmp_path / "runtime/fake"),
        fail_method="evaluate_blue",
    )
    runner = EvolutionRoundRunner(
        config, round_id="R001", adapter=adapter, project_root=tmp_path
    )
    with pytest.raises(RuntimeError, match="injected failure"):
        runner.run()
    before = runner.state.load()
    assert before["current_stage"] == "VALIDITY_GATE"
    summary = runner.run()
    assert summary["promoted"]
    stages = [row["stage"] for row in runner.state.load()["checkpoints"]]
    assert stages == list(dict.fromkeys(stages))


def test_partial_archive_write_recovers_with_original_teacher_evidence(tmp_path):
    config, _registry = _project(tmp_path)
    adapter = FailureInjectionAdapter(
        DeterministicEvolutionAdapter(tmp_path / "runtime/fake"),
        fail_method="probe_learnability",
        fail_on_call=2,
    )
    runner = EvolutionRoundRunner(
        config, round_id="R001", adapter=adapter, project_root=tmp_path
    )
    with pytest.raises(RuntimeError, match="probe_learnability call 2"):
        runner.run()
    archive = tmp_path / "runtime/archives/red_residual_archive.jsonl"
    assert len(load_archive(archive)) == 1
    summary = runner.run()
    assert summary["promoted"]
    assert len(load_archive(archive)) == 4
    verify_frozen_round(tmp_path / "runtime/rounds/R001")


def test_runner_rejects_tampered_completed_stage_output(tmp_path):
    config, _registry = _project(tmp_path)
    adapter = FailureInjectionAdapter(
        DeterministicEvolutionAdapter(tmp_path / "runtime/fake"),
        fail_method="evaluate_blue",
    )
    runner = EvolutionRoundRunner(
        config, round_id="R001", adapter=adapter, project_root=tmp_path
    )
    with pytest.raises(RuntimeError, match="injected failure"):
        runner.run()
    path = tmp_path / "runtime/rounds/R001/red_candidates.jsonl"
    rows = path.read_text().splitlines()
    first = json.loads(rows[0])
    first["effect"] = "tampered"
    rows[0] = json.dumps(first)
    path.write_text("\n".join(rows) + "\n")
    with pytest.raises(RoundRunnerViolation, match="RED_GENERATE"):
        runner.run()


def test_interrupted_atomic_commit_recovers_idempotently(tmp_path):
    config, registry = _project(tmp_path)
    runner = EvolutionRoundRunner(
        config,
        round_id="R001",
        adapter=DeterministicEvolutionAdapter(tmp_path / "runtime/fake"),
        project_root=tmp_path,
    )
    real_checkpoint = runner._checkpoint
    interrupted = {"done": False}

    def interrupt_after_commit(stage, stage_input, stage_output):
        if stage == "ATOMIC_COMMIT" and not interrupted["done"]:
            interrupted["done"] = True
            raise RuntimeError("simulated death after registry replace")
        return real_checkpoint(stage, stage_input, stage_output)

    runner._checkpoint = interrupt_after_commit
    with pytest.raises(RuntimeError, match="simulated death"):
        runner.run()
    active_after_replace = get_active_policy(load_registry(registry))
    assert active_after_replace.policy_id.endswith("C01")
    runner._checkpoint = real_checkpoint
    summary = runner.run()
    assert summary["active_policy_hash"] == active_after_replace.policy_hash


def test_no_promotable_child_keeps_exact_parent(tmp_path):
    config, registry = _project(tmp_path)

    class NoPromotionAdapter(DeterministicEvolutionAdapter):
        def replay(self, policy, case, seed):
            row = super().replay(policy, case, seed)
            if case.get("challenged_policy_hash"):
                row["oracle_ok"] = False
            return row

    parent = get_active_policy(load_registry(registry))
    summary = EvolutionRoundRunner(
        config,
        round_id="R001",
        adapter=NoPromotionAdapter(tmp_path / "runtime/fake"),
        project_root=tmp_path,
    ).run()
    assert not summary["promoted"]
    assert summary["active_policy_hash"] == parent.policy_hash


def test_runner_routes_covered_and_unknown_learnability_out_of_residual(tmp_path):
    config, _registry = _project(tmp_path)

    class RoutingAdapter(DeterministicEvolutionAdapter):
        def evaluate_blue(self, policy, poison, seed):
            row = super().evaluate_blue(policy, poison, seed)
            if str(poison["poison_id"]).endswith("_0"):
                row["oracle_ok"] = True
            return row

        def probe_learnability(self, policy, poison):
            if str(poison["poison_id"]).endswith("_1"):
                return {
                    "label": "unknown",
                    "challenged_policy_hash": policy.policy_hash,
                    "teacher_mode": "same_model_expanded",
                    "teacher_budget": {
                        key: int(value) * 2
                        for key, value in policy.budgets.items()
                    },
                    "attempts": 1,
                    "successes": 0,
                    "budget_exhausted": False,
                    "evidence": {"adapter_mode": "deterministic_fake"},
                }
            return super().probe_learnability(policy, poison)

    summary = EvolutionRoundRunner(
        config,
        round_id="R001",
        adapter=RoutingAdapter(tmp_path / "runtime/fake"),
        project_root=tmp_path,
    ).run()
    round_dir = tmp_path / "runtime/rounds/R001"
    assert not summary["promoted"]
    assert len(
        (round_dir / "covered_archive_updates.jsonl").read_text().splitlines()
    ) == 1
    assert len((round_dir / "archive_exclusions.jsonl").read_text().splitlines()) == 1
    assert len((round_dir / "archive_updates.jsonl").read_text().splitlines()) == 2
    verify_frozen_round(round_dir)


def test_round_defers_promotion_when_residual_designs_are_insufficient(tmp_path):
    config, registry = _project(tmp_path)

    class CoveredAdapter(DeterministicEvolutionAdapter):
        def evaluate_blue(self, policy, poison, seed):
            row = super().evaluate_blue(policy, poison, seed)
            row["oracle_ok"] = True
            return row

        def probe_learnability(self, policy, poison):
            raise AssertionError("covered poison must not invoke teacher")

    parent = get_active_policy(load_registry(registry))
    summary = EvolutionRoundRunner(
        config,
        round_id="R001",
        adapter=CoveredAdapter(tmp_path / "runtime/fake"),
        project_root=tmp_path,
    ).run()
    assert not summary["promoted"]
    assert not summary["promotion_eligible"]
    assert summary["defer_reason"] == "insufficient_residual_designs"
    assert summary["active_policy_hash"] == parent.policy_hash
    round_dir = tmp_path / "runtime/rounds/R001"
    assert json.loads((round_dir / "target_manifest.json").read_text())["row_count"] == 0
    assert len(
        (round_dir / "covered_archive_updates.jsonl").read_text().splitlines()
    ) == 4
    verify_frozen_round(round_dir)


def test_event_streams_are_hash_chained_and_tamper_evident(tmp_path):
    config, _registry = _project(tmp_path)
    EvolutionRoundRunner(
        config,
        round_id="R001",
        adapter=DeterministicEvolutionAdapter(tmp_path / "runtime/fake"),
        project_root=tmp_path,
    ).run()
    for name in ("policy", "red", "oracle", "arena"):
        assert read_events(tmp_path / f"runtime/events/{name}.jsonl")
    assert (tmp_path / "runtime/events/rollback.jsonl").is_file()
    policy_events = read_events(tmp_path / "runtime/events/policy.jsonl")
    promoted = [row for row in policy_events if row["event_type"] == "policy_promoted"]
    assert promoted and promoted[0]["parent_policy_hash"]
    path = tmp_path / "runtime/events/red.jsonl"
    rows = path.read_text().splitlines()
    tampered = json.loads(rows[0])
    tampered["candidate_count"] = 99
    rows[0] = json.dumps(tampered)
    path.write_text("\n".join(rows) + "\n")
    with pytest.raises(EventViolation, match="event hash mismatch"):
        read_events(path)


def test_rollback_event_records_exact_parent_restore(tmp_path):
    registry, parent = _init(tmp_path)
    child = _children(parent, 1)[0]
    events = EventLogger(tmp_path / "events", code_version="test")
    register_candidate(registry, child)
    promote_policy(registry, child.policy_id, _strong_decision(parent, child))
    restored = rollback_policy(registry, event_logger=events, round_id="R900")
    assert get_active_policy(restored).policy_hash == parent.policy_hash
    event = read_events(tmp_path / "events/rollback.jsonl")[0]
    assert event["restored_policy_hash"] == parent.policy_hash


def test_archive_keeps_effect_elites_dedupes_and_rejects_inconclusive(tmp_path):
    path = tmp_path / "archive.jsonl"
    first = update_archive(path, _archive_poison())
    assert update_archive(path, _archive_poison()) == first
    update_archive(
        path,
        _archive_poison(
            poison_id="p1",
            effect="wrong_enable",
            failure_signature="enable:0",
            normalized_diff_hash="diff1",
        ),
    )
    assert len(load_archive(path)) == 2
    with pytest.raises(ArchiveViolation, match="inconclusive"):
        update_archive(
            path,
            _archive_poison(
                poison_id="p2",
                validity={
                    "proven_valid": True,
                    "evidence": {"formal_status": "INCONCLUSIVE"},
                },
            ),
        )


def test_map_elites_keeps_distinct_objective_winners():
    common = {
        "challenged_policy_hash": "sha256:" + "a" * 64,
        "family": "off_by_one",
        "effect": "terminal",
        "affected_role": "control",
        "edit_scope": "expression",
        "first_divergence_cycle_bucket": "same_cycle",
        "hardness_class": "hard_residual",
    }
    rows = [
        {
            **common,
            "poison_id": "hard",
            "hardness": 1.0,
            "novelty": 0.2,
            "normalized_edit_cost": 5,
            "learnability": {"label": "reachable"},
        },
        {
            **common,
            "poison_id": "small",
            "hardness": 0.7,
            "novelty": 1.0,
            "normalized_edit_cost": 1,
            "learnability": {"label": "weakly_reachable"},
        },
        {
            **common,
            "poison_id": "dominated",
            "hardness": 0.5,
            "novelty": 0.1,
            "normalized_edit_cost": 7,
            "learnability": {"label": "unknown"},
        },
    ]
    view = next(iter(materialize_elites(rows).values()))
    assert view["hardest"]["poison_id"] == "hard"
    assert view["minimal_edit"]["poison_id"] == "small"
    assert view["most_learnable"]["poison_id"] == "hard"
    assert {row["poison_id"] for row in pareto_frontier(rows)} == {"hard", "small"}
    assert {
        row["poison_id"] for row in select_residual_elites(rows)
    } == {"hard", "small"}


def test_covered_archive_is_separate_from_adaptation_archive(tmp_path):
    covered = _archive_poison(
        poison_id="covered",
        hardness=0.0,
        hardness_class="covered",
        learnability=None,
    )
    with pytest.raises(ArchiveViolation, match="residual archive"):
        update_archive(tmp_path / "residual.jsonl", covered)
    accepted = update_archive(
        tmp_path / "covered.jsonl",
        covered,
        archive_kind="covered",
    )
    assert accepted["archive_kind"] == "covered"


def test_learnability_teacher_budget_is_hash_bound_and_separated():
    policy = PolicyState.from_dict(
        json.loads(
            (ROOT / "configs/base_policy/frozen_base_policy_v1.json").read_text()
        )
    )
    poison = {
        "poison_id": "p0",
        "challenged_policy_hash": policy.policy_hash,
    }
    raw = {
        "label": "reachable",
        "challenged_policy_hash": policy.policy_hash,
        "teacher_mode": "same_model_expanded",
        "teacher_budget": {
            key: int(value) * 2 for key, value in policy.budgets.items()
        },
        "attempts": 2,
        "successes": 1,
        "budget_exhausted": False,
        "evidence": {"teacher": "fake"},
    }
    result = validate_learnability_result(policy, poison, raw)
    assert verify_learnability_result(result) == result
    tampered = dict(result)
    tampered["successes"] = 0
    with pytest.raises(LearnabilityViolation, match="no success|hash mismatch"):
        verify_learnability_result(tampered)
    unexpanded = dict(raw)
    unexpanded["teacher_budget"] = dict(policy.budgets)
    with pytest.raises(LearnabilityViolation, match="strictly expand"):
        validate_learnability_result(policy, poison, unexpanded)
    wrong_policy = dict(raw)
    wrong_policy["challenged_policy_hash"] = "sha256:" + "f" * 64
    with pytest.raises(LearnabilityViolation, match="wrong policy"):
        validate_learnability_result(policy, poison, wrong_policy)


def test_round_audit_reconstructs_decisions_and_round_ledger_is_idempotent(tmp_path):
    config, _registry = _project(tmp_path)
    runner = EvolutionRoundRunner(
        config,
        round_id="R001",
        adapter=DeterministicEvolutionAdapter(tmp_path / "runtime/fake"),
        project_root=tmp_path,
    )
    summary = runner.run()
    audit = verify_frozen_round(tmp_path / "runtime/rounds/R001")
    assert audit["audit_record_hash"] == summary["audit_record_hash"]
    assert audit["paired_result_hash"]
    ledger = read_ledger(tmp_path / "runtime/rounds/round_ledger.jsonl")
    assert len(ledger) == 1
    assert runner.run() == summary
    assert len(read_ledger(tmp_path / "runtime/rounds/round_ledger.jsonl")) == 1


def test_round_audit_and_run_context_reject_tampering(tmp_path):
    config, _registry = _project(tmp_path)
    EvolutionRoundRunner(
        config,
        round_id="R001",
        adapter=DeterministicEvolutionAdapter(tmp_path / "runtime/fake"),
        project_root=tmp_path,
    ).run()
    round_dir = tmp_path / "runtime/rounds/R001"
    context_path = round_dir / "toolchain.json"
    context = json.loads(context_path.read_text())
    context["toolchain_fingerprint"]["model_calls"] = 99
    context_path.write_text(json.dumps(context))
    with pytest.raises(RunContextViolation, match="fingerprint hash mismatch"):
        verify_run_context(context)
    with pytest.raises(RunContextViolation):
        verify_frozen_round(round_dir)


def test_round_audit_rejects_paired_replay_tampering(tmp_path):
    config, _registry = _project(tmp_path)
    EvolutionRoundRunner(
        config,
        round_id="R001",
        adapter=DeterministicEvolutionAdapter(tmp_path / "runtime/fake"),
        project_root=tmp_path,
    ).run()
    round_dir = tmp_path / "runtime/rounds/R001"
    paired = round_dir / "paired_validation.jsonl"
    lines = paired.read_text().splitlines()
    row = json.loads(lines[0])
    row["oracle_ok"] = not row["oracle_ok"]
    lines[0] = json.dumps(row)
    paired.write_text("\n".join(lines) + "\n")
    with pytest.raises(
        RoundAuditViolation,
        match="provenance mismatch|cannot be reconstructed",
    ):
        verify_frozen_round(round_dir)


def test_lineage_cycle_is_rejected():
    with pytest.raises(ValueError, match="cycle"):
        validate_lineage_graph([
            {"poison_id": "a", "parent_poison_id": "b"},
            {"poison_id": "b", "parent_poison_id": "a"},
        ])


def test_generate_poison_requires_policy_bound_packet():
    parent = PolicyState.from_dict(
        json.loads(
            (ROOT / "configs/base_policy/frozen_base_policy_v1.json").read_text()
        )
    )
    packet = build_capability_packet(
        parent,
        design_id="d0",
        golden_rtl_hash="sha256:" + "1" * 64,
        allowed_mutation_operators=["constant_flip"],
        archive_rows=[],
        recent_challenges=[],
    )
    poison = generate_poison(
        {"design_id": "d0"},
        parent,
        packet,
        [],
        mutator=lambda **kwargs: {
            "poison_id": "p0",
            "archive_seen": len(kwargs["archive"]),
        },
    )
    assert poison["challenged_policy_hash"] == parent.policy_hash
    bad = dict(packet)
    bad["challenged_policy_hash"] = "sha256:" + "2" * 64
    with pytest.raises(ValueError, match="not bound"):
        generate_poison({}, parent, bad, [], mutator=lambda **_: {})


def test_red_search_context_whitelists_archive_fields():
    parent = PolicyState.from_dict(
        json.loads(
            (ROOT / "configs/base_policy/frozen_base_policy_v1.json").read_text()
        )
    )
    with pytest.raises(CapabilityPacketViolation, match="hidden fields"):
        build_red_search_context(
            parent,
            residual_archive=[{
                **_archive_poison(challenged_policy_hash=parent.policy_hash),
                "reference_patch": "must not leak",
            }],
            covered_archive=[],
        )


def test_formal_policy_bounds_and_prompt_asset_tamper_fail_closed(tmp_path):
    raw = json.loads(
        (ROOT / "configs/base_policy/frozen_base_policy_v1.json").read_text()
    )
    raw["configuration"]["evidence_k"] = 2
    with pytest.raises(Exception, match="evidence_k"):
        PolicyState.from_dict(raw)

    registry, _parent = _init(tmp_path)
    prompt_root = tmp_path / "configs/base_policy/prompt_templates"
    prompt_root.mkdir(parents=True)
    for source in (
        ROOT / "configs/base_policy/prompt_templates"
    ).glob("*.txt"):
        (prompt_root / source.name).write_text(
            source.read_text(encoding="utf-8"), encoding="utf-8"
        )
    (prompt_root / "generic_v1.txt").write_text("tampered", encoding="utf-8")
    with pytest.raises(PolicyRuntimeViolation, match="hash mismatch"):
        PolicyRuntime.from_registry(registry, project_root=tmp_path)


def test_formal_repair_budget_exhaustion_discards_candidate(monkeypatch, tmp_path):
    raw = json.loads(
        (ROOT / "configs/base_policy/frozen_base_policy_v1.json").read_text()
    )
    raw["configuration"]["n_candidates"] = 6
    raw["budgets"]["max_llm_calls_per_case"] = 1
    raw["budgets"]["max_tokens_per_case"] = 1
    raw["base_policy_hash"] = hash_payload({
        "schema_version": raw["schema_version"],
        "configuration": raw["configuration"],
        "budgets": raw["budgets"],
        "frozen_assets": raw["frozen_assets"],
    })
    policy = PolicyState.from_dict(raw)
    buggy = tmp_path / "buggy.v"
    buggy.write_text("module top(output y); assign y=1; endmodule\n")
    calls = {"propose": 0, "apply": 0}

    def fake_judge(*_args, **_kwargs):
        return type("Outcome", (), {
            "ok": False,
            "stage": "compare",
            "mismatch": "y mismatch",
            "err": "",
            "structured": "",
            "detail": {},
        })()

    def fake_propose(*_args, **_kwargs):
        calls["propose"] += 1
        return {
            "start_line": 1,
            "end_line": 1,
            "new_code": "module top(output y); assign y=0; endmodule",
            "_formal_prompt_chars": 100,
            "_formal_response_chars": 100,
        }

    def fake_apply(*_args, **_kwargs):
        calls["apply"] += 1
        raise AssertionError("over-budget candidate must not be applied")

    monkeypatch.setattr(functional_repair, "judge", fake_judge)
    monkeypatch.setattr(functional_repair, "propose", fake_propose)
    monkeypatch.setattr(functional_repair, "apply_block", fake_apply)
    result = functional_repair.repair_one(
        {
            "design_name": "budget",
            "top_module": "top",
            "buggy_rtl": str(buggy),
        },
        tmp_path / "work",
        policy=policy,
        formal_mode=True,
    )
    assert not result["repaired"]
    assert result["budget_exhausted"] == "max_tokens_per_case"
    assert calls == {"propose": 1, "apply": 0}


def test_legacy_migration_freezes_skills_without_promoting_history(tmp_path):
    old_registry = tmp_path / "old_registry.json"
    atomic_write_json(
        old_registry,
        {"artifacts": [{"artifact_id": "S1", "runtime_status": "promoted"}]},
    )
    report = migrate_legacy(
        legacy_skills_path=ROOT / "configs/skills.json",
        base_template_path=ROOT / "configs/base_policy/frozen_base_policy_v1.json",
        base_output_path=tmp_path / "migration/base.json",
        registry_path=tmp_path / "registry/policy_registry.json",
        report_path=tmp_path / "migration/report.json",
        legacy_registry_path=old_registry,
    )
    registry = load_registry(tmp_path / "registry/policy_registry.json")
    assert set(registry["policies"]) == {"B0"}
    assert report["automatic_promotions_migrated"] == 0
    assert {
        row["disposition"] for row in report["manual_skills"]
    } == {"frozen_base_asset_no_promotion_authority"}
    assert report["legacy_registry_artifacts"][0]["disposition"].startswith("excluded")
