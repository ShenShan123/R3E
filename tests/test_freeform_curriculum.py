from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from r3e.loop.blue import BlueConfig, BlueRunner
from r3e.loop.budget import BudgetedClient
from r3e.loop.carriers import Port, generate_testbench
from r3e.loop.corpus import Carrier
from r3e.loop.curriculum import CurriculumConfig, lineages
from r3e.loop.env import build_client
from r3e.loop.freeform_curriculum import (
    FreeformCurriculumLoop, FreeformRed, freeform_holdout_variants, public_weakness_view,
)
from r3e.loop.orchestrator import LoopConfig
from r3e.loop.red_design import EditBudget
from r3e.loop.sim import Simulator
from r3e.loop.state import RunState
from r3e.protocol.hashing import hash_payload


ROOT = Path(__file__).resolve().parents[1]
needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in ("iverilog", "vvp", "yosys")),
                                 reason="Icarus/Yosys unavailable")
RTL = """module TopModule(input clk, input reset, input enable, output reg [3:0] q);
always @(posedge clk) begin
  if (reset) q <= 0;
  else if (enable) begin
    if (q == 9) q <= 0;
    else q <= q + 1;
  end
end
endmodule
"""


def carriers(tmp_path):
    tb = tmp_path / "tb.v"
    tb.write_text(generate_testbench(top="TopModule", ports=[Port(n, "input", 1) for n in
                  ("clk", "reset", "enable")] + [Port("q", "output", 4)], clock="clk", reset="reset",
                  seed=11, cycles=64, output_name="trace_visible.txt"))
    return [Carrier(f"c{i}", f"cluster{i}", RTL.replace("== 9", f"== {9-i}"), "TopModule", (tb,),
                    spec=f"TopModule counts enabled rising edges from 0 through {9-i}, then returns to 0. "
                         "Reset synchronously sets q to zero.") for i in range(4)]


class Scripted:
    def __init__(self, *, rejection=None, answer_format="full"):
        self.requests = []
        self.rejection = rejection
        self.answer_format = answer_format

    def __call__(self, **request):
        user = json.loads(request["messages"][-1]["content"])
        self.requests.append(user)
        if user.get("task") == "select_freeform_target":
            offered = user["weakness_view"]["open_weak_points"]
            candidate = next((c for c in user["candidate_designs"] if c["eligible_weakness_ids"]),
                             user["candidate_designs"][0])
            target = next((w for w in offered if w["weakness_id"] in candidate["eligible_weakness_ids"]), None)
            # Single-carrier negative test deliberately requests an already-used lineage.
            if offered and not target:
                target = offered[0]
            result = {"carrier_id": candidate["carrier_id"],
                      "applicability": {"rtl_anchors": ["else if (enable) begin"], "reason": "enable guards state updates"},
                      "skipped_weaknesses": {}, ** {"weakness_id": target["weakness_id"] if target else None,
                      "direction": "same" if target else "explore",
                      "mechanism": target["mechanism"] if target else "missed enable dependency",
                      "reason": "test transfer", "mutation_plan": {
                          "edits": [{"find": "else if (enable) begin", "replace": "else begin"}],
                          "invariant": "q changes only on an enabled edge",
                          "activation": "reset then apply a disabled rising edge; mutant increments",
                          "spec_basis": "counts enabled edges only"}}}
        elif "clean_rtl" in user:
            rtl = user["clean_rtl"].replace("else if (enable) begin", "else begin")
            if self.rejection == "no_change":
                rtl = user["clean_rtl"]
            result = {"buggy_rtl": rtl, "target_weakness_id": user["target_weakness"]["weakness_id"],
                      "hypothesis": "Blue misses the absent enable guard", "expected_symptom": "counts while disabled",
                      "change_summary": "remove enable dependency", "spec_basis": "counts only enabled edges"}
            if self.answer_format == "edits":
                result.pop("buggy_rtl")
                result.pop("target_weakness_id")
                result["edits"] = [{"find": "else if (enable) begin", "replace": "else begin"}]
        else:
            result = {"replacement_rtl": user["current_buggy_rtl"], "edit": "scripted unsuccessful repair"}
        return {"content": json.dumps(result), "input_tokens": 10, "output_tokens": 20,
                "provider_request_id": f"fake-{len(self.requests)}"}


