"""Blue's model call: one repair candidate per attempt, as a proposal only.

The prompt holds only the task, the output contract and the task boundary
(the module interface stays). It carries no repair prior: no "minimal" or
"local" patch scope, since many real bugs need edits in several places
(changed 2026-10-03; earlier runs used a "minimal, local patch" lens and a
``local_block`` patch scope). Blue returns complete replacement RTL and a short
description of its edit. It never verifies, ranks or claims correctness: the
runner simulates every candidate.

Answer format ``edits`` (``BlueConfig.answer_format``): Blue may instead give
its candidate as edits to the current RTL, each replacing an exact piece of it
(any size: a token, a block, a whole module) with new text; an empty ``find``
appends (e.g. a missing module or definition) and an empty ``replace`` deletes.
The runner applies them in order. A ``find`` must occur exactly once, either
verbatim or up to whitespace; otherwise the attempt fails with the reason, and
nothing is guessed. This changes only how a candidate is written, not what it
may change, so a repair costs output in proportion to its size.

``command_hash`` binds the provider's request hash (which covers any
knowledge ``KnowledgeInjectingClient`` added) to the prompt and the case, so
an attempt record shows exactly what Blue was sent.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Mapping

from r3e.protocol.hashing import canonical_json, hash_payload


SYSTEM_PROMPT = (
    "You propose one RTL repair candidate. Return one strict JSON object only. You do not verify, "
    "rank, select, or claim correctness. Return the complete replacement RTL and a concise "
    "description of your edit."
)


SYSTEM_PROMPT_EDITS = (
    "You propose one RTL repair candidate. Return one strict JSON object only. You do not verify, "
    "rank, select, or claim correctness. Give the candidate either as edits to the current RTL or as "
    "the complete replacement RTL, and a concise description of your edit."
)


class BlueOutputViolation(RuntimeError):
    """The model's answer is not a well-formed repair proposal."""


class EditNotApplicable(BlueOutputViolation):
    """An edit's ``find`` text does not occur exactly once in the current RTL."""


def _locate(source: str, find: str) -> tuple[int, int] | str:
    """Span of the unique occurrence of ``find`` (verbatim, else up to whitespace), or a reason."""
    count = source.count(find)
    if count == 1:
        start = source.index(find)
        return start, start + len(find)
    words = find.split()
    if not words:
        return "find is blank"
    matches = list(re.finditer(r"\s+".join(map(re.escape, words)), source))
    if len(matches) == 1:
        return matches[0].span()
    n = count or len(matches)
    return "not found in the current RTL" if n == 0 else f"found {n} times; include more context to make it unique"


def apply_text_edits(source: str, edits: Any) -> str:
    """Apply ``[{"find", "replace"}, ...]`` in order (see the module docstring)."""
    if not isinstance(edits, list) or not edits:
        raise BlueOutputViolation("edits must be a non-empty list")
    out = source
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict) or set(edit) != {"find", "replace"} \
                or not all(isinstance(edit[k], str) for k in ("find", "replace")):
            raise BlueOutputViolation(f"edit {i}: needs exactly the string fields find and replace")
        if edit["find"] == "":
            out = out.rstrip("\n") + "\n" + edit["replace"] + ("" if edit["replace"].endswith("\n") else "\n")
            continue
        span = _locate(out, edit["find"])
        if isinstance(span, str):
            raise EditNotApplicable(f"edit {i}: find text {span}")
        out = out[:span[0]] + edit["replace"] + out[span[1]:]
    return out


def _validate(raw: Mapping[str, Any], *, candidate_id: str, candidate_seed: int,
              source: str | None = None) -> dict[str, Any]:
    """``source``: the current RTL when edits are allowed (answer format ``edits``)."""
    payload = deepcopy(dict(raw))
    if source is not None and "edits" in payload and "replacement_rtl" not in payload:
        payload["replacement_rtl"] = apply_text_edits(source, payload.pop("edits"))
        form = "edits"
    else:
        form = "full"
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
    return {"replacement_rtl": payload["replacement_rtl"], "edit": payload["edit"], "answer_form": form}


class BlueProvider:
    def __init__(self, client: Any, *, answer_format: str = "full"):
        if answer_format not in ("full", "edits"):
            raise ValueError("answer_format must be full or edits")
        self.client = client
        self.answer_format = answer_format

    @staticmethod
    def _edits_contract() -> dict[str, Any]:
        return _edits_contract_fields()

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
            **({"specification": artifact["specification"]} if artifact.get("specification") else {}),
            **(self._edits_contract() if self.answer_format == "edits" else {
                "required_output_schema": {"replacement_rtl": "complete candidate RTL string",
                                           "edit": "concise edit description"},
                "output_constraints": {
                    "required_top_level_fields": ["replacement_rtl", "edit"],
                    "optional_echo_fields": ["candidate_id", "candidate_seed"],
                    "semantic_patch_is_runner_owned": True,
                    "echoed_identity_must_match_request": True,
                }}),
        })
        system = SYSTEM_PROMPT_EDITS if self.answer_format == "edits" else SYSTEM_PROMPT
        response = self.client.complete_json(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            seed=int(slot["candidate_seed"]),
        )
        patch = _validate(response["result"], candidate_id=candidate_id,
                          candidate_seed=int(slot["candidate_seed"]),
                          source=source if self.answer_format == "edits" else None)
        return {
            "patch_payload": patch,
            "raw_response_hash": response["raw_response_hash"],
            "input_tokens": response["input_tokens"],
            "output_tokens": response["output_tokens"],
            "command_hash": hash_payload({"provider_request_hash": response["request_hash"],
                                          "prompt_hash": prompt_hash,
                                          "current_case_artifact_hash": hash_payload(artifact)}),
        }


def _edits_contract_fields() -> dict[str, Any]:
    return {
        "required_output_schema": {
            "edits": [{"find": "exact text of current_buggy_rtl occurring once; empty to append at the end",
                       "replace": "new text; empty to delete"}],
            "replacement_rtl": "complete candidate RTL string, instead of edits",
            "edit": "concise edit description"},
        "output_constraints": {
            "required_top_level_fields": ["edit", "exactly one of edits or replacement_rtl"],
            "edits_apply_in_order_to_current_buggy_rtl": True,
            "optional_echo_fields": ["candidate_id", "candidate_seed"],
            "semantic_patch_is_runner_owned": True,
            "echoed_identity_must_match_request": True,
        }}
