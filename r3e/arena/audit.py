"""Offline reconstruction of a completed whole-policy evolution round."""
from __future__ import annotations

import argparse
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from r3e.arena.manifests import verify_manifest
from r3e.arena.conformance import validate_toolchain_fingerprint
from r3e.arena.portfolio import (
    CANDIDATE_PORTFOLIO_AUTHORITY,
    LEGACY_BLUE_EVALUATION_AUTHORITY,
    extract_grounded_failure_descriptor,
)
from r3e.blue.portfolio.audit import verify_blue_evaluation
from r3e.blue.portfolio.allocator import (
    OfflineAdaptiveAllocator,
    load_offline_allocator_state,
)
from r3e.blue.portfolio.lens_registry import load_lens_registry
from r3e.blue.portfolio.router import load_descriptor_router
from r3e.blue.portfolio.schema import (
    CandidatePortfolio,
    build_candidate_portfolio_binding,
)
from r3e.blue.portfolio.statistics import (
    build_challenge_portfolio_statistics,
)
from r3e.memory.episode_builder import (
    episode_from_challenge,
    episode_from_formal_rejection,
)
from r3e.memory.episode_store import EpisodeStore
from r3e.policy.promotion import (
    decide_policy_promotion,
    select_single_promotable_child,
)
from r3e.policy.registry_v2 import get_active_policy, validate_registry
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import (
    atomic_write_json,
    canonical_json,
    hash_payload,
    read_json,
    utc_now,
)
from r3e.protocol.ledger import read_ledger, writer_lock
from r3e.protocol.provenance import verify_run_context
from r3e.red.feedback_packet import FORBIDDEN_INPUT_KEYS
from r3e.red.feedback_packet import verify_red_search_context
from r3e.red.portfolio_challenge import (
    PortfolioChallengeViolation,
    build_portfolio_red_authority,
)
from r3e.red.grounded.proposal_planner import (
    GroundedProposalViolation,
    grounded_proposal_protocol_hash,
    verify_grounded_proposal_plan,
    verify_proposal_candidates,
)
from r3e.red.grounded.population_scheduler import (
    GroundedPopulationViolation,
    load_population_config,
    population_protocol_hash,
    verify_population_candidates,
    verify_population_schedule,
)
from r3e.red.learnability import verify_learnability_result
from r3e.red.selection import select_residual_elites
from r3e.red.poison_payload import verify_poison_payload
from r3e.red.validity import validity_gate
from r3e.red.grounded.arena_validity import (
    ARENA_GROUNDED_AUTHORITY,
    verify_grounded_arena_validity,
    verify_legacy_grounded_arena_validity,
)
from r3e.red.grounded.execution import (
    verify_grounded_execution_bundle,
)
from r3e.red.grounded.formal_rejection import (
    load_formal_rejection_archive,
    verify_formal_rejection,
    verify_formal_rejection_validity,
)
from r3e.red.grounded.coverage import (
    update_coverage_state,
    verify_coverage_plan,
    verify_coverage_state,
)
from r3e.red.grounded.difficulty import (
    difficulty_profile_from_challenge,
)
from .grounded_authority import (
    GROUNDED_ARENA_COMPAT_AUTHORITY,
    GROUNDED_ARENA_AUTHORITIES,
    load_arena_grounded_registries,
    verify_arena_grounded_authority,
)


ROUND_AUDIT_SCHEMA_VERSION = "r3e-round-audit-v1"


class RoundAuditViolation(RuntimeError):
    """Raised when a completed round cannot be reconstructed exactly."""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise RoundAuditViolation(f"required round artifact is missing: {path.name}")
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _manifest(path: Path) -> dict[str, Any]:
    try:
        return verify_manifest(read_json(path))
    except Exception as exc:
        raise RoundAuditViolation(f"invalid manifest {path.name}: {exc}") from exc


