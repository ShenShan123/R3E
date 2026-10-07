"""The bounded 94-call Red diagnostic. No alternative Blue or scoring implementation."""
from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path

from r3e.protocol.hashing import atomic_write_json, hash_payload
from r3e.protocol.ledger import append_ledger, read_ledger

from .blue import BlueRunner
from .corpus import Challenge
from .curriculum import CurriculumConfig, lineages
from .freeform_curriculum import (FreeformCurriculumLoop, FreeformRed, public_weakness_view,
                                 VIEW_VERSION, SEARCH_VERSION, SEARCH_SELECT_SYSTEM)
from .red_design import EditBudget, validate_design, SYSTEM_PROMPT, SEARCH_INSTRUCTION
from .red_observations import observe_encounter, OBSERVATION_VERSION
from .red_repair_feedback import PublicCandidateRecorder
from .sim import Simulator
from .state import RunState

PILOT_VERSION = "red_search_pilot_v1"
PILOT_BOUND = {"seed_validation": 9, "cold": 22, "warm": 55, "reserve": 8, "total_upper_bound": 94}


def load_seed(run: Path, decisions: Path, challenge_id: str, splits, budget=EditBudget()):
    """Bind an existing PASS to exact source assets; no historical state is imported."""
    rows = read_ledger(Path(run) / "red.jsonl")
    found = [r for r in rows if r.get("challenge_id") == challenge_id and r.get("admitted")]
    if len(found) != 1:
        raise ValueError("seed must identify exactly one admitted historical proposal")
    review = json.loads(Path(decisions).read_text())
    if review.get(challenge_id, {}).get("decision") != "PASS":
        raise ValueError("seed requires a PASS review")
    row = found[0]
    carriers = {c.carrier_id: c for group in splits.values() for c in group}
    carrier = carriers.get(row["carrier_id"])
    if carrier is None:
        raise ValueError("seed carrier is not in the current eligible corpus")
    if carrier.cluster_id not in {c.cluster_id for c in splits["discovery"]}:
        raise ValueError("seed cluster is not discovery; choose an explicit prospective split before running")
    rtl = row["chosen"]["buggy_rtl"]
    if (row.get("clean_rtl_hash") != hash_payload(carrier.clean_rtl)
            or row.get("spec_hash") != hash_payload(carrier.spec)
            or row.get("mutant_hash") != hash_payload(rtl)):
        raise ValueError("seed RTL/spec hashes differ from the reviewed source")
    validate_design(carrier.clean_rtl, rtl, carrier.top_module, budget)
    chosen = row["chosen"]
    mechanism = (chosen.get("target_weakness") or {}).get("mechanism") or chosen.get("hypothesis")
    if not isinstance(mechanism, str) or not mechanism.strip():
        raise ValueError("seed has no repair mechanism hypothesis")
    provenance = {"challenge_id": challenge_id, "carrier_id": carrier.carrier_id,
                  "cluster_id": carrier.cluster_id, "clean_rtl_hash": row["clean_rtl_hash"],
                  "spec_hash": row["spec_hash"], "mutant_hash": row["mutant_hash"],
                  "source_record_hash": hash_payload(row),
                  "source_ledger_hash": hash_payload(Path(run, "red.jsonl").read_text()),
                  "review_hash": hash_payload(review), "review": review[challenge_id],
                  "mechanism": mechanism, "red_hypothesis": chosen.get("hypothesis"),
                  "actual_diff": chosen.get("actual_diff"),
                  "historical_blue_settings": "not inferred from a historical checkpoint hash",
                  "counts_as_new_hit": False}
    return Challenge(challenge_id, carrier, rtl, "red", {"seed_validation": True}), provenance


