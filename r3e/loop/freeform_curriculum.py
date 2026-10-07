"""Free-form Red wiring; Blue, measurement, and admission rules are reused.

Two Red calls per slot: choose a compatible design/mechanism, then write the mutant.
Complete proposals are persisted in the existing Red ledger for exact replay.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import json
import re
from typing import Any, Callable, Mapping, Sequence

from r3e.knowledge.verilog_utils import safe_tokenize
from r3e.protocol.hashing import canonical_json, hash_payload

from .corpus import Carrier, Challenge, normalized_structure_hash
from .curriculum import CurriculumLoop, OPEN, _Slot, lineages
from .red import RedAgent, RedProposal
from .red_design import (
    EditBudget, RedDesignGenerator, RedDesignOutputViolation, SYSTEM_PROMPT, validate_design,
    design_constraints, actual_diff,
    SEARCH_INSTRUCTION, OUTPUT_PROTOCOL_VERSION,
)
from .red_target_check import TargetObservabilityCheck, TARGET_CHECK_VERSION
from .red_repair_feedback import PublicCandidateRecorder, REPAIR_ARTIFACT_VERSION
from .red_design_context import design_context, diversify_design_order, DESIGN_CONTEXT_VERSION
from .blue_provider import apply_text_edits, BlueOutputViolation
from .red_observations import discovery_observations, public_attempt, OBSERVATION_VERSION
from .state import RunState


SCHEDULER_VERSION = "joint_design_mechanism_v1"
SELECTION_PROTOCOL_VERSION = "grounding_v3"
DEFAULT_DESIGN_CANDIDATES = 8


SELECT_SYSTEM = """You are Red. Choose ONE candidate design and plan a real functional corruption of its correct RTL.
This is not a verification exercise or a request to implement another design.
Use only the public pre-bug weakness evidence provided. Prefer pursuing a known
repair mechanism on a different design when it is applicable. Choose an offered
weakness_id and direction same, harder, or simpler, explaining why. same tests
transfer/mastery; harder seeks a subtler or stronger failure; simpler makes a
stuck mechanism learnable. You may instead explore a new mechanism: use null
weakness_id, direction explore, and a prose repair-failure mechanism hypothesis. With no known
weakness, exploration is mandatory; never invent prior Blue failures. There is
no operator catalog. The chosen mechanism MUST exist in the provided design and
specification. Do not invent handshake/backpressure for a design without it, change
widths or the specified algorithm, propose a testbench, or treat a correct refactor
as a bug. Do not assume the clean reference is already buggy: create a deviation.
Prefer a coherent structural/dependency mistake, possibly spanning several sites,
over an obvious typo. There is no minimum size; do not inflate the diff for its own sake.
Respect BOTH code and body token budgets provided here. Plan a concrete legal trigger
within the stated visible-test horizon when known; stealth does not mean unobservable.
Compare the candidate designs before choosing a mechanism. Each candidate includes its
own RTL, specification, budgets, visible horizon and eligible_weakness_ids. Select a
known weakness only from that design's eligible IDs; IDs encode transfer eligibility,
NOT proof that the mechanism exists. Ground applicability with rtl_anchors: a nonempty
JSON array of source strings, ONE contiguous code span per location. Copy each span
from the chosen design. For multiple locations, use separate array entries; do not
join locations with ellipses, slashes, added labels or invented intervening text.
Formatting whitespace may differ, but identifiers, literals and operators must match.
Explain how these locations implement the targeted mechanism. Do not force a
weakness onto an unrelated design. Prefer exploiting an applicable known weakness over
exploration. If exploring while weaknesses are offered, explain for EVERY offered
weakness in skipped_weaknesses why none of its eligible candidates supports pursuit.
These are model assessments, not verified causal conclusions.
Return exactly one JSON object with keys carrier_id, weakness_id, direction, mechanism,
reason, applicability, skipped_weaknesses, mutation_plan. carrier_id must be an offered
candidate ID. applicability has exactly rtl_anchors (a nonempty array of strings) and
reason (a nonempty string). Only weakness_view.open_weak_points supplies known weakness
IDs. mechanism_coverage contains previously explored hypotheses, including bugs Blue
repaired; it is NOT a list of weaknesses, offered IDs, or required skip explanations.
The request's selection_contract lists the exact offered IDs and required skip keys.
If offered_weakness_ids is [], use weakness_id=null, direction="explore", and
skipped_weaknesses={} regardless of how much mechanism_coverage history exists.
Otherwise, when exploring, skipped_weaknesses maps every offered ID to a nonempty
reason. When pursuing an offered ID, use skipped_weaknesses={}. Never use mechanism
prose as an ID. Extra commentary is not a substitute for required offered-ID reasons.
mutation_plan has exactly the keys edits, invariant, activation, spec_basis.
edits is a nonempty list of {find, replace}: exact unique source spans and their changed
text, applied in order; empty replace deletes, empty find appends. These are arbitrary
RTL edits, not a catalog. invariant states what dependency the edits break; activation
describes a concrete input/state sequence and expected original-vs-mutant difference;
spec_basis cites the supplied correct requirement. All three are nonempty strings.
For an offered weakness, copy its mechanism verbatim so identity is runner-owned.
The next call will implement/revise this concrete plan using local edit/budget feedback.
"""
VIEW_VERSION = "public_freeform_weaknesses_v2"
SEARCH_VERSION = "candidate_search_v2"
SEARCH_SELECT_SYSTEM = SELECT_SYSTEM + """
SEARCH CONTRACT EXTENSION: also return alternative_plans (zero to two additional
plans with the same edits/invariant/activation/spec_basis fields) and repair_hypothesis
with exactly predicted_wrong_repair, residual_failure, source_observation_ids.
The first two are nonempty strings, and source_observation_ids is a list of IDs from
weakness_view.observations.records (or [] for an unsupported exploration hypothesis).
All plans target ONE selected design and mechanism, but should differ materially.
These are up to three candidates for local compile/simulation checks, not three Blue
challenges. Distinguish the injected defect from the predicted incorrect Blue repair.
Use successful repairs as counterevidence. no_answer is not a functional misrepair.
An observation's actual_patch, when available, is a runner-computed diff from the
original challenge to Blue's actual candidate, verified against its recorded hash.
Use it to assess which obligations Blue addressed and which hypothesis was refuted.
Missing or truncated patches are explicitly labeled; do not infer omitted edits.
A visible pass establishes only a pass on that test, not full correctness. Any
successor must state why the previous observed repair would not suffice, using the
same specification and the new design's actual dependencies. The statement remains
a hypothesis until validated; source_observation_ids records its public provenance.
Observations and rejected proposals are search evidence, NOT offered weakness IDs.
Only open_weak_points supplies target IDs, including explicitly revalidated seed targets.
The next call will receive local admission results and can select or revise once.
The final call must also give executable predicted_wrong_repair_edits relative to
its final mutant. The runner must observe the SAME residual functional violation
after this hypothetical repair on the existing visible test. Do not compensate for
an invisible targeted defect by adding an unrelated visible defect. Prefer a legal
trigger that distinguishes the intended repair from the tempting incomplete repair.
Candidate design_context fields are source facts, not an elaborated design or
verified mechanism. Across explorations consider diverse applicable dependencies:
transaction boundaries, data/control alignment, flush/reset cleanup, and consistent
definitions/uses across parameters or instances. These are suggestions, not a closed
catalog. Do not invent absent mechanisms, stack independent defects, or enlarge the
diff to claim difficulty. Ground each choice in the RTL and unchanged specification.
"""


class SelectionGroundingViolation(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def selection_contract(offered_ids: Sequence[str]) -> dict[str, Any]:
    """Make the runner's target vocabulary explicit, even with nonempty coverage."""
    return {"version": SELECTION_PROTOCOL_VERSION, "offered_weakness_ids": list(offered_ids),
            "skipped_weaknesses_when_exploring": {wid: "<reason>" for wid in offered_ids},
            "skipped_weaknesses_when_pursuing": {}, "coverage_is_not_a_target_list": True,
            "applicability_format": {"rtl_anchors": ["<one contiguous source span per location>"],
                                     "reason": "<how these sites implement the mechanism>"}}


