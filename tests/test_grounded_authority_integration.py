from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from r3e.arena.audit import RoundAuditViolation, verify_frozen_round
from r3e.arena.conformance import bind_adapter_output
from r3e.arena.fake_adapters import (
    DETERMINISTIC_TOOLCHAIN_FINGERPRINT,
    DeterministicEvolutionAdapter,
)
from r3e.arena.manifests import make_manifest
from r3e.arena.runner import EvolutionRoundRunner, RoundRunnerViolation
from r3e.arena.grounded_authority import (
    GroundedAuthorityIntegrationViolation,
    execute_grounded_arena_validity,
)
from r3e.memory.episode_store import EpisodeStore
from r3e.memory.episode_builder import (
    EpisodeBuildViolation,
    episode_from_challenge,
)
from r3e.policy.schema import PolicyState
from r3e.policy.registry_v2 import initialize_registry
from r3e.protocol.hashing import atomic_write_json, hash_file, hash_payload
from r3e.red.grounded.materializers import materialize_operator
from r3e.red.grounded.arena_validity import (
    verify_legacy_grounded_arena_validity,
)
from r3e.red.grounded.mutation_plan import build_mutation_plan
from r3e.red.grounded.operator_ast import operator_nodes
from r3e.red.grounded.registry import load_grounded_registries
from r3e.red.operators import (
    load_operator_space,
    make_lineage_plan,
    materialize_fresh,
)


ROOT = Path(__file__).resolve().parents[1]
HAS_ICARUS = bool(shutil.which("iverilog") and shutil.which("vvp"))
HAS_YOSYS = bool(shutil.which("yosys"))


def test_grounded_authority_milestone_is_hash_bound():
    milestone = json.loads(
        (
            ROOT
            / "configs/evolution/grounded_authority_integration_v1.json"
        ).read_text(encoding="utf-8")
    )
    parent = json.loads(
        (
            ROOT
            / "configs/evolution/grounded_red_grd2_formal_v1.json"
        ).read_text(encoding="utf-8")
    )
    assert milestone["milestone_hash"] == hash_payload({
        key: value
        for key, value in milestone.items()
        if key != "milestone_hash"
    })
    assert milestone["parent_milestone"] == {
        "milestone_id": parent["milestone_id"],
        "milestone_hash": parent["milestone_hash"],
    }
    for relative, expected in milestone["frozen_assets"].items():
        assert hash_file(ROOT / relative) == expected

    closure = json.loads(
        (
            ROOT
            / (
                "configs/evolution/"
                "grounded_runtime_authority_closure_v1.json"
            )
        ).read_text(encoding="utf-8")
    )
    assert closure["milestone_hash"] == hash_payload({
        key: value
        for key, value in closure.items()
        if key != "milestone_hash"
    })
    assert closure["parent_milestone"] == {
        "milestone_id": milestone["milestone_id"],
        "milestone_hash": milestone["milestone_hash"],
    }
    for relative, expected in closure["frozen_assets"].items():
        assert hash_file(ROOT / relative) == expected

    waveform = json.loads(
        (
            ROOT
            / (
                "configs/evolution/"
                "grounded_waveform_rejection_v1.json"
            )
        ).read_text(encoding="utf-8")
    )
    assert waveform["milestone_hash"] == hash_payload({
        key: value
        for key, value in waveform.items()
        if key != "milestone_hash"
    })
    assert waveform["parent_milestone"] == {
        "milestone_id": closure["milestone_id"],
        "milestone_hash": closure["milestone_hash"],
    }
    sequential_successor = json.loads(
        (
            ROOT
            / (
                "configs/evolution/"
                "grounded_sequential_runtime_gate_v1.json"
            )
        ).read_text(encoding="utf-8")
    )
    population_successor = json.loads(
        (
            ROOT
            / (
                "configs/evolution/"
                "grounded_deterministic_population_v1.json"
            )
        ).read_text(encoding="utf-8")
    )
    for relative, expected in waveform["frozen_assets"].items():
        current = hash_file(ROOT / relative)
        if current != expected:
            assert current in {
                sequential_successor["frozen_assets"].get(relative),
                population_successor["frozen_assets"].get(relative),
            }