class SeededSearchLoop(FreeformCurriculumLoop):
    """Probe the revalidated seed for five slots; preserve negative follow-up evidence."""
    def __init__(self, *, seed_evidence, **kwargs):
        self.seed_evidence = seed_evidence
        self.seed_id = "seed_" + hash_payload(seed_evidence)[7:21]
        super().__init__(view_builder=self._seed_view, view_version="revalidated_seed_view_v1",
                         search=True, **kwargs)
        if self.seed_id not in lineages(self.state):
            self.state.append("lineages", {"event": "open", "lineage_id": self.seed_id,
                "round": -1, "cluster_id": seed_evidence["cluster_id"], "status": "stuck",
                "operators": ["mechanism:" + hash_payload(seed_evidence["mechanism"])[7:21]],
                "seed_import": True, "counts_as_new_hit": False,
                "step": {"round": -1, "challenge_id": seed_evidence["challenge_id"],
                         "carrier_id": seed_evidence["carrier_id"], "direction": "seed",
                         "hypothesis": seed_evidence["mechanism"], "blue_runs": 3,
                         "blue_fails": seed_evidence["fails"], "seed_import": True}})

    def _extra_frozen(self):
        return {**super()._extra_frozen(), "seed_evidence_hash": hash_payload(self.seed_evidence),
                "pursuit_policy": "fixed_seed_five_slots_with_negative_feedback"}

    def _seed_view(self, state, offered, round_index):
        view = public_weakness_view(state, offered, round_index)
        if not offered:
            return view
        lin = offered[0]
        target = next((t for t in view["open_weak_points"] if t["weakness_id"] == self.seed_id), None)
        if target is None:
            target = {"weakness_id": self.seed_id, "mechanism": self.seed_evidence["mechanism"],
                      "mastery_status": lin["status"], "designs_used": list(lin["clusters"]),
                      "causal_chain": []}
        target["seed_evidence"] = self.seed_evidence
        target["mechanism_source"] = "reviewed seed hypothesis; follow-up outcomes may contradict transfer"
        view["open_weak_points"] = [target]
        return view

    def _offered(self, carriers):
        # A passed follow-up is negative evidence, not a reason to erase the
        # remaining predeclared probes of the original, independently validated seed.
        lin = lineages(self.state)[self.seed_id]
        return [lin] if any(c.cluster_id not in lin["clusters"] for c in carriers) else []

    def _propose_slots(self, r):
        self.red.require_pursuit = True
        return super()._propose_slots(r)


