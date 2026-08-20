"""Deterministic, model-free adapters for protocol and failure-recovery tests.

These adapters are engineering fixtures.  Their outputs are intentionally
synthetic and must never be presented as model-driven evolution evidence.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload
from r3e.red.feedback_packet import build_capability_packet
from r3e.red.operators import (
    load_operator_space,
    make_lineage_plan,
    materialize_lineage_operator,
)
from r3e.red.portfolio_challenge import (
    make_portfolio_challenge_plan,
    materialize_portfolio_challenge,
)

from .conformance import bind_adapter_output, make_toolchain_fingerprint


DETERMINISTIC_TOOLCHAIN_FINGERPRINT = make_toolchain_fingerprint(
    adapter_id="r3e-deterministic-fake",
    adapter_version="1",
    model_id="deterministic-fake-model",
    model_version="1",
    verifier_id="deterministic-fake-verifier",
    verifier_version="1",
    runtime_id="python-deterministic-fixture",
    runtime_version="1",
)


class FakeRedAdapter:
    """Generate policy-conditioned, valid-by-construction synthetic poisons."""

    toolchain_fingerprint = DETERMINISTIC_TOOLCHAIN_FINGERPRINT

    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace)

    def generate_red(
        self,
        parent: PolicyState,
        config: dict[str, Any],
        red_search_context: dict[str, Any],
    ) -> Iterable[dict[str, Any]]:
        rtl_dir = self.workspace / parent.policy_hash.replace(":", "_")
        rtl_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        operator_space = load_operator_space(
            Path(__file__).resolve().parents[2]
            / "configs/red/lineage_operator_space_v1.json"
        )
        residual_parents = [
            row for row in red_search_context["archive_summary"]
            if row["archive_kind"] == "residual"
        ]
        proposal_plan = red_search_context.get(
            "grounded_proposal_plan"
        )
        population_schedule = red_search_context.get(
            "grounded_population_schedule"
        )
        population_by_intent = {
            assignment["intent_id"]: assignment
            for assignment in (
                population_schedule.get("assignments", [])
                if population_schedule is not None
                else []
            )
        }
        proposal_intents = []
        if proposal_plan is not None:
            selected = set(proposal_plan["selected_intent_ids"])
            proposal_intents = [
                intent
                for intent in proposal_plan["candidate_intents"]
                if intent["intent_id"] in selected
            ]
            proposal_intents.sort(
                key=lambda intent: proposal_plan[
                    "selected_intent_ids"
                ].index(intent["intent_id"])
            )
        count = len(proposal_intents) if proposal_intents else 4
        generation_tag = str(
            red_search_context["context_hash"]
        ).split(":", 1)[-1][:12]
        for index in range(count):
            intent = (
                proposal_intents[index] if proposal_intents else None
            )
            golden = rtl_dir / f"golden_{index}.v"
            buggy = rtl_dir / f"buggy_{index}.v"
            golden.write_text(
                f"module top(output y); assign y=1'b{index % 2}; endmodule\n",
                encoding="utf-8",
            )
            buggy.write_text(
                f"module top(output y); assign y=1'b{(index + 1) % 2}; endmodule\n",
                encoding="utf-8",
            )
            case = {
                "design_id": f"fake_design_{index}",
                "golden_rtl_hash": hash_payload(golden.read_text(encoding="utf-8")),
                "allowed_mutation_operators": ["constant_flip"],
            }
            packet = build_capability_packet(
                parent,
                design_id=case["design_id"],
                golden_rtl_hash=case["golden_rtl_hash"],
                allowed_mutation_operators=case["allowed_mutation_operators"],
                archive_rows=list(red_search_context["archive_summary"]),
                recent_challenges=[],
            )
            policy_tag = parent.policy_hash.split(":", 1)[-1][:12]
            row = {
                "poison_id": (
                    f"fake_{policy_tag}_{generation_tag}_{index}"
                ),
                "case_id": (
                    f"fake_{policy_tag}_{generation_tag}_{index}"
                ),
                "design": case["design_id"],
                "golden_rtl": str(golden),
                "buggy_rtl": str(buggy),
                "challenged_policy_id": parent.policy_id,
                "challenged_policy_hash": parent.policy_hash,
                "capability_packet_hash": packet["packet_hash"],
                "family": (
                    intent["family_id"]
                    if intent is not None else "constant_error"
                ),
                "effect": (
                    intent["expected_runtime_effect_id"]
                    if intent is not None
                    else f"policy_{policy_tag}_effect_{index}"
                ),
                "affected_role": (
                    intent["target_role"]
                    if intent is not None
                    else ("control" if index % 2 == 0 else "data")
                ),
                "edit_scope": "expression",
                "composition_depth": 1,
                "changed_modules": 1,
                "changed_blocks": 1,
                "sequential_depth": (
                    int(intent["difficulty_target"][
                        "temporal_depth_delta"
                    ])
                    if intent is not None else index % 2
                ),
                "dependency_depth": (
                    int(intent["difficulty_target"][
                        "dependency_depth_delta"
                    ])
                    if intent is not None else 0
                ),
                "first_divergence_signal": "y",
                "first_divergence_cycle_bucket": "combinational",
                "failure_signature": f"{policy_tag}:y:{index}",
                "normalized_diff_hash": hash_payload({
                    "policy": parent.policy_hash,
                    "generation": generation_tag,
                    "index": index,
                }),
            }
            lineage_parent = (
                None
                if proposal_plan is not None
                else (
                    residual_parents[index % len(residual_parents)]
                    if residual_parents else None
                )
            )
            if lineage_parent is None:
                operator = "fresh"
            else:
                operator = "relocate"
                row["family"] = lineage_parent["family"]
                relocation_roles = ("data", "output", "valid_control", "state")
                target_role = relocation_roles[index % len(relocation_roles)]
                if target_role == lineage_parent["affected_role"]:
                    target_role = relocation_roles[
                        (index + 1) % len(relocation_roles)
                    ]
            plan = make_lineage_plan(
                parent,
                operator_space=operator_space,
                operator=operator,
                poison_id=row["poison_id"],
                parent=lineage_parent,
            )
            materialized = materialize_lineage_operator(
                plan,
                lineage_parent,
                **(
                    {"descriptor": row}
                    if operator == "fresh"
                    else {"target_role": target_role}
                ),
            )
            generated = {**row, **materialized}
            if intent is not None:
                generated.update({
                    "grounded_proposal_intent_id": (
                        intent["intent_id"]
                    ),
                    "grounded_proposal_intent_hash": (
                        intent["intent_hash"]
                    ),
                    "grounded_dispatch_kind": (
                        intent["dispatch_kind"]
                    ),
                    "grounded_difficulty_target": dict(
                        intent["difficulty_target"]
                    ),
                    "grounded_proposal_parent_poison_ids": list(
                        intent["parent_poison_ids"]
                    ),
                    "grounded_proposal_target_memory_ids": list(
                        intent["target_memory_ids"]
                    ),
                    "grounded_proposal_memory_operator": (
                        intent["memory_operator"]
                    ),
                })
                assignment = population_by_intent.get(
                    intent["intent_id"]
                )
                if assignment is not None:
                    generated.update({
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
            portfolio_packet = red_search_context.get(
                "portfolio_capability"
            )
            if portfolio_packet is not None:
                portfolio_operators = (
                    "portfolio_bypass",
                    "router_ambiguity",
                    "specialist_deepening",
                    "portfolio_conflict",
                )
                region_hashes = [
                    region["region_hash"]
                    for region in portfolio_packet["coverage_regions"]
                ]
                targets = (
                    [region_hashes[index % len(region_hashes)]]
                    if region_hashes else []
                )
                portfolio_plan = make_portfolio_challenge_plan(
                    policy=parent,
                    packet=portfolio_packet,
                    operator=portfolio_operators[
                        index % len(portfolio_operators)
                    ],
                    poison_id=row["poison_id"],
                    target_region_hashes=targets,
                )
                generated = materialize_portfolio_challenge(
                    portfolio_plan, generated
                )
            rows.append(bind_adapter_output(
                generated,
                "generate_red",
                self.toolchain_fingerprint,
            ))
        return rows

    @staticmethod
    def prepare_validity(poison: dict[str, Any]) -> dict[str, Any]:
        return bind_adapter_output({
            "poison_id": poison["poison_id"],
            "challenged_policy_hash": poison["challenged_policy_hash"],
            "poison_payload_hash": poison["poison_payload_hash"],
            "golden_compile_ok": True,
            "golden_oracle_ok": True,
            "buggy_compile_ok": True,
            "buggy_functional_fail": True,
            "formal_status": "PROVEN_NON_EQUIV",
            "output_complete": True,
            "revert_oracle_ok": True,
            "fresh_output": True,
            "oracle_result_hash": hash_payload({"poison_id": poison["poison_id"]}),
            "counterexample_hash": hash_payload({
                "poison_id": poison["poison_id"],
                "witness": "deterministic_fake",
            }),
            "toolchain_fingerprint_hash": hash_payload({"tool": "deterministic_fake"}),
            "command_hash": hash_payload({"command": "deterministic_fake"}),
        }, "prepare_validity", DETERMINISTIC_TOOLCHAIN_FINGERPRINT)

    @staticmethod
    def evaluate_blue(
        policy: PolicyState, poison: dict[str, Any], seed: int
    ) -> dict[str, Any]:
        return bind_adapter_output({
            "policy_hash": policy.policy_hash,
            "seed": seed,
            "oracle_ok": False,
            "adapter_mode": "deterministic_fake",
        }, "evaluate_blue", DETERMINISTIC_TOOLCHAIN_FINGERPRINT)

    @staticmethod
    def probe_learnability(
        policy: PolicyState, poison: dict[str, Any]
    ) -> dict[str, Any]:
        return bind_adapter_output({
            "label": "reachable",
            "challenged_policy_hash": policy.policy_hash,
            "teacher_mode": "same_model_expanded",
            "teacher_budget": {
                key: int(value) * 2 for key, value in policy.budgets.items()
            },
            "attempts": 1,
            "successes": 1,
            "budget_exhausted": False,
            "evidence": {
                "adapter_mode": "deterministic_fake",
                "poison_id": poison["poison_id"],
                "model_calls": 0,
            },
        }, "probe_learnability", DETERMINISTIC_TOOLCHAIN_FINGERPRINT)


class FakeBlueAdapter:
    """Deterministic parent-fails/child-passes replay fixture."""

    toolchain_fingerprint = DETERMINISTIC_TOOLCHAIN_FINGERPRINT

    @staticmethod
    def evaluate_blue(
        policy: PolicyState, _poison: dict[str, Any], seed: int
    ) -> dict[str, Any]:
        return bind_adapter_output({
            "policy_hash": policy.policy_hash,
            "seed": seed,
            "oracle_ok": False,
            "adapter_mode": "deterministic_fake",
        }, "evaluate_blue", DETERMINISTIC_TOOLCHAIN_FINGERPRINT)

    @staticmethod
    def screen_child(
        parent: PolicyState,
        child: PolicyState,
        adaptation_manifest: dict[str, Any],
    ) -> dict[str, Any]:
        return DeterministicPromotionAdapter.screen_child(
            parent, child, adaptation_manifest
        )

    @staticmethod
    def replay(
        policy: PolicyState, case: dict[str, Any], seed: int
    ) -> dict[str, Any]:
        challenged_hash = str(case.get("challenged_policy_hash") or "")
        is_target = bool(challenged_hash)
        oracle_ok = not is_target or policy.policy_hash != challenged_hash
        return bind_adapter_output({
            "policy_hash": policy.policy_hash,
            "seed": seed,
            "oracle_ok": oracle_ok,
            "model_id": "deterministic-fake-model",
            "budget_hash": hash_payload({"budget": "deterministic-fake"}),
            "verifier_hash": DETERMINISTIC_TOOLCHAIN_FINGERPRINT["verifier_hash"],
            "cost": 1.0,
            "adapter_mode": "deterministic_fake",
        }, "replay", DETERMINISTIC_TOOLCHAIN_FINGERPRINT)


class DeterministicPromotionAdapter:
    """Select one deterministic survivor; formal promotion remains runner-owned."""

    toolchain_fingerprint = DETERMINISTIC_TOOLCHAIN_FINGERPRINT

    @staticmethod
    def screen_child(
        _parent: PolicyState,
        child: PolicyState,
        _adaptation_manifest: dict[str, Any],
    ) -> dict[str, Any]:
        return bind_adapter_output({
            "survive": child.policy_id.endswith("C01"),
            "adapter_mode": "deterministic_fake",
        }, "screen_child", DETERMINISTIC_TOOLCHAIN_FINGERPRINT)


class DeterministicEvolutionAdapter:
    """Composite adapter implementing the runner's full environment protocol."""

    toolchain_fingerprint = DETERMINISTIC_TOOLCHAIN_FINGERPRINT

    def __init__(self, workspace: str | Path):
        self.red = FakeRedAdapter(workspace)
        self.blue = FakeBlueAdapter()
        self.promotion = DeterministicPromotionAdapter()

    def generate_red(
        self,
        parent: PolicyState,
        config: dict[str, Any],
        red_search_context: dict[str, Any],
    ):
        return self.red.generate_red(parent, config, red_search_context)

    def prepare_validity(self, poison: dict[str, Any]):
        return self.red.prepare_validity(poison)

    def evaluate_blue(self, policy: PolicyState, poison: dict[str, Any], seed: int):
        return self.red.evaluate_blue(policy, poison, seed)

    def probe_learnability(self, policy: PolicyState, poison: dict[str, Any]):
        return self.red.probe_learnability(policy, poison)

    def screen_child(
        self,
        parent: PolicyState,
        child: PolicyState,
        adaptation_manifest: dict[str, Any],
    ):
        return self.promotion.screen_child(parent, child, adaptation_manifest)

    def replay(self, policy: PolicyState, case: dict[str, Any], seed: int):
        return self.blue.replay(policy, case, seed)


