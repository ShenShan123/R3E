"""Deterministic ACP fixtures; never model-driven evidence."""
from __future__ import annotations

from typing import Any

from r3e.arena.conformance import bind_adapter_output, make_toolchain_fingerprint
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import hash_payload


FAKE_CANDIDATE_TOOLCHAIN = make_toolchain_fingerprint(
    adapter_id="r3e-acp-deterministic-fake",
    adapter_version="1",
    model_id="deterministic-fake-candidate-model",
    model_version="1",
    verifier_id="runner-owned-fake-candidate-verifier",
    verifier_version="1",
    runtime_id="python-acp-fixture",
    runtime_version="1",
)
FAKE_CANDIDATE_VERIFIER_HASH = hash_payload({
    "verifier": "runner-owned-fake-candidate-verifier-v1",
})


class DeterministicFakeCandidateProvider:
    """Return slot-bound patch proposals without correctness authority."""

    toolchain_fingerprint = FAKE_CANDIDATE_TOOLCHAIN

    def __init__(
        self,
        *,
        input_tokens_per_call: int = 7,
        output_tokens_per_call: int = 3,
        forbidden_authority_field: str = "",
        semantic_groups: dict[int, str] | None = None,
    ):
        self.input_tokens_per_call = int(input_tokens_per_call)
        self.output_tokens_per_call = int(output_tokens_per_call)
        self.forbidden_authority_field = forbidden_authority_field
        self.semantic_groups = {
            int(key): str(value)
            for key, value in (semantic_groups or {}).items()
        }
        self.calls: list[dict[str, Any]] = []

    def generate_candidate(
        self,
        *,
        policy: PolicyState,
        current_case_evidence: dict[str, Any],
        slot: dict[str, Any],
        prompt_asset: str,
        prompt_hash: str,
        candidate_id: str,
    ) -> dict[str, Any]:
        self.calls.append({
            "candidate_id": candidate_id,
            "slot": dict(slot),
            "current_case_evidence": dict(current_case_evidence),
        })
        semantic_group = self.semantic_groups.get(
            int(slot["slot_index"]), f"slot-{slot['slot_index']}"
        )
        patch_payload = {
            "candidate_id": candidate_id,
            "slot_index": slot["slot_index"],
            "lens_id": slot["lens_id"],
            "edit": f"deterministic_edit_{slot['slot_index']}",
            "semantic_patch": {
                "changed_modules": ["dut"],
                "changed_blocks": [f"block:{semantic_group}"],
                "changed_ast_nodes": [f"node:{semantic_group}"],
                "changed_signal_roles": [f"role:{semantic_group}"],
                "operator_classes": [f"operator:{semantic_group}"],
                "patch_scope": policy.configuration["patch_scope"],
                "normalized_ast_patch": {
                    "kind": "deterministic_fake_patch",
                    "semantic_group": semantic_group,
                },
            },
        }
        raw = {
            "candidate_id": candidate_id,
            "slot_index": slot["slot_index"],
            "lens_id": slot["lens_id"],
            "lens_hash": slot["lens_hash"],
            "candidate_seed": slot["candidate_seed"],
            "prompt_hash": prompt_hash,
            "current_case_evidence_hash": hash_payload(
                current_case_evidence
            ),
            "raw_response_hash": hash_payload({
                "prompt_asset": prompt_asset,
                "candidate_id": candidate_id,
            }),
            "patch_payload": patch_payload,
            "patch_payload_hash": hash_payload(patch_payload),
            "input_tokens": self.input_tokens_per_call,
            "output_tokens": self.output_tokens_per_call,
        }
        if self.forbidden_authority_field:
            raw[self.forbidden_authority_field] = True
        return bind_adapter_output(
            raw,
            "generate_blue_candidate",
            self.toolchain_fingerprint,
            budget_hash=hash_payload(policy.budgets),
            command_hash=hash_payload({
                "candidate_id": candidate_id,
                "prompt_hash": prompt_hash,
                "candidate_seed": slot["candidate_seed"],
            }),
        )


class DeterministicFakeCandidateVerifier:
    """Runner-invoked verifier fixture with configurable passing slots."""

    verifier_hash = FAKE_CANDIDATE_VERIFIER_HASH

    def __init__(self, *, successful_slots: set[int] | None = None):
        self.successful_slots = set(successful_slots or set())
        self.calls: list[str] = []

    def __call__(
        self,
        *,
        policy: PolicyState,
        case: dict[str, Any],
        current_case_evidence: dict[str, Any],
        slot: dict[str, Any],
        candidate_id: str,
        patch_payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(candidate_id)
        authority = {
            "policy_hash": policy.policy_hash,
            "case_id": case["case_id"],
            "candidate_id": candidate_id,
            "patch_payload": patch_payload,
            "current_case_evidence": current_case_evidence,
        }
        oracle_ok = int(slot["slot_index"]) in self.successful_slots
        return {
            "parse_ok": True,
            "scope_ok": True,
            "compile_ok": True,
            "compile_receipt_hash": hash_payload({
                **authority, "stage": "compile"
            }),
            "simulation_receipt_hash": hash_payload({
                **authority, "stage": "simulation"
            }),
            "formal_receipt_hash": hash_payload({
                **authority, "stage": "formal"
            }),
            "oracle_ok": oracle_ok,
            "changed_modules": 1,
            "changed_blocks": 1,
            "ast_edit_count": int(slot["slot_index"]) + 1,
        }


def create_candidate_provider(
    config: dict[str, Any],
) -> DeterministicFakeCandidateProvider:
    return DeterministicFakeCandidateProvider(
        input_tokens_per_call=int(
            config.get("fake_candidate_input_tokens") or 7
        ),
        output_tokens_per_call=int(
            config.get("fake_candidate_output_tokens") or 3
        ),
        semantic_groups={
            int(key): str(value)
            for key, value in (
                config.get("fake_candidate_semantic_groups") or {}
            ).items()
        },
    )


def create_candidate_verifier(
    config: dict[str, Any],
) -> DeterministicFakeCandidateVerifier:
    return DeterministicFakeCandidateVerifier(
        successful_slots={
            int(value) for value in config.get(
                "fake_candidate_successful_slots", [1]
            )
        }
    )
