"""Real Integrated Coevolution Shadow V1 orchestration.

This module deliberately exposes a shadow-only runner.  A caller supplies an
OpenAI-compatible client (the tests use an injected transport); the runner
still executes the real Grounded/ACP protocol, while promotion and RAAM
qualification remain hard-disabled.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from r3e.arena.manifests import make_manifest
from r3e.arena.real_grounded_adapter import RealGroundedArenaAdapter
from r3e.arena.runner import EvolutionRoundRunner
from r3e.blue.portfolio.openai_provider import OpenAICompatibleCandidateProvider
from r3e.blue.portfolio.oracle_gate_verifier import PublicManifestOracleVerifier
from r3e.policy.registry_v2 import initialize_registry
from r3e.protocol.hashing import atomic_write_json, hash_payload
from r3e.providers.openai_compatible import OpenAICompatibleJSONClient


SHADOW_SCHEMA = "r3e-real-integrated-coevolution-shadow-v1"


def _population_config(adapter: RealGroundedArenaAdapter) -> dict[str, Any]:
    fingerprint_hash = hash_payload(adapter.toolchain_fingerprint)
    body = {
        "schema_version": "r3e-red-population-config-v1",
        "scheduler_id": "r3e-real-integrated-shadow-population-v1",
        "scheduler_mode": "routed_population",
        "total_assignment_budget": 1,
        "total_input_token_budget": 2048,
        "total_output_token_budget": 1024,
        "total_wall_time_budget_ms": 10000,
        "provider_profiles": [{
            "provider_id": "grounded-shadow-generalist",
            "provider_role": "generalist",
            "model_id": adapter.client.config.model_id,
            "toolchain_fingerprint_hash": fingerprint_hash,
            "budget_hash": hash_payload({
                "lane": "real_integrated_shadow",
                "provider_id": "grounded-shadow-generalist",
            }),
            "supported_specialist_kinds": [],
            "supported_family_prefixes": [],
            "max_assignments": 1,
            "max_input_tokens": 2048,
            "max_output_tokens": 1024,
            "max_wall_time_ms": 10000,
        }],
        "validator_authority": "runner_owned_grounded_execution",
        "minimizer_authority": "runner_owned_structural_minimizer",
    }
    body["config_hash"] = hash_payload(body)
    return body


def build_shadow_runner(
    *,
    project_root: str | Path,
    workspace: str | Path,
    round_id: str,
    client: OpenAICompatibleJSONClient,
    case_id: str | None = None,
) -> EvolutionRoundRunner:
    """Build one formal shadow round without making any promotion decision."""
    root = Path(project_root).resolve()
    work = Path(workspace).resolve()
    work.mkdir(parents=True, exist_ok=True)
    round_root = root / "runtime/real_integrated_shadow"
    adapter = RealGroundedArenaAdapter(
        project_root=root,
        manifest_path=root / "configs/pilot/real_integrated_shadow_manifest_v1.jsonl",
        client=client,
        artifact_root=round_root / "artifacts",
        case_id=case_id,
        round_id=round_id,
    )
    population_path = work / "grounded_population.json"
    atomic_write_json(population_path, _population_config(adapter))
    registry_path = work / "policy_registry.json"
    if not registry_path.is_file():
        initialize_registry(
            root / "configs/base_policy/frozen_base_policy_v3.json",
            registry_path,
        )
    non_target_path = work / "non_target_manifest.json"
    atomic_write_json(
        non_target_path,
        make_manifest(
            [{"case_id": "shadow-non-target-0", "design": "shadow-non-target"}],
            split="non_target",
        ),
    )
    verifier = PublicManifestOracleVerifier(
        project_root=root,
        workspace=work / "blue_verifier",
        run_context_hash=hash_payload({
            "schema_version": SHADOW_SCHEMA,
            "round_id": round_id,
        }),
        timeout_seconds=60,
    )
    blue_provider = OpenAICompatibleCandidateProvider(
        client,
        verifier_id=verifier.verifier_id,
        verifier_version=verifier.verifier_version,
    )
    config = {
        "schema_version": "r3e-round-interface-v1",
        "phase": "real_integrated_shadow",
        "validity_authority": "grounded_runtime_authority_v1",
        "blue_evaluation_authority": "candidate_portfolio_v1",
        "policy_registry": str(registry_path),
        "policy_search_space": str(root / "configs/base_policy/policy_search_space_v1.json"),
        "rounds_root": str(round_root),
        "events_root": str(work / "events"),
        "decision_ledger": str(work / "decision_ledger.jsonl"),
        "round_ledger": str(work / "round_ledger.jsonl"),
        "red_archive": str(work / "red_residual_archive.jsonl"),
        "covered_archive": str(work / "red_covered_archive.jsonl"),
        "red_rejected_archive": str(work / "red_rejected_archive.jsonl"),
        "memory_root": str(work / "memory"),
        "non_target_manifest": str(non_target_path),
        "grounded_coverage_state": str(work / "coverage_state.json"),
        "grounded_population_config": str(population_path),
        "grounded_pre_generation_planner": True,
        "grounded_population_scheduler": True,
        "grounded_red_proposal_budget": 1,
        "grounded_family_quota": 1,
        "grounded_archive_quota": 0,
        "grounded_maximum_difficulty_band": "D3",
        "grounded_allow_controlled_composition": False,
        "grounded_family_registry": str(root / "configs/red/grounded_family_registry_v1.json"),
        "grounded_operator_registry": str(root / "configs/red/grounded_operator_registry_v1.json"),
        "grounded_effect_registry": str(root / "configs/red/grounded_effect_registry_v1.json"),
        "lineage_operator_space": str(root / "configs/red/lineage_operator_space_v1.json"),
        "blue_lens_registry": "configs/blue/lens_registry_v1.json",
        "blue_candidate_portfolio": "configs/blue/adaptive_portfolio_v1.json",
        "blue_offline_allocator_state": "configs/blue/offline_allocator_state_v1.json",
        "blue_offline_allocator_hash": "sha256:2fa2be300e8326403b9bad7f3a61968117c50b6bd8df974dd9bfc2f06ca12faf",
        "blue_candidate_provider_toolchain_fingerprint": blue_provider.toolchain_fingerprint,
        "blue_candidate_verifier_hash": verifier.verifier_hash,
        "blue_semantic_signature_provider_hash": "sha256:c9974065d5a3bcce77c23cceb3e3cd967464f85c4225fbe8e4b912ca6aac6404",
        "challenge_seeds": [1],
        "promotion_seeds": [2],
        "policy_promotion_enabled": False,
        "memory_qualification_enabled": False,
        "grounded_timeout_seconds": 10,
        "grounded_formal_timeout_seconds": 30,
        "adaptation_fraction": 0.5,
        "split_seed": 4,
        "policy_search_seed": 5,
        "code_version": "real-integrated-coevolution-shadow-v1",
    }
    return EvolutionRoundRunner(
        config,
        round_id=round_id,
        adapter=adapter,
        project_root=root,
        candidate_provider=blue_provider,
        candidate_verifier=verifier,
    )


def run_real_integrated_shadow(
    *,
    project_root: str | Path,
    workspace: str | Path,
    round_id: str,
    client: OpenAICompatibleJSONClient,
    case_id: str | None = None,
) -> dict[str, Any]:
    runner = build_shadow_runner(
        project_root=project_root,
        workspace=workspace,
        round_id=round_id,
        client=client,
        case_id=case_id,
    )
    summary = runner.run()
    if summary.get("promoted") is not False:
        raise RuntimeError("shadow round must never promote a policy")
    return {
        "schema_version": SHADOW_SCHEMA,
        "round_id": round_id,
        "summary": summary,
        "promotion_executed": False,
        "memory_qualification_executed": False,
    }
