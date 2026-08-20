"""Real-model ACP candidate provider with proposal-only authority."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from r3e.arena.conformance import (
    bind_adapter_output,
    make_toolchain_fingerprint,
)
from r3e.policy.schema import PolicyState
from r3e.protocol.hashing import canonical_json, hash_payload
from r3e.providers.openai_compatible import OpenAICompatibleJSONClient


class RealCandidateProviderViolation(RuntimeError):
    """Raised when a model response exceeds candidate proposal authority."""


def _validate_patch(
    raw: Mapping[str, Any],
    *,
    expected_candidate_id: str,
    expected_candidate_seed: int,
) -> dict[str, Any]:
    payload = deepcopy(dict(raw))
    base_fields = {"replacement_rtl", "edit"}
    optional_identity_fields = {"candidate_id", "candidate_seed"}
    if (
        not base_fields <= set(payload)
        or not set(payload) <= base_fields | optional_identity_fields
    ):
        raise RealCandidateProviderViolation(
            "real candidate patch fields mismatch: "
            + ",".join(sorted(str(field) for field in payload))
        )
    supplied_candidate_id = payload.pop("candidate_id", None)
    if (
        supplied_candidate_id is not None
        and supplied_candidate_id != expected_candidate_id
    ):
        raise RealCandidateProviderViolation(
            "model candidate identity differs from runner assignment"
        )
    supplied_candidate_seed = payload.pop("candidate_seed", None)
    if supplied_candidate_seed is not None and (
        isinstance(supplied_candidate_seed, bool)
        or not isinstance(supplied_candidate_seed, int)
        or supplied_candidate_seed != expected_candidate_seed
    ):
        raise RealCandidateProviderViolation(
            "model candidate seed differs from runner assignment"
        )
    if not isinstance(payload["replacement_rtl"], str) or not payload[
        "replacement_rtl"
    ]:
        raise RealCandidateProviderViolation(
            "replacement_rtl must be non-empty"
        )
    if not isinstance(payload["edit"], str) or not payload["edit"]:
        raise RealCandidateProviderViolation("edit must be non-empty")
    return {
        "replacement_rtl": payload["replacement_rtl"],
        "edit": payload["edit"],
    }


class OpenAICompatibleCandidateProvider:
    """Call one configured model per frozen slot and return only a proposal."""

    requires_current_case_artifact = True

    def __init__(
        self,
        client: OpenAICompatibleJSONClient,
        *,
        verifier_id: str,
        verifier_version: str,
        adapter_version: str = "2",
    ):
        self.client = client
        config = client.config
        self.toolchain_fingerprint = make_toolchain_fingerprint(
            adapter_id=(
                f"r3e-acp-openai-compatible-{config.provider_id}"
            ),
            adapter_version=adapter_version,
            model_id=config.model_id,
            model_version=config.model_version,
            verifier_id=verifier_id,
            verifier_version=verifier_version,
            runtime_id="openai-compatible-json-client",
            runtime_version=config.config_hash,
        )

    def generate_candidate(
        self,
        *,
        policy: PolicyState,
        current_case_evidence: dict[str, Any],
        current_case_artifact: dict[str, Any],
        slot: dict[str, Any],
        prompt_asset: str,
        prompt_hash: str,
        candidate_id: str,
    ) -> dict[str, Any]:
        source = current_case_artifact.get("buggy_rtl_source")
        if not isinstance(source, str) or not source:
            raise RealCandidateProviderViolation(
                "real candidate provider requires current buggy RTL"
            )
        artifact_hash = hash_payload(current_case_artifact)
        system = (
            "You propose one RTL repair candidate. Return one strict JSON "
            "object only. You do not verify, rank, select, or claim "
            "correctness. Obey the frozen patch scope. Return only complete "
            "replacement RTL and a concise edit description. The runner, "
            "not you, derives all semantic patch metadata from the RTL AST."
        )
        user = canonical_json({
            "candidate_id": candidate_id,
            "candidate_seed": slot["candidate_seed"],
            "lens_id": slot["lens_id"],
            "lens_instruction": prompt_asset,
            "patch_scope": policy.configuration["patch_scope"],
            "current_failure_evidence": current_case_evidence,
            "current_buggy_rtl": source,
            "top_module": current_case_artifact.get("top_module", "top"),
            "required_output_schema": {
                "replacement_rtl": "complete candidate RTL string",
                "edit": "concise edit description",
            },
            "output_constraints": {
                "required_top_level_fields": [
                    "replacement_rtl",
                    "edit",
                ],
                "optional_echo_fields": [
                    "candidate_id",
                    "candidate_seed",
                ],
                "semantic_patch_is_runner_owned": True,
                "echoed_identity_must_match_request": True,
            },
        })
        response = self.client.complete_json(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            seed=int(slot["candidate_seed"]),
        )
        patch = _validate_patch(
            response["result"],
            expected_candidate_id=candidate_id,
            expected_candidate_seed=int(slot["candidate_seed"]),
        )
        body = {
            "candidate_id": candidate_id,
            "slot_index": slot["slot_index"],
            "lens_id": slot["lens_id"],
            "lens_hash": slot["lens_hash"],
            "candidate_seed": slot["candidate_seed"],
            "prompt_hash": prompt_hash,
            "current_case_evidence_hash": hash_payload(
                current_case_evidence
            ),
            "current_case_artifact_hash": artifact_hash,
            "raw_response_hash": response["raw_response_hash"],
            "patch_payload": {
                "candidate_id": candidate_id,
                "slot_index": slot["slot_index"],
                "lens_id": slot["lens_id"],
                **patch,
            },
            "input_tokens": response["input_tokens"],
            "output_tokens": response["output_tokens"],
        }
        body["patch_payload_hash"] = hash_payload(body["patch_payload"])
        return bind_adapter_output(
            body,
            "generate_blue_candidate",
            self.toolchain_fingerprint,
            budget_hash=hash_payload(policy.budgets),
            command_hash=hash_payload({
                "provider_request_hash": response["request_hash"],
                "prompt_hash": prompt_hash,
                "current_case_artifact_hash": artifact_hash,
            }),
        )