def make_loop(tmp_path, *, rounds=2, script=None, state=None, designs=None, budget=None, **kwargs):
    script = script or Scripted()
    designs = designs or carriers(tmp_path)
    client = BudgetedClient(build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=script), max_calls=100)
    state = state or RunState(tmp_path / "run")
    client.on_call = lambda record: state.append("calls", record)
    simulator = Simulator(tmp_path / "sim")
    config = LoopConfig(rounds=rounds, proposals_per_round=1, red_mode="aware", learning=False,
                        blue=BlueConfig(budget_k=1, escalation_k=0))
    blue = BlueRunner(json_client=client, simulator=simulator, project_root=ROOT, config=config.blue)
    loop = FreeformCurriculumLoop(config=config, state=state,
             splits={"discovery": designs[:3], "qualification": [], "holdout": designs[3:]},
             public_challenges=[], blue=blue, red_client=client, simulator=simulator,
             curriculum=CurriculumConfig(confirm_runs=2, reproducible_fails=2),
             edit_budget=budget or EditBudget(), **kwargs)
    return loop, state, script, designs


@needs_tools
def test_rounds_use_freeform_then_transfer_with_visible_causal_history(tmp_path):
    loop, state, script, _ = make_loop(tmp_path, script=Scripted(answer_format="edits"))
    result = loop.run()
    assert [r["admitted"] for r in result["rounds"]] == [1, 1]
    records = state.read("red")
    assert records[0]["decision"]["direction"] == "explore"
    assert records[1]["decision"]["direction"] == "same"
    assert records[0]["carrier_id"] != records[1]["carrier_id"]
    assert len(lineages(state)) == 1
    assert all("buggy_rtl" in r["chosen"] and r["chosen"]["answer_form"] == "edits" for r in records)
    assert all(r["chosen"]["actual_diff"] for r in records)
    assert all(r["chosen"]["edit_budget"]["body"]["within_limit"] for r in records)
    requests = [r for r in script.requests if "clean_rtl" in r and "task" not in r]
    assert requests[0]["target_weakness"]["causal_chain"] == []
    assert requests[0]["mutation_plan"]["edits"]
    assert requests[0]["planning_feedback"]["status"] == "syntactically_feasible_not_admitted"
    assert requests[0]["generation_context"]["visible_sample_counts"] == [64]
    target = requests[1]["target_weakness"]
    assert target["mechanism"] == "missed enable dependency"
    assert target["causal_chain"][0]["blue_fails"] == 3
    assert len(target["causal_chain"][0]["visible_repair_attempts"]) == 3
    assert "feedback" in target["causal_chain"][0]["visible_repair_attempts"][0]["attempts"][0]
    assert requests[1]["weakness_context"]["mechanism_coverage"][target["mechanism"]]["reproducible_failures"] == 1
    assert all("operator_catalog" not in r and "sites" not in r for r in script.requests)
    assert all("target_weakness" not in r and "weakness_view" not in r and "hypothesis" not in r
               for r in script.requests if "current_buggy_rtl" in r)
    assert len([r for r in state.read("calls") if r["phase"] == "red"]) == 4
    assert all(s["runs"] == s["fails"] == 3 for s in state.read("screens"))
    assert not state.store.items_with_status("candidate", "active")
    lin = next(iter(lineages(state).values()))
    used = next(c for c in loop.splits["discovery"] if c.cluster_id in lin["clusters"])
    before = len(script.requests)
    invalid = loop.red.propose_curriculum(used, state=state, offered=[lin], seed=444, round_index=2)
    assert invalid.record["reason"] == "lineage_design_already_used"
    assert len(script.requests) == before + 1  # selection only, no design generation


