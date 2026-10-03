"""Curriculum red–blue loop: Red learns from Blue's failures, Blue from its fixes.

Neither side changes model weights. Each side's strategy is an external
memory that the loop rewrites from real outcomes:

- **Blue** keeps qualified repair knowledge, authored from its own verified
  fixes. It receives the matched items as advice and decides for itself
  whether to use them.
- **Red** keeps an *experience base* of weak points ("lineages"): the bugs
  Blue failed, and what happened to every later variant. Each Red call
  decides for itself whether to pursue a weak point or explore, and which
  direction to take: ``same`` (does Blue now master it?), ``harder`` (Blue
  solved the last variant with and without memory), or ``simpler`` (Blue
  could not fix it even with escalation, so it has nothing to learn from;
  teach with a simpler sibling, then step back up).

The runner is only the referee:
- admission (as in ``red.py``);
- a variant must sit on a design cluster the lineage has not used, so
  mastery means transfer and not recall of a stored patch;
- a variant must use at least one of the lineage's operators, and a
  ``simpler`` variant is a single edit;
- at most one variant per weak point per round;
- mastery measurement and the call budget.

One round:
1. Red proposes on discovery carriers (one decision per call). Each slot
   uses, of the next three designs in rotation, the one where most open weak
   points can be pursued.
2. Pending knowledge candidates are qualified (paired, with vs without the
   candidate) on Red's most recent bugs (explorations and follow-ups), never
   on their own source bugs or source designs. These are Red-made bugs, not
   random mutants, and passing means the knowledge transferred to another
   design.
3. Blue repairs every challenge with its active memory (escalation allowed);
   each encounter becomes an episode.
4. ``same`` and ``harder`` variants get a mastery test: fresh paired runs with
   and without memory, no escalation. The memory arm reuses the encounter's
   within-budget outcome as its first run.
5. Lineages are updated: opened, mastered, too easy, learnable, stuck, or
   closed at ``max_generations``.
6. New knowledge is authored as candidates (qualified next round), and the
   usage monitor runs.

Cross-model check (``cross_model_blues``): the mastery test can be repeated,
with the same memory and seeds, by other Blue models. This answers whether
the memory helps only the model it was learned with. These runs are
measurement only: lineage status and Red's view use the loop's own Blue.

In-loop mastery is a learning curve: the gate used the same variants. The
generalisation claim comes from ``holdout_variants``: Red makes one variant
per weak point on holdout designs that the loop never touched. Those are
evaluated with and without the final memory.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from r3e.protocol.hashing import canonical_json, hash_payload

from .blue import first_attempt
from .corpus import Carrier, Challenge
from .operators import enumerate_sites
from .orchestrator import ClosedLoop, _next_version
from .qualification import QualificationSet, affected_cases, monitor_usage, qualify_candidate
from .memory_usage import record_memory_usage, track_record_view, usage_stats
from .red import (
    MAX_CANDIDATES,
    RED_AWARE_NOTE,
    RED_SYSTEM,
    RedAgent,
    RedProposal,
    blue_state_summary,
    provider_failure,
    sample_sites,
)
from .state import RunState

MAX_EDITS_HARDER = 3  # a "harder" variant may compose up to three catalog edits


DIRECTIONS = ("explore", "same", "harder", "simpler")
OPEN = ("learnable", "stuck", "too_easy", "taught")
CLOSED = ("mastered", "unmastered")

RED_CURRICULUM_NOTE = (
    "experience_base lists Blue's open weak points: bugs you made earlier that Blue failed, and what "
    "happened to each later variant (with_memory / without_memory are Blue's solves out of runs). You "
    "decide this call. Either explore a new kind of bug, or pursue one weak point by making a variant "
    "of its mechanism on this different design, using at least one of its operators, never a copy. "
    "Directions: 'same' = equal difficulty, tests whether Blue has now mastered it; 'harder' = Blue "
    "solved the last variant with and without memory, so strengthen it (a 'harder' variant may compose up to "
    "3 edits); 'simpler' = Blue could not fix "
    "it even with extra attempts, so it cannot learn from it yet: make a simpler single-edit sibling it "
    "can fix and learn from, then step back up later. mechanism_coverage counts, per operator, the bugs you made "
    "with it and how Blue fared, so you can see which kinds of change are well covered and which are not. Return strict JSON: {\"decision\": {\"lineage_id\": "
    "id or null, \"direction\": \"explore\"|\"same\"|\"harder\"|\"simpler\", \"reason\": short}, "
    "\"candidates\": [...as above...]}."
)


@dataclass(frozen=True)
class CurriculumConfig:
    mastery_repeats: int = 3       # runs per arm in the mastery test
    max_generations: int = 4       # variants per lineage before it closes as unmastered
    lineages_offered: int = 4      # weak points shown to Red per call
    gate_window_challenges: int = 12  # the most recent admitted Red bugs are qualification cases
    pending_rounds: int = 2        # rounds a candidate may wait for coverage before rejection


# ------------------------------------------------------------ lineages


def lineages(state: RunState) -> dict[str, dict[str, Any]]:
    """Fold the ``lineages`` ledger into the current experience base."""
    out: dict[str, dict[str, Any]] = {}
    for row in state.read("lineages"):
        lid = row["lineage_id"]
        if row["event"] == "open":
            out[lid] = {"lineage_id": lid, "opened_round": row["round"], "operators": list(row["operators"]),
                        "clusters": [row["cluster_id"]], "status": row["status"], "generation": 0,
                        "steps": [row["step"]]}
        elif row["event"] == "step":
            lin = out[lid]
            lin["clusters"].append(row["cluster_id"])
            lin["generation"] += 1
            lin["status"] = row["status"]
            lin["steps"].append(row["step"])
    return out


def _outcome(enc) -> str:
    if enc.solved_within_budget:
        return "fixed_within_budget"
    return "fixed_only_by_escalation" if enc.solved_by_escalation else "not_fixed"


def red_view(lin: Mapping[str, Any]) -> dict[str, Any]:
    """What Red may see about a weak point: its own edits and Blue's outcome counts."""
    return {
        "lineage_id": lin["lineage_id"],
        "status": lin["status"],
        "operators": lin["operators"],
        "designs_used": list(lin["clusters"]),
        "generation": lin["generation"],
        "history": [{**{k: step.get(k) for k in ("round", "direction", "hypothesis", "edits", "blue_outcome")},
                     "mastery": {k: v for k, v in (step.get("mastery") or {}).items() if k != "by_model"}
                     or None} for step in lin["steps"]],
    }