def _source_anchor(source: str, quote: Any) -> str | None:
    """Return actual source text; permit token-preserving formatting differences.

    No fuzzy identifier/operator/literal substitutions. Unlike whitespace regexes,
    token matching preserves whitespace *inside* string literals and cannot split
    an identifier into several identifiers. Anchors cite code, not edit locations.
    """
    if not isinstance(quote, str) or not quote.strip():
        return None
    wanted = safe_tokenize(quote)
    if not wanted:
        return None
    if quote in source:
        return quote
    # safe_tokenize blanks directives; never silently discard them from a quote.
    if "`" in quote:
        return None
    tokens = safe_tokenize(source)
    values = [t.value for t in tokens]
    expected = [t.value for t in wanted]
    for i in range(len(tokens) - len(wanted) + 1):
        if values[i:i + len(wanted)] == expected:
            return source[tokens[i].start:tokens[i + len(wanted) - 1].end]
    return None


def normalize_selection_grounding(selection: Mapping[str, Any], source: str,
                                  offered_ids: Sequence[str]) -> dict[str, Any]:
    """Normalize annotations without relaxing target identity or mutation admission.

    The caller separately validates carrier/weakness IDs, direction and mechanism.
    Keep the original selection_output unchanged for audit. This function neither
    generates a mutant nor asserts semantic applicability or specification recovery.
    """
    evidence = selection.get("applicability")
    if (not isinstance(evidence, dict) or not isinstance(evidence.get("reason"), str)
            or not evidence["reason"].strip()):
        raise SelectionGroundingViolation("ungrounded_applicability")
    notes = []
    legacy = set(evidence) == {"rtl_anchor", "reason"}
    if legacy:
        quotes = [evidence["rtl_anchor"]]
        notes.append("legacy_single_anchor")
    elif set(evidence) == {"rtl_anchors", "reason"}:
        quotes = evidence["rtl_anchors"]
    else:
        raise SelectionGroundingViolation("ungrounded_applicability")
    if not isinstance(quotes, list) or not quotes:
        raise SelectionGroundingViolation("ungrounded_applicability")
    anchors = [_source_anchor(source, quote) for quote in quotes]
    if any(a is None for a in anchors) and legacy and isinstance(quotes[0], str):
        # Old answers sometimes describe several sites in one annotated string.
        # Do not guess delimiters (semicolons also occur inside HDL). Use the
        # answer's own planned edit finds, provided every one is quoted in that
        # description and exists in clean RTL. Citations need not be unique edit
        # locations: generation receives separate edit applicability feedback.
        plan = selection.get("mutation_plan")
        edits = plan.get("edits") if isinstance(plan, dict) else None
        if (not isinstance(edits, list) or not edits or any(
                not isinstance(e, dict) or set(e) != {"find", "replace"}
                or not isinstance(e["find"], str) or not isinstance(e["replace"], str)
                for e in edits)):
            edits = None
        finds = [e["find"] for e in edits if e["find"].strip()] if edits else []
        planned = [_source_anchor(source, find) for find in finds]
        if (finds and all(planned) and all(
                re.search(r"\s*".join(re.escape(t.value) for t in safe_tokenize(find)), quotes[0])
                for find in finds)):
            anchors = planned
            notes.append("legacy_composite_anchor_from_verified_plan_finds")
    if any(a is None for a in anchors):
        raise SelectionGroundingViolation("ungrounded_applicability")
    if anchors != quotes and "legacy_composite_anchor_from_verified_plan_finds" not in notes:
        notes.append("anchor_formatting_normalized")

    skipped = selection.get("skipped_weaknesses")
    if not isinstance(skipped, dict):
        raise SelectionGroundingViolation("unexplained_exploration")
    required = set(offered_ids) if selection["weakness_id"] is None else set()
    if any(not isinstance(skipped.get(wid), str) or not skipped[wid].strip() for wid in required):
        raise SelectionGroundingViolation("unexplained_exploration")
    # Explanations about coverage/other non-targets are optional annotations.
    # Preserve them, but never turn them into known weaknesses or reject a slot
    # merely because there was extra commentary with no obligation to skip.
    ignored = {key: value for key, value in skipped.items() if key not in required}
    if ignored:
        notes.append("non_target_skip_annotations")
    return {"applicability": {"rtl_anchors": list(dict.fromkeys(anchors)), "reason": evidence["reason"]},
            "skipped_weaknesses": {wid: skipped[wid] for wid in offered_ids if wid in required},
            "ignored_skipped_weaknesses": ignored, "selection_normalization": notes}