@needs_tools
def test_rejected_generator_output_never_reaches_blue(tmp_path):
    loop, state, script, _ = make_loop(tmp_path, rounds=1, script=Scripted(rejection="no_change"))
    loop.run()
    assert state.read("red")[0]["reason"] == "no_token_change"
    rejected = state.read("red")[0]
    assert rejected["generation_output"]["hypothesis"] == "Blue misses the absent enable guard"
    assert rejected["materialized_rtl"] and rejected["actual_diff"] == ""
    assert rejected["selection_output"]["mutation_plan"]["edits"]
    assert not state.read("encounters") and not state.read("screens")
    assert len(script.requests) == 2


@needs_tools
def test_external_judge_hook_can_reject_without_repair_or_reward(tmp_path):
    loop, state, _, _ = make_loop(tmp_path, rounds=1,
        proposal_check=lambda carrier, proposal: "spec_not_recoverable", proposal_check_version="test-v1")
    loop.run()
    assert state.read("red")[0]["reason"] == "spec_not_recoverable"
    assert not state.read("encounters")


@needs_tools
def test_completed_slot_resume_and_duplicate_restore(tmp_path):
    loop, state, script, designs = make_loop(tmp_path, rounds=1)
    first = loop._propose_slots(0)  # simulate restart after proposal, before Blue
    assert len(first) == 1
    restarted, _, second_script, _ = make_loop(tmp_path, rounds=1, state=state, designs=designs)
    restored = restarted._propose_slots(0)
    assert restored[0].challenge.buggy_rtl == first[0].challenge.buggy_rtl
    assert not second_script.requests
    duplicate = restarted.red.propose_curriculum(first[0].challenge.carrier, state=state, offered=[],
                                                 seed=222, round_index=1)
    assert duplicate.record["reason"] == "duplicate" and duplicate.challenge is None
    restarted.run()
    assert state.checkpoint()["completed_rounds"] == 1
    final, _, final_script, _ = make_loop(tmp_path, rounds=1, state=state, designs=designs)
    final.run()
    assert not final_script.requests


@needs_tools
def test_resume_rejects_changed_budget_spec_or_candidate(tmp_path):
    loop, state, _, designs = make_loop(tmp_path, rounds=1)
    loop.run()
    with pytest.raises(RuntimeError, match="resume refused"):
        make_loop(tmp_path, state=state, designs=designs, budget=EditBudget(0.5))
    with pytest.raises(RuntimeError, match="resume refused"):
        make_loop(tmp_path, state=state, designs=designs, design_candidates=4)
    changed = [replace(c, spec=c.spec + "changed") for c in designs]
    with pytest.raises(RuntimeError, match="resume refused"):
        make_loop(tmp_path, state=state, designs=changed)
    row = state.read("red")[0]
    row = {k: v for k, v in row.items() if k not in {"record_hash", "prev_hash", "sequence"}}
    row["chosen"]["buggy_rtl"] += "\n// changed"
    state.append("red", row)
    with pytest.raises(RuntimeError, match="hash mismatch"):
        make_loop(tmp_path, state=state, designs=designs)


@needs_tools
def test_holdout_reuses_full_rtl_and_does_not_feed_discovery(tmp_path):
    loop, state, script, designs = make_loop(tmp_path, rounds=1)
    loop.run()
    before = {name: state.read(name) for name in ("encounters", "screens", "lineages", "episodes")}
    calls = len(script.requests)
    result = freeform_holdout_variants(loop, designs[3:], seed=90)
    assert len(result) == 1 and result[0].carrier in designs[3:]
    assert len(script.requests) == calls + 1  # forced same; no selection call
    assert all(state.read(name) == rows for name, rows in before.items())
    assert script.requests[-1]["direction"] == "same"
    restarted, _, second, _ = make_loop(tmp_path, rounds=1, state=state, designs=designs)
    assert freeform_holdout_variants(restarted, designs[3:], seed=90)[0].buggy_rtl == result[0].buggy_rtl
    assert not second.requests


