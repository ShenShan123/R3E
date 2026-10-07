"""One model-written RTL mutation, targeted at a supplied Blue weakness.

This is a proposal generator, not an admission judge or curriculum runner.
It enforces an immutable top interface and a reproducible syntax edit budget.
The caller owns simulation, loop checks, spec review, deduplication, rewards,
and cross-design mastery tests. No operator catalog is used.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from difflib import SequenceMatcher, unified_diff
import json
import math
import re
from typing import Any, Mapping

from r3e.knowledge.verilog_utils import safe_tokenize
from r3e.protocol.hashing import canonical_json, hash_payload

from .budget import BudgetedClient
from .corpus import Carrier
from .blue_provider import apply_text_edits, BlueOutputViolation


SYSTEM_PROMPT = """You are Red. Actually introduce a functional bug into this supplied correct RTL.
You are NOT repairing the clean design, writing a testbench, or designing a different task.
Implement the supplied mutation plan, correcting it using planning_feedback when needed.
Prefer explicit edits: each find is an exact unique span of the current source, replace
is its changed text; empty replace deletes and empty find appends. Edits apply in order.
You may instead return buggy_rtl containing the complete modified source. Return exactly
one of edits or buggy_rtl. There is no catalog of edits or sites and no edit-location limit.
You may remove definitions or functional structures, reconnect logic, change state
transitions, and coordinate changes at several locations within the edit budget.
Aim for a coherent structural/dependency fault that can mislead repair: do not default
to an obvious one-token typo. Several coupled sites are useful only when they implement
one mechanism. Do not add meaningless edits to increase size; there is no minimum size.
Use the supplied design's existing states, signals, arithmetic and specified behavior.
Do not add handshake protocols, change the algorithm/task/interface, or claim the clean
reference already has the target bug. A syntactic refactor with equivalent behavior is
NOT a bug. Returning the clean source or edits with identical find/replace is NOT a bug.
Use the supplied public causal history (what Blue failed, tried, and why), mastery
state, and mechanism coverage to target the selected weakness. Do not invent prior
Blue outcomes. For 'same', test transfer of that mechanism on this design; for
'harder', seek a stronger, less obvious manifestation; for 'simpler', simplify it.
For 'explore', propose a new mechanism hypothesis; no prior Blue failure is claimed.
Stealth means an observable functional failure on fewer cycles or later in a run,
not syntax damage, oscillation, testbench manipulation, or destruction of the design.
Preserve the top-level interface and compiler directives. Leave the specification
unchanged: it defines the intended correct behavior that Blue must be able to recover.
Introduce a deviation from that behavior, not a new task or an unspecified requirement.
Do not erase and regenerate the whole design or add padding to disguise edit size.
The cap is a ceiling, not an instruction to minimize changes. Both code AND body token
budgets apply. Account for insertion/deletion costs. Keep activation within the visible
test's stated horizon when supplied; do not rely on an invented testbench or rare event
outside that horizon. Before answering, check that the edits really implement the plan,
and identify a concrete legal input/state sequence where original and mutant differ.
Blue will see the specification, buggy RTL and visible evidence, not your hypothesis.
Return one strict JSON object matching required_output_schema. No code fences.
Target identity is assigned by the runner; do not generate or rename a weakness ID.
spec_basis must cite the supplied requirement that makes the intended repair knowable.
Your hypothesis and expected_symptom are predictions, not verified admission,
stealth, Blue failure, reward, or mastery claims. The external referee measures those.
"""
DIRECTIONS = frozenset({"explore", "same", "harder", "simpler"})
OUTPUT_PROTOCOL_VERSION = "red_design_output_v2"
SEARCH_INSTRUCTION = """
This call completes a bounded candidate search. search_feedback contains up to three
locally checked plans. Select one by returning its edits/RTL, or write ONE revised
candidate. No further retries or Blue probes follow this call. A locally admitted
plan is not proof of specification recoverability or Blue difficulty. Prefer a
coherent, evidence-backed repair-failure hypothesis; stealth is secondary. Explain
the tempting incorrect repair and the residual violation, separately from the RTL
defect. Successful prior Blue repairs are counterevidence; no_answer is not a wrong
functional repair. Observation IDs refer to evidence, never confirmed weakness IDs.
Also return predicted_wrong_repair_edits: a nonempty list of {find, replace}, applied
IN ORDER TO YOUR FINAL buggy_rtl (after your mutation edits), not to clean_rtl.
Materialize the plausible but incomplete repair described by predicted_wrong_repair.
It must change executable RTL, preserve the interface/directives, compile, and leave
the SAME targeted specification violation observable under the frozen visible test.
Do not introduce an unrelated defect just to make this hypothetical repair fail.
A no-op, comment-only edit, compile failure, loop, or visible pass cannot support
the hypothesis. If the target-only defect is invisible, adding an observable decoy
does not solve that problem: the output-only repair will still pass and be rejected.
The runner will test this hypothetical repair locally without calling Blue. This
check does not establish that Blue will choose it or that the mechanism is proven.
"""


@dataclass(frozen=True)
class EditBudget:
    """A finite fraction of the ORIGINAL code, never the candidate's length."""

    max_changed_fraction: float = 0.40

    def __post_init__(self) -> None:
        value = self.max_changed_fraction
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value) or not 0 < value < 1:
            raise ValueError("max_changed_fraction must be finite and strictly between 0 and 1")