def mechanism_coverage(state: RunState) -> dict[str, dict[str, int]]:
    """Per catalog operator: Red's admitted bugs using it and how Blue fared (real records only)."""
    outcome = {}
    for row in state.read("encounters"):
        e = row["encounter"]
        if e.get("inconclusive"):
            continue
        tries = [a for a in e.get("attempts", []) if not a.get("infra_failure")]
        first_ok = bool(tries) and tries[0].get("verdict_tier") in {"visible_pass", "hidden_pass"}
        outcome[e["challenge_id"]] = ("fixed_first_try" if first_ok else "fixed_later" if e["solved_within_budget"]
                                      else "fixed_by_escalation" if e["solved_by_escalation"] else "not_fixed")
    out: dict[str, dict[str, int]] = {}
    for row in state.read("red"):
        if not row.get("admitted") or row.get("round", -1) < 0 or row["challenge_id"] not in outcome:
            continue
        for op in set(row["chosen"]["edit_kinds"]):
            s = out.setdefault(op, {"bugs": 0, "fixed_first_try": 0, "fixed_later": 0,
                                    "fixed_by_escalation": 0, "not_fixed": 0})
            s["bugs"] += 1
            s[outcome[row["challenge_id"]]] += 1
    return dict(sorted(out.items()))


def eligible(lin: Mapping[str, Any], carrier: Carrier, sites) -> bool:
    return (lin["status"] in OPEN and carrier.cluster_id not in lin["clusters"]
            and any(s.operator in lin["operators"] for s in sites))