def test_public_view_excludes_future_current_and_holdout_records(tmp_path):
    state = RunState(tmp_path / "run")
    for round_index in (0, 1, -2):
        cid = str(round_index)
        state.append("red", {"round": round_index, "challenge_id": cid, "admitted": True,
                             "chosen": {"hypothesis": cid}})
        state.append("screens", {"round": round_index, "challenge_id": cid, "reproducible": True})
    view = public_weakness_view(state, [], 1)
    assert list(view["mechanism_coverage"]) == ["0"]
    assert set(public_weakness_view(state, [], -2)["mechanism_coverage"]) == {"0", "1"}


def test_blind_generation_never_reads_weakness_history(tmp_path):
    designs = carriers(tmp_path)
    script = Scripted()
    client = BudgetedClient(build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=script), max_calls=5)
    def forbidden_view(*args):
        raise AssertionError("blind Red read the weakness view")
    red = FreeformRed(mode="blind", client=client, simulator=Simulator(tmp_path / "sim"),
                      edit_budget=EditBudget(), view_builder=forbidden_view)
    red._admit = lambda *args: ("test_referee_rejection", None)
    proposal = red.propose_curriculum(designs[0], state=RunState(tmp_path / "run"), offered=[],
                                      seed=1, round_index=0)
    assert proposal.record["reason"] == "test_referee_rejection"
    assert proposal.record["decision"]["direction"] == "explore"
    assert script.requests[0]["weakness_view"]["open_weak_points"] == []
    assert script.requests[1]["weakness_context"]["mechanism_coverage"] == {}


def test_invalid_selection_stops_before_generation_and_config_errors_propagate(tmp_path):
    designs = carriers(tmp_path)
    script = lambda **kwargs: {"content": json.dumps({"weakness_id": "invented", "direction": "same",
                  "mechanism": "anything", "reason": "anything"}), "input_tokens": 1, "output_tokens": 1,
                  "provider_request_id": "fake-invalid"}
    client = BudgetedClient(build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=script), max_calls=5)
    red = FreeformRed(mode="aware", client=client, simulator=Simulator(tmp_path / "sim"), edit_budget=EditBudget())
    state = RunState(tmp_path / "run")
    proposal = red.propose_curriculum(designs[0], state=state, offered=[], seed=1, round_index=0)
    assert proposal.record["reason"] == "invalid_target_selection" and client.total_calls == 1
    def broken(**kwargs):
        raise ValueError("configuration failure")
    client.inner.complete_json = broken
    with pytest.raises(ValueError, match="configuration failure"):
        red.propose_curriculum(designs[0], state=state, offered=[], seed=2, round_index=0)


def test_cli_plan_counts_two_red_calls_per_slot_without_execution(tmp_path):
    proc = subprocess.run([sys.executable, "-m", "r3e.loop.cli", "--state-dir", str(tmp_path / "plan"),
        "--curriculum", "--red-source", "freeform", "--manifests", "--rounds", "2", "--proposals", "3",
        "--no-learning", "--plan"], cwd=ROOT, capture_output=True, text=True, check=True)
    plan = json.loads(proc.stdout)
    assert plan["call_upper_bound"]["red"] == 12
    assert plan["red_max_changed_fraction"] == 0.4
    assert plan["red_thinking"] == "low"
    assert plan["red_design_candidates"] == 8
    assert not (tmp_path / "plan").exists()