class RedDesignInputError(ValueError):
    """Invalid generator input; raised before any model call."""


class RedDesignOutputViolation(ValueError):
    """A rejected proposal, never a Blue failure or admitted bug.

    ``receipt`` is populated after a model call, including for rejected output.
    Provider/configuration exceptions themselves propagate unchanged.
    """

    def __init__(self, reason: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.details = dict(details or {})
        self.receipt: dict[str, Any] = {}
        self.raw_output: Any = None
        self.materialized_rtl: str | None = None
        self.planning_feedback: Any = None


def actual_diff(clean: str, mutant: str, *, fromfile="clean.sv", tofile="buggy.sv") -> str:
    """Runner-computed changes, never the model's claimed change summary."""
    return "".join(unified_diff(clean.splitlines(keepends=True), mutant.splitlines(keepends=True),
                                fromfile=fromfile, tofile=tofile))


def _directives(source: str) -> tuple[str, ...]:
    # Exclude comments; keep string operands of directives (e.g. include paths).
    masked = re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/',
                    lambda m: m[0] if m[0].startswith('"') else re.sub(r"[^\n]", " ", m[0]), source)
    return tuple(line.strip() for line in masked.splitlines() if line.lstrip().startswith("`"))


def _layout(source: str, top: str) -> tuple[list[str], list[str], list[str]]:
    """Return all code tokens, body tokens, and the top's interface tokens.

    A conservative lexical check, not an HDL parser. Headers and input/output
    declarations cannot pad the body budget. Internal declarations DO count,
    so deleting definitions or helper modules remains a permitted mutation.
    Compilation and semantic interface validation still belong to the judge.
    """
    tokens = safe_tokenize(source)
    if not tokens:
        raise RedDesignOutputViolation("unlexable_rtl")
    values = [t.value for t in tokens]
    bodies, interface, names = [], None, set()
    i = 0
    while i < len(values):
        if values[i] != "module":
            raise RedDesignOutputViolation("unsupported_compilation_unit")
        start = i
        i += 1
        if i < len(values) and values[i] in {"automatic", "static"}:
            i += 1
        if i >= len(values):
            raise RedDesignOutputViolation("incomplete_module")
        name = values[i]
        if name in names:
            raise RedDesignOutputViolation("duplicate_module")
        names.add(name)
        try:
            header_end = values.index(";", i)
            end = values.index("endmodule", header_end + 1)
        except ValueError:
            raise RedDesignOutputViolation("incomplete_module") from None
        if "module" in values[i + 1:end]:
            raise RedDesignOutputViolation("nested_or_incomplete_module")
        module_interface = values[start:header_end + 1]
        body_start = len(bodies)
        j = header_end + 1
        while j < end:
            if values[j] in {"function", "task"}:
                closing = "end" + values[j]
                try:
                    stop = values.index(closing, j, end) + 1
                except ValueError:
                    raise RedDesignOutputViolation("incomplete_subroutine") from None
                bodies.extend(values[j:stop])
                j = stop
            elif values[j] in {"input", "output", "inout"}:
                try:
                    stop = values.index(";", j, end) + 1
                except ValueError:
                    raise RedDesignOutputViolation("incomplete_port_declaration") from None
                module_interface.extend(values[j:stop])
                j = stop
            else:
                bodies.append(values[j])
                j += 1
        if name == top:
            if len(bodies) == body_start:
                raise RedDesignOutputViolation("empty_top_module_body")
            interface = module_interface
        i = end + 1
        # Optional SystemVerilog end label.
        if i + 1 < len(values) and values[i] == ":" and values[i + 1] == name:
            i += 2
    if interface is None:
        raise RedDesignOutputViolation("missing_top_module")
    if not bodies:
        raise RedDesignOutputViolation("empty_design_body")
    return values, bodies, interface