def test_grounded_authority_requires_formal_property(tmp_path):
    for name in ("clean.v", "poison.v", "tb.v"):
        (tmp_path / name).write_text(
            "module placeholder; endmodule\n",
            encoding="utf-8",
        )
    policy = PolicyState.from_dict(json.loads(
        (
            ROOT
            / "configs/base_policy/frozen_base_policy_v1.json"
        ).read_text(encoding="utf-8")
    ))
    registries = load_grounded_registries(
        family_registry=(
            ROOT / "configs/red/grounded_family_registry_v1.json"
        ),
        operator_registry=(
            ROOT / "configs/red/grounded_operator_registry_v1.json"
        ),
        effect_registry=(
            ROOT / "configs/red/grounded_effect_registry_v1.json"
        ),
    )
    with pytest.raises(
        GroundedAuthorityIntegrationViolation,
        match="formal property is missing",
    ):
        execute_grounded_arena_validity(
            poison={
                "golden_rtl": str(tmp_path / "clean.v"),
                "buggy_rtl": str(tmp_path / "poison.v"),
                "grounded_testbench": str(tmp_path / "tb.v"),
                "grounded_formal_property": str(
                    tmp_path / "missing_property.v"
                ),
            },
            policy=policy,
            registries=registries,
            project_root=tmp_path,
            round_dir=tmp_path / "round",
            run_context_hash=hash_payload({"test": "missing-formal"}),
        )


