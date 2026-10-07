"""Search/seed integration tests; local transports only, real RTL admission."""
from dataclasses import replace
import json

import pytest

from test_freeform_curriculum import Scripted, carriers, make_loop, needs_tools, ROOT
from r3e.loop.blue import BlueConfig
from r3e.loop.budget import BudgetedClient
from r3e.loop.corpus import Challenge
from r3e.loop.env import build_client
from r3e.loop.freeform_pilot import FreeformPilot, PILOT_BOUND, load_seed
from r3e.loop.freeform_curriculum import FreeformRed
from r3e.loop.orchestrator import LoopConfig
from r3e.loop.red_design import EditBudget
from r3e.loop.red_observations import discovery_observations, observe_encounter
from r3e.loop.sim import Simulator
from r3e.loop.state import RunState
from r3e.protocol.hashing import hash_payload
from r3e.protocol.ledger import append_ledger, read_ledger


class SearchScript(Scripted):
    def __init__(self, *, repaired=False, reject_warm=False, revise=False):
        super().__init__()
        self.repaired, self.reject_warm, self.revise = repaired, reject_warm, revise

    def __call__(self, **request):
        reply = super().__call__(**request)
        user = self.requests[-1]
        result = json.loads(reply["content"])
        if user.get("task") == "select_freeform_target":
            # Duplicate, invisible no-op, then a genuine structural fault.
            plan = result["mutation_plan"]
            result["alternative_plans"] = [dict(plan), {**plan, "edits": [
                {"find": "q <= 0", "replace": "q <= 0"}]}]
            result["repair_hypothesis"] = {"predicted_wrong_repair": "change the count boundary",
                "residual_failure": "disabled edges still update q", "source_observation_ids": []}
            if self.reject_warm and user["weakness_view"]["open_weak_points"]:
                result["weakness_id"] = None
                result["direction"] = "explore"
                result["skipped_weaknesses"] = {w["weakness_id"]: "no applicable design"
                                              for w in user["weakness_view"]["open_weak_points"]}
        elif "clean_rtl" in user:
            result.update(broken_invariant="q must hold on disabled edges",
                          predicted_wrong_repair="change the count boundary",
                          residual_failure="disabled edges still update q")
            if self.revise:
                result["buggy_rtl"] = user["clean_rtl"].replace("q <= q + 1", "q <= q + 2")
            result["predicted_wrong_repair_edits"] = ([{
                "find": "q <= q + 2", "replace": "q <= q + (q == 0 ? 1 : 2)"}]
                if self.revise else [{"find": "else begin",
                                      "replace": "else if (enable || q != 0) begin"}])
        elif self.repaired:
            result["replacement_rtl"] = user["current_buggy_rtl"].replace("else begin", "else if (enable) begin")
        reply["content"] = json.dumps(result)
        return reply


@pytest.mark.parametrize("tiers,solved,expected", [
    (["visible_pass"], True, "fixed_first_attempt"),
    (["no_answer", "visible_pass"], True, "fixed_after_no_answer"),
    (["visible_fail", "visible_pass"], True, "fixed_after_wrong_functional_repair"),
    (["compile_fail", "visible_pass"], True, "fixed_after_other_failure"),
    (["no_answer"], False, "unrepaired"),
])
def test_observation_does_not_turn_no_answer_into_a_wrong_repair(tiers, solved, expected):
    observation = observe_encounter({"attempts": [{"verdict_tier": t} for t in tiers],
                                     "solved_within_budget": solved})
    assert observation["outcome"] == expected
    assert observation["functional_wrong_attempts"] == tiers.count("visible_fail")


def test_bounded_observations_include_successes_rejections_but_not_hidden_future_or_holdout(tmp_path):
    state = RunState(tmp_path)
    for r in [*range(16), -2]:
        state.append("red", {"round": r, "challenge_id": str(r), "admitted": r != 4,
                             "reason": "admitted" if r != 4 else "does_not_compile",
                             "chosen": {"hypothesis": "search prediction", "hidden": "SECRET"}})
        state.append("encounters", {"round": r, "encounter": {"challenge_id": str(r),
            "solved_within_budget": True, "hidden": "SECRET", "attempts": [{
                "verdict_tier": "hidden_pass", "feedback": {"stage": "functional", "hidden": "SECRET"}}]}})
    offered = [{"steps": [{"round": r, "challenge_id": str(r)} for r in range(7)]}]
    view = discovery_observations(state, offered, 15)
    assert len(view["records"]) == 12 and view["omitted_proposals"] == 3
    assert view["total_prior_proposals"] == 15
    assert all(o["round"] < 15 and o["round"] >= 0 for o in view["records"])
    assert any(o["rejection_reason"] == "does_not_compile" for o in view["records"])
    assert "SECRET" not in json.dumps(view) and "hidden_pass" not in json.dumps(view)
    assert all(o["blue_observations"][0]["outcome"] == "fixed_first_attempt" for o in view["records"])


