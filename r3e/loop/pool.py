"""Curriculum over real bugs: Red chooses which public bug Blue faces next.

Bugs come from public benchmark manifests (``load_public_manifest``: CirFix
today, other sets once they are in the same manifest format). Red does not
create bugs here. Each slot it is offered unused bugs from the discovery
designs and picks one, either to explore or to pursue an open weak point
with a bug of the same fix type on a design that weak point has not used.

For each offered bug, Red sees the design, the visible-failure profile, the
lines the bug changed relative to the correct design, and the bug's fix type
(diff classifier). All of this stays on Red's side: Blue sees only the buggy
design and its test evidence, as for every challenge.

With ``red_mode`` ``random`` (the baseline) no model is called: each slot
takes a bug drawn uniformly, with the run's seed, from all unused discovery
bugs, so nothing of the experience base or of the offer order reaches it.

Referee rules:
- the choice is one of the offered bugs;
- a bug is presented at most once per run;
- a pursued weak point needs the same fix type on a new design cluster.

Repair, case memory, the fork-paired gate and mastery tests, and the usage
track records are the curriculum loop's (``curriculum.py``). Bugs on
holdout designs are evaluated directly with and without memory.
"""
from __future__ import annotations

import difflib
from functools import lru_cache
from typing import Any, Mapping, Sequence

from r3e.knowledge import analyze_rtl, build_profile, classify_repair
from r3e.protocol.hashing import canonical_json, hash_payload

from .corpus import Challenge
from .curriculum import DIRECTIONS, OPEN, CurriculumLoop, CurriculumRed, _Slot, lineages, mechanism_coverage, red_view
from .red import RED_AWARE_NOTE, RedProposal, blue_state_summary, provider_failure
from .state import RunState


RED_POOL_SYSTEM = (
    "You are the Red agent in an RTL repair evaluation. You choose which real bug, from a public bug "
    "benchmark, the Blue repair agent faces next. Goal: find bugs Blue fails to repair within its small "
    "attempt budget, and keep testing its weak points until it masters them. Blue sees only the buggy "
    "RTL and which outputs mismatch on a visible test. experience_base lists Blue's open weak points and "
    "how Blue fared per fix type. Either explore, or pursue one weak point by choosing a candidate with "
    "the same fix type on a different design (direction 'same'; 'harder' if you expect it to be harder "
    "than the last one Blue solved). Keep your reasoning brief. Return strict JSON: {\"decision\": "
    "{\"lineage_id\": id or null, \"direction\": \"explore\"|\"same\"|\"harder\"|\"simpler\", \"reason\": "
    "short}, \"choice\": candidate_id}."
)
MAX_OFFERED = 8


def fix_type(ch: Challenge) -> str:
    return _fix_type(ch.buggy_rtl, ch.carrier.clean_rtl)


@lru_cache(maxsize=1024)
def _fix_type(buggy: str, clean: str) -> str:
    return str(classify_repair(buggy, clean)["bug_type"])