def visible_generation_context(carrier: Carrier) -> dict[str, Any]:
    """Read only the known generated harness's sample horizon; never hidden tests.

    Unknown harnesses have no inferred bound. This does not run a test or expose
    expected output values, and does not change the admission/scoring rules.
    """
    counts = []
    for path in carrier.visible_tb:
        source = path.read_text(encoding="utf-8")
        match = re.search(r"for\s*\(i\s*=\s*0;\s*i\s*<\s*(\d+);\s*i\s*=\s*i\s*\+\s*1\)", source)
        counts.append(int(match[1]) if "module r3e_tb;" in source and match else None)
    return {"visible_sample_counts": counts,
            "note": "Sample counts are from visible generated harnesses; null means unknown. "
                    "A proposed trigger must occur within the visible run, not only after it. "
                    "No hidden test or expected-output data is supplied."}


def public_weakness_view(state: RunState, offered: Sequence[Mapping[str, Any]],
                         round_index: int) -> dict[str, Any]:
    """Adapt existing public records, without inventing causal explanations.

    Generation precedes Blue in a round. Only completed earlier discovery
    rounds are visible, even during resume. Holdout sees discovery history only.
    Mechanisms are explicitly Red's hypotheses; visible feedback supplies the
    observed failure, not an asserted root cause.
    """
    def prior(row):
        return 0 <= row.get("round", -1) and (round_index < 0 or row["round"] < round_index)

    red = {row["challenge_id"]: row for row in state.read("red") if prior(row) and row.get("admitted")}
    encounters = [row for row in state.read("encounters") if prior(row)
                  and not row["encounter"].get("inconclusive")]
    screens = {row["challenge_id"]: row for row in state.read("screens") if prior(row)}
    targets = []
    for lin in offered:
        steps = [step for step in lin["steps"] if prior(step)]
        if not steps:
            continue
        origin = red.get(steps[0]["challenge_id"], {}).get("chosen", {})
        mechanism = (origin.get("target_weakness") or {}).get("mechanism") or origin.get("hypothesis")
        if not mechanism:
            continue
        chain = []
        for step in steps:
            cid = step["challenge_id"]
            attempts = []
            for row in encounters:
                if row["encounter"]["challenge_id"] != cid:
                    continue
                attempts.append({"confirmation": bool(row.get("confirmation")), "attempts": [
                    {"index": a.get("index"), "edit": str(a.get("edit", ""))[:800],
                     "verdict_tier": public_attempt(a)["verdict"],
                     "feedback": public_attempt(a)["visible_feedback"]}
                    for a in row["encounter"]["attempts"] if not a.get("infra_failure")]})
            chain.append({"challenge_id": cid, "design": step.get("carrier_id"),
                          "red_hypothesis": step.get("hypothesis"),
                          "observed_stealth": (red.get(cid, {}).get("chosen") or {}).get("stealth"),
                          "blue_outcome": step.get("blue_outcome"),
                          "blue_runs": step.get("blue_runs"), "blue_fails": step.get("blue_fails"),
                          "visible_repair_attempts": attempts, "mastery": step.get("mastery")})
        targets.append({"weakness_id": lin["lineage_id"], "mechanism": mechanism,
                        "mechanism_source": "Red hypothesis, not a verified causal diagnosis",
                        "mastery_status": lin["status"], "designs_used": list(lin["clusters"]),
                        "causal_chain": chain})
    coverage: dict[str, dict[str, int]] = {}
    for cid, row in red.items():
        chosen = row.get("chosen") or {}
        mechanism = (chosen.get("target_weakness") or {}).get("mechanism") or chosen.get("hypothesis")
        if not mechanism:
            continue
        counts = coverage.setdefault(mechanism, {"bugs": 0, "screened": 0, "reproducible_failures": 0})
        counts["bugs"] += 1
        if cid in screens:
            counts["screened"] += 1
            counts["reproducible_failures"] += bool(screens[cid].get("reproducible"))
    return {"view_version": VIEW_VERSION, "open_weak_points": targets,
            "mechanism_coverage": coverage}