@needs_tools
@pytest.mark.parametrize("revise", [False, True])
def test_search_evaluates_candidates_without_consuming_duplicate_identity(tmp_path, revise):
    loop, state, script, _ = make_loop(tmp_path, rounds=1, search=True, script=SearchScript(revise=revise))
    loop.run()
    row = state.read("red")[0]
    assert row["admitted"]
    assert row["target_observability"]["status"] == "residual_observed"
    assert row["target_observability"]["blue_calls"] == 0
    assert row["chosen"]["predicted_wrong_repair_hash"] == hash_payload(
        row["chosen"]["predicted_wrong_repair_rtl"])
    checked = row["search_feedback"]["candidates"]
    assert [p["reason"] for p in checked[:2]] == ["admitted", "admitted"]
    assert checked[2]["reason"] == "edits_not_applicable"  # nonunique source span
    assert row["local_admission_checks"] == (2 if revise else 1)
    assert len(loop.red.seen) == 1
    assert len([r for r in state.read("calls") if r["phase"] == "red"]) == 2
    generated = next(q for q in script.requests if "clean_rtl" in q)
    assert generated["search_feedback"]["candidates"][0]["reason"] == "admitted"
    blue_requests = [q for q in script.requests if "current_buggy_rtl" in q]
    assert blue_requests and all("search_feedback" not in json.dumps(q) for q in blue_requests)


@needs_tools
def test_success_feedback_reaches_next_search_even_without_a_lineage(tmp_path):
    loop, state, script, _ = make_loop(tmp_path, search=True, script=SearchScript(repaired=True))
    loop.run()
    selections = [q for q in script.requests if q.get("task") == "select_freeform_target"]
    second = selections[1]["weakness_view"]
    assert second["open_weak_points"] == []
    assert second["observations"]["records"][0]["blue_observations"][0]["outcome"] == "fixed_first_attempt"
    patch = second["observations"]["records"][0]["blue_observations"][0]["attempts"][0]["actual_patch"]
    assert patch["status"] == "available" and "+  else if (enable) begin" in patch["diff"]
    assert patch["correctness"] == "visible_verdict_only"
    assert not state.read("lineages")


@needs_tools
def test_three_distinct_candidates_plus_one_revision_are_the_local_limit(tmp_path):
    class FourCandidates(SearchScript):
        def __call__(self, **request):
            reply = super().__call__(**request)
            user, result = self.requests[-1], json.loads(reply["content"])
            if user.get("task") == "select_freeform_target":
                result["alternative_plans"] = [{**result["mutation_plan"], "edits": [{
                    "find": "q <= q + 1", "replace": f"q <= q + {i}"}]} for i in (2, 3)]
            elif "clean_rtl" in user:
                result["buggy_rtl"] = user["clean_rtl"].replace("q <= q + 1", "q <= q + 4")
                result["predicted_wrong_repair_edits"] = [{
                    "find": "q <= q + 4", "replace": "q <= q + (q == 0 ? 1 : 4)"}]
            reply["content"] = json.dumps(result)
            return reply
    loop, state, _, _ = make_loop(tmp_path, rounds=1, search=True, script=FourCandidates())
    loop.run()
    row = state.read("red")[0]
    assert row["admitted"] and row["local_admission_checks"] == 4
    assert len(loop.red.seen) == 1


@needs_tools
def test_blind_search_never_reads_observation_ledger(tmp_path):
    state = RunState(tmp_path / "run")
    state.read = lambda name: (_ for _ in ()).throw(AssertionError("history read"))
    script = SearchScript()
    client = BudgetedClient(build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=script), max_calls=2)
    red = FreeformRed(mode="blind", client=client, simulator=Simulator(tmp_path / "sim"),
                      edit_budget=EditBudget(), search=True)
    result = red.propose_curriculum(carriers(tmp_path)[0], state=state, offered=[], seed=1, round_index=1)
    assert result.record["weakness_view"]["observations"]["records"] == []


