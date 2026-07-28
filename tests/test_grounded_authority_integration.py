from __future__ import annotations

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
from r3e.arena.runner import EvolutionRoundRunner
from r3e.policy.registry_v2 import initialize_registry
from r3e.protocol.hashing import atomic_write_json, hash_file, hash_payload
from r3e.red.grounded.materializers import materialize_operator
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


class GroundedRoundAdapter(DeterministicEvolutionAdapter):
    def __init__(self, workspace: Path):
        super().__init__(workspace)
        self.workspace = workspace
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
        for index, limit in enumerate((4, 5)):
            poison_id = f"grounded_arena_{index}"
            clean = source_root / f"clean_{index}.v"
            poison = source_root / f"poison_{index}.v"
            testbench = source_root / f"tb_{index}.v"
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
  if (mismatches == 0)
    $display("R3E_ORACLE pass=1 signature=none first=none topology=none");
  else
    $display("R3E_ORACLE pass=0 signature=boundary_{index} first=cycle_{limit} topology=done");
  $finish;
end
endmodule
""",
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
            }
            lineage = make_lineage_plan(
                parent,
                operator_space=self.lineage_space,
                operator="fresh",
                poison_id=poison_id,
            )
            row = materialize_fresh(lineage, descriptor)
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


@pytest.mark.skipif(not HAS_ICARUS, reason="Icarus is unavailable")
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
        "validity_authority": "grounded_red_execution_v1",
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
        and row["grounded_execution_bundle"]["admission_decision"][
            "admitted"
        ]
        for row in validity_rows
    )
    audit = verify_frozen_round(
        tmp_path / "runtime/rounds/R001"
    )
    assert audit["validity_authority"] == "grounded_red_execution_v1"
    assert audit["validity_results_hash"] == hash_payload(validity_rows)

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

    validity_rows[0]["validity"]["checks"]["G11_minimization"] = False
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