def _change_measure(original: list[str], candidate: list[str], fraction: float) -> dict[str, Any]:
    # For a replacement, count max(deleted, inserted); additions and deletions
    # both cost tokens. autojunk=False preserves repeated HDL token significance.
    opcodes = SequenceMatcher(None, original, candidate, autojunk=False).get_opcodes()
    changed = sum(max(b - a, d - c) for tag, a, b, c, d in opcodes if tag != "equal")
    allowed = math.floor(len(original) * fraction)
    return {"original_tokens": len(original), "candidate_tokens": len(candidate),
            "changed_tokens": changed, "allowed_tokens": allowed,
            "changed_fraction": changed / len(original), "within_limit": changed <= allowed}


def validate_design(clean_rtl: str, buggy_rtl: str, top_module: str,
                    budget: EditBudget = EditBudget()) -> dict[str, Any]:
    """Check the proposal boundary; success is NOT functional admission.

    Both all-code and body-only edits must satisfy the same fraction. Neither
    blank lines, comments, unchanged headers, nor added padding increase the
    denominator. Formatting-only changes do not count as bugs.
    """
    original, original_body, interface = _layout(clean_rtl, top_module)
    mutated, mutated_body, new_interface = _layout(buggy_rtl, top_module)
    if interface != new_interface:
        raise RedDesignOutputViolation("top_interface_changed")
    if _directives(clean_rtl) != _directives(buggy_rtl):
        raise RedDesignOutputViolation("compiler_directives_changed")
    if original == mutated:
        raise RedDesignOutputViolation("no_token_change")
    report = {"metric": "sequence_matcher_token_edits_v1", **asdict(budget),
              "all_code": _change_measure(original, mutated, budget.max_changed_fraction),
              "body": _change_measure(original_body, mutated_body, budget.max_changed_fraction)}
    if not all(report[key]["within_limit"] for key in ("all_code", "body")):
        raise RedDesignOutputViolation("edit_budget_exceeded", details=report)
    return report


def design_constraints(carrier: Carrier, budget: EditBudget) -> dict[str, Any]:
    try:
        original, body, _ = _layout(carrier.clean_rtl, carrier.top_module)
    except RedDesignOutputViolation as exc:
        raise RedDesignInputError(f"unsupported clean RTL: {exc.reason}") from exc
    limits = {"all_code_token_limit": math.floor(len(original) * budget.max_changed_fraction),
              "body_token_limit": math.floor(len(body) * budget.max_changed_fraction)}
    if min(limits.values()) < 1:
        raise RedDesignInputError("edit budget allows no token changes on this design")
    return {**asdict(budget), **limits,
            "metric": "sum max(deleted, inserted) over token diff hunks; both limits apply"}


def materialize_predicted_repair(mutant: str, edits: Any, top: str) -> str:
    """Apply a Red hypothesis, not a restriction on Blue's repair space."""
    try:
        repaired = apply_text_edits(mutant, edits)
    except BlueOutputViolation as exc:
        raise RedDesignOutputViolation("predicted_repair_edits_not_applicable",
                                       details={"message": str(exc)}) from exc
    try:
        before, _, interface = _layout(mutant, top)
        after, _, new_interface = _layout(repaired, top)
        if before == after:
            raise RedDesignOutputViolation("no_token_change")
        if interface != new_interface:
            raise RedDesignOutputViolation("top_interface_changed")
        if _directives(mutant) != _directives(repaired):
            raise RedDesignOutputViolation("compiler_directives_changed")
    except RedDesignOutputViolation as exc:
        raise RedDesignOutputViolation("predicted_repair_invalid",
                                       details={"reason": exc.reason}) from exc
    return repaired


def assess_mutation_plan(carrier: Carrier, plan: Mapping[str, Any], budget: EditBudget) -> dict[str, Any]:
    """Local feedback to the existing second call, not simulation or a retry."""
    try:
        rtl = apply_text_edits(carrier.clean_rtl, plan.get("edits"))
    except BlueOutputViolation as exc:
        return {"status": "revise_plan", "reason": "edits_not_applicable", "message": str(exc)}
    try:
        report = validate_design(carrier.clean_rtl, rtl, carrier.top_module, budget)
    except RedDesignOutputViolation as exc:
        return {"status": "revise_plan", "reason": exc.reason, "details": exc.details,
                "actual_diff": actual_diff(carrier.clean_rtl, rtl)}
    return {"status": "syntactically_feasible_not_admitted", "edit_budget": report,
            "actual_diff": actual_diff(carrier.clean_rtl, rtl)}