class FreeformPilot:
    def __init__(self, *, root: Path, config, splits, seed_challenge, seed_provenance,
                 blue_client, red_client, project_root, provider_settings, edit_budget=EditBudget(),
                 design_candidates=8):
        if (config.learning or config.blue.budget_k != 3 or config.blue.escalation_k != 0
                or config.proposals_per_round != 1 or config.red_mode != "aware"):
            raise ValueError("pilot requires aware Red, no learning/escalation, k=3, one proposal per round")
        if not 0 < blue_client.max_calls <= 94 or blue_client.calls is not red_client.calls:
            raise ValueError("pilot needs one shared positive call cap no greater than 94")
        groups = {k: {c.cluster_id for c in group} for k, group in splits.items()}
        names = list(groups)
        if any(groups[a] & groups[b] for i, a in enumerate(names) for b in names[i+1:]):
            raise ValueError("pilot splits overlap by design cluster")
        self.root, self.config, self.splits = Path(root), config, splits
        self.root.mkdir(parents=True, exist_ok=True)
        self.seed, self.provenance = seed_challenge, seed_provenance
        self.budget, self.red_client = blue_client, red_client
        self.project_root, self.edit_budget = project_root, edit_budget
        self.design_candidates = design_candidates
        self.events_path = self.root / "pilot_events.jsonl"
        self.cur = CurriculumConfig(confirm_runs=2, reproducible_fails=2, max_generations=6)
        frozen = {"version": PILOT_VERSION, "allocation": PILOT_BOUND, "cap": blue_client.max_calls,
                  "config": asdict(config), "providers": provider_settings,
                  "seed": seed_provenance, "budget": asdict(edit_budget),
                  "design_candidates": design_candidates, "curriculum": asdict(self.cur),
                  "view_version": VIEW_VERSION, "observation_version": OBSERVATION_VERSION,
                  "search_version": SEARCH_VERSION,
                  "client_settings": {name: {k: getattr(client.config, k, None) for k in
                       ("model_id", "model_version", "temperature", "maximum_output_tokens")}
                       for name, client in (("blue", blue_client), ("red", red_client))},
                  "output_caps": dict(blue_client.output_caps),
                  "implementation_hashes": {name: hash_payload((Path(__file__).parent / name).read_text())
                      for name in ("freeform_pilot.py", "freeform_curriculum.py", "red_design.py",
                                   "red_observations.py", "red_target_check.py", "red_repair_feedback.py",
                                   "red_design_context.py",
                                   "blue.py", "blue_provider.py", "sim.py",
                                   "../knowledge/feedback.py", "../knowledge/profile.py")},
                  "red_prompt_hash": hash_payload([SEARCH_SELECT_SYSTEM, SYSTEM_PROMPT, SEARCH_INSTRUCTION]),
                  "splits": {k: [{"carrier_id": c.carrier_id, "cluster_id": c.cluster_id,
                      "rtl_hash": hash_payload(c.clean_rtl), "spec_hash": hash_payload(c.spec),
                      "test_deps_hashes": [hash_payload(p.read_text()) for p in
                                            (*c.visible_tb, *c.hidden_tb, *c.deps)]}
                                  for c in group] for k, group in splits.items()}}
        path = self.root / "pilot_config.json"
        if path.exists() and json.loads(path.read_text()) != frozen:
            raise RuntimeError("pilot resume refused: frozen inputs/settings changed")
        if not path.exists():
            if read_ledger(self.root / "calls.jsonl"):
                raise RuntimeError("pilot needs a fresh directory, not an existing experiment")
            atomic_write_json(path, frozen)
        self._restore_cost()

    def _restore_cost(self):
        rows = read_ledger(self.root / "calls.jsonl")
        if self.budget.total_calls:
            if self.budget.total_calls != len(rows):
                raise RuntimeError("in-memory and persistent call counts differ")
            return
        for row in rows:
            phase = row["phase"]
            self.budget.calls[phase] += 1
            self.budget.failed[phase] += not row.get("ok")
            self.budget.tokens[phase]["input"] += row.get("input_tokens", 0)
            self.budget.tokens[phase]["output"] += row.get("output_tokens", 0)

    def _bind(self, branch, state, round_index):
        def on_call(row):
            tagged = {**row, "branch": branch, "round": round_index}
            append_ledger(self.root / "calls.jsonl", tagged)
            state.append("calls", tagged)
        self.budget.on_call = self.red_client.on_call = on_call

    def _step(self, key, required, action):
        events = [r for r in read_ledger(self.events_path) if r["step"] == key]
        if events:
            if events[-1]["status"] == "complete":
                return events[-1]["result"]
            # A lost response/partial Blue screen cannot be exactly reconstructed.
            # Never automatically spend calls again for an interrupted transaction.
            raise RuntimeError(f"pilot interrupted during {key}; partial results are inconclusive; automatic replay refused")
        if self.budget.max_calls - self.budget.total_calls < required:
            return {"stopped": "insufficient_budget_for_complete_step"}
        append_ledger(self.events_path, {"step": key, "status": "started", "calls_before": self.budget.total_calls})
        result = action()
        append_ledger(self.events_path, {"step": key, "status": "complete", "result": result,
                                        "calls_after": self.budget.total_calls})
        return result

    def _runner(self, state):
        # A fresh process must not overwrite simulation artifacts from earlier calls.
        sim = Simulator(state.root / "sim_workspace" / f"calls_{self.budget.total_calls:06d}")
        blue = BlueRunner(json_client=self.budget, simulator=sim, project_root=self.project_root,
                          config=self.config.blue)
        return blue, sim

    def _validate_seed(self):
        state = RunState(self.root / "seed")
        blue, sim = self._runner(state)
        # Seed repairs are public outputs too. Keep their artifacts in seed/,
        # separate from discovery and from the historical source run.
        blue.simulator = PublicCandidateRecorder(sim, state.root)
        checker = FreeformRed(mode="aware", client=self.red_client, simulator=sim, edit_budget=self.edit_budget)
        admission = self._step("seed:admission", 0, lambda: {
            "reason": checker._admit(replace(self.seed.carrier, hidden_tb=()), self.seed.buggy_rtl)[0]})
        reason = admission["reason"]
        if reason != "admitted":
            return {"usable": False, "reason": "seed_" + reason, "runs": 0, "fails": 0}
        for i in range(3):
            def run(i=i):
                self._bind("seed_validation", state, i)
                enc = blue.run(self.seed, mode="none", pool=[], inference=state.inference(),
                               matcher=state.matcher, seed=self.config.seed + 10000 + i,
                               allow_escalation=False, phase="confirmation")
                state.append("encounters", {"round": i, "branch": "seed_validation",
                                             "carrier_id": self.seed.carrier.carrier_id,
                                             "counts_as_new_hit": False, "encounter": enc.record()})
                return {"completed": True}
            result = self._step(f"seed:{i}", 3 + self.config.blue.max_infra_retries, run)
            if result.get("stopped"):
                return {"usable": False, "runs": i, "reason": result["stopped"]}
        encounters = [r["encounter"] for r in state.read("encounters")]
        complete = [e for e in encounters if not e.get("inconclusive")]
        failed = [e for e in complete if not e["solved_within_budget"]]
        functional = any(observe_encounter(e)["functional_wrong_attempts"] for e in failed)
        usable = len(complete) == 3 and len(failed) >= 2 and functional
        return {"usable": usable, "runs": len(complete), "fails": len(failed),
                "reproducible": len(complete) == 3 and len(failed) >= 2,
                "reason": "revalidated" if usable else "not_reproduced_or_no_functional_failure_evidence",
                "counts_as_new_hit": False,
                "public_runs": [observe_encounter(e) for e in encounters]}

    def _branch(self, name, rounds, seed_result):
        state = RunState(self.root / name)
        blue, sim = self._runner(state)
        # A different bug type in the seed problem is not a transfer carrier.
        splits = {**self.splits, "discovery": [c for c in self.splits["discovery"]
                  if c.cluster_id != self.seed.carrier.cluster_id], "qualification": []}
        if not splits["discovery"]:
            return {"stopped": "no_nonseed_discovery_designs", "rounds": []}
        kwargs = dict(config=replace(self.config, rounds=rounds), state=state, splits=splits,
                      public_challenges=[], blue=blue, red_client=self.red_client, simulator=sim,
                      curriculum=self.cur, edit_budget=self.edit_budget, design_candidates=self.design_candidates)
        if name == "warm":
            evidence = {**self.provenance, "runs": seed_result["runs"], "fails": seed_result["fails"],
                        "public_runs": seed_result["public_runs"], "status": "revalidated_seed"}
            loop = SeededSearchLoop(seed_evidence=evidence, **kwargs)
        else:
            loop = FreeformCurriculumLoop(search=True, **kwargs)
        # Branch metadata is runner-owned and never delivered in Blue's prompt.
        original_append = state.append
        state.append = lambda ledger, row: original_append(ledger, {**row, "branch": name})
        completed = []
        for r in range(rounds):
            def run(r=r):
                self._bind(name, state, r)
                loop._round = r
                result = loop.round(r)  # original Blue, confirmation, and lineage rules
                state.save_checkpoint({"completed_rounds": r + 1, "frozen_hash": loop.frozen_hash})
                return {"round": r, "admitted": result["admitted"], "followups": result["followups"]}
            # Reserve a complete primary + two confirmation encounters, including
            # their configured infrastructure retries, before spending on Red.
            result = self._step(f"{name}:{r}", 2 + 3 * (3 + self.config.blue.max_infra_retries), run)
            completed.append(result)
            if result.get("stopped"):
                break
            if name == "warm" and r == 2 and not any(x.get("admitted") for x in completed):
                return {"stopped": "first_three_no_admitted_new_design_bug", "rounds": completed}
        return {"rounds": completed}

    def run(self):
        seed_result = self._validate_seed()
        cold = self._branch("cold", 2, seed_result)
        warm = (self._branch("warm", 5, seed_result) if seed_result.get("usable") else
                {"stopped": "seed_not_revalidated", "rounds": []})
        result = {"version": PILOT_VERSION, "allocation": PILOT_BOUND, "seed": seed_result,
                  "cold": cold, "warm": warm, "cost": self.budget.report(),
                  "hit_policy": "new candidate hits require independent PASS review; seed is never a new hit"}
        atomic_write_json(self.root / "pilot_result.json", result)
        return result
