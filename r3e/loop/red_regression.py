"""Red proposals for the prospective progress-to-preservation experiment.

Separate from red_design's residual-fault protocol. No automatic retries, Blue
calls, or hidden evidence. The caller supplies frozen public carrier material.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import time
from typing import Any

from r3e.protocol.hashing import canonical_json, hash_payload
from r3e.providers.openai_compatible import OpenAICompatibleProviderViolation
from r3e.knowledge.verilog_utils import safe_tokenize, is_identifier
from .budget import BudgetedClient
from .blue_provider import apply_text_edits, BlueOutputViolation
from .red_design import (EditBudget, RedDesignOutputViolation, actual_diff,
                         materialize_predicted_repair, validate_design)

VERSION = "red_progress_preservation_v4"
MANUAL_SUMMARY = (
    "To restore request progress, a repair may relax admission based only on outstanding requests, "
    "without reserving capacity for responses already stored or still to arrive. Preserve delivery "
    "order and data/address association under downstream backpressure while meeting the specified "
    "progress requirement."
)
ABSTRACT_SYSTEM = """Analyze the supplied human-selected development observation. Return one JSON
object with evidence_ids (a nonempty list of supplied IDs), attempted_decision,
violated_obligation, preserved_obligations (a nonempty list of strings),
applicability_preconditions (a nonempty list of strings), and predicted_misleading_repair.
All scalar fields are nonempty strings. Explain a repair decision and its causal consequences,
not a mutation-operator label. Distinguish the original defect from a repair regression.
Use only the supplied evidence; do not invent missing reasoning or claim repeated model failure.
The regression stimulus was added after the source encounter as development evidence.
"""
GENERATE_SYSTEM = """Generate one defective implementation of the supplied correct RTL.
Return a strict JSON object matching the output schema, or {"abstain": "reason"}.
You may delete, reconnect or change several functional structures within the 40% syntax budget.
Preserve interfaces, parameters, compiler directives, dependencies and the specification.
Preserve every existing comment verbatim and in the same order. Do not add, remove,
or edit comments, including when deleting adjacent code. Put all mutation explanations
in the JSON hypothesis fields, never in the RTL shown to Blue. New identifier names
are permitted but recorded for review; use functional names, not repair advice.
Create a coherent, specified progress defect: the mutant must compile, fail public W_bug,
and pass public W_preserve. Do not change tests, configuration, expected outputs or input legality.
Use only the declared editable primary file. Do not add padding or rewrite the entire design.
Supply a plausible predicted repair as edits to YOUR MUTANT, not to the clean implementation.
The predicted repair should restore W_bug while breaking a different, previously preserved
obligation checked by W_preserve. This is a diagnostic prediction, not a requirement to leave
the original defect unfixed. Do not insert a second unrelated bug to manufacture a failure.
Its local result is recorded separately and does NOT gate which admitted mutants reach Blue.
Your hypothesis is not a claim about actual Blue behavior. Do not infer history that was not supplied.
All obligations must be knowable from the unchanged specification. Cite that specification.
Return edits as ordered {find, replace} pairs. Do not return buggy_rtl or copy unchanged RTL.
Use the smallest uniquely matching spans needed for your changes; edits may span whole blocks.
Find spans must match uniquely; empty replace deletes, empty find appends. Predictions and
functional admission are separate from reviewed endpoint errors and confirmed challenges.
"""
TEXT_FIELDS = {"hypothesis", "spec_basis", "original_defect_obligation", "predicted_repair_regression"}

# Strings and escaped identifiers may legally contain comment delimiters.
_COMMENT_LEX = re.compile(r'"(?:\\.|[^"\\])*"|\\[^\s]+|//[^\r\n]*|/\*[\s\S]*?\*/')


def rtl_language_review(clean: str, candidate: str) -> dict:
    def comments(text):
        return [m[0] for m in _COMMENT_LEX.finditer(text) if m[0].startswith(("//", "/*"))]

    before, after = comments(clean), comments(candidate)
    identifiers = lambda text: {t.value for t in safe_tokenize(text) if is_identifier(t)}
    return {"comments_unchanged": before == after,
            "clean_comments_hash": hash_payload(before), "candidate_comments_hash": hash_payload(after),
            "new_identifiers": sorted(identifiers(candidate) - identifiers(clean)),
            "identifier_review": "informational; not an admission veto"}


def save_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as f:
        f.write(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def _strings(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(x, str) and x.strip() for x in value)


def validate_packet(packet: dict) -> None:
    if set(packet) != {"version", "source_task", "source_candidate_hash", "evidence"}:
        raise ValueError("source packet fields mismatch")
    allowed = {"public_task", "public_spec", "public_build", "public_rtl", "public_test",
               "blue_edit", "visible_feedback", "development_regression"}
    if not isinstance(packet["evidence"], list) or not packet["evidence"]:
        raise ValueError("empty source packet")
    ids = []
    for entry in packet["evidence"]:
        if set(entry) != {"id", "kind", "content", "content_hash"} or entry["kind"] not in allowed:
            raise ValueError("non-allowlisted source evidence")
        if not isinstance(entry["id"], str) or not entry["id"]:
            raise ValueError("invalid evidence ID")
        if entry["content_hash"] != hash_payload(entry["content"]):
            raise ValueError("source evidence hash mismatch")
        ids.append(entry["id"])
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate evidence IDs")


def arm_context(arm: str, *, packet: dict | None = None, abstraction: dict | None = None) -> dict:
    if arm == "ordinary":
        if packet is not None or abstraction is not None:
            raise ValueError("ordinary arm cannot receive source history")
        return {"arm": arm}
    if arm not in {"automatic", "manual"} or packet is None:
        raise ValueError("unknown arm or missing source packet")
    validate_packet(packet)
    context = {"arm": arm, "source_packet": packet}
    if arm == "manual":
        if abstraction is not None:
            raise ValueError("manual arm cannot receive automatic output")
        return {**context, "mechanism_summary": MANUAL_SUMMARY}
    if (not isinstance(abstraction, dict) or abstraction.get("status") != "frozen"
            or abstraction.get("packet_hash") != hash_payload(packet)):
        raise ValueError("automatic arm needs its frozen abstraction")
    _validate_abstraction(abstraction["result"], packet)
    if abstraction.get("result_hash") != hash_payload(abstraction["result"]):
        raise ValueError("abstraction hash mismatch")
    return {**context, "abstraction": abstraction["result"]}


def _validate_abstraction(raw: Any, packet: dict) -> None:
    scalar = {"attempted_decision", "violated_obligation", "predicted_misleading_repair"}
    lists = {"evidence_ids", "preserved_obligations", "applicability_preconditions"}
    if (not isinstance(raw, dict) or set(raw) != scalar | lists
            or any(not isinstance(raw[k], str) or not raw[k].strip() for k in scalar)
            or any(not _strings(raw[k]) for k in lists)
            or not set(raw["evidence_ids"]) <= {e["id"] for e in packet["evidence"]}):
        raise ValueError("invalid abstraction schema or unknown evidence IDs")


class RedAnswerOutcome(Exception):
    """A recorded answer/context outcome, distinct from a transport failure."""

    def __init__(self, status, reason, diagnostics=None):
        self.record = {"status": status, "reason": reason, "diagnostics": diagnostics or {}}
        super().__init__(reason)


class RegressionGenerator:
    def __init__(self, client: BudgetedClient, counter, *, context_tokens=131072, output_tokens=32768,
                 abstraction_output_tokens=None):
        if not isinstance(client, BudgetedClient):
            raise TypeError("requires shared BudgetedClient")
        if (type(output_tokens) is not int or type(context_tokens) is not int
                or not 0 < output_tokens <= client.config.maximum_output_tokens
                or context_tokens <= output_tokens):
            raise ValueError("invalid context/output budget or output exceeds client authority")
        self.client, self.counter = client, counter
        self.context_tokens, self.output_tokens = context_tokens, output_tokens
        self.abstraction_output_tokens = (min(output_tokens, 32768) if abstraction_output_tokens is None
                                          else abstraction_output_tokens)
        if (type(self.abstraction_output_tokens) is not int
                or not 0 < self.abstraction_output_tokens <= client.config.maximum_output_tokens
                or self.abstraction_output_tokens >= context_tokens):
            raise ValueError("invalid abstraction output budget")

    def _call(self, system: str, payload: dict, seed: int, directory: Path, *, output_tokens: int) -> Any:
        # Exclusive directory prevents repeated extraction/proposal after interruption.
        directory.mkdir(parents=True, exist_ok=False)
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": canonical_json(payload)}]
        size = self.counter.request(messages)
        save_new(directory / "request.json", {"version": VERSION, "messages": messages,
                 "seed": seed, "request_hash": hash_payload(messages), "counted_request": size,
                 "context_tokens": self.context_tokens, "output_tokens": output_tokens,
                 "tokenizer": self.counter.identity})
        if size + output_tokens > self.context_tokens:
            save_new(directory / "response.json", {"status": "request_too_large", "calls": 0})
            raise RedAnswerOutcome("request_too_large", "context_budget")
        try:
            started = time.monotonic()
            with self.client.in_phase("red"):
                response = self.client.complete_json(messages=messages, seed=seed,
                                                      maximum_output_tokens=output_tokens)
        except Exception as exc:
            diagnostics = getattr(exc, "diagnostics", {})
            outcome = None
            if isinstance(exc, OpenAICompatibleProviderViolation):
                if diagnostics.get("finish_reason") == "length":
                    outcome = RedAnswerOutcome("no_answer", "output_budget", diagnostics)
                elif diagnostics.get("schema_version") == "r3e-openai-compatible-response-diagnostic-v1":
                    outcome = RedAnswerOutcome("malformed", "invalid_answer", diagnostics)
            save_new(directory / "response.json", {"status": "call_error", "type": type(exc).__name__,
                     "diagnostics": diagnostics, **(outcome.record if outcome else {})})
            if outcome is not None:
                raise outcome from exc
            raise  # configuration/provider errors are never functional rejection
        save_new(directory / "response.json", {**response, "counted_request": size,
                 "model_seconds": time.monotonic() - started})
        if response.get("response_diagnostics", {}).get("finish_reason") == "length":
            raise RedAnswerOutcome("no_answer", "output_budget", response["response_diagnostics"])
        return response["result"]

    def extract(self, packet: dict, *, seed: int, directory: Path) -> dict:
        validate_packet(packet)
        try:
            raw = self._call(ABSTRACT_SYSTEM, packet, seed, directory,
                             output_tokens=self.abstraction_output_tokens)
        except RedAnswerOutcome as exc:
            record = {"version": VERSION, "packet_hash": hash_payload(packet), **exc.record}
            save_new(directory / "abstraction.json", record)
            return record
        record = {"version": VERSION, "packet_hash": hash_payload(packet), "result": raw,
                  "result_hash": hash_payload(raw), "status": "frozen"}
        try:
            _validate_abstraction(raw, packet)
        except ValueError as exc:
            record.update(status="malformed", reason=str(exc))
        save_new(directory / "abstraction.json", record)
        return record

    def propose(self, public: dict, context: dict, *, seed: int, directory: Path,
                feedback: dict | None = None) -> dict:
        # Reconstruct context to prevent extra history/narrative fields slipping into ordinary.
        arm = context.get("arm")
        allowed = {"ordinary": {"arm"}, "manual": {"arm", "source_packet", "mechanism_summary"},
                   "automatic": {"arm", "source_packet", "abstraction"}}
        if arm not in allowed or set(context) != allowed[arm]:
            raise ValueError("arm context fields mismatch")
        if arm != "ordinary":
            validate_packet(context["source_packet"])
            if arm == "manual" and context["mechanism_summary"] != MANUAL_SUMMARY:
                raise ValueError("manual summary changed")
            if arm == "automatic":
                _validate_abstraction(context["abstraction"], context["source_packet"])
        schema = {k: "nonempty explanation" for k in sorted(TEXT_FIELDS)}
        schema.update(preserved_obligations=["unchanged specification requirement"],
                      edits=[{"find": "unique span of clean RTL", "replace": "mutated span"}],
                      predicted_wrong_repair_edits=[{"find": "unique span of mutant", "replace": "repair span"}])
        try:
            raw = self._call(GENERATE_SYSTEM, {"carrier": public, "history": context,
                             "feedback": feedback, "required_output_schema": schema}, seed, directory,
                             output_tokens=self.output_tokens)
            result = materialize(raw, public["clean_rtl"], public["top_module"], edits_only=True)
            # Keep the submitted edits for compact, lossless proposal feedback.
            result["submitted_answer"] = raw
        except RedAnswerOutcome as exc:
            result = exc.record
        save_new(directory / "proposal.json", result)
        return result


def materialize(raw: Any, clean: str, top: str, *, edits_only=False) -> dict:
    """A bad predicted repair cannot reject an otherwise valid mutant."""
    if isinstance(raw, dict) and set(raw) == {"abstain"} and isinstance(raw["abstain"], str) and raw["abstain"].strip():
        return {"status": "abstained", "reason": raw["abstain"]}
    required = TEXT_FIELDS | {"preserved_obligations"}
    try:
        if edits_only and isinstance(raw, dict) and "buggy_rtl" in raw:
            raise RedDesignOutputViolation("edits_required")
        if (not isinstance(raw, dict) or not required <= set(raw)
                or set(raw) - (required | {"edits", "buggy_rtl", "predicted_wrong_repair_edits"})
                or ("edits" in raw) == ("buggy_rtl" in raw)
                or any(not isinstance(raw[k], str) or not raw[k].strip() for k in TEXT_FIELDS)
                or not _strings(raw["preserved_obligations"])):
            raise RedDesignOutputViolation("output_fields_mismatch")
        mutant = apply_text_edits(clean, raw["edits"]) if "edits" in raw else raw["buggy_rtl"]
        if not isinstance(mutant, str) or not mutant.strip():
            raise RedDesignOutputViolation("empty_or_nontext_rtl")
        budget = validate_design(clean, mutant, top, EditBudget())
    except (RedDesignOutputViolation, BlueOutputViolation) as exc:
        return {"status": "rejected", "reason": str(exc)}
    result = {"status": "proposal", "buggy_rtl": mutant, "buggy_rtl_hash": hash_payload(mutant),
              "edit_budget": budget, "diff": actual_diff(clean, mutant),
              "claims": {k: raw[k] for k in required}}
    try:
        predicted = materialize_predicted_repair(mutant, raw.get("predicted_wrong_repair_edits"), top)
        result["predicted"] = {"status": "materialized", "rtl": predicted, "hash": hash_payload(predicted),
                               "diff": actual_diff(mutant, predicted)}
    except RedDesignOutputViolation as exc:
        result["predicted"] = {"status": "invalid", "reason": exc.reason}
    return result
