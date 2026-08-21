"""Audited one-case GRD-8 / ACP-7 real-provider smoke pilot.

This command is intentionally not a statistical experiment.  It executes one
Grounded Red target-choice request and one three-slot ACP evaluation, while
keeping materialization, verification, selection, and oracle authority local.
All detailed artifacts are written under an ignored runtime directory.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

from r3e.arena.conformance import bind_adapter_output
from r3e.arena.grounded_authority import (
    execute_grounded_arena_validity,
    verify_arena_grounded_authority,
)
from r3e.blue.portfolio.allocator import (
    OfflineAdaptiveAllocator,
    load_offline_allocator_state,
)
from r3e.blue.portfolio.audit import verify_blue_evaluation
from r3e.blue.portfolio.candidate_executor import CandidatePortfolioExecutor
from r3e.blue.portfolio.lens_registry import load_lens_registry
from r3e.blue.portfolio.openai_provider import (
    OpenAICompatibleCandidateProvider,
)
from r3e.blue.portfolio.oracle_gate_verifier import (
    PublicManifestOracleVerifier,
    public_case_from_manifest,
)
from r3e.blue.portfolio.schema import CandidatePortfolio
from r3e.memory.descriptor import build_failure_descriptor
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import (
    atomic_write_json,
    atomic_write_text,
    hash_file,
    hash_payload,
    read_json,
)
from r3e.providers.openai_compatible import (
    OpenAICompatibleClientConfig,
    OpenAICompatibleJSONClient,
)
from r3e.red.grounded.coverage import freeze_coverage_state
from r3e.red.grounded.materializers import materialize_operator
from r3e.red.grounded.mutation_plan import build_mutation_plan
from r3e.red.grounded.openai_provider import (
    OpenAICompatibleGroundedChoiceProvider,
)
from r3e.red.grounded.operator_ast import operator_nodes
from r3e.red.grounded.population_scheduler import (
    build_population_schedule,
    verify_population_candidates,
    verify_population_config,
)
from r3e.red.grounded.proposal_planner import (
    build_grounded_proposal_plan,
    verify_proposal_candidates,
)
from r3e.red.grounded.registry import load_grounded_registries
from r3e.red.operators import (
    load_operator_space,
    make_lineage_plan,
    materialize_fresh,
)
from r3e.semantic_repair_bench.oracle_gate import judge


SMOKE_SCHEMA = "r3e-grd8-acp7-smoke-result-v1"
GRD_SOURCE = """module m(
  input wire [3:0] a,
  input wire [3:0] b,
  output wire y
);
assign y = (a < 4) && (b > 2);
endmodule
"""
GRD_TESTBENCH = """module tb;
reg [3:0] a;
reg [3:0] b;
wire y;
integer i;
integer j;
integer mismatches;
m dut(.a(a), .b(b), .y(y));
initial begin
  mismatches = 0;
  for (i = 0; i < 7; i = i + 1) begin
    for (j = 0; j < 7; j = j + 1) begin
      a = i; b = j; #1;
      if (y !== ((i < 4) && (j > 2)))
        mismatches = mismatches + 1;
    end
  end
  if (mismatches == 0) begin
    $display("R3E_ORACLE pass=1 signature=none first=none topology=none");
    $display("R3E_WAVEFORM signal=none first_cycle=none cycle_offset=none relation=none assignment=none cone_depth=none pattern=none");
  end else begin
    $display("R3E_ORACLE pass=0 signature=grd8_boundary first=cycle4 topology=y");
    $display("R3E_WAVEFORM signal=y first_cycle=4 cycle_offset=0 relation=same_cycle assignment=continuous cone_depth=1 pattern=boundary_value_mismatch");
  end
  $finish;
end
endmodule
"""
GRD_PROPERTY = """module formal_top;
  (* anyconst *) reg [3:0] a;
  (* anyconst *) reg [3:0] b;
  wire y;
  m dut(.a(a), .b(b), .y(y));
  always @* assert(y == ((a < 4) && (b > 2)));
