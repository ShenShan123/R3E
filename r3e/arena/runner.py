"""Resumable whole-policy evolution round runner.

Tool- and model-specific behavior is supplied by a frozen adapter object. The
runner owns authority, manifests, hashing, state transitions, and registry
mutation; adapters only generate/evaluate individual cases.
"""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import Any

from r3e.policy.promotion import (
    build_policy_promotion_bundle,
    decide_policy_promotion,
    select_single_promotable_child,
)
from r3e.memory.episode_builder import append_round_episodes
from r3e.memory.episode_store import EpisodeStore
from r3e.memory.memory_store import MemoryStore
from r3e.memory.bank_store import ActiveBankStore
from r3e.memory.activation_guard import ActivationGuard
from r3e.memory.plan_compiler import MemoryAwarePlanCompiler
from r3e.memory.retriever import MemoryRetriever
from r3e.memory.schema import (
    ANALYZERS,
    BudgetEnvelope,
    FailureDescriptor,
    RuntimeContext,
)
from r3e.policy.registry_v2 import (
    get_active_policy,
    load_registry,
    promote_policy,
    register_candidate,
    reject_policy,
)
from r3e.policy.search import propose_children
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import (
    atomic_write_json,
    atomic_write_jsonl,
    hash_payload,
    read_json,
)
from r3e.protocol.events import EventLogger, detect_code_version
from r3e.protocol.provenance import build_run_context, verify_run_context
from r3e.red.archive import load_archive, update_archive
from r3e.red.challenge import evaluate_challenge
from r3e.red.feedback_packet import (
    build_red_search_context,
    verify_red_search_context,
)
from r3e.red.learnability import validate_learnability_result
from r3e.red.novelty import archive_cell, novelty_score
from r3e.red.operators import (
    LineageOperatorViolation,
    load_operator_space,
    make_lineage_plan,
    verify_lineage_execution,
)
from r3e.red.selection import select_residual_elites
from r3e.red.validity import validity_gate
from r3e.red.poison_payload import (
    PoisonPayloadViolation,
    bind_poison_payload,
    verify_poison_payload,
)
from r3e.red.memory_challenge import build_memory_capability_packet
from r3e.red.portfolio_challenge import (
    PortfolioChallengeViolation,
    build_portfolio_red_authority,
    build_portfolio_coverage_packet,
    portfolio_red_authority_hash,
    verify_portfolio_challenge_execution,
)
from r3e.red.grounded.formal_rejection import (
    append_formal_rejection,
)
from r3e.red.grounded.coverage import (
    build_coverage_plan,
    load_coverage_state,
    save_coverage_state,
    update_coverage_state,
)
from r3e.red.grounded.difficulty import (
    difficulty_profile_from_challenge,
)
from r3e.red.grounded.proposal_planner import (
    GroundedProposalViolation,
    build_grounded_proposal_plan,
    build_proposal_archive_view,
    grounded_proposal_protocol_hash,
    verify_proposal_candidates,
)
from r3e.red.grounded.population_scheduler import (
    GroundedPopulationViolation,
    build_population_schedule,
    load_population_config,
    population_protocol_hash,
    verify_population_candidates,
)

from .audit import append_round_ledger, freeze_round_audit
from .grounded_authority import (
    ARENA_VALIDITY_AUTHORITIES,
    GROUNDED_ARENA_AUTHORITIES,
    execute_grounded_arena_validity,
    load_arena_grounded_registries,
)
from .conformance import (
    AdapterConformanceGate,
    adapter_output_metadata,
    strip_adapter_metadata,
)
from .manifests import freeze_manifest, grouped_split, make_manifest, verify_manifest
from .paired_replay import paired_replay
from .portfolio import (
    BLUE_EVALUATION_AUTHORITIES,
    CANDIDATE_PORTFOLIO_AUTHORITY,
    LEGACY_BLUE_EVALUATION_AUTHORITY,
    ArenaCandidatePortfolioAuthority,
    extract_grounded_failure_descriptor,
)
from .renewed_challenge import assert_renewed_challenge_binding
from .round_state import RoundState, RoundStateViolation


class RoundRunnerViolation(RuntimeError):
    """Raised when the round config or adapter violates protocol boundaries."""


_VALIDITY_EVIDENCE_FIELDS = {
    "poison_id",
    "challenged_policy_hash",
    "poison_payload_hash",
    "formal_status",
    "golden_compile_ok",
    "golden_oracle_ok",
    "buggy_compile_ok",
    "buggy_functional_fail",
    "output_complete",
    "revert_oracle_ok",
    "fresh_output",
    "oracle_result_hash",
    "counterexample_hash",
    "toolchain_fingerprint_hash",
    "command_hash",
    "output_schema_version",
    "model_id",
    "budget_hash",
    "verifier_hash",
    "result_hash",
}


class _SplitEvolutionAdapter:
    """Bind separately supplied red/blue adapters to the runner protocol."""

    def __init__(self, red_adapter: Any, blue_adapter: Any):
        self.red_adapter = red_adapter
        self.blue_adapter = blue_adapter
        red_fingerprint = getattr(red_adapter, "toolchain_fingerprint", None)
        blue_fingerprint = getattr(blue_adapter, "toolchain_fingerprint", None)
        self.conformance_fingerprints = [
            red_fingerprint,
            blue_fingerprint,
        ]
        self.toolchain_fingerprint = {
            "schema_version": "r3e-split-adapter-toolchain-v1",
            "red": dict(red_fingerprint or {}),
            "blue": dict(blue_fingerprint or {}),
        }

    def generate_red(
        self,
        parent: PolicyState,
        config: dict[str, Any],
        red_search_context: dict[str, Any],
    ):
        return self.red_adapter.generate_red(parent, config, red_search_context)

    def prepare_validity(self, poison: dict[str, Any]):
        return self.red_adapter.prepare_validity(poison)

    def evaluate_blue(self, policy: PolicyState, poison: dict[str, Any], seed: int):
        return self.blue_adapter.evaluate_blue(policy, poison, seed)

    def probe_learnability(self, policy: PolicyState, poison: dict[str, Any]):
        method = getattr(self.red_adapter, "probe_learnability", None)
        if not callable(method):
            method = self.blue_adapter.probe_learnability
        return method(policy, poison)

    def screen_child(
        self,
        parent: PolicyState,
        child: PolicyState,
        adaptation_manifest: dict[str, Any],
    ):
        return self.blue_adapter.screen_child(parent, child, adaptation_manifest)

    def replay(self, policy: PolicyState, case: dict[str, Any], seed: int):
        return self.blue_adapter.replay(policy, case, seed)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    atomic_write_jsonl(path, rows)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _archive_duplicate(
    rows: list[dict[str, Any]], poison: dict[str, Any]
) -> dict[str, Any] | None:
    return next(
        (
            row for row in rows
            if row.get("challenged_policy_hash")
            == poison.get("challenged_policy_hash")
            and row.get("normalized_diff_hash")
            == poison.get("normalized_diff_hash")
            and row.get("failure_signature")
            == poison.get("failure_signature")
        ),
        None,
    )


def _load_adapter(spec: str, config: dict[str, Any]):
    if ":" not in spec:
        raise RoundRunnerViolation("adapter must use module:factory syntax")
    module_name, factory_name = spec.split(":", 1)
    factory = getattr(importlib.import_module(module_name), factory_name)
    return factory(config)


