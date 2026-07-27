from __future__ import annotations

import json
from pathlib import Path

import pytest

from r3e.arena.manifests import ManifestViolation, grouped_split, make_manifest
from r3e.arena.runner import EvolutionRoundRunner
from r3e.arena.round_state import RoundState, RoundStateViolation
from r3e.policy.promotion import decide_policy_promotion
from r3e.policy.registry_v2 import (
    RegistryViolation,
    get_active_policy,
    initialize_registry,
    load_registry,
    promote_policy,
    register_candidate,
    rollback_policy,
)
from r3e.policy.schema import PolicyState
from r3e.policy.search import propose_children
from r3e.protocol.hashing import hash_payload
from semantic_repair_bench import functional_repair


ROOT = Path(__file__).resolve().parents[1]


def _init(tmp_path: Path):
    registry = tmp_path / "policy_registry.json"
    initialize_registry(
        ROOT / "configs/base_policy/frozen_base_policy_v1.json",
        registry,
    )
    loaded = load_registry(registry)
    return registry, get_active_policy(loaded)


def _child(parent: PolicyState, tmp_path: Path) -> PolicyState:
    adaptation = make_manifest(
        [
            {"case_id": "a", "design": "d0", "sequential_depth": 1},
            {"case_id": "b", "design": "d1", "sequential_depth": 1},
        ],
        split="adaptation",
    )
    space = json.loads(
        (ROOT / "configs/base_policy/policy_search_space_v1.json").read_text()
    )
    return propose_children(parent, adaptation, space, round_id="R001", seed=1)[0]