def changed_lines(ch: Challenge, limit: int = 12) -> dict[str, list[str]]:
    """Lines the bug changed relative to the correct design (Red-side only)."""
    diff = [l for l in difflib.unified_diff(ch.carrier.clean_rtl.splitlines(), ch.buggy_rtl.splitlines(),
                                            lineterm="", n=0) if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    return {"correct": [l[1:].strip() for l in diff if l.startswith("-")][:limit],
            "buggy": [l[1:].strip() for l in diff if l.startswith("+")][:limit]}


class PoolRed(CurriculumRed):
    def choose(self, candidates: Sequence[dict[str, Any]], *, state: RunState,
               offered: Sequence[Mapping[str, Any]], seed: int, round_index: int,
               challenges: Mapping[str, Challenge]) -> RedProposal:
        record: dict[str, Any] = {"round": round_index, "mode": "pool", "seed": seed,
                                  "offered_candidates": [c["candidate_id"] for c in candidates],
                                  "offered_lineages": [lin["lineage_id"] for lin in offered]}
        user = {"candidates": list(candidates), "current_blue_state": blue_state_summary(state),
                "experience_base": {"open_weak_points": [red_view(lin) for lin in offered],
                                    "mechanism_coverage": mechanism_coverage(state)}}
        try:
            with self.client.in_phase("red"):
                response = self.client.complete_json(
                    messages=[{"role": "system", "content": RED_POOL_SYSTEM + " " + RED_AWARE_NOTE},
                              {"role": "user", "content": canonical_json(user)}], seed=seed)
        except Exception as exc:
            if type(exc).__name__ == "CallBudgetExceeded":
                raise
            record.update(admitted=False, reason=f"provider_error:{type(exc).__name__}",
                          failure=provider_failure(exc))
            return RedProposal(record, None)
        record["request_hash"] = response.get("request_hash")
        result = dict(response["result"])
        decision = result.get("decision") if isinstance(result.get("decision"), dict) else {}
        lineage_id = decision.get("lineage_id") or None
        direction = str(decision.get("direction") or ("same" if lineage_id else "explore"))
        record["decision"] = {"lineage_id": lineage_id, "direction": direction,
                              "reason": str(decision.get("reason") or "")[:300]}
        by_lineage = {lin["lineage_id"]: lin for lin in offered}
        by_id = {c["candidate_id"]: c for c in candidates}
        choice = result.get("choice")
        ch = challenges.get(choice) if choice in by_id else None
        lin = by_lineage.get(lineage_id)
        reason = ("invalid_decision" if direction not in DIRECTIONS or (lineage_id is None) != (direction == "explore")
                  else "not_offered" if ch is None
                  else "unknown_weak_point" if lineage_id is not None and lin is None
                  else "not_lineage_mechanism" if lin is not None and f"type:{fix_type(ch)}" not in lin["operators"]
                  else "design_already_used" if lin is not None and ch.carrier.cluster_id in lin["clusters"]
                  else None)
        if reason:
            record.update(admitted=False, reason=reason)
            return RedProposal(record, None)
        stealth = self.simulator.stealth(ch.buggy_rtl, ch.carrier)
        record.update(admitted=True, reason="admitted", challenge_id=ch.challenge_id,
                      carrier_id=ch.carrier.carrier_id, mutant_hash=hash_payload(ch.buggy_rtl),
                      chosen={"hypothesis": record["decision"]["reason"], "edit_kinds": [f"type:{fix_type(ch)}"],
                              "edit_text": by_id[choice]["changed_lines"], "stealth": stealth})
        return RedProposal(record, ch)


class PoolCurriculumLoop(CurriculumLoop):
    """The curriculum loop with Red choosing among real public bugs."""

    def __init__(self, *, pool: Sequence[Challenge], **kwargs: Any):
        self.pool = {ch.challenge_id: ch for ch in pool}
        super().__init__(**kwargs)
        mode = self.config.red_mode
        if mode not in ("aware", "random"):
            raise ValueError("pool mode supports red_mode aware or random")
        self.red = PoolRed(mode=mode, client=kwargs["red_client"] if mode == "aware" else None,
                           simulator=self.simulator, seed=self.config.seed)
        self._challenges.update(self.pool)

    def _extra_frozen(self) -> dict[str, Any]:
        return {**super()._extra_frozen(), "pool": sorted(hash_payload(c.buggy_rtl) for c in self.pool.values())}

    def _rebuild_challenges(self) -> None:
        super()._rebuild_challenges()
        self._challenges.update(getattr(self, "pool", {}))

    def _used(self) -> set[str]:
        return {row["challenge_id"] for row in self.state.read("red") if row.get("admitted")}

    def _candidate(self, ch: Challenge) -> dict[str, Any]:
        verdict = self.simulator.verdict(ch.buggy_rtl, ch.carrier)
        profile = build_profile(verdict.feedback, analyze_rtl(ch.buggy_rtl))
        return {"candidate_id": ch.challenge_id, "design": ch.carrier.cluster_id, "fix_type": fix_type(ch),
                "failure_profile": profile.to_dict(), "changed_lines": changed_lines(ch)}

    def _random_choice(self, ch: Challenge, r: int, i: int) -> RedProposal:
        """The baseline's pick: ``available`` is already in a seeded random order."""
        return RedProposal(_random_record(ch, r, i, self.simulator.stealth(ch.buggy_rtl, ch.carrier)), ch)

    def _propose_slots(self, r: int) -> list[_Slot]:
        state, cfg = self.state, self.config
        discovery = {c.cluster_id for c in self.splits["discovery"]}
        slots: list[_Slot] = []
        for i in range(cfg.proposals_per_round):
            used = self._used()
            available = sorted((ch for ch in self.pool.values()
                                if ch.carrier.cluster_id in discovery and ch.challenge_id not in used),
                               key=lambda ch: hash_payload({"s": self._seed("pool", r, i), "c": ch.challenge_id}))
            if not available:
                break
            if self.red.mode == "random":
                proposal = self._random_choice(available[0], r, i)
                state.append("red", proposal.record)
                slots.append(_Slot(proposal.challenge, proposal.record))
                continue
            open_lins = [lin for lin in lineages(state).values()
                         if lin["status"] in OPEN and lin["lineage_id"] not in self._claimed]
            pursuable = [lin for lin in open_lins if any(
                f"type:{fix_type(ch)}" in lin["operators"] and ch.carrier.cluster_id not in lin["clusters"]
                for ch in available)][: self.curriculum.lineages_offered]
            # offer the bugs that could continue a weak point first, then others
            wanted = [ch for ch in available if any(f"type:{fix_type(ch)}" in lin["operators"]
                                                    and ch.carrier.cluster_id not in lin["clusters"]
                                                    for lin in pursuable)]
            offered = (wanted + [ch for ch in available if ch not in wanted])[:MAX_OFFERED]
            proposal = self.red.choose([self._candidate(ch) for ch in offered], state=state, offered=pursuable,
                                       seed=self._seed("red", r, i), round_index=r, challenges=self.pool)
            state.append("red", proposal.record)
            if proposal.challenge is not None:
                slots.append(_Slot(proposal.challenge, proposal.record))
                claimed = (proposal.record.get("decision") or {}).get("lineage_id")
                if claimed:
                    self._claimed.add(claimed)
        return slots


def _random_record(ch: Challenge, r: int, i: int, stealth: Mapping[str, Any]) -> dict[str, Any]:
    return {"round": r, "mode": "pool_random", "slot": i, "admitted": True, "reason": "admitted",
            "decision": {"lineage_id": None, "direction": "explore", "reason": "random"},
            "challenge_id": ch.challenge_id, "carrier_id": ch.carrier.carrier_id,
            "mutant_hash": hash_payload(ch.buggy_rtl),
            "chosen": {"hypothesis": "random baseline", "edit_kinds": [f"type:{fix_type(ch)}"],
                       "edit_text": changed_lines(ch), "stealth": stealth}}


def holdout_pool(loop: PoolCurriculumLoop) -> list[Challenge]:
    """Real bugs on the holdout designs (never presented in the loop)."""
    holdout = {c.cluster_id for c in loop.splits["holdout"]}
    return sorted((ch for ch in loop.pool.values() if ch.carrier.cluster_id in holdout),
                  key=lambda ch: ch.challenge_id)