# ----------------------------------------------------------------- Red


class CurriculumRed(RedAgent):
    """Policy-aware Red that also decides which weak point to pursue, and how."""

    def propose_curriculum(self, carrier: Carrier, *, state: RunState, offered: Sequence[Mapping[str, Any]],
                           seed: int, round_index: int, context: Mapping[str, Any] | None = None) -> RedProposal:
        all_sites = enumerate_sites(carrier)
        sites = sample_sites(all_sites, seed=seed)
        # sites of the offered weak points' operators are always offered
        wanted = {op for lin in offered for op in lin["operators"]}
        extra = [s for s in all_sites if s.operator in wanted and s not in sites][:8]
        sites = sorted([*sites, *extra], key=lambda s: s.start)
        record: dict[str, Any] = {"round": round_index, "mode": "curriculum", "carrier_id": carrier.carrier_id,
                                  "seed": seed, "sites_offered": len(sites),
                                  "offered_lineages": [lin["lineage_id"] for lin in offered]}
        if context:
            record.update(context)
        if not sites:
            record.update(admitted=False, reason="no_sites")
            return RedProposal(record, None)
        user = {"carrier_rtl": carrier.clean_rtl, "top_module": carrier.top_module,
                "sites": [s.public() for s in sites],
                "current_blue_state": blue_state_summary(state),
                "experience_base": {"open_weak_points": [red_view(lin) for lin in offered],
                                    "mechanism_coverage": mechanism_coverage(state)}}
        from .operators import CATALOG
        user["operator_catalog"] = CATALOG
        system = " ".join([RED_SYSTEM, RED_AWARE_NOTE, RED_CURRICULUM_NOTE])
        try:
            with self.client.in_phase("red"):
                response = self.client.complete_json(
                    messages=[{"role": "system", "content": system},
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
        by_id = {lin["lineage_id"]: lin for lin in offered}
        if direction not in DIRECTIONS or (lineage_id is not None and lineage_id not in by_id) \
                or (lineage_id is None) != (direction == "explore"):
            record.update(admitted=False, reason="invalid_decision")
            return RedProposal(record, None)
        lin = by_id.get(lineage_id)

        def referee(entry, resolved):
            if lin is None:
                return None
            if not any(site.operator in lin["operators"] for site, _ in resolved):
                return "not_lineage_mechanism"
            if direction == "simpler" and len(resolved) != 1:
                return "simpler_must_be_single_edit"
            return None

        candidates = result.get("candidates") if isinstance(result.get("candidates"), list) else []
        candidates = [c for c in candidates if isinstance(c, dict)][:MAX_CANDIDATES]
        best = self._evaluate(carrier, sites, candidates, record, check=referee,
                              prefer="most_visible" if direction == "simpler" else "stealthiest",
                              max_edits=MAX_EDITS_HARDER if direction == "harder" else 2)
        if best is None:
            return RedProposal(record, None)
        _, chosen, mutant, shape = best
        self.seen.add(shape)
        challenge_id = "RC_" + hash_payload({"carrier": carrier.carrier_id,
                                             "mutant": hash_payload(mutant)}).split(":", 1)[1][:14]
        record.update(admitted=True, reason="admitted", challenge_id=challenge_id,
                      mutant_hash=hash_payload(mutant), chosen=chosen,
                      choice={"hypothesis": chosen["hypothesis"], "operator": "+".join(chosen["edit_kinds"])})
        return RedProposal(record, Challenge(
            challenge_id=challenge_id, carrier=carrier, buggy_rtl=mutant, origin="red",
            provenance={"round": round_index, "red_mode": "curriculum", "edit_kinds": chosen["edit_kinds"],
                        "lineage_id": lineage_id, "direction": direction},
        ))


# ---------------------------------------------------------------- loop


@dataclass
class _Slot:
    challenge: Challenge
    record: dict[str, Any]


class CurriculumLoop(ClosedLoop):
    def __init__(self, *, curriculum: CurriculumConfig | None = None,
                 cross_model_blues: Mapping[str, Any] | None = None, **kwargs: Any):
        self.curriculum = curriculum or CurriculumConfig()
        self.cross_model_blues = dict(cross_model_blues or {})
        kwargs.setdefault("qualification_challenges", [])  # cases come from Red's variants
        super().__init__(**kwargs)
        self.red = CurriculumRed(mode="aware", client=kwargs["red_client"], simulator=self.simulator,
                                 seed=self.config.seed)
        self._challenges: dict[str, Challenge] = {}
        self._claimed: set[str] = set()
        records = lambda: {k: track_record_view(v) for k, v in usage_stats(self.state).items()}
        for runner in self.cross_model_blues.values():
            runner.track_records = records
        self._rebuild_challenges()

    def _extra_frozen(self) -> dict[str, Any]:
        return {"curriculum": dict(self.curriculum.__dict__), "cross_models": sorted(self.cross_model_blues)}

    # -- helpers
    def _offered(self, carrier: Carrier) -> list[dict[str, Any]]:
        sites = enumerate_sites(carrier)
        open_lins = [lin for lin in lineages(self.state).values()
                     if eligible(lin, carrier, sites) and lin["lineage_id"] not in self._claimed]
        open_lins.sort(key=lambda lin: (lin["steps"][-1]["round"], lin["lineage_id"]))
        return open_lins[: self.curriculum.lineages_offered]

    def _gate_pool(self, r: int) -> list[Challenge]:
        """The most recent admitted Red bugs, explorations and follow-ups alike.

        Counted in bugs, not rounds, so a round without admitted bugs does not
        empty the pool. Holdout variants (round < 0) never enter it.
        """
        rows = [row for row in self.state.read("red")
                if row.get("admitted") and 0 <= row["round"] <= r and row["challenge_id"] in self._challenges]
        keep = [row["challenge_id"] for row in rows][-self.curriculum.gate_window_challenges:]
        return [self._challenges[cid] for cid in dict.fromkeys(keep)]

    def _source_challenges(self, item) -> set[str]:
        hashes = set(item.payload["evidence"].get("source_episode_hashes") or [])
        return {row["encounter"]["challenge_id"] for row in self.state.read("encounters")
                if row["encounter"]["episode_hash"] in hashes}

    def _source_clusters(self, sources: set[str]) -> set[str]:
        return {self._challenges[cid].carrier.cluster_id for cid in sources if cid in self._challenges}

    def _authored_round(self, item) -> int:
        for row in self.state.read("rounds"):
            if f"{item.item_id}@v{item.version}" in row.get("authored", []):
                return row["round"]
        return -1

    def _fork_pairs(self, runner, ch: Challenge, active, *, tag: str, phase: str) -> list[dict | None]:
        """Mastery runs as forks: one first attempt without memory, then, if it
        failed, the same attempt continued without and with memory."""
        state, pairs = self.state, []
        for k in range(self.curriculum.mastery_repeats):
            seed = self._seed("mastery", ch.challenge_id, tag, k)
            without = runner.run(ch, mode="none", pool=[], inference=state.inference(), matcher=state.matcher,
                                 seed=seed, allow_escalation=False, phase=phase)
            first = first_attempt(without)
            if without.inconclusive or first is None:
                pairs.append(None)
                continue
            if first.get("verdict_tier") in {"visible_pass", "hidden_pass", "formal_pass"}:
                pairs.append({"with": True, "without": True, "delivered": False, "first_attempt": True})
                continue
            with_run = runner.run(ch, mode="matched", pool=active, inference=state.inference(),
                                  matcher=state.matcher, seed=seed, allow_escalation=False, phase=phase,
                                  reuse_first=first)
            pairs.append(None if with_run.inconclusive else {
                "with": with_run.solved_within_budget, "without": without.solved_within_budget,
                "delivered": bool(with_run.record()["shown_items"]), "first_attempt": False})
        return pairs

    @staticmethod
    def _summarise(pairs: list[dict | None]) -> dict[str, Any]:
        valid = [p for p in pairs if p]
        m, n = sum(p["with"] for p in valid), sum(p["without"] for p in valid)
        return {"memory_used": any(p["delivered"] for p in valid),
                "with_memory": f"{m}/{len(valid)}", "without_memory": f"{n}/{len(valid)}",
                "fixed_on_first_attempt": sum(p["first_attempt"] for p in valid),
                "_m": m, "_mn": len(valid), "_n": n, "_nn": len(valid)}

    def _mastery(self, ch: Challenge, enc, r: int) -> dict[str, Any]:
        """Fork-paired runs on a follow-up variant (see ``_fork_pairs``)."""
        active = self.state.active_items()
        result = self._summarise(self._fork_pairs(self.blue, ch, active, tag="primary", phase="mastery"))
        by_model = {}
        for label, runner in sorted(self.cross_model_blues.items()):
            summary = self._summarise(self._fork_pairs(runner, ch, active, tag="primary", phase="cross_model"))
            by_model[label] = {k: v for k, v in summary.items() if not k.startswith("_")}
        if by_model:
            result["by_model"] = by_model
        return result

    def _status(self, direction: str, enc, mastery: Mapping[str, Any] | None) -> str:
        if direction == "simpler":
            return "taught" if (enc.solved_within_budget or enc.solved_by_escalation) else "stuck"
        thr = math.ceil(2 * self.curriculum.mastery_repeats / 3)
        if mastery and mastery["_mn"] and mastery["_m"] >= thr:
            if mastery["memory_used"] and mastery["_m"] / mastery["_mn"] > mastery["_n"] / max(1, mastery["_nn"]):
                return "mastered"
            return "too_easy"
        return "learnable" if enc.solved_by_escalation else "stuck"

    # -- round
    def _propose_slots(self, r: int) -> list[_Slot]:
        """Red's bugs for round ``r`` (catalog variants and explorations)."""
        state, cfg = self.state, self.config
        discovery = self.splits["discovery"]
        slots: list[_Slot] = []
        for i in range(cfg.proposals_per_round):
            # scheduling (referee): of the next few designs in rotation, use the
            # one where most open weak points can be pursued; Red decides what to do
            start = r * cfg.proposals_per_round + i
            window = [discovery[(start + k) % len(discovery)] for k in range(min(3, len(discovery)))]
            carrier = max(window, key=lambda c: (len(self._offered(c)), -window.index(c)))
            proposal = self.red.propose_curriculum(carrier, state=state, offered=self._offered(carrier),
                                                   seed=self._seed("red", r, i), round_index=r)
            state.append("red", proposal.record)
            if proposal.challenge is not None:
                self._challenges[proposal.challenge.challenge_id] = proposal.challenge
                slots.append(_Slot(proposal.challenge, proposal.record))
                claimed = (proposal.record.get("decision") or {}).get("lineage_id")
                if claimed:
                    self._claimed.add(claimed)
        return slots

    def round(self, r: int) -> dict[str, Any]:
        state, cfg, cur = self.state, self.config, self.curriculum
        self._claimed: set[str] = set()  # referee: one variant per weak point per round
        slots = self._propose_slots(r)

        # gate: pending candidates on Red's recent bugs, never on their own
        # source bugs or source designs (so passing means transfer)
        qset = QualificationSet.build(self.blue, self._gate_pool(r))
        decisions, baseline_cache = [], {}
        for candidate in state.store.items_with_status("candidate"):
            sources = self._source_challenges(candidate)
            clusters = self._source_clusters(sources)
            keep = [c for c in qset.challenges
                    if c.challenge_id not in sources and c.carrier.cluster_id not in clusters]
            cases = QualificationSet(keep, {c.challenge_id: qset.profiles[c.challenge_id] for c in keep})
            waited = r - self._authored_round(candidate)
            if not affected_cases(candidate, state=state, qset=cases, seed=cfg.qualification.seed) \
                    and waited < cur.pending_rounds:
                decisions.append({"item_id": candidate.item_id, "version": candidate.version,
                                  "decision": "pending", "reason": "no_coverage_yet"})
                continue
            report = qualify_candidate(candidate, runner=self.blue, state=state, qset=cases,
                                       config=cfg.qualification, round_index=r, baseline_cache=baseline_cache,
                                       pending_if_undelivered=waited < cur.pending_rounds)
            decisions.append({k: report[k] for k in ("item_id", "version", "decision", "reason",
                                                     "coverage", "helped", "harmed")})
            if report["decision"] == "active":
                for older in state.active_items():
                    if older.item_id == candidate.item_id and older.version < candidate.version:
                        state.store.transition(older.item_id, older.version, "retired",
                                               reason=f"superseded by v{candidate.version}")

        # Blue with its current memory; lineage bookkeeping
        solved = escalated = 0
        lineage_events = []
        outcomes: dict[str, int] = {}
        for slot in slots:
            ch, rec = slot.challenge, slot.record
            pool = state.active_items()
            enc = self.blue.run(ch, mode="matched", pool=pool, inference=state.inference(),
                                matcher=state.matcher, seed=self._seed("blue", ch.challenge_id),
                                allow_escalation=True)
            if not enc.inconclusive:
                state.record_episode(enc.episode, round_index=r)
                use = record_memory_usage(state, enc, round_index=r, pool=pool)
                outcomes[use["outcome"]] = outcomes.get(use["outcome"], 0) + 1
            state.append("encounters", {"round": r, "encounter": enc.record()})
            solved += enc.solved_within_budget
            escalated += enc.solved_by_escalation
            if enc.inconclusive:
                continue
            decision = rec.get("decision") or {}
            lid, direction = decision.get("lineage_id"), decision.get("direction", "explore")
            step = {"round": r, "challenge_id": ch.challenge_id, "carrier_id": ch.carrier.carrier_id,
                    "direction": direction, "hypothesis": rec["chosen"]["hypothesis"],
                    "edits": rec["chosen"].get("edit_text"), "blue_outcome": _outcome(enc)}
            if lid is None:
                if enc.solved_within_budget:
                    continue  # too simple: not a weak point
                status = "learnable" if enc.solved_by_escalation else "stuck"
                event = {"event": "open", "lineage_id": "L_" + ch.challenge_id[3:], "round": r,
                         "operators": sorted(set(rec["chosen"]["edit_kinds"])),
                         "cluster_id": ch.carrier.cluster_id, "status": status, "step": step}
            else:
                mastery = None
                if direction in ("same", "harder"):
                    mastery = self._mastery(ch, enc, r)
                    step["mastery"] = {k: v for k, v in mastery.items() if not k.startswith("_")}
                status = self._status(direction, enc, mastery)
                lin = lineages(state)[lid]
                if status not in CLOSED and lin["generation"] + 1 >= cur.max_generations:
                    status = "unmastered"
                event = {"event": "step", "lineage_id": lid, "round": r, "cluster_id": ch.carrier.cluster_id,
                         "status": status, "step": step}
            state.append("lineages", event)
            lineage_events.append({"lineage_id": event["lineage_id"], "event": event["event"],
                                   "direction": direction, "status": event["status"]})

        # Blue's memory update: new candidates, qualified next round
        authored = sorted(self.author.author(state.episodes()),
                          key=lambda i: (-i.payload["evidence"]["support"], i.item_id))
        new = []
        for item in authored:
            stored = _next_version(state, item)
            if stored is not None:
                state.store.add(stored, reason=f"authored in round {r}")
                new.append(f"{stored.item_id}@v{stored.version}")
            if len(new) >= cfg.max_candidates_per_round:
                break
        suspended = monitor_usage(state, cfg.monitor, round_index=r)
        lins = lineages(state).values()
        summary = {
            "round": r, "proposals": cfg.proposals_per_round, "admitted": len(slots),
            "followups": sum(1 for s in slots if (s.record.get("decision") or {}).get("lineage_id")),
            "solved_within_budget": solved, "solved_by_escalation": escalated,
            "memory_outcomes": outcomes,
            "lineage_events": lineage_events,
            "lineages_by_status": {s: sum(1 for lin in lins if lin["status"] == s) for s in (*OPEN, *CLOSED)},
            "candidates": len(new), "authored": new, "decisions": decisions, "suspended": suspended,
            "active": [[i.item_id, i.version, i.item_hash] for i in state.active_items()],
            "episodes_total": len(state.read("episodes")), "cost": self.blue.budget.report(),
        }
        state.append("rounds", summary)
        return summary

    def _rebuild_challenges(self) -> None:
        """Re-create admitted Red challenges from the ledger (resume)."""
        from .operators import apply_edits, resolve_recorded_edits
        by_id = {c.carrier_id: c for split in self.splits.values() for c in split}
        for row in self.state.read("red"):
            if not row.get("admitted") or row.get("carrier_id") not in by_id:
                continue
            carrier = by_id[row["carrier_id"]]
            try:
                mutant = apply_edits(carrier, resolve_recorded_edits(carrier, row["chosen"], row["mutant_hash"]))
            except ValueError:
                continue
            if hash_payload(mutant) != row["mutant_hash"]:
                continue
            decision = row.get("decision") or {}
            self._challenges[row["challenge_id"]] = Challenge(
                challenge_id=row["challenge_id"], carrier=carrier, buggy_rtl=mutant, origin="red",
                provenance={"round": row["round"], "red_mode": "curriculum",
                            "edit_kinds": row["chosen"]["edit_kinds"], "lineage_id": decision.get("lineage_id"),
                            "direction": decision.get("direction")})


# ------------------------------------------------------------- holdout


def holdout_variants(loop: CurriculumLoop, carriers: Sequence[Carrier], *, seed: int) -> list[Challenge]:
    """One Red variant per weak point on holdout designs (direction ``same``).

    Red sees only its discovery-side experience base. Nothing from these
    challenges is written back to encounters, episodes or lineages.
    """
    out = []
    done = {row["holdout_for"]: row for row in loop.state.read("red") if row.get("holdout_for")}
    for index, lin in enumerate(sorted(lineages(loop.state).values(), key=lambda l: l["lineage_id"])):
        if lin["lineage_id"] in done:  # resume: reuse the recorded variant, no new call
            row = done[lin["lineage_id"]]
            if row.get("admitted") and row["challenge_id"] in loop._challenges:
                out.append(loop._challenges[row["challenge_id"]])
            continue
        for carrier in sorted(carriers, key=lambda c: hash_payload({"s": seed, "l": lin["lineage_id"],
                                                                     "c": c.carrier_id})):
            if not eligible({**lin, "status": "learnable"}, carrier, enumerate_sites(carrier)):
                continue
            proposal = loop.red.propose_curriculum(
                carrier, state=loop.state, offered=[{**lin, "status": lin["status"]}],
                seed=loop._seed("holdout", seed, index), round_index=-2,
                context={"holdout_for": lin["lineage_id"]})
            loop.state.append("red", proposal.record)
            if proposal.challenge is not None and (proposal.record.get("decision") or {}).get("lineage_id"):
                loop._challenges[proposal.challenge.challenge_id] = proposal.challenge
                out.append(proposal.challenge)
            break
    return out