class RedDesignGenerator:
    """One budgeted 'red' call per proposal; no retries or experiment side effects.

    ``target_weakness`` must include ``weakness_id`` and a prose ``mechanism``.
    Other JSON fields (causal_chain, mastery_status, designs_used, etc.) are
    passed intact. ``weakness_context`` carries the caller's public history
    and mechanism coverage. The caller must construct a pre-bug public view.
    """

    def __init__(self, client: BudgetedClient, *, budget: EditBudget = EditBudget()):
        if not isinstance(client, BudgetedClient):
            raise TypeError("RedDesignGenerator requires a BudgetedClient")
        if not isinstance(budget, EditBudget):
            raise TypeError("budget must be EditBudget")
        self.client = client
        self.budget = budget

    def generate(self, carrier: Carrier, *, target_weakness: Mapping[str, Any],
                 weakness_context: Mapping[str, Any] | None = None,
                 mutation_plan: Mapping[str, Any] | None = None,
                 generation_context: Mapping[str, Any] | None = None,
                 search_feedback: Mapping[str, Any] | None = None,
                 direction: str = "harder", seed: int = 0) -> dict[str, Any]:
        if direction not in DIRECTIONS:
            raise RedDesignInputError("direction must be explore, same, harder, or simpler")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise RedDesignInputError("seed must be an integer")
        if not isinstance(carrier.spec, str) or not carrier.spec.strip():
            raise RedDesignInputError("a nonempty specification is required")
        if not isinstance(target_weakness, Mapping) or any(
            not isinstance(target_weakness.get(k), str) or not target_weakness[k].strip()
            for k in ("weakness_id", "mechanism")
        ):
            raise RedDesignInputError("target_weakness needs nonempty weakness_id and mechanism")
        if weakness_context is not None and not isinstance(weakness_context, Mapping):
            raise RedDesignInputError("weakness_context must be a JSON object")
        constraints = design_constraints(carrier, self.budget)
        for name, value in (("mutation_plan", mutation_plan), ("generation_context", generation_context)):
            if value is not None and not isinstance(value, Mapping):
                raise RedDesignInputError(f"{name} must be a JSON object")
        schema = {"edits": [{"find": "exact unique current source span; empty to append",
                             "replace": "changed text; empty to delete"}],
                  "buggy_rtl": "complete modified RTL INSTEAD OF edits; omit when using edits",
                  "hypothesis": "how this bug targets the supplied repair weakness",
                  "expected_symptom": "predicted visible functional symptom",
                  "change_summary": "structures and behavior changed",
                  "spec_basis": "supplied requirement that determines the correct behavior"}
        if search_feedback is not None:
            schema.update(broken_invariant="dependency broken by this final candidate",
                          predicted_wrong_repair="specific tempting but incorrect Blue repair",
                          residual_failure="why that repair would still violate the specification",
                          predicted_wrong_repair_edits=[{
                              "find": "exact unique span of the FINAL MUTANT, not clean RTL",
                              "replace": "a plausible partial repair leaving the targeted violation"}])
        request = {"carrier_id": carrier.carrier_id, "cluster_id": carrier.cluster_id,
                   "clean_rtl": carrier.clean_rtl, "specification": carrier.spec,
                   "top_module": carrier.top_module, "target_weakness": dict(target_weakness),
                   "weakness_context": dict(weakness_context or {}), "direction": direction,
                   "edit_budget": constraints, "mutation_plan": dict(mutation_plan or {}),
                   "generation_context": dict(generation_context or {}),
                   "planning_feedback": (assess_mutation_plan(carrier, mutation_plan, self.budget)
                                         if mutation_plan is not None else None),
                   "required_output_schema": schema,
                   "output_constraints": {"exactly_one_of": ["edits", "buggy_rtl"],
                       "required_fields": ["hypothesis", "expected_symptom", "change_summary", "spec_basis"],
                       "target_identity_is_runner_owned": True}}
        if search_feedback is not None:
            request["search_feedback"] = dict(search_feedback)
            request["output_constraints"]["required_fields"] += [
                "broken_invariant", "predicted_wrong_repair", "residual_failure",
                "predicted_wrong_repair_edits"]
        try:
            # Reject NaN/Infinity and detach nested history from caller mutation.
            request = json.loads(json.dumps(request, allow_nan=False))
            user = canonical_json(request)
        except (TypeError, ValueError) as exc:
            raise RedDesignInputError("weakness inputs must be JSON serializable") from exc
        messages = [{"role": "system", "content": SYSTEM_PROMPT +
                     (SEARCH_INSTRUCTION if search_feedback is not None else "")},
                    {"role": "user", "content": user}]
        input_hash = hash_payload({"messages": messages, "seed": seed})
        with self.client.in_phase("red"):
            response = self.client.complete_json(messages=messages, seed=seed)
        receipt = {"input_hash": input_hash, "request_hash": response.get("request_hash"),
                   "raw_response_hash": response.get("raw_response_hash"),
                   "input_tokens": response.get("input_tokens", 0),
                   "output_tokens": response.get("output_tokens", 0),
                   "clean_rtl_hash": hash_payload(carrier.clean_rtl),
                   "spec_hash": hash_payload(carrier.spec),
                   "weakness_view_hash": hash_payload({"target": request["target_weakness"],
                                                      "context": request["weakness_context"]})}
        raw_output = response["result"]
        raw = raw_output
        ignored = {}
        # A provider envelope annotation is not a semantic proposal field.
        # Preserve it in the raw audit record; do not strip arbitrary extra keys.
        if isinstance(raw, dict) and raw.get("type") == "json_object":
            ignored = {"type": raw["type"]}
            raw = {k: v for k, v in raw.items() if k != "type"}
        mutant = None
        predicted = None
        try:
            required = {"hypothesis", "expected_symptom", "change_summary", "spec_basis"}
            if search_feedback is not None:
                required |= {"broken_invariant", "predicted_wrong_repair", "residual_failure"}
            fields = required | ({"predicted_wrong_repair_edits"} if search_feedback is not None else set())
            if (not isinstance(raw, dict) or not fields <= set(raw)
                    or not set(raw) <= fields | {"edits", "buggy_rtl", "target_weakness_id"}
                    or ("edits" in raw) == ("buggy_rtl" in raw)):
                raise RedDesignOutputViolation("output_fields_mismatch")
            if any(not isinstance(raw[k], str) or not raw[k].strip() for k in required):
                raise RedDesignOutputViolation("output_fields_must_be_nonempty_strings")
            if raw.get("target_weakness_id", request["target_weakness"]["weakness_id"]) != request["target_weakness"]["weakness_id"]:
                raise RedDesignOutputViolation("target_weakness_mismatch")
            if "edits" in raw:
                try:
                    mutant = apply_text_edits(carrier.clean_rtl, raw["edits"])
                except BlueOutputViolation as exc:
                    raise RedDesignOutputViolation("edits_not_applicable", details={"message": str(exc)}) from exc
            else:
                mutant = raw["buggy_rtl"]
                if not isinstance(mutant, str) or not mutant.strip():
                    raise RedDesignOutputViolation("output_fields_must_be_nonempty_strings")
            report = validate_design(carrier.clean_rtl, mutant, carrier.top_module, self.budget)
            if search_feedback is not None:
                predicted = materialize_predicted_repair(mutant, raw["predicted_wrong_repair_edits"],
                                                        carrier.top_module)
        except RedDesignOutputViolation as exc:
            exc.receipt = receipt
            exc.raw_output = raw_output
            exc.materialized_rtl = mutant if isinstance(mutant, str) else None
            exc.planning_feedback = request["planning_feedback"]
            raise
        return {**raw, "buggy_rtl": mutant, "target_weakness_id": request["target_weakness"]["weakness_id"],
                "answer_form": "edits" if "edits" in raw else "full",
                "actual_diff": actual_diff(carrier.clean_rtl, mutant),
                "raw_output": raw_output, "ignored_output_fields": ignored,
                "output_protocol_version": OUTPUT_PROTOCOL_VERSION,
                **({"predicted_wrong_repair_rtl": predicted,
                    "predicted_wrong_repair_hash": hash_payload(predicted),
                    "predicted_wrong_repair_diff": actual_diff(mutant, predicted,
                        fromfile="mutant.sv", tofile="predicted_repair.sv")}
                   if predicted is not None else {}),
                "planning_feedback": request["planning_feedback"],
                "carrier_id": carrier.carrier_id, "direction": direction,
                "status": "proposal_only", "edit_budget": report,
                "buggy_rtl_hash": hash_payload(mutant), "receipt": receipt}
