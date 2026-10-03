"""Hybrid Red: weakness hypothesis + catalog edit + admission gate.

Three arms share one carrier pool, operator catalog, admission gate and budget:

- ``aware``: the LLM sees a summary of the *current* Blue state. The summary
  covers the applicability of the active knowledge and the profiles of recent
  residual failures, all derived from real loop records.
- ``blind``: the same LLM prompt without any Blue state.
- ``random``: no LLM; the site and option are drawn deterministically from a seed.

Red is never given a target family or label. Blue never sees Red's
hypothesis, operator or site.

Each LLM call may return up to three candidate bugs, each made of one or two
edits. Every candidate passes the same admission gate, and among the admitted
ones the runner keeps the stealthiest: the fewest mismatching cycles on the
visible test, then the latest first mismatch. That choice uses simulation
only. The policy-aware arm also sees how Blue fared on Red's earlier admitted
bugs (its own outcome history).

Admission requires all five:
- the edit parses into new text;
- the mutant compiles and simulates;
- the mutant introduces no combinational logic loop that the clean design
  lacks, so a failure caused only by simulator oscillation is not admitted;
- the mutant fails the *visible* test;
- the mutant is not a duplicate of an earlier admitted mutant.

Rejected proposals are recorded with their reason, and their cost counts.
"""
from __future__ import annotations

import random
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from r3e.protocol.hashing import canonical_json, hash_payload

from .budget import BudgetedClient
from .corpus import Carrier, Challenge, normalized_structure_hash
from .operators import CATALOG, apply_edits, enumerate_sites
from .sim import Simulator
from .state import RunState


RED_SYSTEM = (
    "You are the Red agent in an RTL repair evaluation. Goal: find functional bugs that the "
    "current Blue repair agent fails to repair within its small attempt budget. Blue sees only "
    "the buggy RTL and which outputs mismatch, at which cycle, on a random-stimulus test. "
    "Keep your reasoning brief. Return strict "
    "JSON: {\"candidates\": [{\"hypothesis\": one sentence, \"edits\": [{\"site_id\": ..., "
    "\"option\": ...}], \"expected_symptom\": short}]} with up to 3 candidates, each using 1 or 2 "
    "edits at different offered sites."
)
RED_AWARE_NOTE = (
    "current_blue_state summarises the Blue agent you are attacking, including how it did on "
    "your earlier bugs; use it to target what Blue still fails."
)
MAX_CANDIDATES = 3
MAX_EDITS = 2
MAX_RED_SITES = 24


def sample_sites(sites, *, seed: int, limit: int = MAX_RED_SITES):
    """Operator-balanced deterministic subset, so Red's decision stays small."""
    if len(sites) <= limit:
        return list(sites)
    rng = random.Random(seed)
    by_op: dict[str, list] = {}
    for site in sites:
        by_op.setdefault(site.operator, []).append(site)
    for group in by_op.values():
        rng.shuffle(group)
    picked = []
    while len(picked) < limit:
        for op in sorted(by_op):
            if by_op[op] and len(picked) < limit:
                picked.append(by_op[op].pop())
    return sorted(picked, key=lambda s: s.start)


def has_logic_loop(rtl: str, top: str, workdir: Path) -> bool:
    """Yosys ``check`` reports combinational logic loops (simulator-artifact bugs)."""
    workdir.mkdir(parents=True, exist_ok=True)
    src = workdir / "loop_check.sv"
    src.write_text(rtl, encoding="utf-8")
    proc = subprocess.run(
        ["yosys", "-q", "-p", f"read_verilog -sv {src}; hierarchy -top {top}; proc; flatten; check"],
        capture_output=True, text=True, timeout=120,
    )
    return "logic loop" in (proc.stdout + proc.stderr).lower()


def provider_failure(exc: Exception) -> dict[str, Any]:
    """Sanitized failure record: exception class plus categorical diagnostics."""
    return {"error": type(exc).__name__, "diagnostics": dict(getattr(exc, "diagnostics", {}) or {})}


def blue_state_summary(state: RunState, *, recent: int = 40) -> dict[str, Any]:
    """What the policy-aware Red may know about Blue: real records only."""
    active = state.active_items()
    encounters = [row["encounter"] for row in state.read("encounters")
                  if not row["encounter"].get("inconclusive")][-recent:]
    residual = [e for e in encounters if not e["solved_within_budget"]]

    def key(profile: Mapping[str, Any]) -> str:
        s, c = profile.get("status", {}), profile.get("causal", {})
        return "|".join(str(x) for x in (
            s.get("symptom"), c.get("output_driver_kind"), c.get("register_distance"),
            c.get("temporal_relation")))

    return {
        "active_knowledge": [
            {"likely_bug_type": i.bug_type,
             "symptom": i.payload["applicability"]["status"].get("symptom"),
             "output_driver_kind": i.payload["applicability"]["causal"].get("output_driver_kind"),
             "support": i.payload["evidence"]["support"]}
            for i in active
        ],
        "recent_encounters": len(encounters),
        "recent_solved_within_budget": sum(e["solved_within_budget"] for e in encounters),
        "residual_failure_profiles": dict(Counter(key(e["profile"]) for e in residual).most_common(8)),
        "recent_challenge_outcomes": _red_outcomes(state, encounters),
        "summary_hash": hash_payload([i.item_hash for i in active] + [e["episode_hash"] for e in encounters]),
    }


