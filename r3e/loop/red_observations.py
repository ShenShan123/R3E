"""Bounded public discovery observations for Red, never a weakness verdict."""
from __future__ import annotations

from typing import Any, Mapping

from r3e.protocol.hashing import hash_payload
from .red_design import actual_diff
from .red_repair_feedback import read_public_candidate

OBSERVATION_VERSION = "public_discovery_observations_v2"
PASS = {"visible_pass", "hidden_pass", "formal_pass"}


def public_attempt(attempt: Mapping[str, Any]) -> dict[str, Any]:
    # Do not copy the encounter or verdict wholesale: both may carry hidden data.
    tier = attempt.get("verdict_tier")
    tier = "visible_pass" if tier in PASS else tier
    feedback = attempt.get("feedback") or {}
    visible = {k: feedback[k] for k in ("stage", "message", "cycles_compared") if k in feedback}
    if "first_divergences" in feedback:
        visible["first_divergences"] = [{k: d[k] for k in
            ("signal", "first_cycle", "expected", "observed", "pattern") if k in d}
            for d in feedback["first_divergences"][:4]]
    if "passing_outputs" in feedback:
        visible["passing_outputs"] = list(feedback["passing_outputs"])[:16]
    return {"index": attempt.get("index"), "verdict": tier,
            "edit_summary": str(attempt.get("edit", ""))[:800],
            "edit_summary_source": "model explanation, not a verified patch",
            "candidate_hash": attempt.get("candidate_hash"),
            "visible_feedback": visible}


def observe_encounter(enc: Mapping[str, Any]) -> dict[str, Any]:
    attempts = [a for a in enc.get("attempts", []) if not a.get("infra_failure")]
    functional = sum(a.get("verdict_tier") == "visible_fail" for a in attempts)
    no_answer = sum(a.get("verdict_tier") == "no_answer" for a in attempts)
    compile_fail = sum(a.get("verdict_tier") == "compile_fail" for a in attempts)
    solved = bool(enc.get("solved_within_budget"))
    if enc.get("inconclusive"):
        outcome = "inconclusive"
    elif solved and attempts and attempts[0].get("verdict_tier") in PASS:
        outcome = "fixed_first_attempt"
    elif solved and functional:
        outcome = "fixed_after_wrong_functional_repair"
    elif solved and no_answer and not compile_fail:
        outcome = "fixed_after_no_answer"
    elif solved:
        outcome = "fixed_after_other_failure"
    else:
        outcome = "unrepaired"
    return {"outcome": outcome, "functional_wrong_attempts": functional,
            "no_answer_attempts": no_answer, "compile_failure_attempts": compile_fail,
            "infrastructure_failures": int(enc.get("infra_failures", 0)),
            "attempts": [public_attempt(a) for a in attempts]}


def discovery_observations(state, offered, round_index: int) -> dict[str, Any]:
    def prior(row):
        return (0 <= row.get("round", -1) and
                (round_index < 0 or row["round"] < round_index))

    records = [r for r in state.read("red") if prior(r)]
    encounters: dict[str, list] = {}
    for row in state.read("encounters"):
        if prior(row):
            enc = row["encounter"]
            encounters.setdefault(enc["challenge_id"], []).append({
                "confirmation": bool(row.get("confirmation")), **observe_encounter(enc)})
    relevant = {s["challenge_id"] for lin in offered for s in lin.get("steps", []) if prior(s)}
    # Last six plus up to six older target-relevant records; generation order is stable.
    selected = records[-6:]
    selected_ids = {id(r) for r in selected}
    older = [r for r in records if id(r) not in selected_ids and r.get("challenge_id") in relevant][-6:]
    selected = older + selected
    observations = []
    for row in selected:
        chosen = row.get("chosen") or {}
        observations.append({"observation_id": hash_payload({"round": row["round"],
                                 "slot": row.get("slot"), "input": row.get("selection_receipt"),
                                 "challenge": row.get("challenge_id"), "carrier": row.get("carrier_id")}),
            "round": row["round"], "challenge_id": row.get("challenge_id"),
            "carrier_id": row.get("carrier_id"), "cluster_id": row.get("cluster_id"),
            "admitted": bool(row.get("admitted")), "rejection_reason": row.get("reason"),
            "clean_rtl_hash": row.get("clean_rtl_hash"), "spec_hash": row.get("spec_hash"),
            "mutant_hash": row.get("mutant_hash"),
            "actual_red_diff": str(chosen.get("actual_diff") or row.get("actual_diff") or "")[:6000],
            "red_diff_truncated": len(str(chosen.get("actual_diff") or row.get("actual_diff") or "")) > 6000,
            "red_hypothesis": chosen.get("hypothesis"),
            "blue_observations": encounters.get(row.get("challenge_id"), []),
            "interpretation": "search evidence, not a confirmed weakness or causal diagnosis"})
    # Newest cases first receive a shared, deterministic prompt-size allowance.
    # An artifact alone is never evidence: the admitted challenge and completed
    # encounter must already have survived the prior-discovery allowlist above.
    remaining = 12000
    for row, observation in reversed(list(zip(selected, observations))):
        mutant = (row.get("chosen") or {}).get("buggy_rtl")
        valid_origin = (row.get("admitted") and isinstance(mutant, str)
                        and hash_payload(mutant) == row.get("mutant_hash"))
        for encounter in observation["blue_observations"]:
            for attempt in encounter["attempts"]:
                attempt["actual_patch"] = {"status": "unavailable"}
                if not valid_origin:
                    continue
                rtl = read_public_candidate(state.root, row.get("carrier_id"), attempt["candidate_hash"])
                if rtl is None:
                    continue
                diff = actual_diff(mutant, rtl, fromfile="challenge.sv", tofile="blue_candidate.sv")
                limit = min(2000, remaining)
                attempt["actual_patch"] = {
                    "status": "available" if len(diff) <= limit else "truncated",
                    "base": "original_challenge", "base_hash": row["mutant_hash"],
                    "candidate_hash": attempt["candidate_hash"], "diff_hash": hash_payload(diff),
                    "diff": diff[:limit], "truncated": len(diff) > limit,
                    "source": "hash_verified_public_candidate", "correctness": "visible_verdict_only"}
                remaining -= min(len(diff), limit)
    return {"version": OBSERVATION_VERSION, "records": observations,
            "repair_diff_character_limit": 12000,
            "total_prior_proposals": len(records), "omitted_proposals": len(records) - len(selected),
            "selection": "last_six_plus_up_to_six_older_offered_target_cases"}
