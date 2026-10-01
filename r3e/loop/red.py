"""Hybrid Red: weakness hypothesis + catalog edit + admission gate.

Three arms share one carrier pool, operator catalog, admission gate and budget:

- ``aware``: the LLM sees a summary of the *current* Blue state. The summary
  covers the applicability of the active knowledge and the profiles of recent
  residual failures, all derived from real loop records.
- ``blind``: the same LLM prompt without any Blue state.
- ``random``: no LLM; the site and option are drawn deterministically from a seed.

Red is never given a target family or label. Blue never sees Red's
hypothesis, operator or site.

Admission requires all four:
- the edit parses into new text;
- the mutant compiles and simulates;
- the mutant fails the *visible* test;
- the mutant is not a duplicate of an earlier admitted mutant.

Rejected proposals are recorded with their reason, and their cost counts.
"""
from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from r3e.protocol.hashing import canonical_json, hash_payload

from .budget import BudgetedClient
from .corpus import Carrier, Challenge, normalized_structure_hash
from .operators import CATALOG, enumerate_sites, apply_edit
from .sim import Simulator
from .state import RunState


RED_SYSTEM = (
    "You are the Red agent in an RTL repair evaluation. Propose ONE functional bug "
    "that the current Blue repair agent is likely to fail to repair. State a concrete "
    "weakness hypothesis, then choose exactly one operator, site_id and option from the "
    "offered sites. Return strict JSON with fields: hypothesis, operator, site_id, "
    "option, expected_symptom."
)


def blue_state_summary(state: RunState, *, recent: int = 40) -> dict[str, Any]:
    """What the policy-aware Red may know about Blue: real records only."""
    active = state.active_items()
    encounters = [row["encounter"] for row in state.read("encounters")][-recent:]
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
        "summary_hash": hash_payload([i.item_hash for i in active] + [e["episode_hash"] for e in encounters]),
    }


@dataclass
class RedProposal:
    record: dict[str, Any]
    challenge: Challenge | None


class RedAgent:
    def __init__(self, *, mode: str, client: BudgetedClient | None, simulator: Simulator, seed: int = 0):
        if mode not in {"aware", "blind", "random"}:
            raise ValueError("red mode must be aware, blind or random")
        if mode != "random" and client is None:
            raise ValueError("LLM red modes need a client")
        self.mode = mode
        self.client = client
        self.simulator = simulator
        self.rng = random.Random(seed)
        self.seen: set[str] = set()

    def _choose(self, carrier: Carrier, sites, state: RunState, seed: int) -> dict[str, Any]:
        if self.mode == "random":
            site = self.rng.choice(sites)
            return {"hypothesis": "random baseline", "operator": site.operator,
                    "site_id": site.site_id, "option": self.rng.choice(site.options),
                    "expected_symptom": "unspecified"}
        user = {
            "carrier_rtl": carrier.clean_rtl,
            "top_module": carrier.top_module,
            "operator_catalog": CATALOG,
            "sites": [s.public() for s in sites],
        }
        if self.mode == "aware":
            user["current_blue_state"] = blue_state_summary(state)
        with self.client.in_phase("red"):
            response = self.client.complete_json(
                messages=[{"role": "system", "content": RED_SYSTEM},
                          {"role": "user", "content": canonical_json(user)}],
                seed=seed,
            )
        return dict(response["result"]) | {"request_hash": response.get("request_hash")}

    def propose(self, carrier: Carrier, *, state: RunState, seed: int, round_index: int) -> RedProposal:
        sites = enumerate_sites(carrier)
        record: dict[str, Any] = {"round": round_index, "mode": self.mode,
                                  "carrier_id": carrier.carrier_id, "seed": seed}
        if not sites:
            record.update(admitted=False, reason="no_sites")
            return RedProposal(record, None)
        try:
            choice = self._choose(carrier, sites, state, seed)
        except Exception as exc:
            if type(exc).__name__ == "CallBudgetExceeded":
                raise
            record.update(admitted=False, reason=f"provider_error:{type(exc).__name__}")
            return RedProposal(record, None)
        record["choice"] = {k: choice.get(k) for k in
                            ("hypothesis", "operator", "site_id", "option", "expected_symptom", "request_hash")}
        site = next((s for s in sites if s.site_id == choice.get("site_id")), None)
        if site is None or site.operator != choice.get("operator") or choice.get("option") not in site.options:
            record.update(admitted=False, reason="not_in_catalog")
            return RedProposal(record, None)
        mutant = apply_edit(carrier, site, str(choice["option"]))
        shape = hash_payload({"carrier": carrier.carrier_id, "rtl": normalized_structure_hash(mutant),
                              "text": hash_payload(mutant)})
        if mutant == carrier.clean_rtl:
            record.update(admitted=False, reason="no_change")
            return RedProposal(record, None)
        if shape in self.seen:
            record.update(admitted=False, reason="duplicate")
            return RedProposal(record, None)
        verdict = self.simulator.verdict(mutant, carrier)
        if verdict.tier == "compile_fail":
            record.update(admitted=False, reason="does_not_compile")
            return RedProposal(record, None)
        if verdict.visible_ok:
            record.update(admitted=False, reason="not_observable_by_visible_test")
            return RedProposal(record, None)
        self.seen.add(shape)
        challenge_id = "RC_" + hash_payload({"carrier": carrier.carrier_id, "mutant": hash_payload(mutant)}).split(":", 1)[1][:14]
        record.update(admitted=True, reason="admitted", challenge_id=challenge_id,
                      mutant_hash=hash_payload(mutant))
        return RedProposal(record, Challenge(
            challenge_id=challenge_id, carrier=carrier, buggy_rtl=mutant, origin="red",
            provenance={"round": round_index, "red_mode": self.mode},
        ))