def _decision(parent: PolicyState, child: PolicyState):
    rows = []
    for split, cases in (
        ("target", [("t0", "d2"), ("t1", "d3")]),
        ("non_target", [("n0", "d4"), ("n1", "d5")]),
    ):
        for case_id, design in cases:
            parent_ok = split == "non_target"
            for arm, ok in (("parent", parent_ok), ("candidate", True)):
                rows.append({
                    "case_id": case_id,
                    "design": design,
                    "seed": 7,
                    "split": split,
                    "arm": arm,
                    "oracle_ok": ok,
                    "model_id": "frozen-model",
                    "budget_hash": "budget",
                    "verifier_hash": "verifier",
                    "cost": 1.0,
                })
    return decide_policy_promotion(
        parent,
        child,
        rows,
        validation_manifest_hash="sha256:validation",
        provenance={
            "round_id": "R001",
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


def test_registry_single_active_promote_and_rollback(tmp_path):
    registry, parent = _init(tmp_path)
    child = _child(parent, tmp_path)
    register_candidate(registry, child)
    promoted = promote_policy(registry, child.policy_id, _decision(parent, child))
    assert get_active_policy(promoted).policy_id == child.policy_id
    assert sum(
        entry["status"] == "active" for entry in promoted["policies"].values()
    ) == 1
    restored = rollback_policy(registry)
    assert get_active_policy(restored).policy_id == parent.policy_id


def test_registry_rejects_stale_child(tmp_path):
    registry, parent = _init(tmp_path)
    first = _child(parent, tmp_path)
    register_candidate(registry, first)
    promote_policy(registry, first.policy_id, _decision(parent, first))
    stale_payload = first.to_dict()
    stale_payload["policy_id"] = "stale"
    stale = PolicyState.from_dict(stale_payload)
    with pytest.raises(RegistryViolation, match="current active parent"):
        register_candidate(registry, stale)


def test_policy_schema_rejects_runtime_free_text_hint():
    raw = json.loads(
        (ROOT / "configs/base_policy/frozen_base_policy_v1.json").read_text()
    )
    raw["configuration"]["manual_hint"] = "look for the hidden target literal"
    with pytest.raises(Exception, match="undeclared configuration"):
        PolicyState.from_dict(raw)


def test_grouped_split_is_design_disjoint():
    residual = make_manifest(
        [
            {"case_id": "a0", "design": "a"},
            {"case_id": "a1", "design": "a"},
            {"case_id": "b0", "design": "b"},
            {"case_id": "c0", "design": "c"},
        ],
        split="residual",
    )
    adaptation, target = grouped_split(residual, seed=3)
    left = {row["design"] for row in adaptation["rows"]}
    right = {row["design"] for row in target["rows"]}
    assert left and right and left.isdisjoint(right)


def test_grouped_split_requires_two_designs():
    residual = make_manifest([{"case_id": "a", "design": "only"}], split="residual")
    with pytest.raises(ManifestViolation):
        grouped_split(residual)


def test_round_state_rejects_stage_skip(tmp_path):
    state = RoundState(tmp_path / "state.json", round_id="R001")
    state.initialize({"x": 1})
    with pytest.raises(RoundStateViolation, match="expected stage INIT"):
        state.complete_stage("LOAD_ACTIVE_POLICY", stage_input={}, stage_output={})
    state.complete_stage("INIT", stage_input={}, stage_output={})
    assert state.next_stage() == "LOAD_ACTIVE_POLICY"


def test_minimal_round_promotes_and_binds_renewed_challenge(tmp_path):
    project = tmp_path
    (project / "configs/base_policy").mkdir(parents=True)
    (project / "runtime/registry").mkdir(parents=True)
    base = json.loads(
        (ROOT / "configs/base_policy/frozen_base_policy_v1.json").read_text()
    )
    space = json.loads(
        (ROOT / "configs/base_policy/policy_search_space_v1.json").read_text()
    )
    (project / "configs/base_policy/base.json").write_text(json.dumps(base))
    (project / "configs/base_policy/search.json").write_text(json.dumps(space))
    registry_path = project / "runtime/registry/policy_registry.json"
    initialize_registry(project / "configs/base_policy/base.json", registry_path)
    non_target = make_manifest(
        [
            {"case_id": "n0", "design": "nd0"},
            {"case_id": "n1", "design": "nd1"},
        ],
        split="non_target",
    )
    (project / "non_target.json").write_text(json.dumps(non_target))
    rtl_dir = project / "rtl"
    rtl_dir.mkdir()

    class Adapter:
        toolchain_fingerprint = {"adapter": "test"}

        def generate_red(self, parent, _config, _red_context):
            rows = []
            for index in range(4):
                golden = rtl_dir / f"g{index}.v"
                buggy = rtl_dir / f"b{index}.v"
                golden.write_text("module top(output y); assign y=0; endmodule\n")
                buggy.write_text("module top(output y); assign y=1; endmodule\n")
                rows.append({
                    "poison_id": f"p{index}",
                    "case_id": f"p{index}",
                    "design": f"d{index}",
                    "golden_rtl": str(golden),
                    "buggy_rtl": str(buggy),
                    "challenged_policy_hash": parent.policy_hash,
                    "family": "off_by_one",
                    "effect": f"effect{index}",
                    "affected_role": "control",
                    "edit_scope": "local_block",
                    "composition_depth": 1,
                    "sequential_depth": 1,
                    "first_divergence_cycle_bucket": "one_cycle",
                    "failure_signature": f"signature{index}",
                    "normalized_diff_hash": f"diff{index}",
                })
            return rows

        @staticmethod
        def prepare_validity(poison):
            return {
                **poison,
                "golden_compile_ok": True,
                "golden_oracle_ok": True,
                "buggy_compile_ok": True,
                "buggy_functional_fail": True,
                "formal_status": "PROVEN_NON_EQUIV",
                "output_complete": True,
                "revert_oracle_ok": True,
                "fresh_output": True,
            }

        @staticmethod
        def evaluate_blue(policy, _poison, seed):
            return {
                "policy_hash": policy.policy_hash,
                "seed": seed,
                "oracle_ok": False,
            }

        @staticmethod
        def probe_learnability(policy, poison):
            return {
                "label": "reachable",
                "challenged_policy_hash": policy.policy_hash,
                "teacher_mode": "same_model_expanded",
                "teacher_budget": {
                    key: int(value) * 2 for key, value in policy.budgets.items()
                },
                "attempts": 1,
                "successes": 1,
                "budget_exhausted": False,
                "evidence": {"poison_id": poison["poison_id"], "fixture": True},
            }

        @staticmethod
        def screen_child(_parent, child, _adaptation):
            return {"survive": child.policy_id.endswith("C01")}

        @staticmethod
        def replay(policy, case, seed):
            is_candidate = policy.policy_id != "B0"
            is_target = str(case["case_id"]).startswith("p")
            return {
                "policy_hash": policy.policy_hash,
                "seed": seed,
                "oracle_ok": is_candidate or not is_target,
                "model_id": "fixed",
                "budget_hash": "fixed-budget",
                "verifier_hash": "fixed-verifier",
                "cost": 1.0,
            }

    config = {
        "policy_registry": "runtime/registry/policy_registry.json",
        "policy_search_space": "configs/base_policy/search.json",
        "non_target_manifest": "non_target.json",
        "challenge_seeds": [1, 2, 3],
        "promotion_seeds": [11, 12, 13],
        "split_seed": 4,
    }
    summary = EvolutionRoundRunner(
        config,
        round_id="R001",
        adapter=Adapter(),
        project_root=project,
    ).run()
    assert summary["promoted"] is True
    assert summary["active_policy_id"].endswith("C01")
    binding = json.loads(
        (project / "runtime/rounds/R001/renewed_challenge_binding.json").read_text()
    )
    assert binding["challenged_policy_hash"] == summary["active_policy_hash"]


def test_formal_repair_records_effective_policy_and_blocks_memory(monkeypatch, tmp_path):
    policy = PolicyState.from_dict(
        json.loads(
            (ROOT / "configs/base_policy/frozen_base_policy_v1.json").read_text()
        )
    )
    buggy = tmp_path / "buggy.v"
    buggy.write_text("module top(output y); assign y=1; endmodule\n")
    calls = []

    def fake_judge(_case, candidate, _work, **_kwargs):
        patched = str(candidate).endswith("patched_c0.v")
        return type("Outcome", (), {
            "ok": patched,
            "stage": "compare",
            "mismatch": "" if patched else "y@cycle0: got 1, expected 0",
            "err": "",
            "structured": "",
            "detail": {
                "command_hash": "sha256:command",
                "toolchain_fingerprint_hash": "sha256:tools",
            },
        })()

    def fake_propose(_rtl, _evidence, _memory="", prompt_lens=""):
        calls.append(prompt_lens)
        return {
            "start_line": 1,
            "end_line": 1,
            "new_code": "module top(output y); assign y=0; endmodule",
            "_formal_prompt_chars": 100,
            "_formal_response_chars": 40,
        }

    def fake_apply(_rtl, _start, _end, _code, output):
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("module top(output y); assign y=0; endmodule\n")
        return path

    monkeypatch.setattr(functional_repair, "judge", fake_judge)
    monkeypatch.setattr(functional_repair, "propose", fake_propose)
    monkeypatch.setattr(functional_repair, "apply_block", fake_apply)
    case = {
        "design_name": "demo",
        "top_module": "top",
        "buggy_rtl": str(buggy),
    }
    result = functional_repair.repair_one(
        case,
        tmp_path / "work",
        policy=policy,
        formal_mode=True,
    )
    assert result["repaired"] is True
    assert result["policy_hash"] == policy.policy_hash
    assert result["budget_usage"]["llm_calls"] == 1
    assert calls and "minimal" in calls[0]
    with pytest.raises(Exception, match="forbids recall_fn"):
        functional_repair.repair_one(
            case,
            tmp_path / "blocked",
            recall_fn=lambda _case: "manual memory",
            policy=policy,
            formal_mode=True,
        )