def test_cli_explicit_red_thinking_overrides_new_freeform_default(tmp_path):
    proc = subprocess.run([sys.executable, "-m", "r3e.loop.cli", "--state-dir", str(tmp_path / "plan"),
        "--curriculum", "--red-source", "freeform", "--manifests", "--red-thinking", "disabled",
        "--plan"], cwd=ROOT, capture_output=True, text=True, check=True)
    assert json.loads(proc.stdout)["red_thinking"] == "disabled"
    assert not (tmp_path / "plan").exists()


def test_unknown_visible_horizon_is_not_guessed_and_hidden_tb_is_not_read(tmp_path):
    from r3e.loop.freeform_curriculum import visible_generation_context
    design = carriers(tmp_path)[0]
    source = tmp_path / "custom_tb.v"
    source.write_text("module custom; initial #99 $finish; endmodule")
    design = replace(design, visible_tb=(source,), hidden_tb=(tmp_path / "absent_hidden.v",))
    assert visible_generation_context(design)["visible_sample_counts"] == [None]


@needs_tools
def test_cli_fake_routes_freeform_without_external_calls(tmp_path):
    state_dir = tmp_path / "cli-run"
    proc = subprocess.run([sys.executable, "-m", "r3e.loop.cli", "--state-dir", str(state_dir),
        "--curriculum", "--red-source", "freeform", "--manifests", "--rounds", "1", "--proposals", "1",
        "--no-learning", "--fake", "--max-carriers", "2", "0", "1", "--budget-k", "1",
        "--escalation-k", "0", "--max-calls", "20"], cwd=ROOT, capture_output=True, text=True, check=True)
    state = RunState(state_dir)
    assert state.read("red")[0]["mode"] == "freeform"
    assert state.checkpoint()["completed_rounds"] == 1
    assert len([r for r in state.read("calls") if r["phase"] == "red"]) == 2
    assert '"fake": true' in proc.stdout


def test_joint_selection_uses_compatible_second_design_and_its_constraints(tmp_path):
    from r3e.loop.red_design import design_constraints
    designs = carriers(tmp_path)
    incompatible = replace(designs[0], clean_rtl=designs[0].clean_rtl.replace('enable', 'tick'),
                           spec='Counts every tick; no enable mechanism.')
    lin = {'lineage_id': 'L_enable', 'clusters': [], 'operators': ['mechanism:enable']}
    target = {'weakness_id': 'L_enable', 'mechanism': 'missed enable dependency'}
    script = Scripted(answer_format='edits')
    def transport(**request):
        result = script(**request)
        user = json.loads(request['messages'][-1]['content'])
        if user.get('task') == 'select_freeform_target':
            selection = json.loads(result['content'])
            selection['carrier_id'] = designs[1].carrier_id
            result['content'] = json.dumps(selection)
        return result
    client = BudgetedClient(build_client({'DEEPSEEK_MODEL': 'fake-local'}, transport=transport), max_calls=2)
    red = FreeformRed(mode='aware', client=client, simulator=Simulator(tmp_path/'sim'), edit_budget=EditBudget(),
        view_builder=lambda *args: {'open_weak_points': [target], 'mechanism_coverage': {}})
    red._admit = lambda *args: ('test_referee_rejection', None)
    proposal = red.propose_curriculum(incompatible, candidate_carriers=[incompatible, designs[1]],
        state=RunState(tmp_path/'run'), offered=[lin], seed=10, round_index=1)
    assert client.total_calls == 2
    assert proposal.record['carrier_id'] == designs[1].carrier_id
    assert proposal.record['decision']['lineage_id'] == 'L_enable'
    assert script.requests[1]['clean_rtl'] == designs[1].clean_rtl
    assert script.requests[1]['specification'] == designs[1].spec
    assert proposal.record['edit_constraints'] == design_constraints(designs[1], EditBudget())
    assert proposal.record['reason'] == 'test_referee_rejection'
    assert len(proposal.record['candidate_designs']) == 2
    assert proposal.record['candidate_inputs_hash']