class FreeformRed(RedAgent):
    def __init__(self, *, edit_budget: EditBudget, view_builder=public_weakness_view,
                 proposal_check: Callable[[Carrier, Mapping[str, Any]], str | None] | None = None,
                 search=False,
                 **kwargs):
        super().__init__(**kwargs)
        if self.mode not in {"aware", "blind"}:
            raise ValueError("freeform Red supports aware or blind; random uses the catalog/pool baseline")
        self.generator = RedDesignGenerator(self.client, budget=edit_budget)
        self.view_builder = view_builder
        self.proposal_check = proposal_check
        self.search = search
        self.require_pursuit = False
        self._admission_cache = None
        self.target_check = TargetObservabilityCheck(self.simulator)

    def _admit(self, carrier, mutant):
        # Per-proposal only: previewing a candidate must not consume its identity.
        key = hash_payload({"carrier": carrier.carrier_id, "rtl": mutant})
        if self._admission_cache is None:
            return super()._admit(carrier, mutant)
        if key not in self._admission_cache:
            # Admission depends only on the visible test. Do not run hidden
            # tests for portfolio candidates that happen to pass visibly.
            self._admission_cache[key] = super()._admit(replace(carrier, hidden_tb=()), mutant)
        return self._admission_cache[key]

    def _search_plans(self, carrier, selection):
        plans = [selection["mutation_plan"], *selection["alternative_plans"]]
        results = []
        for index, plan in enumerate(plans):
            item = {"index": index, "plan": plan}
            try:
                rtl = apply_text_edits(carrier.clean_rtl, plan["edits"])
                report = validate_design(carrier.clean_rtl, rtl, carrier.top_module, self.generator.budget)
                reason, _ = self._admit(carrier, rtl)
                item.update(reason=reason, edit_budget=report, mutant_hash=hash_payload(rtl),
                            actual_diff=actual_diff(carrier.clean_rtl, rtl))
            except BlueOutputViolation as exc:
                item.update(reason="edits_not_applicable", details=str(exc))
            except RedDesignOutputViolation as exc:
                item.update(reason=exc.reason, details=exc.details)
            results.append(item)
        return {"version": SEARCH_VERSION, "candidates": results,
                "repair_hypothesis": selection["repair_hypothesis"],
                "remaining_revisions": 1, "recoverability": "requires independent review"}

    def propose_curriculum(self, carrier: Carrier, *, state: RunState, offered,
                           seed: int, round_index: int, context=None, candidate_carriers=None) -> RedProposal:
        record = {"round": round_index, "mode": "freeform", "red_mode": self.mode,
                  "carrier_id": carrier.carrier_id, "seed": seed, **(context or {})}
        self._admission_cache = {} if self.search else None
        candidates = list(candidate_carriers) if candidate_carriers is not None else [carrier]
        if not candidates or len({c.carrier_id for c in candidates}) != len(candidates):
            raise ValueError("candidate designs must be nonempty with unique IDs")
        if any(not c.spec.strip() for c in candidates):
            return RedProposal({**record, "admitted": False, "reason": "missing_specification"}, None)
        view = (self.view_builder(state, offered, round_index) if self.mode == "aware"
                else {"view_version": VIEW_VERSION, "open_weak_points": [], "mechanism_coverage": {}})
        if self.search:
            view["observations"] = (discovery_observations(state, offered, round_index)
                                    if self.mode == "aware" else {
                "version": OBSERVATION_VERSION, "records": [], "total_prior_proposals": 0,
                "omitted_proposals": 0, "selection": "blind_no_history"})
        # Freeze the public snapshot; the same bytes feed selection and generation.
        view = json.loads(json.dumps(view, allow_nan=False))
        targets = {w["weakness_id"]: w for w in view["open_weak_points"]}
        offered_ids = {lin["lineage_id"] for lin in offered}
        if len(targets) != len(view["open_weak_points"]) or not set(targets) <= offered_ids:
            raise ValueError("weakness view contains duplicate or unoffered lineage IDs")
        record.update(offered_lineages=list(targets), weakness_view=view,
                      weakness_view_hash=hash_payload(view))
        by_id = {c.carrier_id: c for c in candidates}
        lineage_by_id = {lin["lineage_id"]: lin for lin in offered}
        candidate_inputs = [{"carrier_id": c.carrier_id, "cluster_id": c.cluster_id,
                             "clean_rtl": c.clean_rtl, "specification": c.spec,
                             "top_module": c.top_module,
                             "edit_budget": design_constraints(c, self.generator.budget),
                             "generation_context": visible_generation_context(c),
                             **({"design_context": design_context(c)} if self.search else {}),
                             "eligible_weakness_ids": [wid for wid in targets
                                 if c.cluster_id not in lineage_by_id[wid]["clusters"]]}
                            for c in candidates]
        record["candidate_designs"] = [{"carrier_id": c.carrier_id,
            "clean_rtl_hash": hash_payload(c.clean_rtl), "spec_hash": hash_payload(c.spec),
            "eligible_weakness_ids": item["eligible_weakness_ids"]}
            for c, item in zip(candidates, candidate_inputs)]
        record["candidate_inputs_hash"] = hash_payload(candidate_inputs)
        forced = record.get("holdout_for")
        if forced:
            if forced not in targets:
                return RedProposal({**record, "admitted": False, "reason": "missing_holdout_weakness"}, None)
            selection = {"weakness_id": forced, "direction": "same",
                         "mechanism": targets[forced]["mechanism"], "reason": "holdout transfer"}
        else:
            contract = selection_contract(list(targets))
            record["selection_contract"] = contract
            request = {"task": "select_freeform_target", "candidate_designs": candidate_inputs,
                       "weakness_view": view, "selection_contract": contract}
            messages = [{"role": "system", "content": SEARCH_SELECT_SYSTEM if self.search else SELECT_SYSTEM},
                        {"role": "user", "content": canonical_json(request)}]
            # No broad exception handler: configuration/provider/budget errors stop.
            with self.client.in_phase("red"):
                response = self.client.complete_json(messages=messages, seed=seed)
            record["selection_receipt"] = {"request_hash": response.get("request_hash"),
                                           "raw_response_hash": response.get("raw_response_hash"),
                                           "input_hash": hash_payload({"messages": messages, "seed": seed})}
            selection = response["result"]
            record["selection_output"] = selection
        keys = {"weakness_id", "direction", "mechanism", "reason"}
        selection_keys = keys if forced else keys | {
            "mutation_plan", "carrier_id", "applicability", "skipped_weaknesses"}
        if self.search and not forced:
            selection_keys |= {"alternative_plans", "repair_hypothesis"}
        valid = isinstance(selection, dict) and set(selection) == selection_keys
        valid = valid and all(isinstance(selection[k], str) and selection[k].strip()
                              for k in ("direction", "mechanism", "reason"))
        if valid and not forced:
            plan = selection["mutation_plan"]
            valid = (isinstance(plan, dict) and set(plan) == {"edits", "invariant", "activation", "spec_basis"}
                     and isinstance(plan["edits"], list) and bool(plan["edits"])
                     and all(isinstance(plan[k], str) and plan[k].strip()
                             for k in ("invariant", "activation", "spec_basis")))
        if valid:
            wid, direction = selection["weakness_id"], selection["direction"]
            valid = ((wid is None and direction == "explore") or
                     (isinstance(wid, str) and wid in targets and direction in {"same", "harder", "simpler"}
                      and selection["mechanism"] == targets[wid]["mechanism"]))
        if valid and self.search and not forced:
            alternatives, hypothesis = selection["alternative_plans"], selection["repair_hypothesis"]
            valid = (isinstance(alternatives, list) and len(alternatives) <= 2
                     and all(isinstance(p, dict) and set(p) == {"edits", "invariant", "activation", "spec_basis"}
                             and isinstance(p["edits"], list) and bool(p["edits"])
                             and all(isinstance(p[k], str) and p[k].strip()
                                     for k in ("invariant", "activation", "spec_basis")) for p in alternatives)
                     and isinstance(hypothesis, dict)
                     and set(hypothesis) == {"predicted_wrong_repair", "residual_failure", "source_observation_ids"}
                     and all(isinstance(hypothesis[k], str) and hypothesis[k].strip()
                             for k in ("predicted_wrong_repair", "residual_failure"))
                     and isinstance(hypothesis["source_observation_ids"], list)
                     and all(isinstance(i, str) and i in {
                         o["observation_id"] for o in view["observations"]["records"]}
                             for i in hypothesis["source_observation_ids"]))
        if not valid:
            return RedProposal({**record, "admitted": False, "reason": "invalid_target_selection"}, None)
        if self.require_pursuit and selection["weakness_id"] is None:
            return RedProposal({**record, "admitted": False, "reason": "no_applicable_seed_target"}, None)
        if not forced:
            selected_id = selection["carrier_id"]
            if not isinstance(selected_id, str) or selected_id not in by_id:
                return RedProposal({**record, "admitted": False, "reason": "unoffered_design"}, None)
            carrier = by_id[selected_id]
            record["carrier_id"] = carrier.carrier_id
            try:
                record.update(normalize_selection_grounding(selection, carrier.clean_rtl, list(targets)))
            except SelectionGroundingViolation as exc:
                return RedProposal({**record, "admitted": False, "reason": exc.reason}, None)
        constraints = design_constraints(carrier, self.generator.budget)
        record.update(cluster_id=carrier.cluster_id, clean_rtl_hash=hash_payload(carrier.clean_rtl),
                      spec_hash=hash_payload(carrier.spec))
        record["edit_constraints"] = constraints
        generation_context = visible_generation_context(carrier)
        record["generation_context"] = generation_context
        wid, direction = selection["weakness_id"], selection["direction"]
        # Check transfer identity independently of any supplied view builder.
        lin = next((lin for lin in offered if lin["lineage_id"] == wid), None)
        if lin is not None and carrier.cluster_id in lin["clusters"]:
            return RedProposal({**record, "admitted": False, "reason": "lineage_design_already_used"}, None)
        target = (targets[wid] if wid else {
            "weakness_id": "explore_" + hash_payload({"carrier": carrier.carrier_id, "seed": seed})[7:21],
            "mechanism": selection["mechanism"], "mastery_status": "unobserved_hypothesis",
            "causal_chain": [], "designs_used": []})
        record["decision"] = {"lineage_id": wid, "direction": direction, "reason": selection["reason"]}
        record["mutation_plan"] = selection.get("mutation_plan")
        search_feedback = None
        if self.search and not forced:
            search_feedback = self._search_plans(carrier, selection)
            record["search_feedback"] = search_feedback
        elif self.search:
            # Forced-target generation still obeys the search output/check contract.
            search_feedback = {"version": SEARCH_VERSION, "candidates": [], "remaining_revisions": 1}
            record["search_feedback"] = search_feedback
        try:
            proposal = self.generator.generate(carrier, target_weakness=target, weakness_context=view,
                                               mutation_plan=selection.get("mutation_plan"),
                                               generation_context=generation_context,
                                               **({"search_feedback": search_feedback} if search_feedback is not None else {}),
                                               direction=direction, seed=seed)
        except RedDesignOutputViolation as exc:
            return RedProposal({**record, "admitted": False, "reason": exc.reason,
                                "rejection_details": exc.details, "generation_receipt": exc.receipt,
                                "generation_output": exc.raw_output,
                                "planning_feedback": exc.planning_feedback,
                                "materialized_rtl": exc.materialized_rtl,
                                "actual_diff": (actual_diff(carrier.clean_rtl, exc.materialized_rtl)
                                                if exc.materialized_rtl is not None else None)}, None)
        # Store the actual complete candidate even on a judge rejection.
        entry = {**proposal, "target_weakness": target,
                 "edit_text": proposal["change_summary"],
                 # Compatibility key for the existing lineage/report schema only.
                 # These opaque mechanism IDs never constrain allowed RTL edits.
                 "edit_kinds": (list(lin["operators"]) if lin else
                                ["mechanism:" + hash_payload(target["mechanism"])[7:21]])}
        record["candidates"] = [entry]
        reason = self.proposal_check(carrier, proposal) if self.proposal_check else None
        shape = None
        if reason is None:
            reason, shape = self._admit(carrier, proposal["buggy_rtl"])
        if self.search and reason == "admitted":
            checked = self.target_check.check(carrier, proposal["buggy_rtl"],
                                             proposal["predicted_wrong_repair_rtl"])
            entry["target_observability"] = checked
            record["target_observability"] = checked
            reason = checked["reason"]
        entry["reason"] = reason
        if self.search:
            record["local_admission_checks"] = len(self._admission_cache)
        if reason != "admitted":
            return RedProposal({**record, "admitted": False, "reason": reason}, None)
        entry["stealth"] = self.simulator.stealth(proposal["buggy_rtl"], carrier)
        self.seen.add(shape)
        cid = "RC_" + hash_payload({"carrier": carrier.carrier_id,
                                   "mutant": proposal["buggy_rtl_hash"]})[7:21]
        record.update(admitted=True, reason="admitted", challenge_id=cid, chosen=entry,
                      mutant_hash=proposal["buggy_rtl_hash"], clean_rtl_hash=hash_payload(carrier.clean_rtl),
                      spec_hash=hash_payload(carrier.spec), request_hash=proposal["receipt"]["request_hash"])
        challenge = Challenge(cid, carrier, proposal["buggy_rtl"], "red",
                              {"round": round_index, "red_mode": "freeform", "lineage_id": wid,
                               "direction": direction, "edit_kinds": entry["edit_kinds"],
                               "mechanism": target["mechanism"]})
        return RedProposal(record, challenge)