class GroundedRoundAdapter(DeterministicEvolutionAdapter):
    def __init__(
        self,
        workspace: Path,
        *,
        formal_rejection_indices: set[int] | None = None,
        formal_tool_error_indices: set[int] | None = None,
    ):
        super().__init__(workspace)
        self.workspace = workspace
        self.formal_rejection_indices = (
            formal_rejection_indices or set()
        )
        self.formal_tool_error_indices = (
            formal_tool_error_indices or set()
        )
        self.prepare_validity_called = False
        self.registries = load_grounded_registries(
            family_registry=(
                ROOT / "configs/red/grounded_family_registry_v1.json"
            ),
            operator_registry=(
                ROOT / "configs/red/grounded_operator_registry_v1.json"
            ),
            effect_registry=(
                ROOT / "configs/red/grounded_effect_registry_v1.json"
            ),
        )
        self.lineage_space = load_operator_space(
            ROOT / "configs/red/lineage_operator_space_v1.json"
        )

    def generate_red(self, parent, _config, _red_search_context):
        rows = []
        source_root = self.workspace / "grounded_sources"
        source_root.mkdir(parents=True, exist_ok=True)
        proposal = _red_search_context.get("grounded_proposal_plan")
        selected_intents = []
        if proposal is not None:
            selected = set(proposal["selected_intent_ids"])
            selected_intents = [
                row for row in proposal["candidate_intents"]
                if row["intent_id"] in selected
            ]
            selected_intents.sort(
                key=lambda row: proposal["selected_intent_ids"].index(
                    row["intent_id"]
                )
            )
        limits = tuple(
            4 + index for index in range(len(selected_intents))
        ) if selected_intents else (4, 5)
        population = _red_search_context.get(
            "grounded_population_schedule"
        )
        assignments = {
            row["intent_id"]: row
            for row in (
                population["assignments"] if population else []
            )
        }
        for index, limit in enumerate(limits):
            poison_id = f"grounded_arena_{index}"
            clean = source_root / f"clean_{index}.v"
            poison = source_root / f"poison_{index}.v"
            testbench = source_root / f"tb_{index}.v"
            formal_property = source_root / f"property_{index}.v"
            clean_source = f"""module counter(
  input wire [3:0] count,
  output wire done
);
assign done = count < {limit};
endmodule
"""
            clean.write_text(clean_source, encoding="utf-8")
            testbench.write_text(
                f"""module tb;
reg [3:0] count;
wire done;
integer index;
integer mismatches;
integer first_bad;
counter dut(.count(count), .done(done));
initial begin
  mismatches = 0;
  first_bad = -1;
  for (index = 0; index < 8; index = index + 1) begin
    count = index;
    #1;
    if (done !== (index < {limit})) begin
      mismatches = mismatches + 1;
      if (first_bad < 0) first_bad = index;
    end
  end
  if (mismatches == 0) begin
    $display("R3E_ORACLE pass=1 signature=none first=none topology=none");
    $display("R3E_WAVEFORM signal=none first_cycle=none cycle_offset=none relation=none assignment=none cone_depth=none pattern=none");
  end else begin
    $display("R3E_ORACLE pass=0 signature=boundary_{index} first=cycle_{limit} topology=done");
    $display("R3E_WAVEFORM signal=done first_cycle={limit} cycle_offset=0 relation=same_cycle assignment=continuous cone_depth=1 pattern=boundary_value_mismatch");
  end
  $finish;
end
endmodule
""",
                encoding="utf-8",
            )
            formal_property.write_text(
                (
                    "module formal_top; syntax is invalid; endmodule\n"
                    if index in self.formal_tool_error_indices
                    else f"""module formal_top;
  (* anyconst *) reg [3:0] count;
  wire done;
  counter dut(.count(count), .done(done));
  always @* assert(done == (count <= {limit}));
endmodule
"""
                    if index in self.formal_rejection_indices
                    else f"""module formal_top;
  (* anyconst *) reg [3:0] count;
  wire done;
  counter dut(.count(count), .done(done));
  always @* assert(done == (count < {limit}));
endmodule
"""
                ),
                encoding="utf-8",
            )
            node = operator_nodes(
                clean_source,
                module="counter",
                operator_id="replace_comparator",
            )[0]
            plan = build_mutation_plan(
                plan_id=poison_id,
                policy=parent,
                registries=self.registries,
                target_design=f"grounded_design_{index}",
                target_module="counter",
                target_ast_node_hash=node.node_hash,
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
            materialized = materialize_operator(
                plan, clean_source=clean_source
            )
            poison.write_text(
                materialized.poison_source, encoding="utf-8"
            )
            descriptor = {
                "poison_id": poison_id,
                "case_id": poison_id,
                "design": f"grounded_design_{index}",
                "golden_rtl": str(clean),
                "buggy_rtl": str(poison),
                "challenged_policy_id": parent.policy_id,
                "challenged_policy_hash": parent.policy_hash,
                "family": "combinational.comparator_boundary",
                "effect": "wrong_combinational_value",
                "affected_role": "output" if index == 0 else "control",
                "edit_scope": "comparison_expression",
                "composition_depth": 1,
                "changed_modules": 1,
                "changed_blocks": 1,
                "sequential_depth": 0,
                "first_divergence_signal": "done",
                "first_divergence_cycle_bucket": "combinational",
                "failure_signature": f"boundary_{index}",
                "normalized_diff_hash": materialized.semantic_diff_receipt[
                    "receipt_hash"
                ],
                "grounded_mutation_plan": plan,
                "grounded_plan_hash": plan["plan_hash"],
                "grounded_testbench": str(testbench),
                "grounded_top_module": "tb",
                "grounded_formal_property": str(formal_property),
                "grounded_formal_top_module": "formal_top",
                "grounded_formal_depth": 1,
            }
            lineage = make_lineage_plan(
                parent,
                operator_space=self.lineage_space,
                operator="fresh",
                poison_id=poison_id,
            )
            row = materialize_fresh(lineage, descriptor)
            if selected_intents:
                intent = selected_intents[index]
                row.update({
                    "family": intent["family_id"],
                    "effect": intent["expected_runtime_effect_id"],
                    "affected_role": intent["target_role"],
                    "grounded_proposal_intent_id": intent["intent_id"],
                    "grounded_proposal_intent_hash": intent["intent_hash"],
                    "grounded_dispatch_kind": intent["dispatch_kind"],
                    "grounded_difficulty_target": dict(
                        intent["difficulty_target"]
                    ),
                    "grounded_proposal_parent_poison_ids": list(
                        intent["parent_poison_ids"]
                    ),
                    "grounded_proposal_target_memory_ids": list(
                        intent["target_memory_ids"]
                    ),
                    "grounded_proposal_memory_operator": intent[
                        "memory_operator"
                    ],
                })
                assignment = assignments.get(intent["intent_id"])
                if assignment is not None:
                    row.update({
                        "grounded_population_assignment_id": (
                            assignment["assignment_id"]
                        ),
                        "grounded_population_assignment_hash": (
                            assignment["assignment_hash"]
                        ),
                        "grounded_generator_provider_id": (
                            assignment["provider_id"]
                        ),
                        "grounded_generator_role": (
                            assignment["provider_role"]
                        ),
                        "grounded_population_arm": (
                            assignment["population_arm"]
                        ),
                        "grounded_generation_usage": {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "wall_time_ms": 0,
                        },
                    })
            rows.append(bind_adapter_output(
                row,
                "generate_red",
                DETERMINISTIC_TOOLCHAIN_FINGERPRINT,
            ))
        return rows

    def prepare_validity(self, _poison):
        self.prepare_validity_called = True
        raise AssertionError(
            "Grounded authority must not call adapter prepare_validity"
        )


@pytest.mark.skipif(
    not (HAS_ICARUS and HAS_YOSYS),
    reason="Icarus or Yosys is unavailable",
)
def test_arena_uses_runner_owned_grounded_authority(tmp_path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "runtime/registry").mkdir(parents=True)
    base = ROOT / "configs/base_policy/frozen_base_policy_v1.json"
    search = ROOT / "configs/base_policy/policy_search_space_v1.json"
    base_copy = tmp_path / "configs/base.json"
    search_copy = tmp_path / "configs/search.json"
    base_copy.write_text(base.read_text(encoding="utf-8"), encoding="utf-8")
    search_copy.write_text(
        search.read_text(encoding="utf-8"), encoding="utf-8"
    )
    registry = tmp_path / "runtime/registry/policy_registry.json"
    initialize_registry(base_copy, registry)
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
        "policy_registry": "runtime/registry/policy_registry.json",
        "policy_search_space": "configs/search.json",
        "non_target_manifest": "non_target.json",
        "validity_authority": "grounded_runtime_authority_v1",
        "grounded_family_registry": str(
            ROOT / "configs/red/grounded_family_registry_v1.json"
        ),
        "grounded_operator_registry": str(
            ROOT / "configs/red/grounded_operator_registry_v1.json"
        ),
        "grounded_effect_registry": str(
            ROOT / "configs/red/grounded_effect_registry_v1.json"
        ),
        "challenge_seeds": [1, 2, 3],
        "promotion_seeds": [11],
        "split_seed": 4,
        "policy_search_seed": 5,
        "code_version": "grounded-authority-integration-test",
    }
    adapter = GroundedRoundAdapter(tmp_path / "adapter")
    summary = EvolutionRoundRunner(
        config,
        round_id="R001",
        adapter=adapter,
        project_root=tmp_path,
    ).run()
    assert summary["promotion_eligible"]
    assert not adapter.prepare_validity_called
    attempt_dirs = sorted(
        (
            tmp_path
            / "runtime/rounds/R001/grounded_authority"
        ).glob("*/attempt-*")
    )
    validity_rows = [
        json.loads(line)
        for line in (
            tmp_path
            / "runtime/rounds/R001/validity_results.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert len(validity_rows) == 2
    assert all(
        row["validity"]["proven_valid"]
        and row["validity"]["evidence"]["authority_mode"]
        == "runner_owned_grounded_execution"
        and row["grounded_authority_bundle"]["execution_bundle"][
            "admission_decision"
        ]["admitted"]
        and row["grounded_authority_bundle"][
            "formal_proof_triplet"
        ]["clean"]["verdict"] == "proved"
        and row["grounded_authority_bundle"][
            "formal_proof_triplet"
        ]["poison"]["verdict"] == "counterexample"
        and row["grounded_authority_bundle"][
            "formal_proof_triplet"
        ]["revert"]["verdict"] == "proved"
        and row["validity"]["evidence"][
            "failure_descriptor_hash"
        ] == row["grounded_authority_bundle"][
            "failure_descriptor"
        ]["descriptor_hash"]
        for row in validity_rows
    )
    legacy_execution = validity_rows[0][
        "grounded_authority_bundle"
    ]["execution_bundle"]
    legacy_evidence = legacy_execution["evidence"]
    legacy_materialization = legacy_execution[
        "materialization_receipt"
    ]
    legacy_decision = legacy_execution["admission_decision"]
    legacy_validity = {
        "proven_valid": legacy_decision["admitted"],
        "checks": legacy_decision["checks"],
        "rejection_reasons": legacy_decision[
            "rejection_reasons"
        ],
        "evidence": {
            "authority_mode": "runner_owned_grounded_execution",
            "execution_bundle_hash": legacy_execution["bundle_hash"],
            "admission_decision_hash": legacy_decision[
                "decision_hash"
            ],
            "plan_hash": legacy_execution["plan"]["plan_hash"],
            "clean_rtl_hash": legacy_materialization[
                "clean_rtl_hash"
            ],
            "poison_rtl_hash": legacy_materialization[
                "poison_rtl_hash"
            ],
            "semantic_diff_receipt_hash": legacy_evidence[
                "semantic_diff"
            ]["receipt_hash"],
            "runtime_effect_receipt_hash": legacy_evidence[
                "runtime_effect"
            ]["receipt_hash"],
        },
    }
    legacy_validity["result_hash"] = hash_payload(legacy_validity)
    assert verify_legacy_grounded_arena_validity(
        legacy_validity,
        execution_bundle=legacy_execution,
    ) == legacy_validity
    episodes = EpisodeStore(
        tmp_path / "runtime/memory/episodes"
    ).audit()
    descriptor_by_poison = {
        row["poison_id"]: row["grounded_authority_bundle"][
            "failure_descriptor"
        ]
        for row in validity_rows
    }
    assert {
        episode.poison_id: episode.failure_descriptor
        for episode in episodes
    } == descriptor_by_poison
    challenge_row = json.loads(
        (
            tmp_path
            / "runtime/rounds/R001/blue_challenge_results.jsonl"
        ).read_text(encoding="utf-8").splitlines()[0]
    )
    challenge_without_authority = deepcopy(challenge_row)
    challenge_without_authority.pop("grounded_authority_bundle")
    active_parent = PolicyState.from_dict(json.loads(
        (
            tmp_path
            / "runtime/rounds/R001/active_parent.json"
        ).read_text(encoding="utf-8")
    ))
    with pytest.raises(
        EpisodeBuildViolation,
        match="lacks its authority bundle",
    ):
        episode_from_challenge(
            challenge_without_authority,
            policy=active_parent,
            round_id="R001",
        )
    for descriptor in descriptor_by_poison.values():
        assert set(descriptor) == {
            "schema_version",
            "oracle_stage",
            "sequential_context",
            "affected_roles",
            "temporal_relation",
            "cycle_offset_bucket",
            "assignment_type",
            "cone_depth_bucket",
            "mismatch_pattern",
            "first_divergence_bucket",
            "first_divergence_signal",
            "observable_artifact_hashes",
            "descriptor_hash",
        }
        assert set(descriptor["observable_artifact_hashes"]) == {
            "grounded_execution_bundle",
            "clean_oracle_receipt",
            "poison_oracle_receipt_1",
            "poison_oracle_receipt_2",
            "revert_oracle_receipt",
            "waveform_observation",
            "semantic_diff_receipt",
            "runtime_effect_receipt",
            "formal_proof_triplet",
        }
        assert descriptor["first_divergence_signal"] == "done"
        assert descriptor["first_divergence_bucket"] == "cycle_4_7"
        assert descriptor["cycle_offset_bucket"] == 0
        assert descriptor["temporal_relation"] == "same_cycle"
        assert descriptor["assignment_type"] == "continuous"
        assert descriptor["cone_depth_bucket"] == "depth_1"
        assert descriptor["mismatch_pattern"] == (
            "boundary_value_mismatch"
        )
        assert not {
            "mutation_family",
            "mutation_operator",
            "red_truth",
            "poison_family",
        } & set(descriptor)
    audit = verify_frozen_round(
        tmp_path / "runtime/rounds/R001"
    )
    assert audit["validity_authority"] == "grounded_runtime_authority_v1"
    assert audit["validity_results_hash"] == hash_payload(validity_rows)
    assert audit["verified_episode_manifest_hash"]

    episode_object = (
        tmp_path
        / "runtime/memory/episodes/objects"
        / (
            episodes[0].episode_hash.replace(":", "_")
            + ".json"
        )
    )
    stored_episode = json.loads(
        episode_object.read_text(encoding="utf-8")
    )
    tampered_episode = deepcopy(stored_episode)
    tampered_episode["failure_descriptor"][
        "mismatch_pattern"
    ] = "tampered"
    atomic_write_json(episode_object, tampered_episode)
    with pytest.raises(
        RoundAuditViolation,
        match="episode",
    ):
        verify_frozen_round(tmp_path / "runtime/rounds/R001")
    atomic_write_json(episode_object, stored_episode)

    resumed = EvolutionRoundRunner(
        config,
        round_id="R001",
        adapter=adapter,
        project_root=tmp_path,
    ).run()
    assert resumed == summary
    assert sorted(
        (
            tmp_path
            / "runtime/rounds/R001/grounded_authority"
        ).glob("*/attempt-*")
    ) == attempt_dirs

    original_rows = deepcopy(validity_rows)
    validity_rows[0]["grounded_authority_bundle"][
        "formal_proof_triplet"
    ]["poison"]["verdict"] = "proved"
    validity_path = (
        tmp_path / "runtime/rounds/R001/validity_results.jsonl"
    )
    validity_path.write_text(
        "\n".join(
            json.dumps(row, sort_keys=True)
            for row in validity_rows
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        RoundAuditViolation,
        match="Grounded|validity",
    ):
        verify_frozen_round(tmp_path / "runtime/rounds/R001")

    validity_rows = original_rows
    validity_rows[0]["validity"]["checks"]["G11_minimization"] = False
    validity_path.write_text(
        "\n".join(
            json.dumps(row, sort_keys=True)
            for row in validity_rows
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        RoundAuditViolation,
        match="Grounded|validity",
    ):
        verify_frozen_round(tmp_path / "runtime/rounds/R001")


@pytest.mark.skipif(
    not (HAS_ICARUS and HAS_YOSYS),
    reason="Icarus or Yosys is unavailable",
)
def test_formal_inconclusive_routes_to_rejected_archive(tmp_path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "runtime/registry").mkdir(parents=True)
    base_copy = tmp_path / "configs/base.json"
    search_copy = tmp_path / "configs/search.json"
    base_copy.write_text(
        (
            ROOT
            / "configs/base_policy/frozen_base_policy_v1.json"
        ).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    search_copy.write_text(
        (
            ROOT
            / "configs/base_policy/policy_search_space_v1.json"
        ).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    initialize_registry(
        base_copy,
        tmp_path / "runtime/registry/policy_registry.json",
    )
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
        "validity_authority": "grounded_runtime_authority_v1",
        "grounded_family_registry": str(
            ROOT / "configs/red/grounded_family_registry_v1.json"
        ),
        "grounded_operator_registry": str(
            ROOT / "configs/red/grounded_operator_registry_v1.json"
        ),
        "grounded_effect_registry": str(
            ROOT / "configs/red/grounded_effect_registry_v1.json"
        ),
        "challenge_seeds": [1, 2, 3],
        "promotion_seeds": [11],
        "split_seed": 4,
        "policy_search_seed": 5,
        "code_version": "formal-rejection-integration-test",
    }
    adapter = GroundedRoundAdapter(
        tmp_path / "adapter",
        formal_rejection_indices={1},
    )
    summary = EvolutionRoundRunner(
        config,
        round_id="R_REJECT",
        adapter=adapter,
        project_root=tmp_path,
    ).run()
    round_dir = tmp_path / "runtime/rounds/R_REJECT"
    validity_rows = [
        json.loads(line)
        for line in (
            round_dir / "validity_results.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    rejected = [
        json.loads(line)
        for line in (
            round_dir / "formal_rejections.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert len(validity_rows) == 2
    assert len(rejected) == 1
    rejected_row = next(
        row for row in validity_rows
        if row.get("formal_rejection")
    )
    assert rejected_row["poison_id"] == "grounded_arena_1"
    assert not rejected_row["validity"]["proven_valid"]
    assert rejected_row["validity"]["evidence"][
        "authority_mode"
    ] == "runner_owned_formal_rejection"
    assert rejected_row["formal_rejection"] == rejected[0]
    assert rejected[0]["formal_proof_assessment"][
        "rejection_reasons"
    ] == [
        "F1_clean_not_proved",
        "F2_poison_counterexample_not_proved",
        "F3_revert_not_proved",
    ]
    challenge_rows = [
        json.loads(line)
        for line in (
            round_dir / "blue_challenge_results.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert [row["poison_id"] for row in challenge_rows] == [
        "grounded_arena_0"
    ]
    archive_path = (
        tmp_path / "runtime/archives/red_rejected_archive.jsonl"
    )
    assert len(archive_path.read_text(encoding="utf-8").splitlines()) == 1
    audit = verify_frozen_round(round_dir)
    assert audit["formal_rejection_count"] == 1
    assert not summary["promotion_eligible"]

    resumed = EvolutionRoundRunner(
        config,
        round_id="R_REJECT",
        adapter=adapter,
        project_root=tmp_path,
    ).run()
    assert resumed == summary
    assert len(archive_path.read_text(encoding="utf-8").splitlines()) == 1
    stored_episodes = EpisodeStore(
        tmp_path / "runtime/memory/episodes"
    ).audit()
    assert [
        episode.poison_id for episode in stored_episodes
    ] == ["grounded_arena_0", "grounded_arena_1"]
    rejected_episode = stored_episodes[1]
    assert rejected_episode.final_outcome == "inconclusive"
    assert rejected_episode.blue_attempts == []
    assert (
        rejected_episode.failure_descriptor["oracle_stage"]
        == "grounded_formal_rejected"
    )

    tool_error_adapter = GroundedRoundAdapter(
        tmp_path / "tool-error-adapter",
        formal_tool_error_indices={1},
    )
    with pytest.raises(
        RoundRunnerViolation,
        match="formal rejection authority objects",
    ):
        EvolutionRoundRunner(
            config,
            round_id="R_TOOL_ERROR",
            adapter=tool_error_adapter,
            project_root=tmp_path,
        ).run()
    assert len(archive_path.read_text(encoding="utf-8").splitlines()) == 1

    tampered = deepcopy(rejected)
    tampered[0]["formal_proof_assessment"]["clean"][
        "verdict"
    ] = "proved"
    (round_dir / "formal_rejections.jsonl").write_text(
        "\n".join(
            json.dumps(row, sort_keys=True) for row in tampered
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        RoundAuditViolation,
        match="rejection|Grounded",
    ):
        verify_frozen_round(round_dir)


@pytest.mark.skipif(
    not (HAS_ICARUS and HAS_YOSYS),
    reason="Icarus or Yosys is unavailable",
)
def test_population_schedule_is_reconstructed_by_completed_round_audit(
    tmp_path,
):
    (tmp_path / "configs").mkdir()
    (tmp_path / "runtime/registry").mkdir(parents=True)
    base_copy = tmp_path / "configs/base.json"
    search_copy = tmp_path / "configs/search.json"
    base_copy.write_text(
        (
            ROOT / "configs/base_policy/frozen_base_policy_v1.json"
        ).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    search_copy.write_text(
        (
            ROOT / "configs/base_policy/policy_search_space_v1.json"
        ).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    initialize_registry(
        base_copy,
        tmp_path / "runtime/registry/policy_registry.json",
    )
    atomic_write_json(
        tmp_path / "non_target.json",
        make_manifest(
            [{"case_id": "n0", "design": "non_target_0"}],
            split="non_target",
        ),
    )
    config = {
        "policy_registry": "runtime/registry/policy_registry.json",
        "policy_search_space": "configs/search.json",
        "non_target_manifest": "non_target.json",
        "validity_authority": "grounded_runtime_authority_v1",
        "grounded_family_registry": str(
            ROOT / "configs/red/grounded_family_registry_v1.json"
        ),
        "grounded_operator_registry": str(
            ROOT / "configs/red/grounded_operator_registry_v1.json"
        ),
        "grounded_effect_registry": str(
            ROOT / "configs/red/grounded_effect_registry_v1.json"
        ),
        "grounded_pre_generation_planner": True,
        "grounded_population_scheduler": True,
        "grounded_population_config": str(
            ROOT / "configs/red/deterministic_population_v1.json"
        ),
        "grounded_red_proposal_budget": 1,
        "grounded_family_quota": 1,
        "grounded_archive_quota": 0,
        "grounded_allow_controlled_composition": False,
        "challenge_seeds": [1, 2, 3],
        "promotion_seeds": [11],
        "split_seed": 4,
        "policy_search_seed": 5,
        "code_version": "grounded-population-audit-test",
    }
    summary = EvolutionRoundRunner(
        config,
        round_id="R_POPULATION_AUDIT",
        adapter=GroundedRoundAdapter(tmp_path / "adapter"),
        project_root=tmp_path,
    ).run()
    assert not summary["promotion_eligible"]
    round_dir = (
        tmp_path / "runtime/rounds/R_POPULATION_AUDIT"
    )
    audit = verify_frozen_round(round_dir)
    schedule = json.loads((
        round_dir / "grounded_population_schedule.json"
    ).read_text(encoding="utf-8"))
    execution = json.loads((
        round_dir / "grounded_population_execution.json"
    ).read_text(encoding="utf-8"))
    assert audit["grounded_population_schedule_hash"] == schedule[
        "schedule_hash"
    ]
    assert audit["grounded_population_execution_hash"] == execution[
        "execution_hash"
    ]
    assert audit["grounded_population_arm"] == "routed_population"

    tampered = deepcopy(execution)
    tampered["provider_usage"][
        next(iter(tampered["provider_usage"]))
    ]["assignments"] += 1
    atomic_write_json(
        round_dir / "grounded_population_execution.json",
        tampered,
    )
    with pytest.raises(
        RoundAuditViolation,
        match="population",
    ):
        verify_frozen_round(round_dir)