endmodule
"""


class PilotSmokeViolation(RuntimeError):
    """Raised when pilot entry cannot satisfy its frozen hard gates."""


def _load_manifest_case(path: Path, case_id: str) -> dict[str, Any]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    matches = [row for row in rows if row.get("case_id") == case_id]
    if len(matches) != 1 or matches[0].get("eligible") is not True:
        raise PilotSmokeViolation(
            "pilot case must be one eligible public manifest row"
        )
    return matches[0]


def _client_from_environment() -> OpenAICompatibleJSONClient:
    def _selected_env(*names: str, default: str) -> str:
        for name in names:
            if os.environ.get(name):
                return name
        return default

    model_env = _selected_env(
        "OPENAI_MODEL",
        "DEEPSEEK_MODEL",
        "LLM_MODEL",
        default="OPENAI_MODEL",
    )
    api_key_env = _selected_env(
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "LLM_API_KEY",
        "R3E_PILOT_API_KEY",
        default="OPENAI_API_KEY",
    )
    base_url_env = _selected_env(
        "OPENAI_BASE_URL",
        "DEEPSEEK_BASE_URL",
        "LLM_BASE_URL",
        "R3E_PILOT_BASE_URL",
        default="OPENAI_BASE_URL",
    )
    model = str(os.environ.get(model_env) or "")
    if not model:
        raise PilotSmokeViolation(
            "OPENAI_MODEL/DEEPSEEK_MODEL/LLM_MODEL is not configured"
        )
    config = OpenAICompatibleClientConfig.from_dict({
        "schema_version": "r3e-openai-compatible-client-config-v1",
        "provider_id": str(
            os.environ.get("LLM_PROVIDER")
            or ("deepseek" if model_env == "DEEPSEEK_MODEL" else "openai-compatible")
        ),
        "provider_version": "pilot-entry-v1",
        "endpoint_id": "environment-bound-openai-compatible",
        "model_id": model,
        "model_version": model,
        "api_key_env": api_key_env,
        "base_url_env": base_url_env,
        "timeout_seconds": 120,
        "maximum_output_tokens": 4096,
        "temperature": 0,
        "require_seed": True,
    })
    client = OpenAICompatibleJSONClient(config)
    if not client.readiness()["ready"]:
        raise PilotSmokeViolation(
            "configured API key/base URL environment variables are not ready"
        )
    return client


def _real_population_config(
    provider: OpenAICompatibleGroundedChoiceProvider,
) -> dict[str, Any]:
    profile_budget = hash_payload({
        "maximum_assignments": 1,
        "maximum_input_tokens": 4096,
        "maximum_output_tokens": 1024,
        "maximum_wall_time_ms": 60000,
    })
    body = {
        "schema_version": "r3e-red-population-config-v1",
        "scheduler_id": "r3e-real-pilot-population-v1",
        "scheduler_mode": "generalist_only",
        "total_assignment_budget": 1,
        "total_input_token_budget": 4096,
        "total_output_token_budget": 1024,
        "total_wall_time_budget_ms": 60000,
        "provider_profiles": [{
            "provider_id": "real-pilot-generalist",
            "provider_role": "generalist",
            "model_id": provider.client.config.model_id,
            "toolchain_fingerprint_hash": (
                provider.toolchain_fingerprint_hash
            ),
            "budget_hash": profile_budget,
            "supported_specialist_kinds": [],
            "supported_family_prefixes": [],
            "max_assignments": 1,
            "max_input_tokens": 4096,
            "max_output_tokens": 1024,
            "max_wall_time_ms": 60000,
            "minimum_wall_time_ms": 60000,
        }],
        "validator_authority": "runner_owned_grounded_execution",
        "minimizer_authority": "runner_owned_structural_minimizer",
    }
    body["config_hash"] = hash_payload(body)
    return verify_population_config(body)


def _run_grd8(
    *,
    root: Path,
    workspace: Path,
    client: OpenAICompatibleJSONClient,
    seed: int,
) -> dict[str, Any]:
    policy = PolicyState.from_dict(read_json(
        root / "configs/base_policy/frozen_base_policy_v1.json"
    ))
    registries = load_grounded_registries(
        family_registry=(
            root / "configs/red/grounded_family_registry_v1.json"
        ),
        operator_registry=(
            root / "configs/red/grounded_operator_registry_v1.json"
        ),
        effect_registry=(
            root / "configs/red/grounded_effect_registry_v1.json"
        ),
    )
    coverage = freeze_coverage_state([])
    proposal = build_grounded_proposal_plan(
        policy=policy,
        registries=registries,
        coverage_state=coverage,
        archive_view=[],
        budget=1,
        family_quota=1,
        archive_quota=0,
        maximum_difficulty_band="D3",
        memory_capability=None,
        allow_composition=False,
    )
    intent = next(
        row for row in proposal["candidate_intents"]
        if row["intent_id"] == proposal["selected_intent_ids"][0]
    )
    if (
        intent["operator_id"] != "replace_comparator"
        or intent["family_id"]
        != "combinational.comparator_boundary"
    ):
        raise PilotSmokeViolation(
            "pilot GRD proposal recipe is not the frozen comparator arm"
        )
    provider = OpenAICompatibleGroundedChoiceProvider(client)
    population_config = _real_population_config(provider)
    schedule = build_population_schedule(
        policy=policy,
        proposal_plan=proposal,
        config=population_config,
    )
    assignment = schedule["assignments"][0]
    nodes = operator_nodes(
        GRD_SOURCE,
        module="m",
        operator_id=intent["operator_id"],
    )
    choice = provider.choose_target(
        policy=policy,
        intent=intent,
        assignment=assignment,
        clean_source=GRD_SOURCE,
        target_module="m",
        nodes=nodes,
        seed=seed,
    )
    selected = next(
        node for node in nodes
        if node.node_hash == choice["selected_node_hash"]
    )
    plan = build_mutation_plan(
        plan_id="GRD8_SMOKE_P0",
        policy=policy,
        registries=registries,
        target_design="grd8_smoke_comparator_pair",
        target_module="m",
        target_ast_node_hash=selected.node_hash,
        family_id=intent["family_id"],
        operator_id=intent["operator_id"],
        expected_runtime_effect_id=intent[
            "expected_runtime_effect_id"
        ],
        preconditions={"comparison_expression": True},
        scope_limits={
            "maximum_changed_modules": 1,
            "maximum_changed_blocks": 1,
            "maximum_ast_edits": 1,
        },
        difficulty_target={
            key: intent["difficulty_target"][key]
            for key in (
                "difficulty_band",
                "dependency_depth_delta",
                "temporal_depth_delta",
            )
        },
    )
    materialized = materialize_operator(plan, clean_source=GRD_SOURCE)
    source_dir = workspace / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    clean = source_dir / "clean.v"
    poison_path = source_dir / "poison.v"
    testbench = source_dir / "tb.v"
    property_path = source_dir / "property.v"
    atomic_write_text(clean, GRD_SOURCE)
    atomic_write_text(poison_path, materialized.poison_source)
    atomic_write_text(testbench, GRD_TESTBENCH)
    atomic_write_text(property_path, GRD_PROPERTY)
    descriptor = {
        "poison_id": "GRD8_SMOKE_P0",
        "case_id": "GRD8_SMOKE_P0",
        "design": "grd8_smoke_comparator_pair",
        "golden_rtl": str(clean),
        "buggy_rtl": str(poison_path),
        "challenged_policy_id": policy.policy_id,
        "challenged_policy_hash": policy.policy_hash,
        "family": intent["family_id"],
        "effect": intent["expected_runtime_effect_id"],
        "affected_role": intent["target_role"],
        "edit_scope": "comparison_expression",
        "normalized_diff_hash": materialized.semantic_diff_receipt[
            "receipt_hash"
        ],
        "grounded_mutation_plan": plan,
        "grounded_plan_hash": plan["plan_hash"],
        "grounded_testbench": str(testbench),
        "grounded_top_module": "tb",
        "grounded_formal_property": str(property_path),
        "grounded_formal_top_module": "formal_top",
        "grounded_formal_depth": 1,
        "grounded_proposal_intent_id": intent["intent_id"],
        "grounded_proposal_intent_hash": intent["intent_hash"],
        "grounded_dispatch_kind": intent["dispatch_kind"],
        "grounded_difficulty_target": deepcopy(
            intent["difficulty_target"]
        ),
        "grounded_proposal_parent_poison_ids": [],
        "grounded_proposal_target_memory_ids": [],
        "grounded_proposal_memory_operator": intent["memory_operator"],
        "grounded_population_assignment_id": assignment[
            "assignment_id"
        ],
        "grounded_population_assignment_hash": assignment[
            "assignment_hash"
        ],
        "grounded_generator_provider_id": assignment["provider_id"],
        "grounded_generator_role": assignment["provider_role"],
        "grounded_population_arm": assignment["population_arm"],
        "grounded_generation_usage": {
            "input_tokens": choice["input_tokens"],
            "output_tokens": choice["output_tokens"],
            "wall_time_ms": choice["wall_time_ms"],
        },
    }
    lineage_space = load_operator_space(
        root / "configs/red/lineage_operator_space_v1.json"
    )
    lineage = make_lineage_plan(
        policy,
        operator_space=lineage_space,
        operator="fresh",
        poison_id=descriptor["poison_id"],
    )
    candidate = materialize_fresh(lineage, descriptor)
    candidate = bind_adapter_output(
        candidate,
        "generate_red",
        provider.toolchain_fingerprint,
        budget_hash=assignment["budget_hash"],
        command_hash=choice["request_hash"],
    )
    verify_proposal_candidates(
        proposal, [candidate], policy=policy
    )
    verify_population_candidates(
        schedule, [candidate], policy=policy
    )
    validity_result = execute_grounded_arena_validity(
        poison=candidate,
        policy=policy,
        registries=registries,
        project_root=workspace,
        round_dir=workspace / "round",
        run_context_hash=hash_payload({
            "pilot": "GRD8",
            "seed": seed,
            "choice_hash": choice["choice_hash"],
        }),
    )
    authority = validity_result.get("grounded_authority_bundle")
    if not isinstance(authority, Mapping):
        raise PilotSmokeViolation(
            "GRD-8 candidate was routed to formal rejection"
        )
    verified = verify_arena_grounded_authority(
        authority,
        policy=policy,
        registries=registries,
    )
    atomic_write_json(workspace / "proposal.json", proposal)
    atomic_write_json(workspace / "population_schedule.json", schedule)
    atomic_write_json(workspace / "model_choice.json", choice)
    atomic_write_json(workspace / "authority.json", verified)
    triplet = verified["formal_proof_triplet"]
    proven_valid = validity_result["validity"]["proven_valid"]
    return {
        "status": (
            "passed"
            if proven_valid
            else "rejected"
        ),
        "proposal_plan_hash": proposal["plan_hash"],
        "population_schedule_hash": schedule["schedule_hash"],
        "assignment_hash": assignment["assignment_hash"],
        "choice_hash": choice["choice_hash"],
        "selected_node_ordinal": choice["selected_node_ordinal"],
        "provider_usage": {
            "input_tokens": choice["input_tokens"],
            "output_tokens": choice["output_tokens"],
            "wall_time_ms": choice["wall_time_ms"],
        },
        "authority_hash": verified["authority_hash"],
        "admitted": proven_valid,
        "formal_triplet": {
            name: triplet[name]["verdict"]
            for name in ("clean", "poison", "revert")
        },
        "failure_descriptor_hash": verified[
            "failure_descriptor"
        ]["descriptor_hash"],
    }


def _baseline_descriptor(
    *,
    case: Mapping[str, Any],
    workspace: Path,
) -> dict[str, Any]:
    outcome = judge(
        {
            "golden_rtl": case["golden_rtl"],
            "deps": case["deps"],
            "tb_sources": case["tb_sources"],
            "tb_output": case["tb_output"],
            "top_module": case["top_module"],
            "sim_timeout": case["sim_timeout"],
        },
        case["buggy_rtl_path"],
        workspace,
        evidence_k=3,
    )
    if outcome.ok or outcome.stage != "compare":
        raise PilotSmokeViolation(
            "public pilot baseline must be a compiling oracle failure"
        )
    return build_failure_descriptor({
        "oracle_stage": outcome.stage,
        "sequential_context": False,
        "temporal_relation": "same_cycle",
        "cycle_offset_bucket": 0,
        "affected_roles": ["observable_output"],
        "assignment_type": "nonblocking",
        "cone_depth_bucket": "depth_1",
        "mismatch_pattern": "wrong_value",
        "first_divergence_bucket": "cycle_10",
        "first_divergence_signal": "out",
        "observable_artifact_hashes": {
            "buggy_rtl": hash_file(case["buggy_rtl_path"]),
            "oracle_mismatch": hash_payload(outcome.mismatch),
            "structured_evidence": hash_payload(outcome.structured),
        },
    }).to_dict()


def _run_acp7(
    *,
    root: Path,
    workspace: Path,
    client: OpenAICompatibleJSONClient,
    manifest_row: Mapping[str, Any],
    seed: int,
) -> dict[str, Any]:
    policy = PolicyState.from_dict(read_json(
        root / "configs/base_policy/frozen_base_policy_v3.json"
    ))
    registry = load_lens_registry(
        root / "configs/blue/lens_registry_v1.json",
        project_root=root,
    )
    portfolio = CandidatePortfolio.from_dict(read_json(
        root / "configs/blue/adaptive_portfolio_v1.json"
    ))
    allocator = OfflineAdaptiveAllocator(
        load_offline_allocator_state(
            root / "configs/blue/offline_allocator_state_v1.json"
        )
    )
    case = public_case_from_manifest(
        manifest_row, project_root=root
    )
    descriptor = _baseline_descriptor(
        case=case, workspace=workspace / "baseline"
    )
    verifier = PublicManifestOracleVerifier(
        project_root=root,
        workspace=workspace / "verifier",
        run_context_hash=hash_payload({
            "pilot": "ACP7",
            "case_id": case["case_id"],
            "seed": seed,
        }),
        maximum_ast_edits=64,
        timeout_seconds=60,
    )
    provider = OpenAICompatibleCandidateProvider(
        client,
        verifier_id=verifier.verifier_id,
        verifier_version=verifier.verifier_version,
    )
    executor = CandidatePortfolioExecutor(
        registry=registry,
        portfolio=portfolio,
        provider=provider,
        verifier=verifier,
        project_root=root,
        allocator=allocator,
    )
    evaluation = executor.execute(
        policy=policy,
        case=case,
        descriptor=descriptor,
        run_seed=seed,
    )
    verified = verify_blue_evaluation(
        evaluation,
        policy=policy,
        registry=registry,
        portfolio=portfolio,
        provider=provider,
        expected_verifier_hash=verifier.verifier_hash,
        expected_semantic_signature_provider_hash=(
            portfolio.semantic_signature_provider_hash
        ),
        allocator=allocator,
        descriptor=descriptor,
    )
    atomic_write_json(workspace / "failure_descriptor.json", descriptor)
    atomic_write_json(workspace / "blue_evaluation.json", verified)
    return {
        "status": "passed",
        "case_id": case["case_id"],
        "descriptor_hash": descriptor["descriptor_hash"],
        "portfolio_hash": portfolio.effective_portfolio_hash,
        "allocation_plan_hash": verified["allocation_plan_hash"],
        "portfolio_execution_hash": verified[
            "portfolio_execution_hash"
        ],
        "provider_calls": verified["resource_usage"]["provider_calls"],
        "input_tokens": verified["resource_usage"]["input_tokens"],
        "output_tokens": verified["resource_usage"]["output_tokens"],
        "verified_candidates": sum(
            row["oracle_ok"]
            for row in verified["candidate_verification_receipts"]
        ),
        "oracle_ok": verified["oracle_ok"],
        "selected_candidate_id": verified["selection_receipt"][
            "selected_candidate_id"
        ],
        "verifier_record_hashes": [
            row["record_hash"] for row in verifier.verification_records
        ],
    }


def run_smoke(
    *,
    project_root: str | Path,
    workspace: str | Path,
    manifest: str | Path,
    case_id: str,
    seed: int,
    stage: str = "both",
) -> dict[str, Any]:
    if stage not in {"both", "grd8", "acp7"}:
        raise PilotSmokeViolation(
            "pilot stage must be both, grd8, or acp7"
        )
    root = Path(project_root).resolve()
    output = Path(workspace).resolve()
    output.mkdir(parents=True, exist_ok=False)
    client = _client_from_environment()
    row = _load_manifest_case(
        (root / manifest).resolve(), case_id
    )
    result = {
        "schema_version": SMOKE_SCHEMA,
        "claim_scope": (
            "One-case pilot-entry smoke only; not a multi-seed, "
            "multi-round, or comparative empirical result."
        ),
        "model_identity": client.config.public_identity,
        "seed": int(seed),
        "requested_stage": stage,
    }
    try:
        if stage in {"both", "grd8"}:
            result["grd8"] = _run_grd8(
                root=root,
                workspace=output / "grd8",
                client=client,
                seed=seed,
            )
        if stage in {"both", "acp7"}:
            result["acp7"] = _run_acp7(
                root=root,
                workspace=output / "acp7",
                client=client,
                manifest_row=row,
                seed=seed,
            )
        executed = [
            result[name] for name in ("grd8", "acp7")
            if name in result
        ]
        result["status"] = (
            "passed"
            if executed and all(
                row["status"] == "passed" for row in executed
            )
            else "failed"
        )
    except Exception as exc:
        result["status"] = "failed"
        result["failure"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        result["result_hash"] = hash_payload(result)
        atomic_write_json(output / "summary.json", result)
        raise
    result["result_hash"] = hash_payload(result)
    atomic_write_json(output / "summary.json", result)
    return result


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one audited GRD-8/ACP-7 pilot-entry smoke"
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--workspace",
        default="runtime/pilots/grd8-acp7-smoke",
    )
    parser.add_argument(
        "--manifest",
        default="datasets/manifests/strider14.jsonl",
    )
    parser.add_argument(
        "--case-id", default="strider:mux_4_1_1"
    )
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument(
        "--stage",
        choices=("both", "grd8", "acp7"),
        default="both",
        help="run both stages or one stage under a bounded call budget",
    )
    args = parser.parse_args()
    try:
        result = run_smoke(
            project_root=args.project_root,
            workspace=args.workspace,
            manifest=args.manifest,
            case_id=args.case_id,
            seed=args.seed,
            stage=args.stage,
        )
    except Exception as exc:
        print(
            json.dumps({
                "status": "failed",
                "type": type(exc).__name__,
                "message": str(exc),
            }, sort_keys=True),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({
        "status": result["status"],
        "result_hash": result["result_hash"],
        **{
            name: result[name]
            for name in ("grd8", "acp7")
            if name in result
        },
    }, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(_main())