@pytest.mark.parametrize('change,reason', [
    ({'carrier_id': 'not-offered'}, 'unoffered_design'),
    ({'applicability': {'rtl_anchor': 'absent_handshake', 'reason': 'claims mechanism'}}, 'ungrounded_applicability'),
    ({'skipped_weaknesses': []}, 'unexplained_exploration'),
])
def test_joint_selection_rejects_invalid_grounding_without_generation(tmp_path, change, reason):
    designs = carriers(tmp_path)
    script = Scripted()
    def transport(**request):
        result = script(**request)
        value = json.loads(result['content'])
        value.update(change)
        result['content'] = json.dumps(value)
        return result
    client = BudgetedClient(build_client({'DEEPSEEK_MODEL': 'fake-local'}, transport=transport), max_calls=2)
    red = FreeformRed(mode='blind', client=client, simulator=Simulator(tmp_path/'sim'), edit_budget=EditBudget())
    proposal = red.propose_curriculum(designs[0], candidate_carriers=designs[:2],
        state=RunState(tmp_path/'run'), offered=[], seed=1, round_index=0)
    assert proposal.record['reason'] == reason
    assert proposal.challenge is None and client.total_calls == 1


def test_aware_and_blind_share_rotating_design_menus(tmp_path):
    from r3e.loop.red import RedProposal
    menus = []
    for mode in ['aware', 'blind']:
        directory = tmp_path/mode
        directory.mkdir()
        loop, state, script, designs = make_loop(directory, design_candidates=2)
        loop.config = replace(loop.config, red_mode=mode)
        arm_menus = []
        def capture(carrier, **kwargs):
            arm_menus.append([c.carrier_id for c in kwargs['candidate_carriers']])
            return RedProposal({'round': kwargs['round_index'], 'mode': 'freeform',
                'slot': kwargs['context']['slot'], 'admitted': False}, None)
        loop.red.propose_curriculum = capture
        for r in range(3):
            loop._propose_slots(r)
        assert len(set(sum(arm_menus, []))) == 3
        assert all(len(m) == len(set(m)) == 2 for m in arm_menus)
        menus.append(arm_menus)
    assert menus[0] == menus[1]


@pytest.mark.parametrize('explain', [False, True])
def test_exploring_with_open_weakness_requires_recorded_inapplicability(tmp_path, explain):
    designs = carriers(tmp_path)
    target = {'weakness_id': 'L_handshake', 'mechanism': 'missed backpressure'}
    lin = {'lineage_id': 'L_handshake', 'clusters': [], 'operators': ['mechanism:handshake']}
    script = Scripted()
    def transport(**request):
        result = script(**request)
        user = json.loads(request['messages'][-1]['content'])
        if user.get('task') == 'select_freeform_target':
            value = json.loads(result['content'])
            value.update(weakness_id=None, direction='explore', mechanism='missed enable dependency',
                         skipped_weaknesses={'L_handshake': 'All offered designs are counters without ready/valid.'}
                         if explain else {})
            result['content'] = json.dumps(value)
        return result
    client = BudgetedClient(build_client({'DEEPSEEK_MODEL': 'fake-local'}, transport=transport), max_calls=2)
    red = FreeformRed(mode='aware', client=client, simulator=Simulator(tmp_path/'sim'), edit_budget=EditBudget(),
        view_builder=lambda *args: {'open_weak_points': [target], 'mechanism_coverage': {}})
    red._admit = lambda *args: ('test_referee_rejection', None)
    proposal = red.propose_curriculum(designs[0], candidate_carriers=designs[:2],
        state=RunState(tmp_path/'run'), offered=[lin], seed=1, round_index=1)
    assert proposal.record['reason'] == ('test_referee_rejection' if explain else 'unexplained_exploration')
    assert client.total_calls == (2 if explain else 1)
    if explain:
        assert proposal.record['decision']['lineage_id'] is None
        assert proposal.record['skipped_weaknesses']['L_handshake']
        assert script.requests[1]['target_weakness']['mastery_status'] == 'unobserved_hypothesis'


