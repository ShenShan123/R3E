"""Blue's model call: one repair candidate per attempt, as a proposal only.

The prompt holds only the task, the output contract and the task boundary
(the module interface stays). It carries no repair prior: no "minimal" or
"local" patch scope, since many real bugs need edits in several places
(changed 2026-10-03; earlier runs used a "minimal, local patch" lens and a
``local_block`` patch scope). Blue returns complete replacement RTL and a short
description of its edit. It never verifies, ranks or claims correctness: the
runner simulates every candidate.

``command_hash`` binds the provider's request hash (which covers any
knowledge ``KnowledgeInjectingClient`` added) to the prompt and the case, so
an attempt record shows exactly what Blue was sent.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from r3e.protocol.hashing import canonical_json, hash_payload


SYSTEM_PROMPT = (
    "You propose one RTL repair candidate. Return one strict JSON object only. You do not verify, "
    "rank, select, or claim correctness. Return the complete replacement RTL and a concise "
    "description of your edit."
)


class BlueOutputViolation(RuntimeError):
    """The model's answer is not a well-formed repair proposal."""


def _validate(raw: Mapping[str, Any], *, candidate_id: str, candidate_seed: int) -> dict[str, str]:
    payload = deepcopy(dict(raw))
    required, echoed = {"replacement_rtl", "edit"}, {"candidate_id", "candidate_seed"}
    if not required <= set(payload) or not set(payload) <= required | echoed:
        raise BlueOutputViolation("candidate fields mismatch: " + ",".join(sorted(map(str, payload))))
    if payload.get("candidate_id", candidate_id) != candidate_id:
        raise BlueOutputViolation("model candidate identity differs from runner assignment")
    seed = payload.get("candidate_seed", candidate_seed)
    if isinstance(seed, bool) or not isinstance(seed, int) or seed != candidate_seed:
        raise BlueOutputViolation("model candidate seed differs from runner assignment")
    for key in ("replacement_rtl", "edit"):
        if not isinstance(payload[key], str) or not payload[key]:
            raise BlueOutputViolation(f"{key} must be non-empty")
    return {"replacement_rtl": payload["replacement_rtl"], "edit": payload["edit"]}


class BlueProvider:
    def __init__(self, client: Any):
        self.client = client

    def generate_candidate(self, *, evidence: dict[str, Any], artifact: dict[str, Any], slot: dict[str, Any],
                           lens_instruction: str, prompt_hash: str, candidate_id: str) -> dict[str, Any]:
        source = artifact.get("buggy_rtl_source")
        if not isinstance(source, str) or not source:
            raise BlueOutputViolation("the provider needs the current buggy RTL")
        user = canonical_json({
            "candidate_id": candidate_id,
            "candidate_seed": slot["candidate_seed"],
            "lens_id": slot["lens_id"],
            "lens_instruction": lens_instruction,
            "current_failure_evidence": evidence,
            "current_buggy_rtl": source,
            "top_module": artifact.get("top_module", "top"),
            "required_output_schema": {"replacement_rtl": "complete candidate RTL string",
                                       "edit": "concise edit description"},
            "output_constraints": {
                "required_top_level_fields": ["replacement_rtl", "edit"],
                "optional_echo_fields": ["candidate_id", "candidate_seed"],
                "semantic_patch_is_runner_owned": True,
                "echoed_identity_must_match_request": True,
            },
        })
        response = self.client.complete_json(
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
            seed=int(slot["candidate_seed"]),
        )
        patch = _validate(response["result"], candidate_id=candidate_id,
                          candidate_seed=int(slot["candidate_seed"]))
        return {
            "patch_payload": patch,
            "raw_response_hash": response["raw_response_hash"],
            "input_tokens": response["input_tokens"],
            "output_tokens": response["output_tokens"],
            "command_hash": hash_payload({"provider_request_hash": response["request_hash"],
                                          "prompt_hash": prompt_hash,
                                          "current_case_artifact_hash": hash_payload(artifact)}),
        }