def reconstruct_round(round_dir: str | Path) -> dict[str, Any]:
    root = Path(round_dir).resolve()
    config = read_json(root / "round_config.json")
    configured_project_root = config.get("project_root")
    if configured_project_root:
        project_root = Path(str(configured_project_root))
        if not project_root.is_absolute():
            project_root = (root.parents[2] / project_root).resolve()
        else:
            project_root = project_root.resolve()
    else:
        # Backward-compatible layout for historical in-tree runtime rounds.
        project_root = root.parents[2]
    context = verify_run_context(read_json(root / "toolchain.json"))
    if context["round_config_hash"] != hash_payload(config):
        raise RoundAuditViolation("round config does not match frozen run context")
    parent = PolicyState.from_dict(read_json(root / "active_parent.json"))
    if parent.policy_hash != context["active_policy_hash"]:
        raise RoundAuditViolation("active parent does not match frozen run context")
    validity_authority = str(
        config.get(
            "validity_authority",
            "legacy_adapter_evidence_v1",
        )
    )
    validity_rows = _read_jsonl(root / "validity_results.jsonl")
    formal_rejections_path = root / "formal_rejections.jsonl"
    formal_rejections = (
        _read_jsonl(formal_rejections_path)
        if formal_rejections_path.is_file()
        else []
    )
    registries = None
    if validity_authority in GROUNDED_ARENA_AUTHORITIES:
        registries = load_arena_grounded_registries(
            project_root, config
        )
        reconstructed_rejections = []
        for row in validity_rows:
            try:
                verify_poison_payload(row)
                if row.get("formal_rejection"):
                    rejection = verify_formal_rejection(
                        row["formal_rejection"],
                        policy=parent,
                        registries=registries,
                    )
                    if rejection["poison_payload"] != {
                        key: value for key, value in row.items()
                        if key not in {
                            "validity",
                            "formal_rejection",
                        }
                    }:
                        raise RoundAuditViolation(
                            "formal rejection poison payload mismatch"
                        )
                    validity = verify_formal_rejection_validity(
                        row.get("validity") or {},
                        rejection,
                    )
                    bundle = rejection["execution_bundle"]
                    reconstructed_rejections.append(rejection)
                elif row.get("grounded_authority_bundle"):
                    authority = verify_arena_grounded_authority(
                        row["grounded_authority_bundle"],
                        policy=parent,
                        registries=registries,
                    )
                    bundle = authority["execution_bundle"]
                    validity = verify_grounded_arena_validity(
                        row.get("validity") or {},
                        authority_bundle=authority,
                    )
                elif (
                    validity_authority
                    == GROUNDED_ARENA_COMPAT_AUTHORITY
                ):
                    bundle = verify_grounded_execution_bundle(
                        row.get("grounded_execution_bundle") or {},
                        policy=parent,
                        registries=registries,
                    )
                    validity = (
                        verify_legacy_grounded_arena_validity(
                            row.get("validity") or {},
                            execution_bundle=bundle,
                        )
                    )
                else:
                    raise RoundAuditViolation(
                        "Grounded Runtime authority bundle is missing"
                    )
            except Exception as exc:
                raise RoundAuditViolation(
                    "Grounded validity row cannot be reconstructed"
                ) from exc
            if (
                row.get("grounded_plan_hash")
                != bundle["plan"]["plan_hash"]
                or row.get("poison_id")
                != (
                    bundle["plan"].get("plan_id")
                    or bundle["plan"].get("poison_id")
                )
            ):
                raise RoundAuditViolation(
                    "Grounded validity row is not cross-bound"
                )
            if (
                row.get("formal_rejection") is None
                and validity["evidence"]["authority_mode"]
                != ARENA_GROUNDED_AUTHORITY
            ):
                raise RoundAuditViolation(
                    "Grounded validity authority mode mismatch"
                )
        if reconstructed_rejections != formal_rejections:
            raise RoundAuditViolation(
                "round formal rejection ledger mismatch"
            )
        rejected_archive = load_formal_rejection_archive(
            project_root
            / config.get(
                "red_rejected_archive",
                "runtime/archives/red_rejected_archive.jsonl",
            ),
            registries=registries,
        )
        archived_hashes = {
            row["rejection_hash"] for row in rejected_archive
        }
        if any(
            row["rejection_hash"] not in archived_hashes
            for row in formal_rejections
        ):
            raise RoundAuditViolation(
                "formal rejection is missing from rejected archive"
            )
    else:
        for row in validity_rows:
            verify_poison_payload(row)
            rebuilt = validity_gate(row)
            expected = {
                "proven_valid": rebuilt.proven_valid,
                "checks": rebuilt.checks,
                "rejection_reasons": rebuilt.rejection_reasons,
                "evidence": rebuilt.evidence,
                "result_hash": rebuilt.result_hash,
            }
            if row.get("validity") != expected:
                raise RoundAuditViolation(
                    "legacy validity result cannot be reconstructed"
                )
    challenge_rows = _read_jsonl(
        root / "blue_challenge_results.jsonl"
    )
    blue_evaluation_authority = str(
        config.get(
            "blue_evaluation_authority",
            LEGACY_BLUE_EVALUATION_AUTHORITY,
        )
    )
    for challenge in challenge_rows:
        if challenge.get("challenge_result_hash") != hash_payload({
            key: value for key, value in challenge.items()
            if key != "challenge_result_hash"
        }):
            raise RoundAuditViolation(
                "blue challenge result hash mismatch"
            )
    blue_portfolio_audit: dict[str, Any] = {}
    if blue_evaluation_authority == CANDIDATE_PORTFOLIO_AUTHORITY:
        if validity_authority not in GROUNDED_ARENA_AUTHORITIES:
            raise RoundAuditViolation(
                "candidate portfolio lacks Grounded validity"
            )
        registry = load_lens_registry(
            project_root
            / config.get(
                "blue_lens_registry",
                "configs/blue/lens_registry_v1.json",
            ),
            project_root=project_root,
        )
        portfolio = CandidatePortfolio.from_dict(read_json(
            project_root / config["blue_candidate_portfolio"]
        ))
        router = None
        router_path_value = str(
            config.get("blue_descriptor_router") or ""
        )
        if portfolio.mode == "descriptor_routed":
            if not router_path_value:
                raise RoundAuditViolation(
                    "descriptor portfolio lacks frozen router config"
                )
            router = load_descriptor_router(
                project_root / router_path_value
            )
            if router.router_hash != portfolio.router_hash:
                raise RoundAuditViolation(
                    "audited portfolio/router binding mismatch"
                )
        elif router_path_value:
            raise RoundAuditViolation(
                "static portfolio declares descriptor router"
            )
        allocator = None
        allocator_hash = ""
        if portfolio.mode == "adaptive":
            allocator_path_value = str(
                config.get("blue_offline_allocator_state") or ""
            )
            allocator_path = project_root / allocator_path_value
            if (
                not allocator_path_value
                or not allocator_path.is_file()
            ):
                raise RoundAuditViolation(
                    "adaptive portfolio lacks frozen allocator state"
                )
            allocator = OfflineAdaptiveAllocator(
                load_offline_allocator_state(allocator_path)
            )
            allocator_hash = str(
                config.get("blue_offline_allocator_hash") or ""
            )
            if (
                allocator.allocator_hash != allocator_hash
                or allocator_hash != portfolio.allocator_hash
            ):
                raise RoundAuditViolation(
                    "audited offline allocator binding mismatch"
                )
        elif config.get("blue_offline_allocator_state"):
            raise RoundAuditViolation(
                "non-adaptive portfolio declares offline allocator"
            )
        if (
            parent.candidate_portfolio_binding
            != build_candidate_portfolio_binding(portfolio)
        ):
            raise RoundAuditViolation(
                "active policy does not authorize audited portfolio"
            )
        provider_fingerprint = validate_toolchain_fingerprint(
            config.get("blue_candidate_provider_toolchain_fingerprint")
            or {}
        )
        verifier_hash = str(
            config.get("blue_candidate_verifier_hash") or ""
        )
        semantic_provider_hash = str(
            config.get("blue_semantic_signature_provider_hash")
            or portfolio.semantic_signature_provider_hash
        )
        if (
            semantic_provider_hash
            != portfolio.semantic_signature_provider_hash
        ):
            raise RoundAuditViolation(
                "semantic signature provider/portfolio binding mismatch"
            )
        authority_hash = hash_payload({
            "authority": CANDIDATE_PORTFOLIO_AUTHORITY,
            "lens_registry_hash": registry.registry_hash,
            "portfolio_hash": portfolio.portfolio_hash,
            "effective_portfolio_hash": (
                portfolio.effective_portfolio_hash
            ),
            "descriptor_router_hash": (
                router.router_hash if router is not None else ""
            ),
            "provider_toolchain_fingerprint": provider_fingerprint,
            "verifier_hash": verifier_hash,
            "semantic_signature_provider_hash": semantic_provider_hash,
            "offline_allocator_hash": allocator_hash,
        })
        round_toolchain = context["toolchain_fingerprint"]
        if (
            round_toolchain.get("schema_version")
            != "r3e-arena-composite-toolchain-v1"
            or round_toolchain.get("blue_candidate_provider")
            != provider_fingerprint
            or round_toolchain.get("blue_candidate_verifier_hash")
            != verifier_hash
            or round_toolchain.get(
                "blue_semantic_signature_provider_hash"
            )
            != semantic_provider_hash
            or round_toolchain.get("blue_offline_allocator_hash")
            != allocator_hash
            or round_toolchain.get("blue_descriptor_router_hash")
            != (router.router_hash if router is not None else "")
            or round_toolchain.get("blue_portfolio_authority_hash")
            != authority_hash
        ):
            raise RoundAuditViolation(
                "round toolchain does not bind portfolio authority"
            )
        for challenge in challenge_rows:
            try:
                descriptor = extract_grounded_failure_descriptor(challenge)
            except Exception as exc:
                raise RoundAuditViolation(
                    "portfolio challenge lacks bound Grounded descriptor"
                ) from exc
            blue_results = challenge.get("blue_results")
            configured_seeds = [
                int(seed)
                for seed in config.get("challenge_seeds", [1, 2, 3])
            ]
            if (
                not isinstance(blue_results, list)
                or challenge.get("challenged_policy_hash")
                != parent.policy_hash
                or challenge.get("challenge_budget_hash")
                != hash_payload(parent.budgets)
                or challenge.get("repair_attempts") != len(blue_results)
                or challenge.get("repair_successes") != sum(
                    bool(row.get("oracle_ok")) for row in blue_results
                )
                or [row.get("seed") for row in blue_results]
                != configured_seeds
            ):
                raise RoundAuditViolation(
                    "portfolio challenge aggregate cannot be reconstructed"
                )
            try:
                portfolio_statistics = (
                    build_challenge_portfolio_statistics(blue_results)
                )
            except Exception as exc:
                raise RoundAuditViolation(
                    "portfolio challenge statistics are invalid"
                ) from exc
            if (
                challenge.get("portfolio_statistics")
                != portfolio_statistics
            ):
                raise RoundAuditViolation(
                    "portfolio challenge statistics cannot be reconstructed"
                )
            for result in blue_results:
                if result.get("descriptor_hash") != descriptor.get(
                    "descriptor_hash"
                ):
                    raise RoundAuditViolation(
                        "BlueEvaluation descriptor differs from challenge"
                    )
                try:
                    verify_blue_evaluation(
                        result,
                        policy=parent,
                        registry=registry,
                        portfolio=portfolio,
                        provider_fingerprints=[provider_fingerprint],
                        expected_verifier_hash=verifier_hash,
                        expected_semantic_signature_provider_hash=(
                            semantic_provider_hash
                        ),
                        router=router,
                        allocator=allocator,
                        descriptor=descriptor,
                    )
                except Exception as exc:
                    raise RoundAuditViolation(
                        "BlueEvaluation V2 cannot be reconstructed"
                    ) from exc
        blue_portfolio_audit = {
            "blue_portfolio_authority_hash": authority_hash,
            "blue_lens_registry_hash": registry.registry_hash,
            "blue_candidate_portfolio_hash": portfolio.portfolio_hash,
            "blue_effective_portfolio_hash": (
                portfolio.effective_portfolio_hash
            ),
            "blue_descriptor_router_hash": (
                router.router_hash if router is not None else ""
            ),
            "blue_semantic_signature_provider_hash": (
                semantic_provider_hash
            ),
            "blue_offline_allocator_hash": allocator_hash,
        }
    elif blue_evaluation_authority != LEGACY_BLUE_EVALUATION_AUTHORITY:
        raise RoundAuditViolation(
            "unsupported blue evaluation authority"
        )
    coverage_audit: dict[str, Any] = {}
    if validity_authority in GROUNDED_ARENA_AUTHORITIES:
        generated = _read_jsonl(root / "red_candidates.jsonl")
        planned = _read_jsonl(
            root / "planned_red_candidates.jsonl"
        )
        coverage_before = verify_coverage_state(
            read_json(root / "coverage_state_before.json")
        )
        plan = verify_coverage_plan(
            read_json(root / "coverage_plan.json"),
            candidates=generated,
            coverage_state=coverage_before,
            policy=parent,
        )
        generated_by_id = {
            str(row["poison_id"]): row for row in generated
        }
        expected_planned = [
            generated_by_id[poison_id]
            for poison_id in plan["selected_poison_ids"]
        ]
        if planned != expected_planned or {
            str(row["poison_id"]) for row in validity_rows
        } != set(plan["selected_poison_ids"]):
            raise RoundAuditViolation(
                "coverage planner selection cannot be reconstructed"
            )
        difficulty_rows = _read_jsonl(
            root / "difficulty_profiles.jsonl"
        )
        family_by_id = {
            str(entry["poison_id"]): str(
                entry["cell"]["family_id"]
            )
            for entry in plan["candidate_entries"]
        }
        expected_difficulty = []
        for challenge in challenge_rows:
            poison_id = str(challenge["poison_id"])
            ambiguity = sum(
                family == family_by_id[poison_id]
                for family in family_by_id.values()
            )
            profile = difficulty_profile_from_challenge(
                challenge,
                candidate_ambiguity=ambiguity,
            )
            value = {
                "schema_version": (
                    "r3e-grounded-difficulty-record-v1"
                ),
                "round_id": context["round_id"],
                "poison_id": poison_id,
                "challenge_result_hash": challenge[
                    "challenge_result_hash"
                ],
                "profile": profile,
            }
            value["record_hash"] = hash_payload(value)
            expected_difficulty.append(value)
        if difficulty_rows != expected_difficulty:
            raise RoundAuditViolation(
                "difficulty curriculum evidence cannot be reconstructed"
            )
        coverage_after = update_coverage_state(
            coverage_before,
            plan=plan,
            validity_rows=validity_rows,
            challenge_rows=challenge_rows,
            round_id=context["round_id"],
        )
        if coverage_after != verify_coverage_state(
            read_json(root / "coverage_state_after.json")
        ):
            raise RoundAuditViolation(
                "coverage state transition cannot be reconstructed"
            )
        coverage_audit = {
            "coverage_plan_hash": plan["plan_hash"],
            "coverage_state_hash_before": coverage_before[
                "coverage_hash"
            ],
            "coverage_state_hash_after": coverage_after[
                "coverage_hash"
            ],
            "difficulty_profiles_hash": hash_payload(
                difficulty_rows
            ),
        }
    rejected_poison_ids = {
        row["poison_payload"]["poison_id"]
        for row in formal_rejections
    }
    if rejected_poison_ids.intersection({
        str(row.get("poison_id") or "") for row in challenge_rows
    }):
        raise RoundAuditViolation(
            "formal-rejected poison reached blue challenge"
        )
    try:
        expected_episodes = [
            episode_from_challenge(
                row,
                policy=parent,
                round_id=context["round_id"],
            )
            for row in challenge_rows
        ]
        expected_episodes.extend(
            episode_from_formal_rejection(
                row,
                policy=parent,
                round_id=context["round_id"],
            )
            for row in formal_rejections
        )
    except Exception as exc:
        raise RoundAuditViolation(
            "verified episodes cannot be reconstructed"
        ) from exc
    episode_manifest = read_json(root / "verified_episodes.json")
    expected_episode_manifest = {
        "schema_version": "r3e-round-episode-manifest-v1",
        "round_id": context["round_id"],
        "challenged_policy_hash": parent.policy_hash,
        "episode_ids": [
            episode.episode_id for episode in expected_episodes
        ],
        "episode_hashes": [
            episode.episode_hash for episode in expected_episodes
        ],
    }
    expected_episode_manifest["manifest_hash"] = hash_payload(
        expected_episode_manifest
    )
    if episode_manifest != expected_episode_manifest:
        raise RoundAuditViolation(
            "verified episode manifest cannot be reconstructed"
        )
    episode_store = EpisodeStore(
        project_root
        / config.get("memory_root", "runtime/memory")
        / "episodes"
    )
    try:
        for expected_episode in expected_episodes:
            if (
                episode_store.get(
                    expected_episode.episode_id
                ).to_dict()
                != expected_episode.to_dict()
            ):
                raise RoundAuditViolation(
                    "stored verified episode differs from authority"
                )
    except Exception as exc:
        if isinstance(exc, RoundAuditViolation):
            raise
        raise RoundAuditViolation(
            "stored verified episode cannot be audited"
        ) from exc
    red_context = verify_red_search_context(
        read_json(root / "red_search_context.json")
    )
    if red_context["challenged_policy_hash"] != parent.policy_hash:
        raise RoundAuditViolation("red search context policy binding mismatch")
    grounded_proposal_audit: dict[str, Any] = {}
    grounded_population_audit: dict[str, Any] = {}
    proposal_path = root / "grounded_proposal_plan.json"
    proposal_execution_path = (
        root / "grounded_proposal_execution.json"
    )
    proposal_archive_path = (
        root / "grounded_proposal_archive_view.json"
    )
    if config.get("grounded_pre_generation_planner"):
        if (
            registries is None
            or not proposal_path.is_file()
            or not proposal_execution_path.is_file()
            or not proposal_archive_path.is_file()
            or red_context.get("grounded_proposal_plan")
            != read_json(proposal_path)
        ):
            raise RoundAuditViolation(
                "Grounded proposal authority artifacts are missing"
            )
        try:
            proposal = verify_grounded_proposal_plan(
                read_json(proposal_path),
                policy=parent,
                registries=registries,
                coverage_state=read_json(
                    root / "coverage_state_before.json"
                ),
                archive_view=read_json(proposal_archive_path),
                memory_capability=red_context.get(
                    "memory_capability"
                ),
            )
            proposal_execution = verify_proposal_candidates(
                proposal,
                _read_jsonl(root / "red_candidates.jsonl"),
                policy=parent,
            )
        except GroundedProposalViolation as exc:
            raise RoundAuditViolation(
                "Grounded proposal authority cannot be reconstructed"
            ) from exc
        if (
            proposal_execution
            != read_json(proposal_execution_path)
            or context["toolchain_fingerprint"].get(
                "grounded_proposal_authority_hash"
            )
            != grounded_proposal_protocol_hash(registries)
        ):
            raise RoundAuditViolation(
                "Grounded proposal execution/toolchain mismatch"
            )
        grounded_proposal_audit = {
            "grounded_proposal_plan_hash": proposal["plan_hash"],
            "grounded_proposal_execution_hash": (
                proposal_execution["execution_hash"]
            ),
            "grounded_proposal_selected_count": len(
                proposal["selected_intent_ids"]
            ),
        }
    elif (
        red_context.get("grounded_proposal_plan") is not None
        or proposal_path.exists()
        or proposal_execution_path.exists()
    ):
        raise RoundAuditViolation(
            "Grounded proposal authority exists without config permission"
        )
    population_schedule_path = (
        root / "grounded_population_schedule.json"
    )
    population_execution_path = (
        root / "grounded_population_execution.json"
    )
    if config.get("grounded_population_scheduler"):
        if (
            not config.get("grounded_pre_generation_planner")
            or not population_schedule_path.is_file()
            or not population_execution_path.is_file()
            or grounded_proposal_audit == {}
        ):
            raise RoundAuditViolation(
                "Grounded population authority artifacts are missing"
            )
        population_config_path = project_root / config.get(
            "grounded_population_config",
            "configs/red/deterministic_population_v1.json",
        )
        if (
            not population_config_path.is_file()
            and "grounded_population_config" not in config
        ):
            population_config_path = (
                Path(__file__).resolve().parents[2]
                / "configs/red/deterministic_population_v1.json"
            )
        try:
            population_config = load_population_config(
                population_config_path
            )
            schedule = verify_population_schedule(
                read_json(population_schedule_path),
                policy=parent,
                proposal_plan=proposal,
                config=population_config,
            )
            execution = verify_population_candidates(
                schedule,
                _read_jsonl(root / "red_candidates.jsonl"),
                policy=parent,
            )
        except GroundedPopulationViolation as exc:
            raise RoundAuditViolation(
                "Grounded population authority cannot be reconstructed"
            ) from exc
        if (
            red_context.get("grounded_population_schedule") != schedule
            or read_json(population_execution_path) != execution
            or context["toolchain_fingerprint"].get(
                "grounded_population_authority_hash"
            )
            != population_protocol_hash(population_config)
            or context["toolchain_fingerprint"].get(
                "grounded_population_config_hash"
            )
            != population_config["config_hash"]
        ):
            raise RoundAuditViolation(
                "Grounded population execution/toolchain mismatch"
            )
        grounded_population_audit = {
            "grounded_population_schedule_hash": schedule[
                "schedule_hash"
            ],
            "grounded_population_execution_hash": execution[
                "execution_hash"
            ],
            "grounded_population_config_hash": population_config[
                "config_hash"
            ],
            "grounded_population_arm": schedule["scheduler_mode"],
            "grounded_population_provider_usage_hash": hash_payload(
                execution["provider_usage"]
            ),
        }
    elif (
        red_context.get("grounded_population_schedule") is not None
        or population_schedule_path.exists()
        or population_execution_path.exists()
    ):
        raise RoundAuditViolation(
            "Grounded population authority exists without config permission"
        )
    portfolio_red_audit: dict[str, Any] = {}
    portfolio_capability = red_context.get("portfolio_capability")
    portfolio_authority_path = root / "portfolio_red_authority.json"
    if config.get("portfolio_aware_red_challenge"):
        if (
            not isinstance(portfolio_capability, dict)
            or not portfolio_authority_path.is_file()
        ):
            raise RoundAuditViolation(
                "portfolio red authority artifacts are missing"
            )
        try:
            rebuilt_portfolio_red = build_portfolio_red_authority(
                policy=parent,
                packet=portfolio_capability,
                poisons=_read_jsonl(root / "red_candidates.jsonl"),
                toolchain_fingerprint=context[
                    "toolchain_fingerprint"
                ],
            )
        except PortfolioChallengeViolation as exc:
            raise RoundAuditViolation(
                "portfolio red authority cannot be reconstructed"
            ) from exc
        if read_json(portfolio_authority_path) != rebuilt_portfolio_red:
            raise RoundAuditViolation(
                "frozen portfolio red authority differs from reconstruction"
            )
        portfolio_red_audit = {
            "portfolio_red_authority_record_hash": (
                rebuilt_portfolio_red["authority_record_hash"]
            ),
            "portfolio_red_capability_packet_hash": (
                rebuilt_portfolio_red["capability_packet_hash"]
            ),
            "portfolio_red_candidate_count": (
                rebuilt_portfolio_red["candidate_count"]
            ),
            "portfolio_red_operator_counts": (
                rebuilt_portfolio_red["operator_counts"]
            ),
        }
    elif (
        portfolio_capability is not None
        or portfolio_authority_path.exists()
    ):
        raise RoundAuditViolation(
            "portfolio red authority is present without config permission"
        )
    registry_before = validate_registry(read_json(root / "registry_before.json"))
    if registry_before["registry_hash"] != context["registry_hash_before"]:
        raise RoundAuditViolation("registry_before does not match frozen run context")
    residual = _manifest(root / "residual_manifest.json")
    archive_updates = _read_jsonl(root / "archive_updates.jsonl")
    accumulated = _read_jsonl(root / "accumulated_residuals.jsonl")
    accumulation = read_json(root / "residual_accumulation.json")
    covered_updates = _read_jsonl(root / "covered_archive_updates.jsonl")
    archive_exclusions = _read_jsonl(root / "archive_exclusions.jsonl")
    selection = read_json(root / "residual_selection.json")
    if selection.get("schema_version") != "r3e-residual-selection-v2":
        raise RoundAuditViolation("residual selection schema mismatch")
    selection_body = {
        key: value for key, value in selection.items() if key != "selection_hash"
    }
    if selection.get("selection_hash") != hash_payload(selection_body):
        raise RoundAuditViolation("residual selection hash mismatch")
    accumulation_body = {
        key: value for key, value in accumulation.items()
        if key != "accumulation_hash"
    }
    if (
        accumulation.get("schema_version") != "r3e-residual-accumulation-v1"
        or accumulation.get("accumulation_hash") != hash_payload(accumulation_body)
        or accumulation.get("accumulated_rows_hash") != hash_payload(accumulated)
        or accumulation.get("current_round_updates_hash") != hash_payload(archive_updates)
        or int(accumulation.get("row_count", -1)) != len(accumulated)
    ):
        raise RoundAuditViolation("residual accumulation hash mismatch")
    for row in accumulated:
        row_body = {
            key: value for key, value in row.items()
            if key != "archive_entry_hash"
        }
        if row.get("archive_entry_hash") != hash_payload(row_body):
            raise RoundAuditViolation("accumulated archive entry hash mismatch")
    if accumulation.get("archive_entry_hashes") != [
        str(row.get("archive_entry_hash") or "") for row in accumulated
    ]:
        raise RoundAuditViolation("residual accumulation entry list mismatch")
    if accumulation.get("included_round_ids") != sorted({
        str(row.get("discovered_round") or "")
        for row in accumulated if row.get("discovered_round")
    }):
        raise RoundAuditViolation("residual accumulation round list mismatch")
    if any(
        row.get("challenged_policy_hash") != parent.policy_hash
        for row in accumulated
    ):
        raise RoundAuditViolation("residual accumulation mixed policy hashes")
    if (
        selection.get("current_round_updates_hash") != hash_payload(archive_updates)
        or selection.get("accumulated_rows_hash") != hash_payload(accumulated)
        or selection.get("accumulation_hash") != accumulation["accumulation_hash"]
    ):
        raise RoundAuditViolation("residual selection accumulation mismatch")
    if selection.get("challenged_policy_hash") != parent.policy_hash:
        raise RoundAuditViolation("residual selection policy binding mismatch")
    selected_ids = {str(row.get("poison_id") or "") for row in residual["rows"]}
    recorded_ids = {str(value) for value in selection.get("selected_poison_ids") or []}
    expected_order = [
        str(row.get("poison_id") or "")
        for row in select_residual_elites(accumulated)
    ]
    if (
        selected_ids != recorded_ids
        or len(selected_ids) != int(selection.get("selected_count", -1))
        or list(selection.get("selected_poison_ids") or []) != expected_order
    ):
        raise RoundAuditViolation("residual manifest does not match elite selection")
    if residual.get("metadata", {}).get("selection_hash") != selection["selection_hash"]:
        raise RoundAuditViolation("residual manifest selection hash mismatch")
    if (
        residual.get("metadata", {}).get("accumulation_hash")
        != accumulation["accumulation_hash"]
    ):
        raise RoundAuditViolation("residual manifest accumulation hash mismatch")
    adaptation = _manifest(root / "adaptation_manifest.json")
    target = _manifest(root / "target_manifest.json")
    non_target = _manifest(root / "non_target_manifest.json")
    promotion = _manifest(root / "promotion_manifest.json")
    if adaptation["source_residual_manifest_hash"] != residual["manifest_hash"]:
        raise RoundAuditViolation("adaptation manifest source mismatch")
    if target["source_residual_manifest_hash"] != residual["manifest_hash"]:
        raise RoundAuditViolation("target manifest source mismatch")
    adaptation_designs = {
        str(row.get("design") or row.get("design_id") or "")
        for row in adaptation["rows"]
    }
    target_designs = {
        str(row.get("design") or row.get("design_id") or "")
        for row in target["rows"]
    }
    if adaptation_designs & target_designs:
        raise RoundAuditViolation("adaptation/target design leakage")
    for row in adaptation["rows"]:
        leaked = FORBIDDEN_INPUT_KEYS & row.keys()
        if leaked:
            raise RoundAuditViolation(
                f"adaptation row contains hidden fields: {sorted(leaked)}"
            )
    if (
        promotion.get("metadata", {}).get("non_target_manifest_hash")
        != non_target["manifest_hash"]
    ):
        raise RoundAuditViolation("promotion/non-target manifest binding mismatch")
    if promotion["source_residual_manifest_hash"] != target["manifest_hash"]:
        raise RoundAuditViolation("promotion/target manifest binding mismatch")

    children = {
        child.policy_id: child
        for child in (
            PolicyState.from_dict(row)
            for row in _read_jsonl(root / "child_policies.jsonl")
        )
    }
    for child in children.values():
        if child.parent_policy_hash != parent.policy_hash:
            raise RoundAuditViolation("child is not bound to audited parent")
        if child.created_from_residual_manifest_hash != residual["manifest_hash"]:
            raise RoundAuditViolation("child residual-manifest binding mismatch")

    replay_rows = _read_jsonl(root / "paired_validation.jsonl")
    decisions = _read_jsonl(root / "promotion_decisions.jsonl")
    promoted_candidates = {
        str(row.get("policy_id") or "") for row in promotion["rows"]
    }
    decided_candidates = {
        str(row.get("candidate_policy_id") or "") for row in decisions
    }
    if promoted_candidates != decided_candidates:
        raise RoundAuditViolation("promotion candidates and decisions differ")
    reconstructed = []
    for decision in decisions:
        candidate_id = str(decision.get("candidate_policy_id") or "")
        child = children.get(candidate_id)
        if child is None:
            raise RoundAuditViolation(f"decision candidate is missing: {candidate_id}")
        rows = [
            row for row in replay_rows
            if row.get("candidate_policy_id") == candidate_id
        ]
        provenance = decision.get("provenance") or {}
        expected_provenance = {
            "round_id": context["round_id"],
            "residual_manifest_hash": residual["manifest_hash"],
            "adaptation_manifest_hash": adaptation["manifest_hash"],
            "target_manifest_hash": target["manifest_hash"],
            "non_target_manifest_hash": non_target["manifest_hash"],
            "paired_result_hash": hash_payload(rows),
            "code_commit_sha": context["code_version"],
            "run_context_hash": context["run_context_hash"],
            "toolchain_fingerprint_hash": context["toolchain_fingerprint_hash"],
            "toolchain_fingerprint": context["toolchain_fingerprint"],
        }
        if provenance != expected_provenance:
            raise RoundAuditViolation(
                f"promotion provenance mismatch: {candidate_id}"
            )
        if decision.get("validation_manifest_hash") != promotion["manifest_hash"]:
            raise RoundAuditViolation(
                f"promotion validation manifest mismatch: {candidate_id}"
            )
        rebuilt = decide_policy_promotion(
            parent,
            child,
            rows,
            validation_manifest_hash=str(decision["validation_manifest_hash"]),
            thresholds=deepcopy(decision.get("thresholds") or {}),
            provenance=deepcopy(provenance),
        )
        if rebuilt != decision:
            raise RoundAuditViolation(
                f"promotion decision cannot be reconstructed: {candidate_id}"
            )
        reconstructed.append(rebuilt)
    winner = read_json(root / "winner.json")
    rebuilt_winner = select_single_promotable_child(reconstructed) or {}
    if winner != rebuilt_winner:
        raise RoundAuditViolation("recorded winner does not match reconstructed decisions")

    learnability_rows = _read_jsonl(root / "learnability_results.jsonl")
    learnability_by_id = {}
    for row in learnability_rows:
        verified = verify_learnability_result(row)
        poison_id = verified["poison_id"]
        if poison_id in learnability_by_id:
            raise RoundAuditViolation("duplicate learnability result")
        learnability_by_id[poison_id] = verified
    for row in archive_updates:
        evidence = row.get("learnability")
        poison_id = str(row.get("poison_id") or "")
        if evidence != learnability_by_id.get(poison_id):
            raise RoundAuditViolation("residual learnability evidence mismatch")

    registry_after = validate_registry(read_json(root / "registry_after.json"))
    active_after = get_active_policy(registry_after)
    if winner:
        if (
            active_after.policy_id != winner.get("candidate_policy_id")
            or active_after.policy_hash != winner.get("candidate_policy_hash")
        ):
            raise RoundAuditViolation("registry active policy is not the audited winner")
    elif active_after.policy_hash != parent.policy_hash:
        raise RoundAuditViolation("registry changed active policy without a winner")
    renewed = read_json(root / "renewed_challenge_binding.json")
    if renewed.get("challenged_policy_hash") != active_after.policy_hash:
        raise RoundAuditViolation("renewed challenge is not bound to final active policy")
    promotion_enabled = bool(
        config.get("policy_promotion_enabled", True)
    )
    promotion_eligible = (
        promotion_enabled and target["row_count"] > 0
    )
    record = {
        "schema_version": ROUND_AUDIT_SCHEMA_VERSION,
        "round_id": context["round_id"],
        "code_version": context["code_version"],
        "run_context_hash": context["run_context_hash"],
        "toolchain_fingerprint_hash": context["toolchain_fingerprint_hash"],
        "red_search_context_hash": red_context["context_hash"],
        "parent_policy_id": parent.policy_id,
        "parent_policy_hash": parent.policy_hash,
        "active_policy_id": active_after.policy_id,
        "active_policy_hash": active_after.policy_hash,
        "registry_hash_before": registry_before["registry_hash"],
        "registry_hash_after": registry_after["registry_hash"],
        "residual_manifest_hash": residual["manifest_hash"],
        "residual_selection_hash": selection["selection_hash"],
        "residual_archive_updates_hash": hash_payload(archive_updates),
        "validity_authority": validity_authority,
        "blue_evaluation_authority": blue_evaluation_authority,
        "validity_results_hash": hash_payload(validity_rows),
        "blue_challenge_results_hash": hash_payload(
            challenge_rows
        ),
        "verified_episode_manifest_hash": episode_manifest[
            "manifest_hash"
        ],
        "covered_archive_updates_hash": hash_payload(covered_updates),
        "archive_exclusions_hash": hash_payload(archive_exclusions),
        "learnability_results_hash": hash_payload(learnability_rows),
        "adaptation_manifest_hash": adaptation["manifest_hash"],
        "target_manifest_hash": target["manifest_hash"],
        "non_target_manifest_hash": non_target["manifest_hash"],
        "promotion_manifest_hash": promotion["manifest_hash"],
        "paired_result_hash": hash_payload(replay_rows),
        "decisions_hash": hash_payload(decisions),
        "winner_decision_hash": str(winner.get("decision_hash") or ""),
        "promoted": active_after.policy_hash != parent.policy_hash,
        "promotion_eligible": promotion_eligible,
        "defer_reason": (
            ""
            if promotion_eligible
            else (
                "promotion_epoch_reserved"
                if not promotion_enabled
                else "insufficient_residual_designs"
            )
        ),
    }
    if formal_rejections_path.is_file():
        record.update({
            "formal_rejection_count": len(formal_rejections),
            "formal_rejections_hash": hash_payload(
                formal_rejections
            ),
        })
    record.update(coverage_audit)
    record.update(blue_portfolio_audit)
    record.update(portfolio_red_audit)
    record.update(grounded_proposal_audit)
    record.update(grounded_population_audit)
    record["audit_record_hash"] = hash_payload(record)
    return record