def test_no_open_weakness_normalizes_coverage_annotations_without_extra_calls(tmp_path):
    from copy import deepcopy
    designs = carriers(tmp_path)
    script = Scripted()
    captured = []
    def transport(**request):
        result = script(**request)
        user = json.loads(request['messages'][-1]['content'])
        if user.get('task') == 'select_freeform_target':
            assert user['selection_contract']['offered_weakness_ids'] == []
            assert user['selection_contract']['skipped_weaknesses_when_exploring'] == {}
            assert user['weakness_view']['mechanism_coverage']
            value = json.loads(result['content'])
            value['skipped_weaknesses'] = {'previously solved hypothesis': 'not an offered target'}
            captured.append(deepcopy(value))
            result['content'] = json.dumps(value)
        return result
    client = BudgetedClient(build_client({'DEEPSEEK_MODEL': 'fake-local'}, transport=transport), max_calls=2)
    red = FreeformRed(mode='aware', client=client, simulator=Simulator(tmp_path/'sim'), edit_budget=EditBudget(),
        view_builder=lambda *args: {'open_weak_points': [], 'mechanism_coverage': {
            'previously solved hypothesis': {'bugs': 1, 'screened': 1, 'reproducible_failures': 0}}})
    red._admit = lambda *args: ('test_referee_rejection', None)
    proposal = red.propose_curriculum(designs[0], state=RunState(tmp_path/'run'), offered=[], seed=1, round_index=1)
    assert proposal.record['reason'] == 'test_referee_rejection' and client.total_calls == 2
    assert proposal.record['skipped_weaknesses'] == {}
    assert proposal.record['ignored_skipped_weaknesses'] == captured[0]['skipped_weaknesses']
    assert proposal.record['selection_output'] == captured[0]  # never rewrite raw evidence
    assert proposal.record['decision']['lineage_id'] is None
    assert script.requests[1]['target_weakness']['mastery_status'] == 'unobserved_hypothesis'


def grounding_selection(**updates):
    value = {'weakness_id': None, 'applicability': {
        'rtl_anchors': ['else if (enable) begin', 'q <= q + 1;'], 'reason': 'two dependent locations'},
        'skipped_weaknesses': {}, 'mutation_plan': {'edits': [
            {'find': 'else if (enable) begin', 'replace': 'else begin'},
            {'find': 'q <= q + 1;', 'replace': 'q <= q + 2;'}]}}
    value.update(updates)
    return value


def test_multiple_anchors_match_independently_and_normalize_only_formatting():
    from copy import deepcopy
    from r3e.loop.freeform_curriculum import normalize_selection_grounding
    value = grounding_selection(applicability={
        'rtl_anchors': ['else if(enable) begin', 'q<=q+1;'], 'reason': 'distributed sites'})
    raw = deepcopy(value)
    normalized = normalize_selection_grounding(value, RTL, [])
    assert normalized['applicability']['rtl_anchors'] == ['else if (enable) begin', 'q <= q + 1;']
    assert value == raw
    assert 'anchor_formatting_normalized' in normalized['selection_normalization']


@pytest.mark.parametrize('quote', [
    'else if (enable) begin ... q <= q + 1;',
    'else if (enable) begin / q <= q + 1;',
    'site_one: else if (enable) begin; //first guard; site_two: q <= q + 1;',
])
def test_legacy_composite_citations_use_verified_plan_finds(quote):
    from r3e.loop.freeform_curriculum import normalize_selection_grounding
    value = grounding_selection(applicability={'rtl_anchor': quote, 'reason': 'multiple sites'})
    result = normalize_selection_grounding(value, RTL, [])
    assert result['applicability']['rtl_anchors'] == ['else if (enable) begin', 'q <= q + 1;']
    assert 'legacy_composite_anchor_from_verified_plan_finds' in result['selection_normalization']