class FreeformCurriculumLoop(CurriculumLoop):
    """Replace only generation/scheduling/replay; inherit repair and measurement."""

    def __init__(self, *, edit_budget=EditBudget(), view_builder=public_weakness_view,
                 view_version=VIEW_VERSION, proposal_check=None, proposal_check_version="none",
                 design_candidates=DEFAULT_DESIGN_CANDIDATES, search=False, **kwargs):
        if kwargs["config"].red_mode not in {"aware", "blind"}:
            raise ValueError("freeform Red supports aware or blind")
        if proposal_check is not None and proposal_check_version == "none":
            raise ValueError("a custom proposal_check requires a version for resume identity")
        if view_builder is not public_weakness_view and view_version == VIEW_VERSION:
            raise ValueError("a custom weakness view requires its own version")
        if isinstance(design_candidates, bool) or not isinstance(design_candidates, int) or design_candidates < 1:
            raise ValueError("design_candidates must be a positive integer")
        self.design_candidates = design_candidates
        self.search = bool(search)
        if search and kwargs["config"].proposals_per_round != 1:
            raise ValueError("candidate search requires one proposal per round for immediate feedback")
        self.edit_budget = edit_budget
        self.view_version = view_version
        self.proposal_check_version = proposal_check_version
        if not isinstance(edit_budget, EditBudget):
            raise TypeError("edit_budget must be EditBudget")
        if not any(c.spec.strip() for c in kwargs["splits"]["discovery"]):
            raise ValueError("freeform Red needs discovery carriers with specifications")
        super().__init__(**kwargs)
        self.red = FreeformRed(mode=self.config.red_mode, client=kwargs["red_client"],
                               simulator=self.simulator, seed=self.config.seed, edit_budget=edit_budget,
                               view_builder=view_builder, proposal_check=proposal_check, search=self.search)
        for ch in self._challenges.values():
            self.red.seen.add(hash_payload({"carrier": ch.carrier.carrier_id,
                                           "rtl": normalized_structure_hash(ch.buggy_rtl),
                                           "text": hash_payload(ch.buggy_rtl)}))

    def _extra_frozen(self):
        return {**super()._extra_frozen(), "red_source": "freeform", "edit_budget": asdict(self.edit_budget),
                "view_version": self.view_version, "proposal_check_version": self.proposal_check_version,
                "scheduler_version": SCHEDULER_VERSION, "design_candidates": self.design_candidates,
                "selection_protocol_version": SELECTION_PROTOCOL_VERSION,
                "output_protocol_version": OUTPUT_PROTOCOL_VERSION,
                "red_prompt_hash": hash_payload([SELECT_SYSTEM, SYSTEM_PROMPT]),
                "search_version": SEARCH_VERSION if self.search else None,
                "search_prompt_hash": hash_payload([SEARCH_SELECT_SYSTEM, SEARCH_INSTRUCTION]) if self.search else None,
                "observation_version": OBSERVATION_VERSION if self.search else None,
                "target_check_version": TARGET_CHECK_VERSION if self.search else None,
                "repair_artifact_version": REPAIR_ARTIFACT_VERSION if self.search else None,
                "design_context_version": DESIGN_CONTEXT_VERSION if self.search else None,
                "search_visible_inputs": (sorted((c.carrier_id, c.top_module, c.sim_timeout,
                    [hash_payload(p.read_text()) for p in c.visible_tb],
                    [hash_payload(p.read_text()) for p in c.deps])
                    for group in self.splits.values() for c in group) if self.search else None),
                "carrier_inputs": sorted((c.carrier_id, hash_payload(c.clean_rtl), hash_payload(c.spec))
                                         for group in self.splits.values() for c in group)}

    def round(self, r):
        if not self.search or r < 0:
            return super().round(r)
        simulator = self.blue.simulator
        self.blue.simulator = PublicCandidateRecorder(simulator, self.state.root)
        try:
            return super().round(r)
        finally:
            self.blue.simulator = simulator

    def _offered(self, carriers):
        if self.config.red_mode == "blind":
            return []
        offered = [lin for lin in lineages(self.state).values() if lin["status"] in OPEN
                   and any(c.cluster_id not in lin["clusters"] for c in carriers)
                   and lin["lineage_id"] not in self._claimed]
        offered.sort(key=lambda lin: (lin["steps"][-1]["round"], lin["lineage_id"]))
        return offered[:self.curriculum.lineages_offered]

    def _propose_slots(self, r):
        discovery = [c for c in self._rotation() if c.spec.strip()]
        if self.search:
            discovery = diversify_design_order(discovery)
        if not discovery:
            raise ValueError("freeform Red needs discovery carriers with specifications")
        completed = {row["slot"]: row for row in self.state.read("red")
                     if row.get("mode") == "freeform" and row.get("round") == r and "slot" in row}
        slots = []
        for i in range(self.config.proposals_per_round):
            if i in completed:
                record = completed[i]
                ch = self._challenges.get(record.get("challenge_id"))
            else:
                # Identical history-independent design menus in aware and blind arms.
                # Sweep disjoint windows before wrapping, rather than repeatedly
                # forcing a weakness onto the next arbitrary carrier.
                size = min(self.design_candidates, len(discovery))
                ordinal = r * self.config.proposals_per_round + i
                start = ordinal if size == len(discovery) else ordinal * size
                window = [discovery[(start + k) % len(discovery)] for k in range(size)]
                proposal = self.red.propose_curriculum(window[0], candidate_carriers=window,
                                                       state=self.state, offered=self._offered(window),
                                                       seed=self._seed("red", r, i), round_index=r,
                                                       context={"slot": i})
                record, ch = proposal.record, proposal.challenge
                self.state.append("red", record)
            if ch is not None:
                self._challenges[ch.challenge_id] = ch
                slots.append(_Slot(ch, record))
                if record["decision"].get("lineage_id"):
                    self._claimed.add(record["decision"]["lineage_id"])
        return slots

    def _rebuild_challenges(self):
        by_id = {c.carrier_id: c for group in self.splits.values() for c in group}
        for row in self.state.read("red"):
            if row.get("mode") != "freeform" or not row.get("admitted"):
                continue
            carrier = by_id.get(row["carrier_id"])
            if carrier is None:
                raise RuntimeError("freeform replay carrier is missing")
            rtl = row["chosen"]["buggy_rtl"]
            if (hash_payload(rtl) != row["mutant_hash"] or hash_payload(carrier.clean_rtl) != row["clean_rtl_hash"]
                    or hash_payload(carrier.spec) != row["spec_hash"]):
                raise RuntimeError("freeform replay RTL/spec hash mismatch")
            validate_design(carrier.clean_rtl, rtl, carrier.top_module, self.edit_budget)
            decision = row["decision"]
            self._challenges[row["challenge_id"]] = Challenge(row["challenge_id"], carrier, rtl, "red",
                {"round": row["round"], "red_mode": "freeform", "lineage_id": decision["lineage_id"],
                 "direction": decision["direction"], "edit_kinds": row["chosen"]["edit_kinds"],
                 "mechanism": row["chosen"]["target_weakness"]["mechanism"]})