class FailureInjectionAdapter:
    """Raise once at a named adapter method, then delegate normally."""

    def __init__(
        self,
        wrapped: Any,
        *,
        fail_method: str,
        fail_on_call: int = 1,
    ):
        if fail_on_call < 1:
            raise ValueError("fail_on_call must be positive")
        self.wrapped = wrapped
        self.fail_method = fail_method
        self.fail_on_call = fail_on_call
        self.failed = False
        self.call_counts: dict[str, int] = {}
        self.toolchain_fingerprint = dict(wrapped.toolchain_fingerprint)
        self.conformance_fingerprints = list(
            getattr(
                wrapped,
                "conformance_fingerprints",
                [wrapped.toolchain_fingerprint],
            )
        )

    def __getattr__(self, name: str):
        target = getattr(self.wrapped, name)
        if not callable(target):
            return target

        def call(*args: Any, **kwargs: Any):
            self.call_counts[name] = self.call_counts.get(name, 0) + 1
            if (
                name == self.fail_method
                and not self.failed
                and self.call_counts[name] == self.fail_on_call
            ):
                self.failed = True
                raise RuntimeError(
                    f"injected failure at adapter method: {name}"
                    f" call {self.fail_on_call}"
                )
            return target(*args, **kwargs)

        return call


def create_adapter(config: dict[str, Any]) -> DeterministicEvolutionAdapter | FailureInjectionAdapter:
    """Factory used by the runner CLI for model-free system validation."""
    project_root = Path(str(config.get("project_root") or Path.cwd()))
    workspace = Path(str(config.get("fake_workspace") or "runtime/fake_adapter"))
    if not workspace.is_absolute():
        workspace = project_root / workspace
    adapter: DeterministicEvolutionAdapter | FailureInjectionAdapter
    adapter = DeterministicEvolutionAdapter(workspace)
    fail_method = str(config.get("failure_injection_method") or "")
    if fail_method:
        adapter = FailureInjectionAdapter(
            adapter,
            fail_method=fail_method,
            fail_on_call=int(config.get("failure_injection_call") or 1),
        )
    return adapter