def _red_outcomes(state: RunState, encounters, limit: int = 10) -> list[dict[str, Any]]:
    """How Blue fared on Red's own earlier admitted bugs (Red's memory)."""
    by_id = {e["challenge_id"]: e for e in encounters}
    out = []
    for row in state.read("red"):
        enc = by_id.get(row.get("challenge_id"))
        if not row.get("admitted") or enc is None:
            continue
        chosen = row.get("chosen") or {}
        out.append({
            "edit_kinds": chosen.get("edit_kinds", []),
            "hypothesis": str(chosen.get("hypothesis", ""))[:160],
            "stealth_mismatch_fraction": (chosen.get("stealth") or {}).get("mismatch_fraction"),
            "blue_repair_attempts": len([a for a in enc["attempts"] if not a.get("infra_failure")]),
            "blue_solved_within_budget": enc["solved_within_budget"],
            "blue_solved_by_escalation": enc["solved_by_escalation"],
        })
    return out[-limit:]


@dataclass
class RedProposal:
    record: dict[str, Any]
    challenge: Challenge | None


class RedAgent:
    def __init__(self, *, mode: str, client: BudgetedClient | None, simulator: Simulator, seed: int = 0,
                 candidates_per_call: int | None = None, random_max_edits: int = 1):
        if mode not in {"aware", "blind", "random"}:
            raise ValueError("red mode must be aware, blind or random")
        if mode != "random" and client is None:
            raise ValueError("LLM red modes need a client")
        self.mode = mode
        self.client = client
        self.simulator = simulator
        self.rng = random.Random(seed)
        self.seen: set[str] = set()
        self._clean_loops: dict[str, bool] = {}
        # the random baseline keeps single-edit, single-candidate mutation (no
        # stealth ranking); LLM arms propose up to MAX_CANDIDATES per call
        self.candidates_per_call = candidates_per_call or (1 if mode == "random" else MAX_CANDIDATES)
        self.random_max_edits = max(1, min(int(random_max_edits), MAX_EDITS))

    def _choose(self, carrier: Carrier, sites, state: RunState, seed: int) -> tuple[list[dict[str, Any]], str | None]:
        if self.mode == "random":
            out = []
            for _ in range(self.candidates_per_call):
                picked = self.rng.sample(sites, k=min(len(sites), self.rng.randint(1, self.random_max_edits)))
                out.append({"hypothesis": "random baseline",
                            "edits": [{"site_id": s.site_id, "option": self.rng.choice(s.options)} for s in picked],
                            "expected_symptom": "unspecified"})
            return out, None
        user = {
            "carrier_rtl": carrier.clean_rtl,
            "top_module": carrier.top_module,
            "operator_catalog": CATALOG,
            "sites": [s.public() for s in sites],
        }
        system = RED_SYSTEM
        if self.mode == "aware":
            user["current_blue_state"] = blue_state_summary(state)
            system = RED_SYSTEM + " " + RED_AWARE_NOTE
        with self.client.in_phase("red"):
            response = self.client.complete_json(
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": canonical_json(user)}],
                seed=seed,
            )
        result = dict(response["result"])
        if "candidates" not in result and "site_id" in result:  # single-edit legacy form
            result = {"candidates": [{"hypothesis": result.get("hypothesis"),
                                      "edits": [{"site_id": result.get("site_id"), "option": result.get("option")}],
                                      "expected_symptom": result.get("expected_symptom")}]}
        candidates = result.get("candidates") if isinstance(result.get("candidates"), list) else []
        return [c for c in candidates if isinstance(c, dict)][:self.candidates_per_call], response.get("request_hash")

    def _admit(self, carrier: Carrier, mutant: str) -> tuple[str, Any]:
        if mutant == carrier.clean_rtl:
            return "no_change", None
        shape = hash_payload({"carrier": carrier.carrier_id, "rtl": normalized_structure_hash(mutant),
                              "text": hash_payload(mutant)})
        if shape in self.seen:
            return "duplicate", None
        verdict = self.simulator.verdict(mutant, carrier)
        if verdict.tier == "compile_fail":
            return "does_not_compile", None
        scratch = self.simulator.workspace / "loop_check"
        if carrier.carrier_id not in self._clean_loops:
            self._clean_loops[carrier.carrier_id] = has_logic_loop(
                carrier.clean_rtl, carrier.top_module, scratch / "clean")
        if not self._clean_loops[carrier.carrier_id] and has_logic_loop(
                mutant, carrier.top_module, scratch / "mutant"):
            return "introduces_combinational_loop", None
        if verdict.visible_ok:
            return "not_observable_by_visible_test", None
        return "admitted", shape

    def _evaluate(self, carrier: Carrier, sites, candidates, record: dict[str, Any], *,
                  check=None, prefer: str = "stealthiest", max_edits: int = MAX_EDITS):
        """Admit each candidate and pick one; records every candidate's fate.

        ``check(entry, resolved)`` may return a rejection reason (referee rules
        a caller adds, e.g. curriculum constraints). ``prefer`` is
        ``stealthiest`` (fewest mismatching cycles) or ``most_visible``.
        Returns ``(rank, entry, mutant, shape)`` or ``None``.
        """
        by_id = {s.site_id: s for s in sites}
        evaluated, admitted = [], []
        for cand in candidates:
            edits = cand.get("edits") if isinstance(cand.get("edits"), list) else []
            entry = {"hypothesis": str(cand.get("hypothesis") or "")[:400],
                     "expected_symptom": str(cand.get("expected_symptom") or "")[:200],
                     "edits": [{"site_id": e.get("site_id"), "option": e.get("option")}
                               for e in edits if isinstance(e, dict)][:max_edits]}
            for extra in ("lineage_id", "direction"):
                if extra in cand:
                    entry[extra] = cand.get(extra)
            resolved = [(by_id.get(e["site_id"]), e["option"]) for e in entry["edits"]]
            if (not resolved or any(site is None or option not in site.options for site, option in resolved)
                    or len({site.site_id for site, _ in resolved}) != len(resolved)):
                entry["reason"] = "not_in_catalog"
                evaluated.append(entry)
                continue
            entry["edit_kinds"] = [site.operator for site, _ in resolved]
            rule = check(entry, resolved) if check else None
            if rule:
                entry["reason"] = rule
                evaluated.append(entry)
                continue
            try:
                mutant = apply_edits(carrier, [(site, str(option)) for site, option in resolved])
            except ValueError:
                entry["reason"] = "overlapping_edits"
                evaluated.append(entry)
                continue
            reason, shape = self._admit(carrier, mutant)
            entry["reason"] = reason
            if reason == "admitted":
                entry["edit_text"] = [{"operator": site.operator, "line": site.line + 1, "snippet": site.snippet,
                                       "replacement": str(option)}
                                      for site, option in resolved]
                admitted.append((entry, mutant, shape))
            evaluated.append(entry)
        record["candidates"] = evaluated
        best = None
        for entry, mutant, shape in admitted:
            # stealth ranking (simulation only) is needed when there is a choice,
            # and is recorded for LLM arms; the random baseline skips it
            if len(admitted) > 1 or self.mode != "random":
                entry["stealth"] = self.simulator.stealth(mutant, carrier)
            stealth = entry.get("stealth") or {}
            rank = (stealth.get("mismatch_fraction", 1.0), -(stealth.get("first_mismatch") or 0))
            if prefer == "most_visible":
                rank = (-rank[0], -rank[1])
            if best is None or rank < best[0]:
                best = (rank, entry, mutant, shape)
        if best is None:
            reasons = [c["reason"] for c in evaluated] or ["no_candidates"]
            record.update(admitted=False, reason=reasons[0] if len(reasons) == 1 else "none_admitted")
        return best

    def propose(self, carrier: Carrier, *, state: RunState, seed: int, round_index: int) -> RedProposal:
        sites = sample_sites(enumerate_sites(carrier), seed=seed)
        record: dict[str, Any] = {"round": round_index, "mode": self.mode,
                                  "carrier_id": carrier.carrier_id, "seed": seed,
                                  "sites_offered": len(sites)}
        if not sites:
            record.update(admitted=False, reason="no_sites")
            return RedProposal(record, None)
        try:
            candidates, request_hash = self._choose(carrier, sites, state, seed)
        except Exception as exc:
            if type(exc).__name__ == "CallBudgetExceeded":
                raise
            record.update(admitted=False, reason=f"provider_error:{type(exc).__name__}",
                          failure=provider_failure(exc))
            return RedProposal(record, None)
        record["request_hash"] = request_hash
        best = self._evaluate(carrier, sites, candidates, record)
        if best is None:
            return RedProposal(record, None)
        _, chosen, mutant, shape = best
        self.seen.add(shape)
        challenge_id = "RC_" + hash_payload({"carrier": carrier.carrier_id, "mutant": hash_payload(mutant)}).split(":", 1)[1][:14]
        record.update(admitted=True, reason="admitted", challenge_id=challenge_id,
                      mutant_hash=hash_payload(mutant), chosen=chosen,
                      choice={"hypothesis": chosen["hypothesis"],
                              "operator": "+".join(chosen["edit_kinds"])})
        return RedProposal(record, Challenge(
            challenge_id=challenge_id, carrier=carrier, buggy_rtl=mutant, origin="red",
            provenance={"round": round_index, "red_mode": self.mode,
                        "edit_kinds": chosen["edit_kinds"]},
        ))