class EvolutionRoundRunner:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        round_id: str,
        adapter: Any,
        project_root: str | Path,
        candidate_provider: Any | None = None,
        candidate_verifier: Any | None = None,
        semantic_signature_provider: Any | None = None,
        offline_allocator: Any | None = None,
    ):
        self.config = config
        self.round_id = round_id
        self.adapter = adapter
        self.validity_authority = str(
            config.get(
                "validity_authority",
                "legacy_adapter_evidence_v1",
            )
        )
        if self.validity_authority not in ARENA_VALIDITY_AUTHORITIES:
            raise RoundRunnerViolation(
                "round validity authority is unsupported"
            )
        self.blue_evaluation_authority = str(
            config.get(
                "blue_evaluation_authority",
                LEGACY_BLUE_EVALUATION_AUTHORITY,
            )
        )
        if self.blue_evaluation_authority not in BLUE_EVALUATION_AUTHORITIES:
            raise RoundRunnerViolation(
                "round blue evaluation authority is unsupported"
            )
        required_methods = {
            "generate_red",
            "probe_learnability",
            "screen_child",
            "replay",
        }
        if self.blue_evaluation_authority == LEGACY_BLUE_EVALUATION_AUTHORITY:
            required_methods.add("evaluate_blue")
        if self.validity_authority not in GROUNDED_ARENA_AUTHORITIES:
            required_methods.add("prepare_validity")
        missing = sorted(
            name for name in required_methods if not callable(getattr(adapter, name, None))
        )
        if missing:
            raise RoundRunnerViolation(f"evolution adapter missing methods: {missing}")
        self.adapter_gate = AdapterConformanceGate(adapter)
        self.root = Path(project_root)
        self.candidate_portfolio_authority = None
        if self.blue_evaluation_authority == CANDIDATE_PORTFOLIO_AUTHORITY:
            if self.validity_authority not in GROUNDED_ARENA_AUTHORITIES:
                raise RoundRunnerViolation(
                    "candidate portfolio requires Grounded validity authority"
                )
            if candidate_provider is None or candidate_verifier is None:
                raise RoundRunnerViolation(
                    "candidate portfolio provider and verifier are required"
                )
            try:
                self.candidate_portfolio_authority = (
                    ArenaCandidatePortfolioAuthority(
                        project_root=self.root,
                        config=config,
                        provider=candidate_provider,
                        verifier=candidate_verifier,
                        semantic_signature_provider=(
                            semantic_signature_provider
                        ),
                        allocator=offline_allocator,
                    )
                )
            except Exception as exc:
                raise RoundRunnerViolation(str(exc)) from exc
        adapter_toolchain = dict(
            getattr(self.adapter, "toolchain_fingerprint", {}) or {}
        )
        self.round_toolchain_fingerprint: dict[str, Any] = adapter_toolchain
        if self.candidate_portfolio_authority is not None:
            self.round_toolchain_fingerprint = {
                "schema_version": "r3e-arena-composite-toolchain-v1",
                "evolution_adapter": adapter_toolchain,
                "blue_candidate_provider": (
                    self.candidate_portfolio_authority.provider_fingerprint
                ),
                "blue_candidate_verifier_hash": (
                    self.candidate_portfolio_authority.verifier_hash
                ),
                "blue_semantic_signature_provider_hash": (
                    self.candidate_portfolio_authority
                    .semantic_signature_provider_hash
                ),
                "blue_offline_allocator_hash": (
                    self.candidate_portfolio_authority
                    .allocator.allocator_hash
                    if (
                        self.candidate_portfolio_authority.allocator
                        is not None
                    )
                    else ""
                ),
                "blue_descriptor_router_hash": (
                    self.candidate_portfolio_authority.router.router_hash
                    if self.candidate_portfolio_authority.router is not None
                    else ""
                ),
                "blue_portfolio_authority_hash": (
                    self.candidate_portfolio_authority.authority_hash
                ),
                "blue_portfolio_template_registry_hash": (
                    self.candidate_portfolio_authority
                    .portfolio_template_registry.registry_hash
                    if (
                        self.candidate_portfolio_authority
                        .portfolio_template_registry is not None
                    )
                    else ""
                ),
            }
            if self.config.get("portfolio_aware_red_challenge"):
                self.round_toolchain_fingerprint[
                    "portfolio_red_authority_hash"
                ] = portfolio_red_authority_hash()
        self.round_dir = self.root / config.get("rounds_root", "runtime/rounds") / round_id
        self.round_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.root / config["policy_registry"]
        self.ledger_path = self.root / config.get(
            "decision_ledger", "runtime/registry/decision_ledger.jsonl"
        )
        self.archive_path = self.root / config.get(
            "red_archive", "runtime/archives/red_residual_archive.jsonl"
        )
        self.covered_archive_path = self.root / config.get(
            "covered_archive", "runtime/archives/red_covered_archive.jsonl"
        )
        self.rejected_archive_path = self.root / config.get(
            "red_rejected_archive",
            "runtime/archives/red_rejected_archive.jsonl",
        )
        self.coverage_state_path = self.root / config.get(
            "grounded_coverage_state",
            "runtime/red/grounded_coverage_state.json",
        )
        self.round_ledger_path = self.root / config.get(
            "round_ledger", "runtime/rounds/round_ledger.jsonl"
        )
        operator_space_path = self.root / config.get(
            "lineage_operator_space",
            "configs/red/lineage_operator_space_v1.json",
        )
        if not operator_space_path.is_file() and "lineage_operator_space" not in config:
            operator_space_path = (
                Path(__file__).resolve().parents[2]
                / "configs/red/lineage_operator_space_v1.json"
            )
        self.operator_space = load_operator_space(operator_space_path)
        self.grounded_registries = (
            load_arena_grounded_registries(self.root, config)
            if self.validity_authority in GROUNDED_ARENA_AUTHORITIES
            else None
        )
        if self.config.get("grounded_pre_generation_planner"):
            if self.grounded_registries is None:
                raise RoundRunnerViolation(
                    "pre-generation planner requires Grounded validity"
                )
            proposal_hash = grounded_proposal_protocol_hash(
                self.grounded_registries
            )
            if (
                self.round_toolchain_fingerprint.get("schema_version")
                == "r3e-arena-composite-toolchain-v1"
            ):
                self.round_toolchain_fingerprint[
                    "grounded_proposal_authority_hash"
                ] = proposal_hash
            else:
                self.round_toolchain_fingerprint = {
                    "schema_version": (
                        "r3e-arena-grounded-planner-toolchain-v1"
                    ),
                    "evolution_adapter": adapter_toolchain,
                    "grounded_proposal_authority_hash": proposal_hash,
                }
        self.population_config = None
        if self.config.get("grounded_population_scheduler"):
            if not self.config.get("grounded_pre_generation_planner"):
                raise RoundRunnerViolation(
                    "population scheduler requires proposal authority"
                )
            population_path = self.root / self.config.get(
                "grounded_population_config",
                "configs/red/deterministic_population_v1.json",
            )
            if (
                not population_path.is_file()
                and "grounded_population_config" not in self.config
            ):
                population_path = (
                    Path(__file__).resolve().parents[2]
                    / "configs/red/deterministic_population_v1.json"
                )
            try:
                self.population_config = load_population_config(
                    population_path
                )
            except GroundedPopulationViolation as exc:
                raise RoundRunnerViolation(str(exc)) from exc
            self.round_toolchain_fingerprint.update({
                "schema_version": (
                    "r3e-arena-grounded-population-toolchain-v1"
                ),
                "grounded_population_authority_hash": (
                    population_protocol_hash(self.population_config)
                ),
                "grounded_population_config_hash": (
                    self.population_config["config_hash"]
                ),
            })
            # Population scheduling enriches the composite Arena binding; it
            # must not erase the ACP authority envelope that the offline
            # round auditor reconstructs.
            if self.candidate_portfolio_authority is not None:
                self.round_toolchain_fingerprint[
                    "schema_version"
                ] = "r3e-arena-composite-toolchain-v1"
        self.code_version = str(
            config.get("code_version") or detect_code_version(self.root)
        )
        self.events = EventLogger(
            self.root / config.get("events_root", "runtime/events"),
            code_version=self.code_version,
        )
        self.events.ensure_streams()
        self.state = RoundState(self.round_dir / "round_state.json", round_id=round_id)

    def _checkpoint(self, stage: str, stage_input: Any, stage_output: Any) -> None:
        self.state.complete_stage(stage, stage_input=stage_input, stage_output=stage_output)
        self.events.emit(
            "arena",
            "round_stage_completed",
            round_id=self.round_id,
            stage=stage,
            stage_input_hash=hash_payload(stage_input),
            stage_output_hash=hash_payload(stage_output),
        )

    def _evaluate_blue_challenges(
        self,
        parent: PolicyState,
        valid_poisons: list[dict[str, Any]],
        *,
        seeds: list[int],
    ) -> list[dict[str, Any]]:
        if self.candidate_portfolio_authority is not None:
            challenged = []
            for poison in valid_poisons:
                execution_plan = (
                    self._compile_portfolio_memory_plan(parent, poison)
                    if parent.memory_binding
                    else None
                )
                challenged.append(
                    self.candidate_portfolio_authority.evaluate_challenge(
                        parent,
                        poison,
                        seeds=seeds,
                        execution_plan=execution_plan,
                    )
                )
            return challenged

        def evaluate_blue(policy, poison, seed):
            return self.adapter_gate.validate(
                "evaluate_blue",
                self.adapter.evaluate_blue(policy, poison, seed),
            )

        return [
            evaluate_challenge(
                parent,
                poison,
                seeds=seeds,
                evaluator=evaluate_blue,
            )
            for poison in valid_poisons
        ]

    def _compile_portfolio_memory_plan(
        self,
        parent: PolicyState,
        poison: dict[str, Any],
    ):
        authority = self.candidate_portfolio_authority
        if (
            authority is None
            or authority.portfolio_template_registry is None
        ):
            raise RoundRunnerViolation(
                "memory-bound ACP round requires template registry"
            )
        memory_root = (
            self.root
            / self.config.get("memory_root", "runtime/memory")
        )
        episode_store = EpisodeStore(memory_root / "episodes")
        memory_store = MemoryStore(
            memory_root / "library",
            episode_store=episode_store,
        )
        bank_store = ActiveBankStore(
            memory_root / "active_banks",
            memory_store=memory_store,
        )
        bank = bank_store.load_for_policy(parent)
        retriever = MemoryRetriever(memory_store)
        guard = ActivationGuard(
            memory_store,
            portfolio_template_registry=(
                authority.portfolio_template_registry
            ),
        )
        if (
            bank.retriever_hash != retriever.retriever_hash
            or bank.activation_guard_hash != guard.guard_hash
            or bank.control_whitelist_hash
            != guard.control_whitelist_hash
        ):
            raise RoundRunnerViolation(
                "arena memory runtime differs from active bank authority"
            )
        descriptor = FailureDescriptor.from_dict(
            extract_grounded_failure_descriptor(poison)
        )
        matches = retriever.retrieve(descriptor, bank, top_k=3)
        verifier_calls = (
            int(parent.budgets["max_llm_calls_per_case"])
            * len(parent.configuration["verifier_order"])
        )
        context = RuntimeContext(
            effective_policy_hash=parent.effective_policy_hash,
            policy_instance_hash=parent.policy_instance_hash,
            available_analyzers=tuple(sorted(ANALYZERS)),
            budget=BudgetEnvelope(
                int(parent.budgets["max_llm_calls_per_case"]),
                verifier_calls,
                int(parent.budgets["max_tokens_per_case"]),
                float(parent.budgets["max_wall_seconds_per_case"]),
            ),
            control_whitelist_hash=guard.control_whitelist_hash,
        )
        decision = guard.reactivate(
            matches=matches,
            active_policy=parent,
            active_bank=bank,
            runtime_context=context,
        )
        return MemoryAwarePlanCompiler(
            memory_store,
            bank,
            portfolio_template_registry=(
                authority.portfolio_template_registry
            ),
        ).compile(base_policy=parent, reactivation=decision)

    def _build_portfolio_red_capability(
        self,
        parent: PolicyState,
        *,
        residual_archive: list[dict[str, Any]],
        covered_archive: list[dict[str, Any]],
    ):
        if not self.config.get("portfolio_aware_red_challenge"):
            return None
        if self.candidate_portfolio_authority is None:
            raise RoundRunnerViolation(
                "portfolio-aware red requires candidate portfolio authority"
            )
        return build_portfolio_coverage_packet(
            parent,
            archive_rows=residual_archive + covered_archive,
        )

    @staticmethod
    def _verify_portfolio_red_candidate(
        row: dict[str, Any],
        *,
        parent: PolicyState,
        portfolio_capability: dict[str, Any] | None,
    ) -> None:
        if portfolio_capability is None:
            return
        try:
            verify_portfolio_challenge_execution(
                row,
                plan=row.get("portfolio_challenge_plan") or {},
                packet=portfolio_capability,
                policy=parent,
            )
        except PortfolioChallengeViolation as exc:
            raise RoundRunnerViolation(str(exc)) from exc

    def _load_red_checkpoint_output(self) -> Any:
        generated = _read_jsonl(
            self.round_dir / "red_candidates.jsonl"
        )
        coverage_path = self.round_dir / "coverage_plan.json"
        authority_path = (
            self.round_dir / "portfolio_red_authority.json"
        )
        proposal_path = self.round_dir / "grounded_proposal_plan.json"
        execution_path = (
            self.round_dir / "grounded_proposal_execution.json"
        )
        population_schedule_path = (
            self.round_dir / "grounded_population_schedule.json"
        )
        population_execution_path = (
            self.round_dir / "grounded_population_execution.json"
        )
        if (
            not coverage_path.is_file()
            and not authority_path.is_file()
            and not proposal_path.is_file()
        ):
            return generated
        output: dict[str, Any] = {"generated": generated}
        if coverage_path.is_file():
            output.update({
                "coverage_plan": read_json(coverage_path),
                "planned": _read_jsonl(
                    self.round_dir
                    / "planned_red_candidates.jsonl"
                ),
            })
        if authority_path.is_file():
            output["portfolio_red_authority"] = read_json(
                authority_path
            )
        if proposal_path.is_file():
            output["grounded_proposal_plan"] = read_json(
                proposal_path
            )
            output["grounded_proposal_execution"] = read_json(
                execution_path
            )
        if population_schedule_path.is_file():
            output["grounded_population_schedule"] = read_json(
                population_schedule_path
            )
            output["grounded_population_execution"] = read_json(
                population_execution_path
            )
        return output

    def _verify_persisted_checkpoints(self) -> None:
        """Fail closed when any completed stage artifact was changed."""
        state = self.state.load()
        completed = {row["stage"] for row in state["checkpoints"]}
        loaders = {
            "INIT": lambda: {"round_dir": str(self.round_dir)},
            "LOAD_ACTIVE_POLICY": lambda: {
                "policy": read_json(self.round_dir / "active_parent.json"),
                "run_context_hash": verify_run_context(
                    read_json(self.round_dir / "toolchain.json")
                )["run_context_hash"],
            },
            "RED_GENERATE": self._load_red_checkpoint_output,
            "VALIDITY_GATE": lambda: (
                {
                    "validity": _read_jsonl(
                        self.round_dir / "validity_results.jsonl"
                    ),
                    "formal_rejections": _read_jsonl(
                        self.round_dir / "formal_rejections.jsonl"
                    ),
                }
                if (
                    self.round_dir / "formal_rejections.jsonl"
                ).is_file()
                else _read_jsonl(
                    self.round_dir / "validity_results.jsonl"
                )
            ),
            "BLUE_CHALLENGE": lambda: _read_jsonl(
                self.round_dir / "blue_challenge_results.jsonl"
            ),
            "ARCHIVE_UPDATE": lambda: {
                "residual": _read_jsonl(
                    self.round_dir / "archive_updates.jsonl"
                ),
                "covered": _read_jsonl(
                    self.round_dir / "covered_archive_updates.jsonl"
                ),
                "learnability": _read_jsonl(
                    self.round_dir / "learnability_results.jsonl"
                ),
                "excluded": _read_jsonl(
                    self.round_dir / "archive_exclusions.jsonl"
                ),
                **(
                    {
                        "difficulty": _read_jsonl(
                            self.round_dir
                            / "difficulty_profiles.jsonl"
                        ),
                        "coverage_state_after": read_json(
                            self.round_dir
                            / "coverage_state_after.json"
                        ),
                    }
                    if (
                        self.round_dir
                        / "coverage_state_after.json"
                    ).is_file()
                    else {}
                ),
            },
            "FREEZE_RESIDUAL_MANIFEST": lambda: read_json(
                self.round_dir / "residual_manifest.json"
            ),
            "SPLIT_ADAPT_TARGET": lambda: {
                "adaptation": read_json(
                    self.round_dir / "adaptation_manifest.json"
                )["manifest_hash"],
                "target": read_json(
                    self.round_dir / "target_manifest.json"
                )["manifest_hash"],
            },
            "PROPOSE_CHILDREN": lambda: [
                PolicyState.from_dict(row).policy_hash
                for row in _read_jsonl(self.round_dir / "child_policies.jsonl")
            ],
            "SCREEN_CHILDREN": lambda: _read_jsonl(
                self.round_dir / "screening_results.jsonl"
            ),
            "FREEZE_PROMOTION_MANIFEST": lambda: read_json(
                self.round_dir / "promotion_manifest.json"
            ),
            "PAIRED_REPLAY": lambda: _read_jsonl(
                self.round_dir / "paired_validation.jsonl"
            ),
            "DECIDE": lambda: {
                "decisions": _read_jsonl(
                    self.round_dir / "promotion_decisions.jsonl"
                ),
                "bundles": _read_jsonl(
                    self.round_dir / "promotion_bundles.jsonl"
                ),
            },
            "ATOMIC_COMMIT": lambda: read_json(
                self.round_dir / "registry_after.json"
            )["registry_hash"],
            "RENEWED_CHALLENGE": lambda: read_json(
                self.round_dir / "renewed_challenge_binding.json"
            ),
            "COMPLETE": lambda: read_json(self.round_dir / "round_summary.json"),
        }
        for stage in completed:
            loader = loaders.get(stage)
            if loader is None:
                raise RoundRunnerViolation(f"no artifact verifier for stage: {stage}")
            try:
                output = loader()
                stage_input = None
                if stage == "RED_GENERATE":
                    stage_input = verify_red_search_context(
                        read_json(self.round_dir / "red_search_context.json")
                    )["context_hash"]
                elif stage == "BLUE_CHALLENGE":
                    active_parent = PolicyState.from_dict(
                        read_json(self.round_dir / "active_parent.json")
                    )
                    stage_input = {
                        "policy": active_parent.policy_hash,
                        "seeds": [
                            int(seed) for seed in self.config.get(
                                "challenge_seeds", [1, 2, 3]
                            )
                        ],
                        "blue_evaluation_authority": (
                            self.blue_evaluation_authority
                        ),
                        "blue_portfolio_authority_hash": (
                            self.candidate_portfolio_authority.authority_hash
                            if self.candidate_portfolio_authority is not None
                            else ""
                        ),
                        "blue_semantic_signature_provider_hash": (
                            self.candidate_portfolio_authority
                            .semantic_signature_provider_hash
                            if self.candidate_portfolio_authority is not None
                            else ""
                        ),
                        "blue_offline_allocator_hash": (
                            self.candidate_portfolio_authority
                            .allocator.allocator_hash
                            if (
                                self.candidate_portfolio_authority
                                is not None
                                and self.candidate_portfolio_authority
                                .allocator is not None
                            )
                            else ""
                        ),
                        "blue_descriptor_router_hash": (
                            self.candidate_portfolio_authority.router.router_hash
                            if (
                                self.candidate_portfolio_authority is not None
                                and self.candidate_portfolio_authority.router
                                is not None
                            )
                            else ""
                        ),
                    }
                self.state.verify_stage(
                    stage,
                    stage_input=stage_input,
                    stage_output=output,
                )
            except (OSError, KeyError, ValueError, RoundStateViolation) as exc:
                raise RoundRunnerViolation(
                    f"persisted stage artifact mismatch: {stage}: {exc}"
                ) from exc

    def run(self) -> dict[str, Any]:
        state = self.state.initialize(self.config)
        if state["round_config_hash"] != hash_payload(self.config):
            raise RoundRunnerViolation("round config changed after initialization")
        self._verify_persisted_checkpoints()
        atomic_write_json(self.round_dir / "round_config.json", self.config)

        if self.state.next_stage() == "INIT":
            self._checkpoint("INIT", self.config, {"round_dir": str(self.round_dir)})

        if self.state.next_stage() == "LOAD_ACTIVE_POLICY":
            registry = load_registry(self.registry_path)
            parent = get_active_policy(registry)
            if self.candidate_portfolio_authority is not None:
                try:
                    self.candidate_portfolio_authority.assert_policy_authorized(
                        parent
                    )
                except Exception as exc:
                    raise RoundRunnerViolation(str(exc)) from exc
            expected = self.config.get("expected_parent_policy_hash")
            if expected and expected != parent.policy_hash:
                raise RoundRunnerViolation("configured parent policy hash is stale")
            atomic_write_json(self.round_dir / "active_parent.json", parent.to_dict())
            atomic_write_json(self.round_dir / "registry_before.json", registry)
            run_context = build_run_context(
                round_id=self.round_id,
                code_version=self.code_version,
                round_config=self.config,
                registry_hash=registry["registry_hash"],
                active_policy_id=parent.policy_id,
                active_policy_hash=parent.policy_hash,
                toolchain_fingerprint=self.round_toolchain_fingerprint,
            )
            atomic_write_json(self.round_dir / "toolchain.json", run_context)
            self._checkpoint(
                "LOAD_ACTIVE_POLICY",
                registry["registry_hash"],
                {
                    "policy": parent.to_dict(),
                    "run_context_hash": run_context["run_context_hash"],
                },
            )

        parent = PolicyState.from_dict(read_json(self.round_dir / "active_parent.json"))
        run_context = verify_run_context(read_json(self.round_dir / "toolchain.json"))

        if self.state.next_stage() == "RED_GENERATE":
            memory_capability = None
            portfolio_capability = None
            grounded_proposal_plan = None
            grounded_proposal_execution = None
            grounded_population_schedule = None
            grounded_population_execution = None
            coverage_before = None
            if parent.memory_binding:
                memory_root = (
                    self.root
                    / self.config.get("memory_root", "runtime/memory")
                )
                episode_store = EpisodeStore(memory_root / "episodes")
                memory_store = MemoryStore(
                    memory_root / "library",
                    episode_store=episode_store,
                )
                bank_store = ActiveBankStore(
                    memory_root / "active_banks",
                    memory_store=memory_store,
                )
                active_bank = bank_store.load_for_policy(parent)
                memory_capability = build_memory_capability_packet(
                    parent,
                    active_bank,
                    memory_store,
                )
            residual_archive = load_archive(self.archive_path)
            covered_archive = load_archive(self.covered_archive_path)
            if self.validity_authority in GROUNDED_ARENA_AUTHORITIES:
                coverage_before = load_coverage_state(
                    self.coverage_state_path
                )
                atomic_write_json(
                    self.round_dir / "coverage_state_before.json",
                    coverage_before,
                )
            if self.config.get("grounded_pre_generation_planner"):
                if (
                    coverage_before is None
                    or self.grounded_registries is None
                ):
                    raise RoundRunnerViolation(
                        "Grounded proposal authority is unavailable"
                    )
                proposal_archive_view = build_proposal_archive_view(
                    residual_archive=residual_archive,
                    covered_archive=covered_archive,
                )
                atomic_write_json(
                    self.round_dir / "grounded_proposal_archive_view.json",
                    proposal_archive_view,
                )
                grounded_proposal_plan = build_grounded_proposal_plan(
                    policy=parent,
                    registries=self.grounded_registries,
                    coverage_state=coverage_before,
                    archive_view=proposal_archive_view,
                    budget=int(self.config.get(
                        "grounded_red_proposal_budget", 1
                    )),
                    family_quota=int(self.config.get(
                        "grounded_family_quota", 1
                    )),
                    archive_quota=int(self.config.get(
                        "grounded_archive_quota", 1
                    )),
                    maximum_difficulty_band=str(self.config.get(
                        "grounded_maximum_difficulty_band", "D3"
                    )),
                    memory_capability=memory_capability,
                    allow_composition=bool(self.config.get(
                        "grounded_allow_controlled_composition", True
                    )),
                )
                atomic_write_json(
                    self.round_dir / "grounded_proposal_plan.json",
                    grounded_proposal_plan,
                )
                if self.population_config is not None:
                    try:
                        grounded_population_schedule = (
                            build_population_schedule(
                                policy=parent,
                                proposal_plan=grounded_proposal_plan,
                                config=self.population_config,
                            )
                        )
                    except GroundedPopulationViolation as exc:
                        raise RoundRunnerViolation(str(exc)) from exc
                    atomic_write_json(
                        self.round_dir
                        / "grounded_population_schedule.json",
                        grounded_population_schedule,
                    )
            portfolio_capability = self._build_portfolio_red_capability(
                parent,
                residual_archive=residual_archive,
                covered_archive=covered_archive,
            )
            red_context = build_red_search_context(
                parent,
                residual_archive=residual_archive,
                covered_archive=covered_archive,
                memory_capability=memory_capability,
                portfolio_capability=portfolio_capability,
                grounded_proposal_plan=grounded_proposal_plan,
                grounded_population_schedule=(
                    grounded_population_schedule
                ),
            )
            atomic_write_json(
                self.round_dir / "red_search_context.json",
                red_context,
            )
            candidates = [
                self.adapter_gate.validate("generate_red", row)
                for row in self.adapter.generate_red(
                    parent, self.config, red_context
                )
            ]
            poison_ids = [str(row.get("poison_id") or "") for row in candidates]
            if any(not poison_id for poison_id in poison_ids):
                raise RoundRunnerViolation("red candidate is missing poison_id")
            if len(poison_ids) != len(set(poison_ids)):
                raise RoundRunnerViolation("red candidate poison_id must be unique")
            if grounded_proposal_plan is not None:
                try:
                    grounded_proposal_execution = (
                        verify_proposal_candidates(
                            grounded_proposal_plan,
                            candidates,
                            policy=parent,
                        )
                    )
                except GroundedProposalViolation as exc:
                    raise RoundRunnerViolation(str(exc)) from exc
                atomic_write_json(
                    self.round_dir
                    / "grounded_proposal_execution.json",
                    grounded_proposal_execution,
                )
            if grounded_population_schedule is not None:
                try:
                    grounded_population_execution = (
                        verify_population_candidates(
                            grounded_population_schedule,
                            candidates,
                            policy=parent,
                        )
                    )
                except GroundedPopulationViolation as exc:
                    raise RoundRunnerViolation(str(exc)) from exc
                atomic_write_json(
                    self.round_dir
                    / "grounded_population_execution.json",
                    grounded_population_execution,
                )
            for row in candidates:
                if row.get("challenged_policy_hash") != parent.policy_hash:
                    raise RoundRunnerViolation("red candidate is not bound to active parent")
                # V1 adapters that emit a genuinely fresh poison are upgraded
                # by the authoritative runner. Non-fresh ancestry always
                # requires an explicit, hash-bound operator plan.
                if "lineage_plan" not in row and not row.get("parent_poison_id"):
                    row.update({
                        "parent_poison_id": "",
                        "parent_challenged_policy_hash": "",
                        "lineage_depth": 0,
                        "evolution_operator": "fresh",
                    })
                    row["lineage_plan"] = make_lineage_plan(
                        parent,
                        operator_space=self.operator_space,
                        operator="fresh",
                        poison_id=str(row["poison_id"]),
                    )
                try:
                    verify_lineage_execution(
                        row,
                        policy=parent,
                        operator_space=self.operator_space,
                        available_parents={
                            str(item["poison_id"]): item
                            for item in red_context["archive_summary"]
                        },
                    )
                except LineageOperatorViolation as exc:
                    raise RoundRunnerViolation(str(exc)) from exc
                self._verify_portfolio_red_candidate(
                    row,
                    parent=parent,
                    portfolio_capability=portfolio_capability,
                )
                bound_poison = bind_poison_payload(row)
                row.clear()
                row.update(bound_poison)
            _write_jsonl(self.round_dir / "red_candidates.jsonl", candidates)
            portfolio_red_authority = None
            if portfolio_capability is not None:
                portfolio_red_authority = build_portfolio_red_authority(
                    policy=parent,
                    packet=portfolio_capability,
                    poisons=candidates,
                    toolchain_fingerprint=(
                        self.round_toolchain_fingerprint
                    ),
                )
                atomic_write_json(
                    self.round_dir / "portfolio_red_authority.json",
                    portfolio_red_authority,
                )
            red_stage_output: Any = (
                {
                    "generated": candidates,
                    "portfolio_red_authority": (
                        portfolio_red_authority
                    ),
                }
                if portfolio_red_authority is not None
                else candidates
            )
            if grounded_proposal_plan is not None:
                if not isinstance(red_stage_output, dict):
                    red_stage_output = {"generated": candidates}
                red_stage_output.update({
                    "grounded_proposal_plan": grounded_proposal_plan,
                    "grounded_proposal_execution": (
                        grounded_proposal_execution
                    ),
                })
            if self.validity_authority in GROUNDED_ARENA_AUTHORITIES:
                if coverage_before is None:
                    raise RoundRunnerViolation(
                        "Grounded coverage state was not frozen"
                    )
                coverage_plan = build_coverage_plan(
                    candidates,
                    coverage_state=coverage_before,
                    policy=parent,
                    budget=int(
                        self.config.get(
                            "grounded_red_proposal_budget",
                            max(1, len(candidates)),
                        )
                    ),
                    family_quota=int(
                        self.config.get(
                            "grounded_family_quota",
                            max(1, len(candidates)),
                        )
                    ),
                    maximum_difficulty_band=str(
                        self.config.get(
                            "grounded_maximum_difficulty_band",
                            "D3",
                        )
                    ),
                )
                selected_ids = set(
                    coverage_plan["selected_poison_ids"]
                )
                planned = [
                    row for row in candidates
                    if row["poison_id"] in selected_ids
                ]
                planned.sort(
                    key=lambda row: coverage_plan[
                        "selected_poison_ids"
                    ].index(row["poison_id"])
                )
                atomic_write_json(
                    self.round_dir / "coverage_plan.json",
                    coverage_plan,
                )
                _write_jsonl(
                    self.round_dir / "planned_red_candidates.jsonl",
                    planned,
                )
                red_stage_output = {
                    "generated": candidates,
                    "coverage_plan": coverage_plan,
                    "planned": planned,
                }
                if portfolio_red_authority is not None:
                    red_stage_output["portfolio_red_authority"] = (
                        portfolio_red_authority
                    )
                if grounded_proposal_plan is not None:
                    red_stage_output.update({
                        "grounded_proposal_plan": (
                            grounded_proposal_plan
                        ),
                        "grounded_proposal_execution": (
                            grounded_proposal_execution
                        ),
                    })
                if grounded_population_schedule is not None:
                    red_stage_output.update({
                        "grounded_population_schedule": (
                            grounded_population_schedule
                        ),
                        "grounded_population_execution": (
                            grounded_population_execution
                        ),
                    })
                self.events.emit(
                    "red",
                    "grounded_coverage_plan_frozen",
                    round_id=self.round_id,
                    challenged_policy_hash=parent.policy_hash,
                    coverage_state_hash_before=coverage_before[
                        "coverage_hash"
                    ],
                    coverage_plan_hash=coverage_plan["plan_hash"],
                    generated_count=len(candidates),
                    selected_count=len(planned),
                    deferred_count=len(candidates) - len(planned),
                )
            self.events.emit(
                "red",
                "red_candidates_generated",
                round_id=self.round_id,
                challenged_policy_hash=parent.policy_hash,
                candidate_count=len(candidates),
                candidates_hash=hash_payload(candidates),
                portfolio_capability_packet_hash=(
                    portfolio_capability["packet_hash"]
                    if portfolio_capability is not None
                    else ""
                ),
                portfolio_coverage_region_count=(
                    len(portfolio_capability["coverage_regions"])
                    if portfolio_capability is not None
                    else 0
                ),
                grounded_proposal_plan_hash=(
                    grounded_proposal_plan["plan_hash"]
                    if grounded_proposal_plan is not None
                    else ""
                ),
                grounded_proposal_execution_hash=(
                    grounded_proposal_execution["execution_hash"]
                    if grounded_proposal_execution is not None
                    else ""
                ),
                grounded_population_schedule_hash=(
                    grounded_population_schedule["schedule_hash"]
                    if grounded_population_schedule is not None
                    else ""
                ),
                grounded_population_execution_hash=(
                    grounded_population_execution["execution_hash"]
                    if grounded_population_execution is not None
                    else ""
                ),
                grounded_population_provider_usage=(
                    grounded_population_execution["provider_usage"]
                    if grounded_population_execution is not None
                    else {}
                ),
            )
            self._checkpoint(
                "RED_GENERATE",
                red_context["context_hash"],
                red_stage_output,
            )

        candidates_path = (
            self.round_dir / "planned_red_candidates.jsonl"
        )
        candidates = _read_jsonl(
            candidates_path
            if candidates_path.is_file()
            else self.round_dir / "red_candidates.jsonl"
        )
        if self.state.next_stage() == "VALIDITY_GATE":
            validity_rows = []
            valid = []
            formal_rejections = []
            for poison in candidates:
                try:
                    payload_hash = verify_poison_payload(poison)
                except PoisonPayloadViolation as exc:
                    raise RoundRunnerViolation(str(exc)) from exc
                if self.validity_authority in GROUNDED_ARENA_AUTHORITIES:
                    if self.grounded_registries is None:
                        raise RoundRunnerViolation(
                            "Grounded registries are unavailable"
                        )
                    try:
                        grounded = execute_grounded_arena_validity(
                            poison=poison,
                            policy=parent,
                            registries=self.grounded_registries,
                            project_root=self.root,
                            round_dir=self.round_dir,
                            run_context_hash=run_context[
                                "run_context_hash"
                            ],
                            timeout_seconds=float(
                                self.config.get(
                                    "grounded_timeout_seconds", 10.0
                                )
                            ),
                            formal_timeout_seconds=float(
                                self.config.get(
                                    "grounded_formal_timeout_seconds",
                                    30.0,
                                )
                            ),
                        )
                    except Exception as exc:
                        raise RoundRunnerViolation(str(exc)) from exc
                    if grounded.get("formal_rejection"):
                        rejection = append_formal_rejection(
                            self.rejected_archive_path,
                            grounded["formal_rejection"],
                            policy=parent,
                            registries=self.grounded_registries,
                        )
                        row = {
                            **dict(poison),
                            "validity": grounded["validity"],
                            "formal_rejection": rejection,
                        }
                        formal_rejections.append(rejection)
                    else:
                        row = {
                            **dict(poison),
                            "validity": grounded["validity"],
                            "grounded_authority_bundle": grounded[
                                "grounded_authority_bundle"
                            ],
                        }
                    verify_poison_payload(row)
                else:
                    prepared = self.adapter_gate.validate(
                        "prepare_validity",
                        self.adapter.prepare_validity(poison),
                    )
                    undeclared = set(prepared) - _VALIDITY_EVIDENCE_FIELDS
                    if undeclared:
                        raise RoundRunnerViolation(
                            "validity adapter returned poison payload fields: "
                            f"{sorted(undeclared)}"
                        )
                    if (
                        prepared.get("poison_id") != poison.get("poison_id")
                        or prepared.get("challenged_policy_hash")
                        != poison.get("challenged_policy_hash")
                        or prepared.get("poison_payload_hash") != payload_hash
                    ):
                        raise RoundRunnerViolation(
                            "validity adapter changed poison identity, "
                            "policy binding, or payload hash"
                        )
                    validity_input = dict(poison)
                    validity_input.update({
                        key: value
                        for key, value in prepared.items()
                        if key in {
                            "formal_status",
                            "golden_compile_ok",
                            "golden_oracle_ok",
                            "buggy_compile_ok",
                            "buggy_functional_fail",
                            "output_complete",
                            "revert_oracle_ok",
                            "fresh_output",
                            "oracle_result_hash",
                            "counterexample_hash",
                            "toolchain_fingerprint_hash",
                            "command_hash",
                        }
                    })
                    validity_input["validity_adapter_output"] = (
                        adapter_output_metadata(prepared)
                    )
                    verify_poison_payload(validity_input)
                    result = validity_gate(validity_input)
                    row = dict(validity_input)
                    row["validity"] = {
                        "proven_valid": result.proven_valid,
                        "checks": result.checks,
                        "rejection_reasons": result.rejection_reasons,
                        "evidence": result.evidence,
                        "result_hash": result.result_hash,
                    }
                validity_rows.append(row)
                if row["validity"]["proven_valid"]:
                    valid.append(row)
            _write_jsonl(self.round_dir / "validity_results.jsonl", validity_rows)
            _write_jsonl(self.round_dir / "valid_poisons.jsonl", valid)
            _write_jsonl(
                self.round_dir / "formal_rejections.jsonl",
                formal_rejections,
            )
            self.events.emit(
                "oracle",
                "validity_gate_completed",
                round_id=self.round_id,
                challenged_policy_hash=parent.policy_hash,
                candidate_count=len(candidates),
                proven_valid_count=len(valid),
                formal_rejection_count=len(formal_rejections),
                results_hash=hash_payload(validity_rows),
                formal_rejections_hash=hash_payload(
                    formal_rejections
                ),
                grounded_authority_hashes=[
                    row["grounded_authority_bundle"][
                        "authority_hash"
                    ]
                    for row in validity_rows
                    if row.get("grounded_authority_bundle")
                ],
                formal_proof_triplet_hashes=[
                    row["grounded_authority_bundle"][
                        "formal_proof_triplet"
                    ]["triplet_hash"]
                    for row in validity_rows
                    if row.get("grounded_authority_bundle")
                ],
                failure_descriptor_hashes=[
                    row["grounded_authority_bundle"][
                        "failure_descriptor"
                    ]["descriptor_hash"]
                    for row in validity_rows
                    if row.get("grounded_authority_bundle")
                ],
                grounded_execution_schema_versions=[
                    row["grounded_authority_bundle"][
                        "execution_bundle"
                    ]["schema_version"]
                    for row in validity_rows
                    if row.get("grounded_authority_bundle")
                ],
                formal_assessment_hashes=[
                    row["formal_rejection"][
                        "formal_proof_assessment"
                    ]["assessment_hash"]
                    for row in validity_rows
                    if row.get("formal_rejection")
                ],
            )
            self._checkpoint(
                "VALIDITY_GATE",
                candidates,
                {
                    "validity": validity_rows,
                    "formal_rejections": formal_rejections,
                },
            )

        valid = _read_jsonl(self.round_dir / "valid_poisons.jsonl")
        if self.state.next_stage() == "BLUE_CHALLENGE":
            seeds = [int(seed) for seed in self.config.get("challenge_seeds", [1, 2, 3])]
            challenged = self._evaluate_blue_challenges(
                parent,
                valid,
                seeds=seeds,
            )
            _write_jsonl(self.round_dir / "blue_challenge_results.jsonl", challenged)
            self.events.emit(
                "red",
                "blue_challenge_completed",
                round_id=self.round_id,
                challenged_policy_hash=parent.policy_hash,
                blue_evaluation_authority=self.blue_evaluation_authority,
                blue_portfolio_authority_hash=(
                    self.candidate_portfolio_authority.authority_hash
                    if self.candidate_portfolio_authority is not None
                    else ""
                ),
                blue_semantic_signature_provider_hash=(
                    self.candidate_portfolio_authority
                    .semantic_signature_provider_hash
                    if self.candidate_portfolio_authority is not None
                    else ""
                ),
                blue_offline_allocator_hash=(
                    self.candidate_portfolio_authority
                    .allocator.allocator_hash
                    if (
                        self.candidate_portfolio_authority is not None
                        and self.candidate_portfolio_authority.allocator
                        is not None
                    )
                    else ""
                ),
                blue_descriptor_router_hash=(
                    self.candidate_portfolio_authority.router.router_hash
                    if (
                        self.candidate_portfolio_authority is not None
                        and self.candidate_portfolio_authority.router
                        is not None
                    )
                    else ""
                ),
                blue_portfolio_template_registry_hash=(
                    self.candidate_portfolio_authority
                    .portfolio_template_registry.registry_hash
                    if (
                        self.candidate_portfolio_authority is not None
                        and self.candidate_portfolio_authority
                        .portfolio_template_registry is not None
                    )
                    else ""
                ),
                memory_execution_plan_hashes=sorted({
                    str(
                        blue_result.get(
                            "memory_execution_plan_hash"
                        )
                        or ""
                    )
                    for challenge in challenged
                    for blue_result in challenge.get(
                        "blue_results", []
                    )
                    if blue_result.get(
                        "memory_execution_plan_hash"
                    )
                }),
                result_count=len(challenged),
                results_hash=hash_payload(challenged),
            )
            self._checkpoint(
                "BLUE_CHALLENGE",
                {
                    "policy": parent.policy_hash,
                    "seeds": seeds,
                    "blue_evaluation_authority": (
                        self.blue_evaluation_authority
                    ),
                    "blue_portfolio_authority_hash": (
                        self.candidate_portfolio_authority.authority_hash
                        if self.candidate_portfolio_authority is not None
                        else ""
                    ),
                    "blue_semantic_signature_provider_hash": (
                        self.candidate_portfolio_authority
                        .semantic_signature_provider_hash
                        if self.candidate_portfolio_authority is not None
                        else ""
                    ),
                    "blue_offline_allocator_hash": (
                        self.candidate_portfolio_authority
                        .allocator.allocator_hash
                        if (
                            self.candidate_portfolio_authority is not None
                            and self.candidate_portfolio_authority.allocator
                            is not None
                        )
                        else ""
                    ),
                    "blue_descriptor_router_hash": (
                        self.candidate_portfolio_authority.router.router_hash
                        if (
                            self.candidate_portfolio_authority is not None
                            and self.candidate_portfolio_authority.router
                            is not None
                        )
                        else ""
                    ),
                },
                challenged,
            )

        challenged = _read_jsonl(self.round_dir / "blue_challenge_results.jsonl")
        episode_store = EpisodeStore(
            self.root
            / self.config.get("memory_root", "runtime/memory")
            / "episodes"
        )
        round_formal_rejections = (
            _read_jsonl(
                self.round_dir / "formal_rejections.jsonl"
            )
            if (
                self.round_dir / "formal_rejections.jsonl"
            ).is_file()
            else []
        )
        verified_episodes = append_round_episodes(
            challenged,
            policy=parent,
            round_id=self.round_id,
            store=episode_store,
            formal_rejections=round_formal_rejections,
        )
        episode_manifest = {
            "schema_version": "r3e-round-episode-manifest-v1",
            "round_id": self.round_id,
            "challenged_policy_hash": parent.policy_hash,
            "episode_ids": [episode.episode_id for episode in verified_episodes],
            "episode_hashes": [
                episode.episode_hash for episode in verified_episodes
            ],
        }
        episode_manifest["manifest_hash"] = hash_payload(episode_manifest)
        episode_manifest_path = self.round_dir / "verified_episodes.json"
        episode_manifest_created = not episode_manifest_path.exists()
        if episode_manifest_path.exists():
            if read_json(episode_manifest_path) != episode_manifest:
                raise RoundRunnerViolation(
                    "verified episode manifest changed during resume"
                )
        else:
            atomic_write_json(episode_manifest_path, episode_manifest)
        if verified_episodes and episode_manifest_created:
            self.events.emit(
                "memory",
                "verified_episodes_appended",
                round_id=self.round_id,
                challenged_policy_hash=parent.policy_hash,
                episode_count=len(verified_episodes),
                episode_manifest_hash=episode_manifest["manifest_hash"],
            )
        if self.state.next_stage() == "ARCHIVE_UPDATE":
            measured_challenged = list(challenged)
            difficulty_rows = []
            coverage_after = None
            if self.validity_authority in GROUNDED_ARENA_AUTHORITIES:
                plan = read_json(
                    self.round_dir / "coverage_plan.json"
                )
                family_by_id = {
                    str(entry["poison_id"]): str(
                        entry["cell"]["family_id"]
                    )
                    for entry in plan["candidate_entries"]
                }
                measured_challenged = []
                for challenge in challenged:
                    poison_id = str(challenge["poison_id"])
                    ambiguity = sum(
                        family == family_by_id[poison_id]
                        for family in family_by_id.values()
                    )
                    profile = difficulty_profile_from_challenge(
                        challenge,
                        candidate_ambiguity=ambiguity,
                    )
                    record = {
                        "schema_version": (
                            "r3e-grounded-difficulty-record-v1"
                        ),
                        "round_id": self.round_id,
                        "poison_id": poison_id,
                        "challenge_result_hash": challenge[
                            "challenge_result_hash"
                        ],
                        "profile": profile,
                    }
                    record["record_hash"] = hash_payload(record)
                    difficulty_rows.append(record)
                    measured_challenged.append({
                        **challenge,
                        "grounded_difficulty_profile": profile,
                    })
                _write_jsonl(
                    self.round_dir / "difficulty_profiles.jsonl",
                    difficulty_rows,
                )
                coverage_before = read_json(
                    self.round_dir / "coverage_state_before.json"
                )
                validity_rows = _read_jsonl(
                    self.round_dir / "validity_results.jsonl"
                )
                coverage_after = update_coverage_state(
                    coverage_before,
                    plan=plan,
                    validity_rows=validity_rows,
                    challenge_rows=challenged,
                    round_id=self.round_id,
                )
                atomic_write_json(
                    self.round_dir / "coverage_state_after.json",
                    coverage_after,
                )
                save_coverage_state(
                    self.coverage_state_path,
                    coverage_after,
                )
                self.events.emit(
                    "red",
                    "grounded_coverage_state_updated",
                    round_id=self.round_id,
                    challenged_policy_hash=parent.policy_hash,
                    coverage_plan_hash=plan["plan_hash"],
                    coverage_state_hash_before=coverage_before[
                        "coverage_hash"
                    ],
                    coverage_state_hash_after=coverage_after[
                        "coverage_hash"
                    ],
                    difficulty_records_hash=hash_payload(
                        difficulty_rows
                    ),
                )
            residual_before = load_archive(self.archive_path)
            covered_before = load_archive(self.covered_archive_path)
            archive_before = residual_before + covered_before
            archived = []
            covered_archived = []
            learnability_rows = []
            excluded = []
            for row in measured_challenged:
                enriched = dict(row)
                current = archive_before + archived + covered_archived
                hardness_class = str(enriched.get("hardness_class") or "")
                if hardness_class in {"hard_residual", "borderline_residual"}:
                    duplicate = _archive_duplicate(
                        residual_before + archived,
                        enriched,
                    )
                    if duplicate is not None:
                        archived.append(duplicate)
                        learnability = duplicate.get("learnability")
                        if isinstance(learnability, dict):
                            learnability_rows.append(learnability)
                        continue
                    enriched["novelty"] = novelty_score(enriched, current)
                    enriched["archive_cell"] = archive_cell(enriched)
                    enriched["discovered_round"] = self.round_id
                    probe = self.adapter_gate.validate(
                        "probe_learnability",
                        self.adapter.probe_learnability(parent, enriched),
                    )
                    probe_payload = strip_adapter_metadata(probe)
                    probe_evidence = dict(probe_payload.get("evidence") or {})
                    probe_evidence["adapter_output"] = adapter_output_metadata(probe)
                    probe_payload["evidence"] = probe_evidence
                    learnability = validate_learnability_result(
                        parent,
                        enriched,
                        probe_payload,
                    )
                    learnability_rows.append(learnability)
                    enriched["learnability"] = learnability
                    if learnability["label"] not in {
                        "reachable",
                        "weakly_reachable",
                    }:
                        excluded.append({
                            "poison_id": enriched["poison_id"],
                            "challenged_policy_hash": parent.policy_hash,
                            "reason": f"learnability:{learnability['label']}",
                            "learnability_result_hash": learnability["result_hash"],
                        })
                        continue
                    archived.append(update_archive(
                        self.archive_path,
                        enriched,
                        archive_kind="residual",
                    ))
                elif hardness_class in {"mostly_covered", "covered"}:
                    duplicate = _archive_duplicate(
                        covered_before + covered_archived,
                        enriched,
                    )
                    if duplicate is not None:
                        covered_archived.append(duplicate)
                        continue
                    enriched["novelty"] = novelty_score(enriched, current)
                    enriched["archive_cell"] = archive_cell(enriched)
                    enriched["discovered_round"] = self.round_id
                    covered_archived.append(update_archive(
                        self.covered_archive_path,
                        enriched,
                        archive_kind="covered",
                    ))
                else:
                    raise RoundRunnerViolation(
                        f"unknown hardness class: {hardness_class}"
                    )
            _write_jsonl(self.round_dir / "archive_updates.jsonl", archived)
            _write_jsonl(
                self.round_dir / "covered_archive_updates.jsonl",
                covered_archived,
            )
            _write_jsonl(
                self.round_dir / "learnability_results.jsonl",
                learnability_rows,
            )
            _write_jsonl(self.round_dir / "archive_exclusions.jsonl", excluded)
            self.events.emit(
                "red",
                "residual_archive_updated",
                round_id=self.round_id,
                challenged_policy_hash=parent.policy_hash,
                archived_count=len(archived),
                covered_count=len(covered_archived),
                excluded_count=len(excluded),
                archive_updates_hash=hash_payload(archived),
            )
            archive_stage_output = {
                "residual": archived,
                "covered": covered_archived,
                "learnability": learnability_rows,
                "excluded": excluded,
            }
            if coverage_after is not None:
                archive_stage_output.update({
                    "difficulty": difficulty_rows,
                    "coverage_state_after": coverage_after,
                })
            self._checkpoint(
                "ARCHIVE_UPDATE",
                challenged,
                archive_stage_output,
            )

        archived = _read_jsonl(self.round_dir / "archive_updates.jsonl")
        if self.state.next_stage() == "FREEZE_RESIDUAL_MANIFEST":
            accumulated = [
                row for row in load_archive(self.archive_path)
                if row.get("challenged_policy_hash") == parent.policy_hash
            ]
            accumulated.sort(key=lambda row: (
                str(row.get("discovered_round") or ""),
                str(row.get("poison_id") or ""),
                str(row.get("archive_entry_hash") or ""),
            ))
            if len({
                str(row.get("poison_id") or "") for row in accumulated
            }) != len(accumulated):
                raise RoundRunnerViolation("accumulated residual poison ids are not unique")
            _write_jsonl(
                self.round_dir / "accumulated_residuals.jsonl",
                accumulated,
            )
            accumulation = {
                "schema_version": "r3e-residual-accumulation-v1",
                "challenged_policy_id": parent.policy_id,
                "challenged_policy_hash": parent.policy_hash,
                "included_round_ids": sorted({
                    str(row.get("discovered_round") or "")
                    for row in accumulated if row.get("discovered_round")
                }),
                "archive_entry_hashes": [
                    str(row.get("archive_entry_hash") or "") for row in accumulated
                ],
                "current_round_updates_hash": hash_payload(archived),
                "accumulated_rows_hash": hash_payload(accumulated),
                "row_count": len(accumulated),
            }
            accumulation["accumulation_hash"] = hash_payload(accumulation)
            atomic_write_json(
                self.round_dir / "residual_accumulation.json",
                accumulation,
            )
            selected = select_residual_elites(accumulated)
            selection = {
                "schema_version": "r3e-residual-selection-v2",
                "challenged_policy_id": parent.policy_id,
                "challenged_policy_hash": parent.policy_hash,
                "current_round_updates_hash": hash_payload(archived),
                "accumulated_rows_hash": hash_payload(accumulated),
                "accumulation_hash": accumulation["accumulation_hash"],
                "selected_poison_ids": [
                    str(row["poison_id"]) for row in selected
                ],
                "selected_count": len(selected),
            }
            selection["selection_hash"] = hash_payload(selection)
            atomic_write_json(self.round_dir / "residual_selection.json", selection)
            residual_manifest = make_manifest(
                selected,
                split="residual",
                metadata={
                    "challenged_policy_id": parent.policy_id,
                    "challenged_policy_hash": parent.policy_hash,
                    "selection_hash": selection["selection_hash"],
                    "accumulation_hash": accumulation["accumulation_hash"],
                },
            )
            freeze_manifest(self.round_dir / "residual_manifest.json", residual_manifest)
            self._checkpoint("FREEZE_RESIDUAL_MANIFEST", selection, residual_manifest)

        residual_manifest = verify_manifest(read_json(self.round_dir / "residual_manifest.json"))
        if self.state.next_stage() == "SPLIT_ADAPT_TARGET":
            residual_designs = {
                str(row.get("design") or row.get("design_id") or "")
                for row in residual_manifest["rows"]
            }
            if len(residual_designs) < 2:
                source_hash = residual_manifest["manifest_hash"]
                defer_metadata = {
                    "group_key": "design",
                    "promotion_eligible": False,
                    "defer_reason": "insufficient_residual_designs",
                }
                adaptation = make_manifest(
                    residual_manifest["rows"],
                    split="adaptation",
                    source_manifest_hash=source_hash,
                    metadata=defer_metadata,
                )
                target = make_manifest(
                    [],
                    split="target",
                    source_manifest_hash=source_hash,
                    metadata=defer_metadata,
                )
            else:
                adaptation, target = grouped_split(
                    residual_manifest,
                    adaptation_fraction=float(
                        self.config.get("adaptation_fraction", 0.5)
                    ),
                    seed=int(self.config.get("split_seed", 0)),
                )
            freeze_manifest(self.round_dir / "adaptation_manifest.json", adaptation)
            freeze_manifest(self.round_dir / "target_manifest.json", target)
            self._checkpoint("SPLIT_ADAPT_TARGET", residual_manifest, {
                "adaptation": adaptation["manifest_hash"],
                "target": target["manifest_hash"],
            })

        adaptation = verify_manifest(read_json(self.round_dir / "adaptation_manifest.json"))
        target = verify_manifest(read_json(self.round_dir / "target_manifest.json"))
        if self.state.next_stage() == "PROPOSE_CHILDREN":
            if (
                target["row_count"] == 0
                or not bool(
                    self.config.get("policy_promotion_enabled", True)
                )
            ):
                children = []
            else:
                search_space = read_json(self.root / self.config["policy_search_space"])
                children = propose_children(
                    parent,
                    adaptation,
                    search_space,
                    round_id=self.round_id,
                    seed=int(self.config.get("policy_search_seed", 0)),
                )
            registry = load_registry(self.registry_path)
            for child in children:
                if child.policy_id not in registry["policies"]:
                    registry = register_candidate(
                        self.registry_path,
                        child,
                        ledger_path=self.ledger_path,
                        event_logger=self.events,
                        round_id=self.round_id,
                    )
            _write_jsonl(
                self.round_dir / "child_policies.jsonl",
                [child.to_dict() for child in children],
            )
            self._checkpoint("PROPOSE_CHILDREN", adaptation["manifest_hash"], [
                child.policy_hash for child in children
            ])

        children = [
            PolicyState.from_dict(row)
            for row in _read_jsonl(self.round_dir / "child_policies.jsonl")
        ]
        if self.state.next_stage() == "SCREEN_CHILDREN":
            screening = [
                {
                    "policy_id": child.policy_id,
                    "policy_hash": child.policy_hash,
                    **self.adapter_gate.validate(
                        "screen_child",
                        self.adapter.screen_child(parent, child, adaptation),
                    ),
                }
                for child in children
            ]
            _write_jsonl(self.round_dir / "screening_results.jsonl", screening)
            self._checkpoint("SCREEN_CHILDREN", adaptation["manifest_hash"], screening)

        screening = _read_jsonl(self.round_dir / "screening_results.jsonl")
        survivors = {
            row["policy_id"] for row in screening if row.get("survive")
        }
        if self.state.next_stage() == "FREEZE_PROMOTION_MANIFEST":
            non_target_source = read_json(self.root / self.config["non_target_manifest"])
            verify_manifest(non_target_source)
            if non_target_source.get("split") != "non_target":
                raise RoundRunnerViolation("configured non-target manifest has wrong split")
            non_target = freeze_manifest(
                self.round_dir / "non_target_manifest.json", non_target_source
            )
            promotion = make_manifest(
                [
                    {"policy_id": child.policy_id, "policy_hash": child.policy_hash}
                    for child in children if child.policy_id in survivors
                ],
                split="promotion_candidates",
                source_manifest_hash=target["manifest_hash"],
                metadata={"non_target_manifest_hash": non_target["manifest_hash"]},
            )
            freeze_manifest(self.round_dir / "promotion_manifest.json", promotion)
            self._checkpoint("FREEZE_PROMOTION_MANIFEST", screening, promotion)

        non_target = verify_manifest(read_json(self.round_dir / "non_target_manifest.json"))
        promotion_manifest = verify_manifest(read_json(self.round_dir / "promotion_manifest.json"))
        if self.state.next_stage() == "PAIRED_REPLAY":
            replay_rows = []
            seeds = [int(seed) for seed in self.config.get("promotion_seeds", [11, 12, 13])]

            def replay(policy, case, seed):
                return self.adapter_gate.validate(
                    "replay",
                    self.adapter.replay(policy, case, seed),
                )

            for child in children:
                if child.policy_id not in survivors:
                    continue
                for row in paired_replay(
                    parent,
                    child,
                    target_manifest=target,
                    non_target_manifest=non_target,
                    seeds=seeds,
                    evaluator=replay,
                ):
                    row["candidate_policy_id"] = child.policy_id
                    replay_rows.append(row)
            _write_jsonl(self.round_dir / "paired_validation.jsonl", replay_rows)
            self._checkpoint("PAIRED_REPLAY", promotion_manifest["manifest_hash"], replay_rows)

        replay_rows = _read_jsonl(self.round_dir / "paired_validation.jsonl")
        if self.state.next_stage() == "DECIDE":
            decisions = []
            promotion_bundles = []
            for child in children:
                rows = [
                    row for row in replay_rows
                    if row.get("candidate_policy_id") == child.policy_id
                ]
                if not rows:
                    continue
                provenance = {
                    "round_id": self.round_id,
                    "residual_manifest_hash": residual_manifest["manifest_hash"],
                    "adaptation_manifest_hash": adaptation["manifest_hash"],
                    "target_manifest_hash": target["manifest_hash"],
                    "non_target_manifest_hash": non_target["manifest_hash"],
                    "paired_result_hash": hash_payload(rows),
                    "code_commit_sha": run_context["code_version"],
                    "run_context_hash": run_context["run_context_hash"],
                    "toolchain_fingerprint_hash": run_context[
                        "toolchain_fingerprint_hash"
                    ],
                    "toolchain_fingerprint": run_context[
                        "toolchain_fingerprint"
                    ],
                }
                decision = decide_policy_promotion(
                    parent,
                    child,
                    rows,
                    validation_manifest_hash=promotion_manifest["manifest_hash"],
                    thresholds=self.config.get("promotion_thresholds"),
                    provenance=provenance,
                )
                decisions.append(decision)
                promotion_bundles.append(build_policy_promotion_bundle(
                    parent,
                    child,
                    rows,
                    validation_manifest_hash=promotion_manifest["manifest_hash"],
                    target_manifest=target,
                    non_target_manifest=non_target,
                    thresholds=self.config.get("promotion_thresholds"),
                    provenance=provenance,
                    recorded_decision=decision,
                ))
            _write_jsonl(self.round_dir / "promotion_decisions.jsonl", decisions)
            _write_jsonl(
                self.round_dir / "promotion_bundles.jsonl",
                promotion_bundles,
            )
            winner = select_single_promotable_child(decisions)
            atomic_write_json(self.round_dir / "winner.json", winner or {})
            self._checkpoint(
                "DECIDE",
                replay_rows,
                {"decisions": decisions, "bundles": promotion_bundles},
            )

        decisions = _read_jsonl(self.round_dir / "promotion_decisions.jsonl")
        promotion_bundles = _read_jsonl(
            self.round_dir / "promotion_bundles.jsonl"
        )
        winner = read_json(self.round_dir / "winner.json")
        if self.state.next_stage() == "ATOMIC_COMMIT":
            if winner:
                winner_bundle = next(
                    (
                        item for item in promotion_bundles
                        if item["recorded_decision"]["candidate_policy_id"]
                        == winner["candidate_policy_id"]
                    ),
                    None,
                )
                if winner_bundle is None:
                    raise RoundRunnerViolation("winner promotion bundle is missing")
                current_registry = load_registry(self.registry_path)
                current_active = get_active_policy(current_registry)
                if current_active.policy_id == winner["candidate_policy_id"]:
                    # A process may die after the atomic registry replace but
                    # before the round checkpoint. Treat the exact winner as an
                    # idempotently completed commit.
                    if current_active.policy_hash != winner["candidate_policy_hash"]:
                        raise RoundRunnerViolation("active winner policy hash mismatch")
                    registry_after = current_registry
                else:
                    registry_after = promote_policy(
                        self.registry_path,
                        winner["candidate_policy_id"],
                        winner_bundle,
                        ledger_path=self.ledger_path,
                        event_logger=self.events,
                        round_id=self.round_id,
                    )
            else:
                registry_after = load_registry(self.registry_path)
            decision_by_id = {
                row["candidate_policy_id"]: row for row in decisions
            }
            for child in children:
                if winner and child.policy_id == winner["candidate_policy_id"]:
                    continue
                entry = registry_after["policies"].get(child.policy_id)
                if entry and entry["status"] == "candidate":
                    decision = decision_by_id.get(child.policy_id, {})
                    registry_after = reject_policy(
                        self.registry_path,
                        child.policy_id,
                        decision.get("rejection_reasons") or ["screening_reject"],
                        provisional=decision.get("decision") == "provisional_candidate",
                        ledger_path=self.ledger_path,
                        event_logger=self.events,
                        round_id=self.round_id,
                    )
            atomic_write_json(self.round_dir / "registry_after.json", registry_after)
            self._checkpoint("ATOMIC_COMMIT", decisions, registry_after["registry_hash"])

        registry_after = load_registry(self.registry_path)
        active = get_active_policy(registry_after)
        if self.state.next_stage() == "RENEWED_CHALLENGE":
            binding = assert_renewed_challenge_binding(
                str(self.registry_path), active.policy_hash
            )
            atomic_write_json(self.round_dir / "renewed_challenge_binding.json", binding)
            self._checkpoint("RENEWED_CHALLENGE", registry_after["registry_hash"], binding)

        if self.state.next_stage() == "COMPLETE":
            audit = freeze_round_audit(self.round_dir)
            promotion_enabled = bool(
                self.config.get("policy_promotion_enabled", True)
            )
            promotion_eligible = (
                promotion_enabled and target["row_count"] > 0
            )
            summary = {
                "round_id": self.round_id,
                "parent_policy_id": parent.policy_id,
                "parent_policy_hash": parent.policy_hash,
                "active_policy_id": active.policy_id,
                "active_policy_hash": active.policy_hash,
                "promoted": active.policy_hash != parent.policy_hash,
                "residual_manifest_hash": residual_manifest["manifest_hash"],
                "target_manifest_hash": target["manifest_hash"],
                "registry_hash_after": registry_after["registry_hash"],
                "run_context_hash": run_context["run_context_hash"],
                "audit_record_hash": audit["audit_record_hash"],
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
            atomic_write_json(self.round_dir / "round_summary.json", summary)
            append_round_ledger(self.round_ledger_path, audit)
            self._checkpoint("COMPLETE", active.policy_hash, summary)
        return read_json(self.round_dir / "round_summary.json")


def run_round(
    registry: str | Path,
    red_adapter: Any,
    blue_adapter: Any,
    manifests: dict[str, Any],
) -> dict[str, Any]:
    """Stable software interface for one authoritative evolution round.

    ``manifests`` carries paths/configuration plus ``round_id`` and optionally
    ``project_root``.  It does not grant either adapter registry authority.
    """
    config = dict(manifests)
    round_id = str(config.pop("round_id"))
    project_root = Path(str(config.pop("project_root", Path(registry).parent)))
    registry_path = Path(registry)
    try:
        config["policy_registry"] = str(registry_path.relative_to(project_root))
    except ValueError:
        config["policy_registry"] = str(registry_path)
    return EvolutionRoundRunner(
        config,
        round_id=round_id,
        adapter=_SplitEvolutionAdapter(red_adapter, blue_adapter),
        project_root=project_root,
    ).run()


def _read_config(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".json":
        return read_json(path)
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RoundRunnerViolation("YAML config requires PyYAML; JSON is supported natively") from exc
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RoundRunnerViolation("round config must be an object")
    return payload


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--round-id", required=True)
    parser.add_argument("--project-root", default=str(Path.cwd()))
    args = parser.parse_args()
    config = _read_config(Path(args.config))
    config.setdefault("project_root", args.project_root)
    adapter = _load_adapter(config["adapter"], config)
    candidate_provider = None
    candidate_verifier = None
    semantic_signature_provider = None
    if (
        config.get("blue_evaluation_authority")
        == CANDIDATE_PORTFOLIO_AUTHORITY
    ):
        candidate_provider = _load_adapter(
            config["blue_candidate_provider"], config
        )
        candidate_verifier = _load_adapter(
            config["blue_candidate_verifier"], config
        )
        semantic_provider_spec = str(
            config.get("blue_semantic_signature_provider") or ""
        )
        if semantic_provider_spec:
            semantic_signature_provider = _load_adapter(
                semantic_provider_spec, config
            )
    result = EvolutionRoundRunner(
        config,
        round_id=args.round_id,
        adapter=adapter,
        project_root=args.project_root,
        candidate_provider=candidate_provider,
        candidate_verifier=candidate_verifier,
        semantic_signature_provider=semantic_signature_provider,
    ).run()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