def freeform_holdout_variants(loop: FreeformCurriculumLoop, carriers: Sequence[Carrier], *, seed: int):
    """One same-mechanism generation call per lineage on an unused holdout design."""
    if loop.config.red_mode == "blind":
        raise ValueError("targeted freeform holdout needs aware Red; blind has no weakness view")
    holdout_ids = {c.carrier_id for c in loop.splits["holdout"]}
    if any(c.carrier_id not in holdout_ids for c in carriers):
        raise ValueError("freeform holdout generation requires carriers from the frozen holdout split")
    done = {r["holdout_for"]: r for r in loop.state.read("red")
            if r.get("holdout_for") and r.get("mode") == "freeform"}
    out = []
    for i, lin in enumerate(sorted(lineages(loop.state).values(), key=lambda l: l["lineage_id"])):
        lid = lin["lineage_id"]
        if lid in done:
            row = done[lid]
            if row.get("admitted"):
                out.append(loop._challenges[row["challenge_id"]])
            continue
        eligible = [c for c in carriers if c.spec.strip() and c.cluster_id not in lin["clusters"]]
        eligible.sort(key=lambda c: hash_payload({"seed": seed, "lineage": lid, "carrier": c.carrier_id}))
        if not eligible:
            continue
        proposal = loop.red.propose_curriculum(eligible[0], state=loop.state, offered=[lin],
                    seed=loop._seed("holdout", seed, i), round_index=-2, context={"holdout_for": lid})
        loop.state.append("red", proposal.record)
        if proposal.challenge is not None:
            loop._challenges[proposal.challenge.challenge_id] = proposal.challenge
            out.append(proposal.challenge)
    return out


