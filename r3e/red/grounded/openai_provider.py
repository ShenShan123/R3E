"""Real-model Grounded Red target chooser with proposal-only authority."""
from __future__ import annotations

from copy import deepcopy
import time
from typing import Any, Iterable, Mapping

from r3e.arena.conformance import make_toolchain_fingerprint
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import canonical_json, hash_payload
from r3e.providers.openai_compatible import OpenAICompatibleJSONClient

from .operator_ast import OperatorAstNode


CHOICE_SCHEMA = "r3e-grounded-model-target-choice-v1"


class RealGroundedPlannerViolation(RuntimeError):
    """Raised when a model attempts to widen frozen Grounded authority."""


class OpenAICompatibleGroundedChoiceProvider:
    """Choose one parser-backed node under a frozen GRD intent/assignment."""

    def __init__(
        self,
        client: OpenAICompatibleJSONClient,
        *,
        adapter_version: str = "1",
    ):
        self.client = client
        config = client.config
        self.toolchain_fingerprint = make_toolchain_fingerprint(
            adapter_id=(
                f"r3e-grd-openai-compatible-{config.provider_id}"
            ),
            adapter_version=adapter_version,
            model_id=config.model_id,
            model_version=config.model_version,
            verifier_id="runner-owned-grounded-execution",
            verifier_version="1",
            runtime_id="openai-compatible-json-client",
            runtime_version=config.config_hash,
        )
        self.toolchain_fingerprint_hash = hash_payload(
            self.toolchain_fingerprint
        )

    def choose_target(
        self,
        *,
        policy: PolicyState,
        intent: Mapping[str, Any],
        assignment: Mapping[str, Any],
        clean_source: str,
        target_module: str,
        nodes: Iterable[OperatorAstNode],
        seed: int,
    ) -> dict[str, Any]:
        options = list(nodes)
        if not options:
            raise RealGroundedPlannerViolation(
                "Grounded planner has no parser-backed target options"
            )
        if (
            intent.get("challenged_policy_hash") != policy.policy_hash
            or intent.get("challenged_effective_policy_hash")
            != policy.effective_policy_hash
            or assignment.get("intent_id") != intent.get("intent_id")
            or assignment.get("intent_hash") != intent.get("intent_hash")
            or assignment.get("challenged_policy_hash")
            != policy.policy_hash
            or assignment.get("model_id")
            != self.client.config.model_id
            or assignment.get("toolchain_fingerprint_hash")
            != self.toolchain_fingerprint_hash
        ):
            raise RealGroundedPlannerViolation(
                "Grounded provider assignment authority mismatch"
            )
        option_rows = [
            {
                "node_ordinal": node.ordinal,
                "node_hash": node.node_hash,
                "node_kind": node.node_kind,
                "old_text": node.old_text,
                "metadata": deepcopy(node.metadata),
            }
            for node in options
        ]
        system = (
            "You are a proposal-only Grounded Red planner. Select exactly "
            "one supplied parser-backed node. You cannot change the family, "
            "operator, expected effect, policy, validator, or budget. Return "
            "one strict JSON object only."
        )
        user = canonical_json({
            "assignment_id": assignment["assignment_id"],
            "intent_id": intent["intent_id"],
            "frozen_authority": {
                "family_id": intent["family_id"],
                "operator_id": intent["operator_id"],
                "expected_runtime_effect_id": intent[
                    "expected_runtime_effect_id"
                ],
                "difficulty_target": intent["difficulty_target"],
                "target_role": intent["target_role"],
            },
            "target_module": target_module,
            # The runner uses clean_source to derive parser-backed nodes, but
            # the provider must not receive golden RTL.  A hash preserves
            # request binding without leaking the oracle/reference artifact.
            "clean_rtl_hash": hash_payload(clean_source),
            "allowed_nodes": option_rows,
            "required_output_schema": {
                "target_module": target_module,
                "node_ordinal": "integer from allowed_nodes",
                "rationale": "brief target-selection rationale",
            },
        })
        started = time.monotonic()
        response = self.client.complete_json(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            seed=int(seed),
        )
        wall_time_ms = int((time.monotonic() - started) * 1000)
        choice = response["result"]
        if set(choice) != {
            "target_module",
            "node_ordinal",
            "rationale",
        }:
            raise RealGroundedPlannerViolation(
                "Grounded target choice fields mismatch"
            )
        ordinal = choice["node_ordinal"]
        if (
            not isinstance(ordinal, int)
            or isinstance(ordinal, bool)
            or choice["target_module"] != target_module
            or not isinstance(choice["rationale"], str)
            or not choice["rationale"]
        ):
            raise RealGroundedPlannerViolation(
                "Grounded target choice is invalid"
            )
        selected = next(
            (node for node in options if node.ordinal == ordinal),
            None,
        )
        if selected is None:
            raise RealGroundedPlannerViolation(
                "Grounded target choice is outside parser authority"
            )
        budget_violations = [
            field
            for field, used, maximum in (
                (
                    "input_tokens",
                    response["input_tokens"],
                    assignment["input_token_budget"],
                ),
                (
                    "output_tokens",
                    response["output_tokens"],
                    assignment["output_token_budget"],
                ),
                (
                    "wall_time_ms",
                    wall_time_ms,
                    assignment["wall_time_budget_ms"],
                ),
            )
            if used > maximum
        ]
        if budget_violations:
            raise RealGroundedPlannerViolation(
                "Grounded target choice exceeded scheduled budget: "
                + ",".join(budget_violations)
            )
        body = {
            "schema_version": CHOICE_SCHEMA,
            "assignment_id": assignment["assignment_id"],
            "assignment_hash": assignment["assignment_hash"],
            "intent_id": intent["intent_id"],
            "intent_hash": intent["intent_hash"],
            "challenged_policy_hash": policy.policy_hash,
            "target_module": target_module,
            "selected_node_ordinal": ordinal,
            "selected_node_hash": selected.node_hash,
            "allowed_node_set_hash": hash_payload(option_rows),
            "model_id": self.client.config.model_id,
            "toolchain_fingerprint_hash": (
                self.toolchain_fingerprint_hash
            ),
            "budget_hash": assignment["budget_hash"],
            "request_hash": response["request_hash"],
            "raw_response_hash": response["raw_response_hash"],
            "input_tokens": response["input_tokens"],
            "output_tokens": response["output_tokens"],
            "wall_time_ms": wall_time_ms,
            "rationale_hash": hash_payload(choice["rationale"]),
        }
        body["choice_hash"] = hash_payload(body)
        return body