@needs_tools
def test_invisible_portfolio_candidate_does_not_run_hidden_test(tmp_path):
    class InvisiblePlan(SearchScript):
        def __call__(self, **request):
            reply = super().__call__(**request)
            if self.requests[-1].get("task") == "select_freeform_target":
                result = json.loads(reply["content"])
                result["alternative_plans"] = []
                result["mutation_plan"]["edits"] = [{"find": "else if (enable) begin",
                                                     "replace": "else if (enable == 1'b1) begin"}]
                reply["content"] = json.dumps(result)
            return reply
    script = InvisiblePlan()
    client = BudgetedClient(build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=script), max_calls=2)
    red = FreeformRed(mode="aware", client=client, simulator=Simulator(tmp_path / "sim"),
                      edit_budget=EditBudget(), search=True)
    carrier = replace(carriers(tmp_path)[0], hidden_tb=(tmp_path / "must_not_read.v",))
    result = red.propose_curriculum(carrier, state=RunState(tmp_path / "run"),
                                   offered=[], seed=1, round_index=0)
    assert result.record["search_feedback"]["candidates"][0]["reason"] == "not_observable_by_visible_test"
    assert result.record["admitted"]  # final revision is a visible fault


def pilot_fixture(tmp_path, script=None, max_calls=94):
    designs = carriers(tmp_path)
    base = designs[0]
    designs += [replace(base, carrier_id=f"c{i}", cluster_id=f"cluster{i}",
                        clean_rtl=base.clean_rtl.replace("== 9", f"== {9-i}"),
                        spec=f"Count enabled edges through {9-i}, reset to zero.") for i in range(4, 8)]
    seed = Challenge("historical-seed", base, base.clean_rtl.replace("else if (enable) begin", "else begin"), "red")
    script = script or SearchScript()
    budget = BudgetedClient(build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=script), max_calls=max_calls)
    config = LoopConfig(rounds=5, proposals_per_round=1, red_mode="aware", learning=False,
                        blue=BlueConfig(budget_k=3, escalation_k=0))
    provenance = {"challenge_id": seed.challenge_id, "carrier_id": base.carrier_id,
                  "cluster_id": base.cluster_id, "mechanism": "misses an enable dependency",
                  "review": {"decision": "PASS"}, "counts_as_new_hit": False}
    kwargs = dict(root=tmp_path / "pilot", config=config,
                  splits={"discovery": designs[:7], "qualification": [], "holdout": designs[7:]},
                  seed_challenge=seed, seed_provenance=provenance, blue_client=budget, red_client=budget,
                  project_root=ROOT, provider_settings={"model": "fake-local"})
    return FreeformPilot(**kwargs), script, kwargs


@needs_tools
def test_pilot_94_bound_branch_isolation_transfer_and_exact_completed_resume(tmp_path):
    pilot, script, kwargs = pilot_fixture(tmp_path)
    result = pilot.run()
    assert result["seed"]["usable"] and result["seed"]["runs"] == result["seed"]["fails"] == 3
    assert len(result["cold"]["rounds"]) == 2 and len(result["warm"]["rounds"]) == 5
    assert result["cost"]["total_calls"] == 86
    assert sum(PILOT_BOUND[k] for k in ("seed_validation", "cold", "warm", "reserve")) == 94
    warm = RunState(pilot.root / "warm")
    records = warm.read("red")
    assert all(r["branch"] == "warm" and r["decision"]["direction"] == "same" for r in records)
    assert len({r["cluster_id"] for r in records}) == 5
    assert not any(r["cluster_id"] == pilot.seed.carrier.cluster_id for r in records)
    cold = RunState(pilot.root / "cold")
    assert all("seed_evidence" not in json.dumps(r["weakness_view"]) for r in cold.read("red"))
    assert not cold.store.items_with_status("candidate", "active")
    assert len(warm.read("encounters")) == 15  # seed encounters are elsewhere
    assert len(warm.read("lineages")) == 6 and warm.read("lineages")[0]["seed_import"]
    assert all(not any(k in q for k in ("seed_evidence", "weakness_view", "search_feedback"))
               for q in script.requests if "current_buggy_rtl" in q)
    before = len(script.requests)
    # A new process restores cost from the root ledger, never reissues completed calls.
    budget = BudgetedClient(build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=script), max_calls=94)
    kwargs.update(blue_client=budget, red_client=budget)
    resumed = FreeformPilot(**kwargs).run()
    assert len(script.requests) == before and resumed["cost"]["total_calls"] == 86