@pytest.mark.parametrize('anchor', ['q <= q - 1;', 'q <= q + 2;', 'e nable', '// comment only'])
def test_canonical_anchor_never_fuzzes_operators_literals_or_identifiers(anchor):
    from r3e.loop.freeform_curriculum import normalize_selection_grounding, SelectionGroundingViolation
    value = grounding_selection(applicability={'rtl_anchors': ['else if(enable) begin', anchor],
                                               'reason': 'must ground every location'})
    with pytest.raises(SelectionGroundingViolation, match='ungrounded_applicability'):
        normalize_selection_grounding(value, RTL, [])


def test_anchor_preserves_string_literal_contents():
    from r3e.loop.freeform_curriculum import normalize_selection_grounding, SelectionGroundingViolation
    value = grounding_selection(applicability={'rtl_anchors': ['$display("a  b");'], 'reason': 'literal'})
    with pytest.raises(SelectionGroundingViolation, match='ungrounded_applicability'):
        normalize_selection_grounding(value, 'initial $display("a b");', [])


def test_legacy_composite_fails_when_plan_has_unverified_find():
    from r3e.loop.freeform_curriculum import normalize_selection_grounding, SelectionGroundingViolation
    value = grounding_selection(applicability={'rtl_anchor': 'else if(enable) begin ... absent <= 1;',
                                               'reason': 'claims missing site'})
    value['mutation_plan']['edits'][1]['find'] = 'absent <= 1;'
    with pytest.raises(SelectionGroundingViolation, match='ungrounded_applicability'):
        normalize_selection_grounding(value, RTL, [])


def test_coverage_notes_cannot_substitute_for_required_offered_id():
    from r3e.loop.freeform_curriculum import normalize_selection_grounding, SelectionGroundingViolation
    value = grounding_selection(skipped_weaknesses={'hypothesis prose': 'coverage note'})
    with pytest.raises(SelectionGroundingViolation, match='unexplained_exploration'):
        normalize_selection_grounding(value, RTL, ['L_known'])
    value['skipped_weaknesses']['L_known'] = 'no compatible candidate in menu'
    result = normalize_selection_grounding(value, RTL, ['L_known'])
    assert result['skipped_weaknesses'] == {'L_known': 'no compatible candidate in menu'}
    assert result['ignored_skipped_weaknesses'] == {'hypothesis prose': 'coverage note'}


def test_pursuit_has_no_skip_obligation_but_never_creates_targets_from_notes():
    from r3e.loop.freeform_curriculum import normalize_selection_grounding
    result = normalize_selection_grounding(grounding_selection(weakness_id='L_known',
        skipped_weaknesses={'coverage hypothesis': 'annotation'}), RTL, ['L_known'])
    assert result['skipped_weaknesses'] == {}
    assert result['ignored_skipped_weaknesses'] == {'coverage hypothesis': 'annotation'}


def test_legacy_multisite_citation_does_not_require_unique_edit_locations(tmp_path):
    from r3e.loop.freeform_curriculum import normalize_selection_grounding
    from r3e.loop.red_design import assess_mutation_plan
    source = RTL.replace('q <= 0;', 'q <= 0;\n    q <= 0;', 1)
    value = grounding_selection(applicability={
        'rtl_anchor': 'reset site: q <= 0; ... increment site: q <= q + 1;',
        'reason': 'two real code locations'})
    value['mutation_plan']['edits'][0]['find'] = 'q <= 0;'
    result = normalize_selection_grounding(value, source, [])
    assert result['applicability']['rtl_anchors'] == ['q <= 0;', 'q <= q + 1;']
    design = replace(carriers(tmp_path)[0], clean_rtl=source)
    feedback = assess_mutation_plan(design, value['mutation_plan'], EditBudget())
    assert feedback['status'] == 'revise_plan'
    assert feedback['reason'] == 'edits_not_applicable'
    assert 'found' in feedback['message']  # the generator must disambiguate; no edit is guessed