def freeze_round_audit(round_dir: str | Path) -> dict[str, Any]:
    root = Path(round_dir)
    target = root / "round_audit.json"
    rebuilt = reconstruct_round(root)
    if target.exists():
        existing = read_json(target)
        if existing != rebuilt:
            raise RoundAuditViolation("frozen round audit differs from reconstruction")
        return existing
    atomic_write_json(target, rebuilt)
    return rebuilt


def append_round_ledger(path: str | Path, audit: dict[str, Any]) -> dict[str, Any]:
    """Idempotently append one completed round to a hash-chain ledger."""
    target = Path(path)
    with writer_lock(target.with_suffix(target.suffix + ".lock")):
        existing = read_ledger(target)
        same_round = [
            row for row in existing
            if row.get("round_id") == audit.get("round_id")
        ]
        if same_round:
            if (
                len(same_round) != 1
                or same_round[0].get("audit_record_hash")
                != audit.get("audit_record_hash")
            ):
                raise RoundAuditViolation("round ledger contains a conflicting round")
            return same_round[0]
        payload = {
            "operation": "round_complete",
            "round_id": audit["round_id"],
            "audit_record_hash": audit["audit_record_hash"],
            "parent_policy_hash": audit["parent_policy_hash"],
            "active_policy_hash": audit["active_policy_hash"],
            "registry_hash_before": audit["registry_hash_before"],
            "registry_hash_after": audit["registry_hash_after"],
            "timestamp": utc_now(),
            "ledger_index": len(existing),
            "previous_entry_hash": (
                existing[-1]["ledger_entry_hash"] if existing else ""
            ),
        }
        payload["ledger_entry_hash"] = hash_payload(payload)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as stream:
            stream.write(canonical_json(payload) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return payload


def verify_frozen_round(round_dir: str | Path) -> dict[str, Any]:
    target = Path(round_dir) / "round_audit.json"
    if not target.is_file():
        raise RoundAuditViolation("frozen round audit is missing")
    existing = read_json(target)
    rebuilt = reconstruct_round(round_dir)
    if existing != rebuilt:
        raise RoundAuditViolation("round artifacts differ from frozen audit")
    return rebuilt


def _main() -> None:
    parser = argparse.ArgumentParser(description="Reconstruct and audit an R³E round")
    parser.add_argument("--round-dir", required=True)
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()
    result = (
        freeze_round_audit(args.round_dir)
        if args.freeze
        else verify_frozen_round(args.round_dir)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