@needs_tools
def test_seed_not_reproduced_stops_warm_but_preserves_cold(tmp_path):
    pilot, _, _ = pilot_fixture(tmp_path, SearchScript(repaired=True))
    result = pilot.run()
    assert not result["seed"]["usable"] and result["seed"]["runs"] == 3
    assert result["warm"]["stopped"] == "seed_not_revalidated"
    assert len(result["cold"]["rounds"]) == 2
    assert not (pilot.root / "warm").exists()
    from r3e.loop.red_repair_feedback import read_public_candidate
    from experiments.loop_probes.repair_verify import passing_attempts
    seed_state = RunState(pilot.root / "seed")
    attempts = passing_attempts(seed_state)
    assert len(attempts) == 3 and {a["run"] for a in attempts} == {"seed:0", "seed:1", "seed:2"}
    assert not seed_state.read("red")  # historical seed is never a new admitted bug
    for attempt in attempts:
        rtl = read_public_candidate(seed_state.root, attempt["carrier_id"], attempt["candidate_hash"])
        assert rtl is not None and hash_payload(rtl) == attempt["candidate_hash"]
        assert attempt["counts_as_new_hit"] is False


@needs_tools
def test_all_no_answer_seed_runs_do_not_establish_a_functional_target(tmp_path, monkeypatch):
    from types import SimpleNamespace
    pilot, _, _ = pilot_fixture(tmp_path)
    record = {"challenge_id": pilot.seed.challenge_id, "solved_within_budget": False,
              "attempts": [{"verdict_tier": "no_answer"}] * 3}
    monkeypatch.setattr("r3e.loop.freeform_pilot.BlueRunner.run",
                        lambda *a, **k: SimpleNamespace(record=lambda: record))
    result = pilot._validate_seed()
    assert result["reproducible"] and result["fails"] == 3
    assert not result["usable"]


@needs_tools
def test_first_three_unusable_pursuits_stop_without_extra_calls(tmp_path):
    pilot, _, _ = pilot_fixture(tmp_path, SearchScript(reject_warm=True))
    result = pilot.run()
    assert result["warm"]["stopped"] == "first_three_no_admitted_new_design_bug"
    assert len(result["warm"]["rounds"]) == 3
    assert not RunState(pilot.root / "warm").read("encounters")


def test_interrupted_step_refuses_automatic_replay_and_changed_freeze(tmp_path):
    pilot, _, kwargs = pilot_fixture(tmp_path)
    append_ledger(pilot.events_path, {"step": "cold:0", "status": "started"})
    with pytest.raises(RuntimeError, match="automatic replay refused"):
        pilot._step("cold:0", 1, lambda: pytest.fail("replayed"))
    with pytest.raises(RuntimeError, match="frozen inputs"):
        FreeformPilot(**{**kwargs, "provider_settings": {"model": "different"}})


def test_whole_step_budget_reservation_prevents_partial_screen(tmp_path):
    pilot, _, _ = pilot_fixture(tmp_path, max_calls=4)
    assert pilot._step("seed:0", 5, lambda: pytest.fail("spent"))["stopped"]
    assert pilot.budget.total_calls == 0


@needs_tools
def test_seed_import_binds_review_hashes_and_discovery_split(tmp_path):
    loop, state, _, designs = make_loop(tmp_path, rounds=1)
    loop.run()
    row = state.read("red")[0]
    review = tmp_path / "decisions.json"
    review.write_text(json.dumps({row["challenge_id"]: {"decision": "PASS"}}))
    challenge, provenance = load_seed(state.root, review, row["challenge_id"], loop.splits)
    assert provenance["counts_as_new_hit"] is False and challenge.buggy_hash == row["mutant_hash"]
    with pytest.raises(ValueError, match="not discovery"):
        load_seed(state.root, review, row["challenge_id"], {
            "discovery": [], "qualification": [], "holdout": designs})
    review.write_text(json.dumps({row["challenge_id"]: {"decision": "EXCLUDED"}}))
    with pytest.raises(ValueError, match="PASS"):
        load_seed(state.root, review, row["challenge_id"], loop.splits)
