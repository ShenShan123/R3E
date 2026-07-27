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
from r3e.red.operators import load_operator_space, make_lineage_plan


class FakeRedAdapter:
    """Generate policy-conditioned, valid-by-construction synthetic poisons."""

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
        for index in range(4):
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
                "poison_id": f"fake_{policy_tag}_{index}",
                "case_id": f"fake_{policy_tag}_{index}",
                "design": case["design_id"],
                "golden_rtl": str(golden),
                "buggy_rtl": str(buggy),
                "challenged_policy_id": parent.policy_id,
                "challenged_policy_hash": parent.policy_hash,
                "capability_packet_hash": packet["packet_hash"],
                "family": "constant_error",
                "effect": f"policy_{policy_tag}_effect_{index}",
                "affected_role": "control" if index % 2 == 0 else "data",
                "edit_scope": "expression",
                "composition_depth": 1,
                "changed_modules": 1,
                "changed_blocks": 1,
                "sequential_depth": index % 2,
                "first_divergence_signal": "y",
                "first_divergence_cycle_bucket": "combinational",
                "failure_signature": f"{policy_tag}:y:{index}",
                "normalized_diff_hash": hash_payload({
                    "policy": parent.policy_hash,
                    "index": index,
                }),
            }
            lineage_parent = residual_parents[index % len(residual_parents)] if residual_parents else None
            if lineage_parent is None:
                operator = "fresh"
                row.update({
                    "parent_poison_id": "",
                    "parent_challenged_policy_hash": "",
                    "lineage_depth": 0,
                    "evolution_operator": operator,
                })
            else:
                operator = "relocate"
                row["family"] = lineage_parent["family"]
                row["affected_role"] = (
                    "data"
                    if lineage_parent["affected_role"] != "data"
                    else "control"
                )
                row.update({
                    "parent_poison_id": lineage_parent["poison_id"],
                    "parent_challenged_policy_hash": lineage_parent[
                        "challenged_policy_hash"
                    ],
                    "lineage_depth": int(lineage_parent["lineage_depth"]) + 1,
                    "evolution_operator": operator,
                })
            row["lineage_plan"] = make_lineage_plan(
                parent,
                operator_space=operator_space,
                operator=operator,
                poison_id=row["poison_id"],
                parent=lineage_parent,
            )
            rows.append(row)
        return rows

    @staticmethod
    def prepare_validity(poison: dict[str, Any]) -> dict[str, Any]:
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
            "oracle_result_hash": hash_payload({"poison_id": poison["poison_id"]}),
            "toolchain_fingerprint_hash": hash_payload({"tool": "deterministic_fake"}),
            "command_hash": hash_payload({"command": "deterministic_fake"}),
        }

    @staticmethod
    def evaluate_blue(
        policy: PolicyState, poison: dict[str, Any], seed: int
    ) -> dict[str, Any]:
        return {
            "policy_hash": policy.policy_hash,
            "seed": seed,
            "oracle_ok": False,
            "adapter_mode": "deterministic_fake",
        }

    @staticmethod
    def probe_learnability(
        policy: PolicyState, poison: dict[str, Any]
    ) -> dict[str, Any]:
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
            "evidence": {
                "adapter_mode": "deterministic_fake",
                "poison_id": poison["poison_id"],
                "model_calls": 0,
            },
        }


class FakeBlueAdapter:
    """Deterministic parent-fails/child-passes replay fixture."""

    @staticmethod
    def evaluate_blue(
        policy: PolicyState, _poison: dict[str, Any], seed: int
    ) -> dict[str, Any]:
        return {
            "policy_hash": policy.policy_hash,
            "seed": seed,
            "oracle_ok": False,
            "adapter_mode": "deterministic_fake",
        }

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
        return {
            "policy_hash": policy.policy_hash,
            "seed": seed,
            "oracle_ok": oracle_ok,
            "model_id": "deterministic-fake-model",
            "budget_hash": "deterministic-fake-budget",
            "verifier_hash": "deterministic-fake-verifier",
            "cost": 1.0,
            "adapter_mode": "deterministic_fake",
        }


class DeterministicPromotionAdapter:
    """Select one deterministic survivor; formal promotion remains runner-owned."""

    @staticmethod
    def screen_child(
        _parent: PolicyState,
        child: PolicyState,
        _adaptation_manifest: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "survive": child.policy_id.endswith("C01"),
            "adapter_mode": "deterministic_fake",
        }


class DeterministicEvolutionAdapter:
    """Composite adapter implementing the runner's full environment protocol."""

    toolchain_fingerprint = {
        "adapter_mode": "deterministic_fake",
        "model_calls": 0,
        "eda_calls": 0,
    }

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
        self.toolchain_fingerprint = {
            **dict(getattr(wrapped, "toolchain_fingerprint", {}) or {}),
            "failure_injection_method": fail_method,
            "failure_injection_call": fail_on_call,
        }

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
