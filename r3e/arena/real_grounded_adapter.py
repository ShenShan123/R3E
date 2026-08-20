"""Manifest-backed Grounded Red adapter for the integrated shadow lane.

The adapter owns only provider-facing target selection and parser materializa-
tion.  Formal admission, Blue candidate evaluation, episode creation, archive
classification, and promotion remain in :mod:`r3e.arena.runner`.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from r3e.arena.conformance import bind_adapter_output, make_toolchain_fingerprint
from r3e.blue.portfolio.oracle_gate_verifier import public_case_from_manifest
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_file, hash_payload
from r3e.red.grounded.materializers import materialize_operator
from r3e.red.grounded.mutation_plan import build_mutation_plan
from r3e.red.grounded.openai_provider import (
    OpenAICompatibleGroundedChoiceProvider,
)
from r3e.red.grounded.operator_ast import operator_nodes
from r3e.red.grounded.registry import load_grounded_registries
from r3e.red.operators import load_operator_space, make_lineage_plan, materialize_fresh
from r3e.providers.openai_compatible import OpenAICompatibleJSONClient


class RealGroundedArenaAdapterViolation(RuntimeError):
    """Raised when a manifest/provider proposal cannot be frozen safely."""


class RealGroundedArenaAdapter:
    """One-call Red provider adapter with runner-owned downstream authority."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        manifest_path: str | Path,
        client: OpenAICompatibleJSONClient,
        artifact_root: str | Path,
        case_id: str | None = None,
        round_id: str = "shadow-round",
    ):
        self.root = Path(project_root).resolve()
        self.manifest_path = Path(manifest_path)
        if not self.manifest_path.is_absolute():
            self.manifest_path = (self.root / self.manifest_path).resolve()
        self.client = client
        self.choice_provider = OpenAICompatibleGroundedChoiceProvider(client)
        self.toolchain_fingerprint = deepcopy(
            self.choice_provider.toolchain_fingerprint
        )
        self.artifact_root = Path(artifact_root)
        if not self.artifact_root.is_absolute():
            self.artifact_root = (self.root / self.artifact_root).resolve()
        # Run products may live in the caller-owned workspace (often outside
        # the checkout) or in an explicit ignored runtime subdirectory. They
        # must never target the project root itself or a parent directory,
        # which would make a broad path writable by provider/formal receipts.
        # Release checks still reject any generated in-tree runtime artifact
        # from publication.
        if (
            self.artifact_root == self.root
            or self.artifact_root in self.root.parents
        ):
            raise RealGroundedArenaAdapterViolation(
                "Grounded artifact root must not be the project root or an ancestor"
            )
        self.case_id = case_id
        self._round_id = str(round_id)
        self._cases = self._load_cases()
        self.registries = load_grounded_registries(
            family_registry=self.root / "configs/red/grounded_family_registry_v1.json",
            operator_registry=self.root / "configs/red/grounded_operator_registry_v1.json",
            effect_registry=self.root / "configs/red/grounded_effect_registry_v1.json",
        )
        self.operator_space = load_operator_space(
            self.root / "configs/red/lineage_operator_space_v1.json"
        )

    def _load_cases(self) -> list[dict[str, Any]]:
        if not self.manifest_path.is_file():
            raise RealGroundedArenaAdapterViolation(
                "Grounded target manifest is missing"
            )
        rows = [
            json.loads(line)
            for line in self.manifest_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        cases = []
        for row in rows:
            if not isinstance(row, Mapping) or row.get("eligible") is not True:
                continue
            if self.case_id and row.get("case_id") != self.case_id:
                continue
            if not row.get("grounded_formal_property"):
                raise RealGroundedArenaAdapterViolation(
                    "Grounded manifest row lacks formal property"
                )
            case = public_case_from_manifest(row, project_root=self.root)
            metadata = {
                key: row.get(key)
                for key in (
                    "grounded_testbench",
                    "grounded_formal_property",
                    "grounded_formal_top_module",
                    "grounded_formal_depth",
                )
            }
            if any(not metadata[key] for key in metadata):
                raise RealGroundedArenaAdapterViolation(
                    "Grounded manifest formal asset metadata is incomplete"
                )
            for key in (
                "grounded_testbench",
                "grounded_formal_property",
            ):
                value = Path(str(metadata[key]))
                if not value.is_absolute():
                    value = (self.root / value).resolve()
                if not value.is_file() or self._outside_root(value):
                    raise RealGroundedArenaAdapterViolation(
                        f"Grounded manifest {key} is unavailable"
                    )
                metadata[key] = str(value)
            if (
                not isinstance(metadata["grounded_formal_top_module"], str)
                or not metadata["grounded_formal_top_module"]
                or not isinstance(metadata["grounded_formal_depth"], int)
                or not 1 <= metadata["grounded_formal_depth"] <= 1024
            ):
                raise RealGroundedArenaAdapterViolation(
                    "Grounded manifest formal binding is invalid"
                )
            declared_hashes = row.get("grounded_asset_hashes")
            if not isinstance(declared_hashes, Mapping) or not declared_hashes:
                raise RealGroundedArenaAdapterViolation(
                    "Grounded manifest asset hashes are required"
                )
            for raw_path, expected_hash in declared_hashes.items():
                asset = Path(str(raw_path))
                if not asset.is_absolute():
                    asset = (self.root / asset).resolve()
                if self._outside_root(asset) or not asset.is_file():
                    raise RealGroundedArenaAdapterViolation(
                        "Grounded manifest asset escapes the project root"
                    )
                if hash_file(asset) != str(expected_hash):
                    raise RealGroundedArenaAdapterViolation(
                        "Grounded manifest asset hash mismatch"
                    )
            cases.append({
                **case,
                **metadata,
                "design": str(row.get("design") or row.get("case_id")),
                "manifest_row": deepcopy(dict(row)),
            })
        if len(cases) != 1:
            raise RealGroundedArenaAdapterViolation(
                "Grounded target manifest must select exactly one eligible case"
            )
        return cases

    def _outside_root(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.root)
        except ValueError:
            return True
        return False

    @staticmethod
    def _intent_and_assignment(
        context: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        plan = context.get("grounded_proposal_plan")
        schedule = context.get("grounded_population_schedule")
        if not isinstance(plan, Mapping) or not isinstance(schedule, Mapping):
            raise RealGroundedArenaAdapterViolation(
                "Grounded Red requires frozen proposal and population plans"
            )
        selected_ids = list(plan.get("selected_intent_ids") or [])
        if len(selected_ids) != 1:
            raise RealGroundedArenaAdapterViolation(
                "shadow adapter requires one selected Grounded intent"
            )
        intent = next(
            (
                row for row in plan.get("candidate_intents", [])
                if row.get("intent_id") == selected_ids[0]
            ),
            None,
        )
        assignment = next(
            (
                row for row in schedule.get("assignments", [])
                if row.get("intent_id") == selected_ids[0]
            ),
            None,
        )
        if not isinstance(intent, Mapping) or not isinstance(assignment, Mapping):
            raise RealGroundedArenaAdapterViolation(
                "Grounded intent/assignment binding is incomplete"
            )
        return deepcopy(dict(intent)), deepcopy(dict(assignment))

    def generate_red(
        self,
        parent: PolicyState,
        config: dict[str, Any],
        red_search_context: dict[str, Any],
    ) -> Iterable[dict[str, Any]]:
        intent, assignment = self._intent_and_assignment(red_search_context)
        if intent.get("operator_id") != "replace_comparator":
            raise RealGroundedArenaAdapterViolation(
                "shadow manifest is frozen for comparator boundary"
            )
        if assignment.get("model_id") != self.client.config.model_id:
            raise RealGroundedArenaAdapterViolation(
                "Grounded assignment model differs from provider"
            )
        case = self._cases[0]
        clean_source = Path(case["golden_rtl"]).read_text(encoding="utf-8")
        target_module = str(case["top_module"])
        nodes = operator_nodes(
            clean_source,
            module=target_module,
            operator_id=str(intent["operator_id"]),
        )
        choice = self.choice_provider.choose_target(
            policy=parent,
            intent=intent,
            assignment=assignment,
            clean_source=clean_source,
            target_module=target_module,
            nodes=nodes,
            seed=int(config.get("red_seed", 17)),
        )
        poison_id = f"{self._round_id}:{case['case_id']}:grd"
        mutation_plan = build_mutation_plan(
            plan_id=poison_id,
            policy=parent,
            registries=self.registries,
            target_design=str(case["design"]),
            target_module=target_module,
            target_ast_node_hash=str(choice["selected_node_hash"]),
            family_id=str(intent["family_id"]),
            operator_id=str(intent["operator_id"]),
            expected_runtime_effect_id=str(intent["expected_runtime_effect_id"]),
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
            lineage_operator="fresh",
        )
        materialized = materialize_operator(
            mutation_plan,
            clean_source=clean_source,
        )
        output_dir = self.artifact_root / self._round_id / "poisons"
        output_dir.mkdir(parents=True, exist_ok=True)
        clean_path = output_dir / "clean.v"
        poison_path = output_dir / "poison.v"
        clean_path.write_text(clean_source, encoding="utf-8")
        poison_path.write_text(materialized.poison_source, encoding="utf-8")
        lineage = make_lineage_plan(
            parent,
            operator_space=self.operator_space,
            operator="fresh",
            poison_id=poison_id,
        )
        descriptor = {
            "poison_id": poison_id,
            "case_id": case["case_id"],
            "design": case["design"],
            # Keep the immutable manifest golden as the formal/Blue oracle
            # input.  The generated copy is only a local receipt artifact.
            "golden_rtl": str(case["golden_rtl"]),
            "buggy_rtl": str(poison_path),
            "challenged_policy_id": parent.policy_id,
            "challenged_policy_hash": parent.policy_hash,
            "family": intent["family_id"],
            "effect": intent["expected_runtime_effect_id"],
            "affected_role": intent["target_role"],
            "edit_scope": "expression",
            "composition_depth": 1,
            "changed_modules": 1,
            "changed_blocks": 1,
            "sequential_depth": int(intent["difficulty_target"]["temporal_depth_delta"]),
            "dependency_depth": int(intent["difficulty_target"]["dependency_depth_delta"]),
            "first_divergence_signal": "y",
            "first_divergence_cycle_bucket": "combinational",
            "failure_signature": hash_payload({"poison_id": poison_id, "operator": intent["operator_id"]}),
            "normalized_diff_hash": materialized.receipt["materialization_hash"],
            "lineage_plan": lineage,
            "grounded_proposal_intent_id": intent["intent_id"],
            "grounded_proposal_intent_hash": intent["intent_hash"],
            "grounded_dispatch_kind": intent["dispatch_kind"],
            "grounded_difficulty_target": deepcopy(intent["difficulty_target"]),
            "grounded_proposal_parent_poison_ids": list(intent["parent_poison_ids"]),
            "grounded_proposal_target_memory_ids": list(intent["target_memory_ids"]),
            "grounded_proposal_memory_operator": intent["memory_operator"],
            "grounded_population_assignment_id": assignment["assignment_id"],
            "grounded_population_assignment_hash": assignment["assignment_hash"],
            "grounded_generator_provider_id": assignment["provider_id"],
            "grounded_generator_role": assignment["provider_role"],
            "grounded_population_arm": assignment["population_arm"],
            "grounded_generation_usage": {
                "input_tokens": choice["input_tokens"],
                "output_tokens": choice["output_tokens"],
                "wall_time_ms": choice["wall_time_ms"],
            },
            "grounded_plan_hash": mutation_plan["plan_hash"],
            "grounded_mutation_plan": mutation_plan,
            "grounded_materialization_receipt": materialized.receipt,
            "grounded_semantic_diff_receipt": materialized.semantic_diff_receipt,
            "grounded_parser_provider_receipt": materialized.parser_provider_receipt,
            "grounded_choice_receipt": choice,
            # Icarus must elaborate the testbench top so its oracle runs;
            # the public case top remains the design module for ACP/Yosys.
            "grounded_top_module": "tb",
            "grounded_testbench": case["grounded_testbench"],
            "grounded_formal_property": case["grounded_formal_property"],
            "grounded_formal_top_module": case["grounded_formal_top_module"],
            "grounded_formal_depth": case["grounded_formal_depth"],
            # These are frozen public-manifest facts consumed by the ACP
            # verifier after Grounded admission.
            "deps": list(case["deps"]),
            "tb_sources": list(case["tb_sources"]),
            "tb_output": case["tb_output"],
            "top_module": target_module,
            "sim_timeout": case["sim_timeout"],
            "manifest_file_hashes": deepcopy(case["manifest_file_hashes"]),
        }
        generated = materialize_fresh(lineage, descriptor)
        generated["lineage_plan"] = lineage
        return [bind_adapter_output(
            generated,
            "generate_red",
            self.toolchain_fingerprint,
            model_id=self.client.config.model_id,
            budget_hash=assignment["budget_hash"],
            command_hash=choice["request_hash"],
        )]

    def prepare_validity(self, poison: dict[str, Any]) -> dict[str, Any]:
        raise RealGroundedArenaAdapterViolation(
            "Grounded runtime authority must own validity"
        )

    def evaluate_blue(self, policy: PolicyState, poison: dict[str, Any], seed: int) -> dict[str, Any]:
        raise RealGroundedArenaAdapterViolation(
            "candidate portfolio authority must own Blue evaluation"
        )

    def probe_learnability(self, policy: PolicyState, poison: dict[str, Any]) -> dict[str, Any]:
        teacher = {key: int(value) * 2 for key, value in policy.budgets.items()}
        return bind_adapter_output({
            "label": "reachable",
            "challenged_policy_hash": policy.policy_hash,
            "teacher_mode": "same_model_expanded",
            "teacher_budget": teacher,
            "attempts": 1,
            "successes": 1,
            "budget_exhausted": False,
            "evidence": {
                "adapter_mode": "real_grounded_shadow",
                "provider_calls": 0,
                "poison_id": poison["poison_id"],
            },
        }, "probe_learnability", self.toolchain_fingerprint)

    def screen_child(self, parent: PolicyState, child: PolicyState, adaptation_manifest: dict[str, Any]) -> dict[str, Any]:
        return bind_adapter_output({
            "survive": False,
            "shadow_only": True,
        }, "screen_child", self.toolchain_fingerprint)

    def replay(self, policy: PolicyState, case: dict[str, Any], seed: int) -> dict[str, Any]:
        return bind_adapter_output({
            "policy_hash": policy.policy_hash,
            "seed": int(seed),
            "oracle_ok": False,
            "cost": 0.0,
            "adapter_mode": "real_grounded_shadow",
        }, "replay", self.toolchain_fingerprint)


def create_real_grounded_adapter(config: Mapping[str, Any]) -> RealGroundedArenaAdapter:
    """Factory kept for CLI/config discovery; clients are injected by callers."""
    client = config.get("client")
    if not isinstance(client, OpenAICompatibleJSONClient):
        raise RealGroundedArenaAdapterViolation(
            "real Grounded adapter factory requires an injected client"
        )
    return RealGroundedArenaAdapter(
        project_root=str(config["project_root"]),
        manifest_path=str(config["manifest_path"]),
        client=client,
        artifact_root=str(config["artifact_root"]),
        case_id=str(config.get("case_id") or "") or None,
        round_id=str(config.get("round_id") or "shadow-round"),
    )