class FakeFreeformTransport:
    """CLI plumbing only: scripted target selection and a single literal change."""

    def __call__(self, **request):
        user = json.loads(request["messages"][-1]["content"])
        if user.get("task") == "select_freeform_target":
            targets = user["weakness_view"]["open_weak_points"]
            candidate = next((c for c in user["candidate_designs"] if c["eligible_weakness_ids"]),
                             user["candidate_designs"][0])
            target = next((t for t in targets if t["weakness_id"] in candidate["eligible_weakness_ids"]), None)
            payload = {"carrier_id": candidate["carrier_id"],
                       "applicability": {"rtl_anchors": [candidate["clean_rtl"]], "reason": "scripted plumbing only"},
                       "skipped_weaknesses": {t["weakness_id"]: "scripted no eligible candidate" for t in targets} if not target else {},
                       "weakness_id": target["weakness_id"] if target else None,
                       "direction": "same" if target else "explore",
                       "mechanism": target["mechanism"] if target else "incorrect internal constant",
                       "reason": "scripted local plumbing test",
                       "mutation_plan": {"edits": self._edit(candidate["clean_rtl"]),
                                         "invariant": "specified internal constant",
                                         "activation": "exercise the changed constant", "spec_basis": "original specification"}}
            if "SEARCH CONTRACT EXTENSION" in request["messages"][0]["content"]:
                payload.update(alternative_plans=[], repair_hypothesis={
                    "predicted_wrong_repair": "change the output instead of its dependency",
                    "residual_failure": "the incorrect constant still affects the state",
                    "source_observation_ids": []})
        else:
            payload = {"edits": self._edit(user["clean_rtl"]),
                       "hypothesis": "scripted literal mutation", "expected_symptom": "wrong value",
                       "change_summary": "one internal literal changed",
                       "spec_basis": "original supplied specification (plumbing only)"}
            if "search_feedback" in user:
                checked = user["search_feedback"]["candidates"]
                selected = next((c for c in checked if c["reason"] == "admitted"),
                                checked[0] if checked else None)
                if selected:
                    payload["edits"] = selected["plan"]["edits"]
                mutant = apply_text_edits(user["clean_rtl"], payload["edits"])
                payload.update(broken_invariant="specified internal constant",
                               predicted_wrong_repair="scripted full reversion, intentionally rejected",
                               residual_failure="none; the dry run exercises the residual-observability rejection",
                               predicted_wrong_repair_edits=[{"find": mutant, "replace": user["clean_rtl"]}])
        return {"content": json.dumps(payload), "input_tokens": 100, "output_tokens": 40,
                "provider_request_id": "fake-freeform"}

    @staticmethod
    def _edit(rtl):
        tokens = safe_tokenize(rtl)
        header_end = next(t.end for t in tokens if t.value == ";")
        for token in tokens:
            if token.start > header_end and token.kind == "number" and token.value in {"0", "1", "1'b0", "1'b1"}:
                value = token.value[:-1] + ("1" if token.value[-1] == "0" else "0")
                # Full-source find is unambiguous, with just one token changed.
                return [{"find": rtl, "replace": rtl[:token.start] + value + rtl[token.end:]}]
        return [{"find": rtl, "replace": rtl}]  # intentionally rejected, never fabricate a valid bug
