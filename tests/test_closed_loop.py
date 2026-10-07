from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from r3e.knowledge import BugTypeInference, KnowledgeMatcher
from r3e.loop.blue import BlueConfig, BlueRunner
from r3e.loop.budget import BudgetedClient, CallBudgetExceeded
from r3e.loop.corpus import Carrier, load_public_manifest, split_by_cluster
from r3e.loop.env import LlmEnvError, build_client, load_llm_env
from r3e.loop.evaluate import evaluate_holdout
from r3e.loop.fakes import (
    FakeBlueTransport,
    FakeRedTransport,
    knowledge_types,
    nearest_clean_resolver,
)
from r3e.loop.operators import apply_edits, enumerate_sites
from r3e.loop.orchestrator import ClosedLoop, LoopConfig, frozen_challenges
from r3e.loop.qualification import QualificationConfig
from r3e.loop.red import RedAgent
from r3e.loop.sim import Simulator
from r3e.loop.state import RunState
from r3e.protocol.hashing import hash_payload


ROOT = Path(__file__).resolve().parents[1]
HAS_ICARUS = all(shutil.which(n) for n in ("iverilog", "vvp"))
needs_icarus = pytest.mark.skipif(not HAS_ICARUS, reason="Icarus is unavailable")


@pytest.fixture(scope="module")
def corpus():
    carriers, public = load_public_manifest(ROOT / "datasets/manifests/cirfix39.jsonl", ROOT)
    return carriers, public


def _carrier(corpus, family):
    return next(c for c in corpus[0] if c.cluster_id == family)


def _client(transport):
    return build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=transport)


def _router(blue, red):
    def transport(**request):
        user = json.loads(request["messages"][-1]["content"])
        return (blue if "current_buggy_rtl" in user else red)(**request)
    return transport


# ------------------------------------------------------------------ env


def test_llm_env_is_parsed_privately(tmp_path):
    env_file = tmp_path / ".llm_env"
    env_file.write_text(
        "export DEEPSEEK_API_KEY=first\nexport DEEPSEEK_MODEL='m-1'\n"
        "export DEEPSEEK_API_KEY=\"second-secret\"\nexport DEEPSEEK_BASE_URL=https://x.invalid\n",
        encoding="utf-8",
    )
    env = load_llm_env(env_file)
    assert env["DEEPSEEK_API_KEY"] == "second-secret" and env["DEEPSEEK_MODEL"] == "m-1"
    client = build_client(env)
    assert client.readiness()["ready"] is True
    assert "second-secret" not in json.dumps(client.readiness())
    with pytest.raises(LlmEnvError) as missing:
        build_client({"DEEPSEEK_MODEL": "m"})
    assert "secret" not in str(missing.value)
    with pytest.raises(LlmEnvError):
        load_llm_env(tmp_path / "absent")


def test_call_budget_refuses_before_sending():
    sent = []

    def transport(**request):
        sent.append(request)
        return {"content": json.dumps({"ok": 1}), "input_tokens": 1, "output_tokens": 1,
                "provider_request_id": "x"}

    budget = BudgetedClient(_client(transport), max_calls=1)
    with budget.in_phase("red"):
        budget.complete_json(messages=[{"role": "user", "content": "Return a JSON object: {}"}], seed=1)
    with pytest.raises(CallBudgetExceeded):
        budget.complete_json(messages=[{"role": "user", "content": "Return a JSON object: {}"}], seed=1)
    assert len(sent) == 1
    assert budget.report()["learning"]["calls"] == 1


# ------------------------------------------------------------- red side


def test_operator_sites_apply_legal_edits(corpus):
    carrier = _carrier(corpus, "first_counter_overflow")
    sites = enumerate_sites(carrier)
    assert {"condition_negate", "compare_swap", "constant_shift", "assign_kind_swap"} <= {s.operator for s in sites}
    negate = next(s for s in sites if s.operator == "condition_negate")
    edited = apply_edits(carrier, [(negate, "negate")])
    assert edited != carrier.clean_rtl and "(!(" in edited
    with pytest.raises(ValueError):
        apply_edits(carrier, [(negate, "not-an-option")])


@needs_icarus
def test_red_admission_records_reasons_and_rejects_duplicates(corpus, tmp_path):
    carrier = _carrier(corpus, "first_counter_overflow")
    sim = Simulator(tmp_path / "sim")
    state = RunState(tmp_path / "run")
    client = BudgetedClient(_client(FakeRedTransport(operator="condition_negate")), max_calls=10)
    red = RedAgent(mode="aware", client=client, simulator=sim)
    first = red.propose(carrier, state=state, seed=1, round_index=0)
    assert first.record["admitted"] and first.challenge is not None
    user = json.loads(client.inner._transport.requests[0]["messages"][-1]["content"])
    assert "current_blue_state" in user and "mutation" not in json.dumps(user["current_blue_state"])
    blind = RedAgent(mode="blind", client=client, simulator=sim)
    blind.propose(carrier, state=state, seed=2, round_index=0)
    blind_user = json.loads(client.inner._transport.requests[1]["messages"][-1]["content"])
    assert "current_blue_state" not in blind_user
    reasons = set()
    random_red = RedAgent(mode="random", client=None, simulator=sim, seed=3)
    for i in range(12):
        reasons.add(random_red.propose(carrier, state=state, seed=i, round_index=0).record["reason"])
    assert "admitted" in reasons
    # the identical edit proposed again is rejected as a duplicate
    repeat = RedAgent(mode="aware", client=BudgetedClient(
        _client(FakeRedTransport(operator="condition_negate")), max_calls=10), simulator=sim)
    repeat.seen = set(red.seen)
    assert repeat.propose(carrier, state=state, seed=1, round_index=0).record["reason"] == "duplicate"


# ------------------------------------------------------------ blue side


@needs_icarus
def test_blue_escalation_is_billed_as_learning(corpus, tmp_path):
    ch = next(c for c in corpus[1] if c.provenance["design"] == "flip_flop__wadden_buggy1")
    fake = FakeBlueTransport({hash_payload(ch.buggy_rtl): ch.carrier.clean_rtl}, succeed=lambda u, n: n >= 4)
    runner = BlueRunner(json_client=_client(fake), simulator=Simulator(tmp_path), project_root=ROOT, max_calls=20)
    enc = runner.run(ch, mode="none", pool=[], inference=BugTypeInference(), matcher=KnowledgeMatcher(), seed=1)
    assert [a["phase"] for a in enc.attempts] == ["blue_inference"] * 3 + ["escalation"] * 2
    assert enc.solved_by_escalation and not enc.solved_within_budget
    assert enc.episode.verified_bug_type == "reset_or_enable"
    report = runner.budget.report()
    assert report["by_phase"]["escalation"]["calls"] == 2 and report["learning"]["calls"] == 2
    third = json.loads(fake.requests[2]["messages"][-1]["content"])["current_failure_evidence"]
    assert len(third["previous_attempts"]) == 2
    assert "golden" not in json.dumps(third)


# ---------------------------------------------------- loop end to end


def _loop(corpus, root, *, objective="solve_rate", succeed=None, rounds=2):
    carriers, public = corpus
    # Fixed, lightweight splits: test speed must not depend on which designs a
    # hash happens to place in each split (reed_solomon simulates for ~10 s).
    layout = {"discovery": {"decoder_3_to_8", "flip_flop", "lshift_reg"},
              "qualification": {"fsm_full", "mux_4_1"},
              "holdout": {"first_counter_overflow", "sdram_controller"}}
    splits = {name: [c for c in carriers if c.cluster_id in families] for name, families in layout.items()}
    blue_t = FakeBlueTransport(nearest_clean_resolver([c.clean_rtl for c in carriers]),
                               succeed=succeed or (lambda u, n: n >= 1 or bool(knowledge_types(u))))
    budget = BudgetedClient(_client(_router(blue_t, FakeRedTransport())), max_calls=5000)
    sim = Simulator(root / "sim")
    cfg = LoopConfig(rounds=rounds, proposals_per_round=3, red_mode="aware", seed=0,
                     blue=BlueConfig(), qualification=QualificationConfig(objective=objective, max_cases=4))
    state = RunState(root / "run")
    blue = BlueRunner(json_client=budget, simulator=sim, project_root=ROOT, config=cfg.blue)
    loop = ClosedLoop(config=cfg, state=state, splits=splits, public_challenges=public, blue=blue,
                      red_client=budget, simulator=sim)
    return loop, state, blue, splits, sim


@needs_icarus
def test_closed_loop_promotes_useful_knowledge_and_resumes(corpus, tmp_path):
    # memory is offered from Blue's second attempt; with it, that attempt
    # succeeds, without it Blue needs a third
    loop, state, blue, splits, sim = _loop(corpus, tmp_path, objective="cost",
                                           succeed=lambda u, n: n >= 2 or bool(knowledge_types(u)))
    loop.run()
    rounds = state.read("rounds")
    assert [r["round"] for r in rounds] == [0, 1]
    assert rounds[-1]["active"], "a knowledge item should be promoted under the cost objective"
    # promoted knowledge reached Blue during qualification (coverage), and the
    # policy-aware Red of round 1 saw how Blue did on its round-0 bugs
    assert any(r["decision"] == "active" and r["coverage"] > 0 for r in state.read("qualification"))
    from r3e.loop.red import blue_state_summary
    summary = blue_state_summary(state)
    assert summary["recent_challenge_outcomes"], "Red memory should include its admitted bugs"
    assert all("blue_solved_within_budget" in o for o in summary["recent_challenge_outcomes"])
    assert all(r["decision"] in {"active", "rejected"} for r in state.read("qualification"))
    calls_before = blue.budget.total_calls
    loop.run()  # resume: everything is complete, nothing re-runs
    assert blue.budget.total_calls == calls_before
    assert len(state.read("rounds")) == 2

    holdout = frozen_challenges(splits["holdout"], corpus[1], sim, per_carrier=1, seed=2000)[:3]
    report = evaluate_holdout(state, blue, holdout, modes=("none", "matched", "shuffled"), rounds=[1], repeats=2)
    summary = report["snapshots"][0]["summary"]
    assert set(summary) == {"none", "matched", "shuffled"}
    assert summary["none"]["cases_worse_than_none"] == 0


@needs_icarus
def test_gate_rejects_knowledge_without_gain(corpus, tmp_path):
    loop, state, *_ = _loop(corpus, tmp_path, objective="solve_rate", rounds=1)
    loop.run()
    decisions = state.read("qualification")
    assert decisions and all(d["decision"] == "rejected" for d in decisions)
    assert {d["reason"] for d in decisions} <= {"no_net_gain", "not_significant", "no_coverage"}
    assert state.active_items() == []
    # more support for an unchanged card is not a new version (no re-qualification)
    from r3e.knowledge.schema import KnowledgeItem
    from r3e.loop.orchestrator import _next_version
    stored = state.store.items_with_status("rejected")[0]
    body = stored.to_dict()
    grown = dict(body["evidence"], support=int(body["evidence"].get("support", 1)) + 3)
    same_card = KnowledgeItem.create(item_id=body["item_id"], version=1, applicability=body["applicability"],
                                     card=body["card"], example=body["example"], evidence=grown,
                                     author=body["author"])
    assert _next_version(state, same_card) is None
    first = dict(body["card"]["cases"][0])
    first["repair"] = dict(first["repair"], explanation_by_repairer="a different verified explanation")
    changed = {"cases": [first, *body["card"]["cases"][1:]]}
    new_card = KnowledgeItem.create(item_id=body["item_id"], version=1, applicability=body["applicability"],
                                    card=changed, example=body["example"], evidence=grown, author=body["author"])
    assert _next_version(state, new_card).version == stored.version + 1


@needs_icarus
def test_resume_refuses_changed_frozen_config(corpus, tmp_path):
    loop, state, *_ = _loop(corpus, tmp_path, rounds=1)
    loop.run()
    with pytest.raises(RuntimeError):
        _loop(corpus, tmp_path, rounds=1, objective="cost")
    # extending a completed run with more rounds of the same config is allowed
    longer, state2, blue, *_ = _loop(corpus, tmp_path, rounds=2)
    longer.run()
    assert [r["round"] for r in state2.read("rounds")] == [0, 1]


def test_cli_refuses_real_calls_without_explicit_approval(tmp_path):
    out = subprocess.run(
        [sys.executable, "-B", "-m", "r3e.loop.cli", "--state-dir", str(tmp_path / "s")], cwd=ROOT,
        capture_output=True, text=True, timeout=300,
    )
    assert out.returncode == 2 and "refusing" in out.stdout


def test_failed_calls_still_count_tokens_and_sign_test():
    from r3e.loop.qualification import _sign_test
    from r3e.providers.openai_compatible import OpenAICompatibleEmptyContentViolation

    def transport(**request):
        return {"content": "", "input_tokens": 50, "output_tokens": 8192,
                "provider_request_id": "x", "finish_reason": "length"}

    budget = BudgetedClient(_client(transport), max_calls=3)
    with budget.in_phase("red"), pytest.raises(OpenAICompatibleEmptyContentViolation) as err:
        budget.complete_json(messages=[{"role": "user", "content": "Return a JSON object: {}"}], seed=1)
    assert err.value.diagnostics["finish_reason"] == "length"
    red = budget.report()["by_phase"]["red"]
    assert red["calls"] == 1 and red["failed_calls"] == 1 and red["output"] == 8192
    assert _sign_test(3, 0) == 0.125 and _sign_test(0, 0) == 1.0 and _sign_test(1, 1) == 0.75


@needs_icarus
def test_red_rejects_combinational_loops_and_caps_sites(corpus, tmp_path):
    from r3e.loop.red import MAX_RED_SITES, has_logic_loop, sample_sites
    big = max(corpus[0], key=lambda c: len(enumerate_sites(c)))
    picked = sample_sites(enumerate_sites(big), seed=1)
    assert len(picked) <= MAX_RED_SITES and len({s.operator for s in picked}) >= 3
    loop = "module m(input s, input a, output y); assign y = s ? a : y; endmodule\n"
    ok = "module m(input s, input a, output y); assign y = s ? a : 1'b0; endmodule\n"
    assert has_logic_loop(loop, "m", tmp_path / "l") and not has_logic_loop(ok, "m", tmp_path / "o")


def test_thinking_setting_is_bound_and_red_shares_the_budget():
    from r3e.loop.env import ThinkingTransport
    env = {"DEEPSEEK_API_KEY": "k", "DEEPSEEK_BASE_URL": "https://x.invalid", "DEEPSEEK_MODEL": "m"}
    red = build_client(env, thinking="disabled")
    blue = build_client(env)
    assert isinstance(red._transport, ThinkingTransport) and red._transport.thinking == "disabled"
    assert blue._transport is None
    assert red.config.config_hash != blue.config.config_hash
    with pytest.raises(LlmEnvError):
        build_client(env, thinking="extreme")

    def transport(**request):
        return {"content": json.dumps({"ok": 1}), "input_tokens": 1, "output_tokens": 1,
                "provider_request_id": "x"}

    shared = BudgetedClient(_client(transport), max_calls=2)
    red_budget = shared.sibling(_client(transport))
    with red_budget.in_phase("red"):
        red_budget.complete_json(messages=[{"role": "user", "content": "Return a JSON object: {}"}], seed=1)
    shared.complete_json(messages=[{"role": "user", "content": "Return a JSON object: {}"}], seed=1)
    with pytest.raises(CallBudgetExceeded):
        red_budget.complete_json(messages=[{"role": "user", "content": "Return a JSON object: {}"}], seed=1)
    assert shared.report()["by_phase"]["red"]["calls"] == 1 and shared.total_calls == 2


@needs_icarus
def test_provider_failures_do_not_consume_repair_attempts(corpus, tmp_path):
    from r3e.providers.openai_compatible import OpenAICompatibleProviderViolation
    ch = next(c for c in corpus[1] if c.provenance["design"] == "flip_flop__wadden_buggy1")
    calls = {"n": 0}
    good = FakeBlueTransport({hash_payload(ch.buggy_rtl): ch.carrier.clean_rtl}, succeed=lambda u, n: True)

    def flaky(**request):
        calls["n"] += 1
        if calls["n"] <= 2:  # two transport timeouts, then a real answer
            raise OpenAICompatibleProviderViolation("real provider request failed: APITimeoutError")
        return good(**request)

    runner = BlueRunner(json_client=_client(flaky), simulator=Simulator(tmp_path / "a"), project_root=ROOT, max_calls=20)
    enc = runner.run(ch, mode="none", pool=[], inference=BugTypeInference(), matcher=KnowledgeMatcher(), seed=1)
    assert enc.infra_failures == 2 and not enc.inconclusive
    assert enc.solved_within_budget and len(enc.repair_attempts) == 1

    def dead(**request):
        raise OpenAICompatibleProviderViolation("real provider request failed: APIConnectionError")

    runner = BlueRunner(json_client=_client(dead), simulator=Simulator(tmp_path / "b"), project_root=ROOT, max_calls=20)
    enc = runner.run(ch, mode="none", pool=[], inference=BugTypeInference(), matcher=KnowledgeMatcher(), seed=1)
    assert enc.inconclusive and enc.episode["outcome"] == "inconclusive"
    assert enc.repair_attempts == [] and enc.infra_failures == 3


@needs_icarus
def test_output_budget_exhaustion_is_a_blue_failure(corpus, tmp_path):
    ch = next(c for c in corpus[1] if c.provenance["design"] == "flip_flop__wadden_buggy1")

    def runaway(**request):  # the model spends its whole output budget reasoning
        return {"content": "", "input_tokens": 10, "output_tokens": 8192,
                "provider_request_id": "x", "finish_reason": "length"}

    runner = BlueRunner(json_client=_client(runaway), simulator=Simulator(tmp_path), project_root=ROOT,
                        config=BlueConfig(budget_k=3, escalation_k=0), max_calls=20)
    enc = runner.run(ch, mode="none", pool=[], inference=BugTypeInference(), matcher=KnowledgeMatcher(),
                     seed=1, allow_escalation=False)
    assert not enc.inconclusive and enc.infra_failures == 0
    assert [a["verdict_tier"] for a in enc.repair_attempts] == ["no_answer"] * 3
    assert not enc.solved_within_budget and enc.episode["outcome"] == "unresolved"
    assert "output budget" in enc.attempts[-1]["feedback"]["message"]


@needs_icarus
def test_qualification_shares_baseline_and_ledger_records_every_call(corpus, tmp_path):
    from r3e.loop.budget import cost_from_calls
    loop, state, blue, *_ = _loop(corpus, tmp_path, objective="solve_rate", rounds=1,
                                  succeed=lambda u, n: True)
    blue.budget.on_call = lambda entry: state.append("calls", entry)
    loop.run()
    reports = state.read("qualification")
    assert reports, "candidates should be qualified"
    # every case is solved in every baseline run -> saturated -> one with-run each
    assert all(o["baseline_saturated"] for r in reports for o in r["outcomes"])
    assert sum(r["with_runs_skipped_saturated"] for r in reports) > 0
    qual_calls = blue.budget.report()["by_phase"]["qualification"]["calls"]
    cases = {o["challenge_id"] for r in reports for o in r["outcomes"]}
    with_runs = sum(1 for r in reports for o in r["outcomes"] for x in o["runs"] if "solved_with" in x)
    # baseline: 3 seeds per distinct case, computed once; plus one with-run per case per candidate
    assert qual_calls == 3 * len(cases) + with_runs
    ledger = cost_from_calls(state.read("calls"))
    assert ledger["total_calls"] == blue.budget.total_calls
    assert ledger["by_phase"]["qualification"]["calls"] == qual_calls


@needs_icarus
def test_red_compound_candidates_pick_the_stealthiest(corpus, tmp_path):
    from r3e.loop.operators import apply_edits
    from r3e.loop.red import sample_sites
    carrier = _carrier(corpus, "first_counter_overflow")
    sim = Simulator(tmp_path / "sim")
    state = RunState(tmp_path / "run")

    class TwoCandidates(FakeRedTransport):
        def __call__(self, **request):
            self.requests.append(request)
            user = json.loads(request["messages"][-1]["content"])
            sites = user["sites"]
            cands = [{"hypothesis": f"h{i}", "edits": [{"site_id": s["site_id"], "option": s["options"][0]}],
                      "expected_symptom": "x"} for i, s in enumerate(sites[:3])]
            cands.append({"hypothesis": "compound", "edits": [
                {"site_id": sites[0]["site_id"], "option": sites[0]["options"][0]},
                {"site_id": sites[-1]["site_id"], "option": sites[-1]["options"][0]}], "expected_symptom": "y"})
            return {"content": json.dumps({"candidates": cands}), "input_tokens": 1, "output_tokens": 1,
                    "provider_request_id": "r"}

    red = RedAgent(mode="aware", client=BudgetedClient(_client(TwoCandidates()), max_calls=5), simulator=sim)
    proposal = red.propose(carrier, state=state, seed=4, round_index=0)
    rec = proposal.record
    assert len(rec["candidates"]) == 3  # capped at MAX_CANDIDATES
    admitted = [c for c in rec["candidates"] if c["reason"] == "admitted"]
    if proposal.challenge is not None:
        best = min(c["stealth"]["mismatch_fraction"] for c in admitted)
        assert rec["chosen"]["stealth"]["mismatch_fraction"] == best
    offered = sample_sites(enumerate_sites(carrier), seed=4)
    a, b = offered[0], offered[-1]
    assert apply_edits(carrier, [(a, a.options[0]), (b, b.options[0])]) != carrier.clean_rtl
    with pytest.raises(ValueError):
        apply_edits(carrier, [(a, a.options[0]), (a, a.options[0])])


def test_escalation_gets_its_own_output_cap():
    seen = []

    def transport(**request):
        seen.append(request["max_tokens"])
        return {"content": json.dumps({"ok": 1}), "input_tokens": 1, "output_tokens": 1,
                "provider_request_id": "x"}

    client = build_client({"DEEPSEEK_MODEL": "m"}, transport=transport, maximum_output_tokens=32768)
    budget = BudgetedClient(client, max_calls=5)
    budget.output_caps = {"blue_inference": 8192, "escalation": 32768}
    budget.complete_json(messages=[{"role": "user", "content": "Return a JSON object: {}"}], seed=1)
    with budget.in_phase("escalation"):
        budget.complete_json(messages=[{"role": "user", "content": "Return a JSON object: {}"}], seed=1)
    assert seen == [8192, 32768]


def test_public_carrier_ids_do_not_depend_on_checkout_path():
    import os
    relative, _ = load_public_manifest(Path(os.path.relpath(ROOT)) / "datasets/manifests/cirfix39.jsonl",
                                       Path(os.path.relpath(ROOT)))
    absolute, _ = load_public_manifest(ROOT / "datasets/manifests/cirfix39.jsonl", ROOT)
    assert sorted(c.carrier_id for c in relative) == sorted(c.carrier_id for c in absolute)



def test_json_recovery_only_strips_wrapping_and_keeps_failures(tmp_path):
    from types import SimpleNamespace
    from r3e.loop.env import recover_json_object
    t = SimpleNamespace(failure_dir=tmp_path / "bad", recovered=0)
    assert recover_json_object('{"a": 1}', t) == '{"a": 1}' and t.recovered == 0
    assert recover_json_object('```json\n{"a": 1}\n```', t) == '{"a": 1}' and t.recovered == 1
    assert recover_json_object('Here it is: {"a": {"b": 2}} done', t) == '{"a": {"b": 2}}'
    assert recover_json_object('{"a": {"b": 2}}}', t) == '{"a": {"b": 2}}'  # stray closing brace
    bad = '{"option": 4\'b0}'  # an unquoted Verilog literal is not repaired
    assert recover_json_object(bad, t) == bad
    kept = list((tmp_path / "bad").glob("unparseable_*.txt"))
    assert len(kept) == 1 and kept[0].read_text() == bad



@needs_icarus
def test_memory_is_a_reference_offered_only_after_blues_own_first_attempt(corpus, tmp_path):
    from r3e.knowledge import CaseMemoryAuthor
    from r3e.knowledge.schema import RepairEpisode
    carriers, public = corpus
    ch = next(c for c in public if c.provenance["design"] == "flip_flop__wadden_buggy1")
    # one verified case from another design to retrieve
    other = next(c for c in public if c.carrier.cluster_id != ch.carrier.cluster_id)
    teacher = FakeBlueTransport({hash_payload(other.buggy_rtl): other.carrier.clean_rtl}, succeed=lambda u, n: True)
    t_runner = BlueRunner(json_client=_client(teacher), simulator=Simulator(tmp_path / "t"), project_root=ROOT,
                          max_calls=5)
    episode = t_runner.run(other, mode="none", pool=[], inference=BugTypeInference(), matcher=KnowledgeMatcher(),
                           seed=1).episode
    items = CaseMemoryAuthor().author([episode])
    assert items
    seen = []
    fake = FakeBlueTransport({hash_payload(ch.buggy_rtl): ch.carrier.clean_rtl}, succeed=lambda u, n: n >= 2)

    def recording(**request):
        seen.append(json.loads(request["messages"][-1]["content"]))
        return fake(**request)

    runner = BlueRunner(json_client=_client(recording), simulator=Simulator(tmp_path / "b"), project_root=ROOT,
                        max_calls=10)
    loose = KnowledgeMatcher(threshold=0.0)  # always retrieve, to observe delivery
    enc = runner.run(ch, mode="matched", pool=items, inference=BugTypeInference(), matcher=loose, seed=3,
                     allow_escalation=False)
    assert enc.record()["retrieved_items"]
    assert "reference_cases" not in seen[0]  # Blue's own first attempt: no memory
    assert all("reference_cases" in user for user in seen[1:])
    first_alone = BlueRunner(json_client=_client(lambda **r: (seen.append(json.loads(r["messages"][-1]["content"]))
                                                              or fake(**r))),
                             simulator=Simulator(tmp_path / "c"), project_root=ROOT, max_calls=10)
    first_alone.run(ch, mode="none", pool=[], inference=BugTypeInference(), matcher=loose, seed=3,
                    allow_escalation=False)
    assert seen[len(enc.attempts)] == seen[0]  # identical to the no-memory system's first request
    # the evidence includes the cycle window around the first mismatch
    window = seen[0]["current_failure_evidence"]["initial_visible_test_failure"]["cycle_window"]
    assert window["rows"] and window["wrong_cycles"]
    assert enc.record()["shown_items"]  # delivered (attempt 1 failed), and recorded as such


def test_memory_outcomes_separate_simple_bugs_transfer_failures_and_novel_bugs(tmp_path):
    from types import SimpleNamespace
    from r3e.loop.memory_usage import classify, record_memory_usage, usage_stats

    def enc(tiers, *, within, escal=False, shown=(), retrieved=(), fix_type=None):
        attempts = [{"verdict_tier": t} for t in tiers]
        rec = {"shown_items": list(shown), "retrieved_items": list(retrieved)}
        return SimpleNamespace(attempts=attempts, solved_within_budget=within, solved_by_escalation=escal,
                               challenge_id="C", record=lambda: rec,
                               episode=SimpleNamespace(verified_bug_type=fix_type))
    p = ["hidden_pass"]; f = ["visible_fail"]
    assert classify(enc(p, within=True, retrieved=["K@v1"])) == "solved_alone"
    assert classify(enc(f + p, within=True, shown=["K@v1"])) == "fixed_after_memory"
    assert classify(enc(f * 3 + p, within=False, escal=True, shown=["K@v1"])) == "escalation_after_memory"
    assert classify(enc(f * 3, within=False, shown=["K@v1"])) == "unresolved_with_memory"
    assert classify(enc(f * 3 + p, within=False, escal=True)) == "novel_fixed"
    assert classify(enc(f * 6, within=False)) == "novel_unresolved"
    state = RunState(tmp_path / "s")
    item = SimpleNamespace(item_id="K", version=1, bug_type="state_transition")
    record_memory_usage(state, enc(f * 3 + p, within=False, escal=True, shown=["K@v1"], fix_type="reset_or_enable"),
                        round_index=0, pool=[item])
    record_memory_usage(state, enc(f + p, within=True, shown=["K@v1"], fix_type="state_transition"),
                        round_index=1, pool=[item])
    stats = usage_stats(state)["K@v1"]
    assert stats == {"offered": 2, "fixed_after": 1, "escalation_fix_agreed": 0, "escalation_fix_differed": 1,
                     "unresolved": 0}


@needs_icarus
def test_gate_forks_after_a_failed_first_attempt_and_keeps_undelivered_candidates_pending(corpus, tmp_path):
    from r3e.loop.qualification import qualify_candidate
    loop, state, blue, *_ = _loop(corpus, tmp_path, succeed=lambda u, n: n >= 1, rounds=1)
    loop.run()
    candidate = state.store.items_with_status("candidate", "rejected")[0]
    # Blue fixes everything on its first attempt: one run per seed, nothing delivered
    easy = BlueRunner(json_client=BudgetedClient(_client(FakeBlueTransport(
        nearest_clean_resolver([c.clean_rtl for c in corpus[0]]), succeed=lambda u, n: True)), max_calls=500),
        simulator=Simulator(tmp_path / "easy"), project_root=ROOT)
    calls = easy.budget.total_calls
    report = qualify_candidate(candidate, runner=easy, state=state, qset=loop.qset,
                               config=QualificationConfig(max_cases=2, repeats=2), round_index=9,
                               pending_if_undelivered=True)
    assert report["decision"] == "pending" and report["reason"] == "not_delivered"
    runs = [r for o in report["outcomes"] for r in o["runs"]]
    assert runs and all(r.get("solved_first_attempt") for r in runs)
    assert easy.budget.total_calls - calls == len(runs)  # a first-attempt fix costs a single run


@needs_icarus
def test_evaluation_skips_snapshots_without_active_memory(corpus, tmp_path):
    loop, state, blue, splits, sim = _loop(corpus, tmp_path, rounds=1, succeed=lambda u, n: True)
    loop.run()
    assert state.read("rounds")[-1]["active"] == []
    holdout = frozen_challenges(splits["holdout"], corpus[1], sim, per_carrier=1, seed=2000)[:2]
    calls = blue.budget.total_calls
    report = evaluate_holdout(state, blue, holdout, modes=("none", "matched"), repeats=1)
    assert report["snapshots"][0]["skipped"]
    assert blue.budget.total_calls - calls == len(holdout)  # only the shared "none" baseline ran


@needs_icarus
def test_population_ledger_records_every_branch_and_eligibility_rules(corpus, tmp_path):
    from r3e.loop.population import eligible
    loop, state, blue, splits, sim = _loop(corpus, tmp_path, rounds=1, succeed=lambda u, n: n >= 2)
    loop.run()
    records = state.read("population")
    encounters = [row["encounter"] for row in state.read("encounters")]
    attempts = sum(1 for e in encounters for a in e["attempts"] if not a.get("infra_failure") and not a.get("reused"))
    assert len([r for r in records if r["phase"] in ("blue_inference", "escalation")]) == attempts
    failed = [r for r in records if r["evidence"]["tier"] == "visible_fail"]
    passed = [r for r in records if r["evidence"]["tier"] in ("visible_pass", "hidden_pass")]
    assert failed and passed  # failed branches are kept, not only verified repairs
    assert all(r["action"] is not None and 0 <= r["reward"] <= 0.25 for r in failed)
    # an unchanged candidate has an empty patch and no structural identity
    assert all((r["structure"] is None) == (not r["action"]["patch"]) for r in failed)
    assert all(r["reward"] >= 0.5 and r["structure"]["signature"] for r in passed)
    assert all(r["round"] == 0 and r["state"]["buggy_hash"] for r in records)
    # eligibility: holdout evaluation never counts; duplicates and stale records drop out
    fake_eval = dict(passed[0], phase="evaluation")
    kept = eligible([*records, fake_eval, dict(passed[0])], now_round=0)
    assert fake_eval not in kept and sum(r == passed[0] for r in kept) == 1
    assert eligible(records, now_round=10, max_age=6) == []


_COUNTER = """module top(input clk, input rst, output out);
  reg [1:0] count;
  always @(posedge clk) count <= rst ? 2'd0 : count + 2'd1;
  assign out = (count == 2'd%s);
endmodule
"""
_COUNTER_TB = """`timescale 1ns/1ps
module tb;
  reg clk = 0, rst = 1;
  wire out;
  integer f;
  top dut(.clk(clk), .rst(rst), .out(out));
  always #5 clk = ~clk;
  initial begin
    f = $fopen("trace_visible.txt", "w");
    $fwrite(f, "time,out\\n");
    #12 rst = 0;
    #120 $fclose(f);
    $finish;
  end
  always @(posedge clk) %s(f, "%%0d,%%b", %s, out);
endmodule
"""


@needs_icarus
@pytest.mark.parametrize("sampling,time,shown", [("$fstrobe", "$time", True),     # end of the time step
                                                 ("$fdisplay", "$time", True),    # before the step's updates
                                                 ("$fstrobe", "0", False)])       # no time per sample
def test_register_trace_is_given_only_when_aligned_with_the_logged_outputs(tmp_path, sampling, time, shown):
    tb = tmp_path / "tb.v"
    tb.write_text(_COUNTER_TB % (sampling, time))
    carrier = Carrier(carrier_id="c", cluster_id="c", clean_rtl=_COUNTER % "3", top_module="top", visible_tb=(tb,))
    sim = Simulator(tmp_path / "sim")
    plain = sim.verdict(_COUNTER % "2", carrier)
    traced = sim.verdict(_COUNTER % "2", carrier, registers=True)
    assert plain.tier == traced.tier == "visible_fail"
    rows = traced.window["rows"]
    # without a simulation time per sample the dump cannot be aligned, so no values are given
    assert all("registers" in r for r in rows) is shown and ("registers_note" in traced.window) is shown
    assert {k: v for k, v in traced.window.items() if k not in {"rows", "registers_note"}} == \
        {k: v for k, v in plain.window.items() if k != "rows"}
    if shown:
        for r in rows:  # out = (count == 2): the trace shows the count that explains each output
            assert (r["registers"]["count"] == "10") == (r["outputs"]["out"]["observed"] == "1")


@needs_icarus
def test_register_trace_reaches_blue_only_when_switched_on(tmp_path):
    _, bugs = load_public_manifest(ROOT / "datasets/manifests/chipbench89.jsonl", ROOT)
    bug = next(b for b in bugs if b.challenge_id == "chipbench:state_machine:Prob020_write_state_machine_two_stage")
    for on in (False, True):
        seen = []
        fake = FakeBlueTransport({hash_payload(bug.buggy_rtl): bug.carrier.clean_rtl}, succeed=lambda u, n: True)

        def transport(fake=fake, **request):
            seen.append(json.loads(request["messages"][-1]["content"]))
            return fake(**request)
        runner = BlueRunner(json_client=_client(transport), simulator=Simulator(tmp_path / str(on)), project_root=ROOT,
                            config=BlueConfig(register_trace=on), max_calls=3)
        runner.run(bug, mode="none", pool=[], inference=BugTypeInference(), matcher=KnowledgeMatcher(), seed=1,
                   allow_escalation=False)
        rows = seen[0]["current_failure_evidence"]["initial_visible_test_failure"]["cycle_window"]["rows"]
        assert all(("registers" in r) is on for r in rows)
        if on:
            assert rows[0]["registers"]["current_state"].startswith("S")  # state values carry their names


def test_manifest_rows_marked_ineligible_are_skipped(tmp_path):
    rows = [json.loads(l) for l in (ROOT / "datasets/manifests/cirfix39.jsonl").read_text().splitlines()[:3]]
    rows[1]["eligible"] = False
    manifest = tmp_path / "m.jsonl"
    manifest.write_text("".join(json.dumps(r) + "\n" for r in rows))
    _, bugs = load_public_manifest(manifest, ROOT)
    ids = {b.challenge_id for b in bugs}
    assert str(rows[1]["case_id"]) not in ids and str(rows[0]["case_id"]) in ids


@needs_icarus
def test_no_evidence_mode_hides_the_test_details(corpus, tmp_path):
    carriers, public = corpus
    ch = next(c for c in public if c.provenance["design"] == "flip_flop__wadden_buggy1")
    seen = []
    fake = FakeBlueTransport({hash_payload(ch.buggy_rtl): ch.carrier.clean_rtl}, succeed=lambda u, n: n >= 1)
    runner = BlueRunner(json_client=_client(lambda **r: (seen.append(json.loads(r["messages"][-1]["content"]))
                                                         or fake(**r))),
                        simulator=Simulator(tmp_path), project_root=ROOT,
                        config=BlueConfig(evidence="none"), max_calls=3)
    runner.run(ch, mode="none", pool=[], inference=BugTypeInference(), matcher=KnowledgeMatcher(), seed=1,
               allow_escalation=False)
    for user in seen:
        text = json.dumps(user["current_failure_evidence"])
        assert "cycle_window" not in text and "first_divergences" not in text and "expected" not in text
        assert user["current_failure_evidence"]["initial_visible_test_failure"]["stage"] == "functional"


@needs_icarus
def test_each_repair_records_calls_tokens_time_and_memory(corpus, tmp_path):
    loop, state, blue, splits, sim = _loop(corpus, tmp_path, rounds=1, succeed=lambda u, n: n >= 1)
    loop.run()
    encounters = [row["encounter"] for row in state.read("encounters")]
    for enc in encounters:
        acc = enc["accounting"]
        assert acc["llm_calls"] == len(enc["attempts"])
        assert acc["input_tokens"] == 100 * acc["llm_calls"] and acc["output_tokens"] == 40 * acc["llm_calls"]
        assert acc["memory_delivered"] is False and acc["memory_items"] == []
        if acc["fixed"]:
            assert acc["fixed_at_attempt"] == 2  # scripted: the second attempt succeeds
    assert all(r["cost"]["tokens"] == {"input": 100, "output": 40} for r in state.read("population"))
    from experiments.loop_probes.repair_costs import repairs, summary
    table = summary(repairs(state.root))
    assert table["round 0"]["repairs"] == len(encounters)


@needs_icarus
def test_specification_reaches_blue_only_when_the_dataset_has_one(corpus, tmp_path):
    all_rows = [json.loads(l) for l in (ROOT / "datasets/manifests/cirfix39.jsonl").read_text().splitlines() if l.strip()]
    rows = [all_rows[0], next(r for r in all_rows if r["golden_rtl"] != all_rows[0]["golden_rtl"])]  # two designs
    spec = tmp_path / "spec.txt"
    spec.write_text("The module counts up and asserts overflow when it wraps.")
    rows[0]["spec"] = str(spec)
    manifest = tmp_path / "m.jsonl"
    manifest.write_text("".join(json.dumps(r) + "\n" for r in rows))
    _, bugs = load_public_manifest(manifest, ROOT)
    seen = {}
    for bug in bugs:
        fake = FakeBlueTransport({hash_payload(bug.buggy_rtl): bug.carrier.clean_rtl}, succeed=lambda u, n: True)
        def transport(fake=fake, cid=bug.challenge_id, **request):
            seen.setdefault(cid, json.loads(request["messages"][-1]["content"]))
            return fake(**request)
        runner = BlueRunner(json_client=_client(transport), simulator=Simulator(tmp_path / bug.challenge_id),
                            project_root=ROOT, max_calls=4)
        runner.run(bug, mode="none", pool=[], inference=BugTypeInference(), matcher=KnowledgeMatcher(), seed=1,
                   allow_escalation=False)
    with_spec, without = seen[str(rows[0]["case_id"])], seen[str(rows[1]["case_id"])]
    assert with_spec["specification"] == spec.read_text()
    assert "specification" not in without  # no spec: the request is unchanged
    spec.write_text("module x; endmodule")  # code in a spec would leak a solution
    with pytest.raises(ValueError, match="specification contains code"):
        load_public_manifest(manifest, ROOT)


def test_evidence_window_labels_clock_cycles_and_edges():
    from r3e.knowledge.feedback import evidence_window
    expected = "time,q\n" + "\n".join(f"{i},{(i // 2) % 2}" for i in range(12))
    observed = "time,q\n" + "\n".join(f"{i},{(i // 2) % 2 if i < 6 else 1 - (i // 2) % 2}" for i in range(12))
    stimulus = "#reset note\ntime,clk,d\n" + "\n".join(f"{i},{1 - i % 2},{i % 3 % 2}" for i in range(12))
    w = evidence_window(expected, observed, stimulus)
    assert w["cycles_compared"] == 6 and "two samples per clock cycle" in w["sampling"]
    assert w["wrong_cycles"]["q"] == {"count": 3, "first_cycles": [3, 4, 5]}
    assert all("edge" in r and "clk" not in r["inputs"] for r in w["rows"])
    assert w["rows"][0]["edge"] == "rising" and w["rows"][1]["edge"] == "falling"


def test_an_unknown_phase_stops_the_run_instead_of_counting_as_a_failed_repair(corpus, tmp_path):
    carriers, public = corpus
    ch = next(c for c in public if c.provenance["design"] == "flip_flop__wadden_buggy1")
    fake = FakeBlueTransport({hash_payload(ch.buggy_rtl): ch.carrier.clean_rtl})
    runner = BlueRunner(json_client=_client(fake), simulator=Simulator(tmp_path), project_root=ROOT, max_calls=3)
    with pytest.raises(ValueError, match="unknown phase"):
        runner.run(ch, mode="none", pool=[], inference=BugTypeInference(), matcher=KnowledgeMatcher(), seed=1,
                   allow_escalation=False, phase="no_such_phase")
    assert not fake.requests


def test_text_edits_apply_exactly_once_or_fail_with_the_reason():
    from r3e.loop.blue_provider import BlueOutputViolation, EditNotApplicable, apply_text_edits
    src = "module m(input a, output y);\n  assign y = a;\nendmodule\n"
    out = apply_text_edits(src, [{"find": "assign y = a;", "replace": "assign y = ~a;"},
                                 {"find": "", "replace": "module helper(); endmodule"}])
    assert "assign y = ~a;" in out and out.rstrip().endswith("module helper(); endmodule")
    # whitespace in find may differ from the source
    assert "assign y = ~a;" in apply_text_edits(src, [{"find": "assign  y =\n a;", "replace": "assign y = ~a;"}])
    assert "assign" not in apply_text_edits(src, [{"find": "  assign y = a;\n", "replace": ""}])  # deletion
    with pytest.raises(EditNotApplicable, match="edit 0: find text not found"):
        apply_text_edits(src, [{"find": "assign z = a;", "replace": ""}])
    with pytest.raises(EditNotApplicable, match="found 2 times"):
        apply_text_edits(src + src, [{"find": "assign y = a;", "replace": ""}])
    with pytest.raises(BlueOutputViolation, match="find and replace"):
        apply_text_edits(src, [{"find": "a"}])


@needs_icarus
def test_blue_edit_answers_repair_and_bad_edits_are_reported_back(corpus, tmp_path):
    from r3e.loop.fakes import _reply
    carriers, public = corpus
    ch = next(c for c in public if c.provenance["design"] == "flip_flop__wadden_buggy1")
    import difflib
    bug, ref = ch.buggy_rtl.splitlines(keepends=True), ch.carrier.clean_rtl.splitlines(keepends=True)
    fix_edits = [{"find": "".join(bug[a:b]), "replace": "".join(ref[c:d])}
                 for tag, a, b, c, d in difflib.SequenceMatcher(None, bug, ref, autojunk=False).get_opcodes()
                 if tag == "replace" and "".join(bug[a:b]).strip()][::-1]
    assert fix_edits and all(ch.buggy_rtl.count(e["find"]) == 1 for e in fix_edits)
    seen = []

    def transport(**request):
        user = json.loads(request["messages"][-1]["content"])
        seen.append(user)
        edits = [{"find": "no such text", "replace": ""}] if len(seen) == 1 else fix_edits
        return _reply({"edits": edits, "edit": "scripted edit"}, len(seen))
    runner = BlueRunner(json_client=_client(transport), simulator=Simulator(tmp_path), project_root=ROOT,
                        config=BlueConfig(answer_format="edits"), max_calls=5)
    enc = runner.run(ch, mode="none", pool=[], inference=BugTypeInference(), matcher=KnowledgeMatcher(), seed=1,
                     allow_escalation=False)
    assert enc.solved_within_budget and len(enc.repair_attempts) == 2
    assert enc.attempts[0]["verdict_tier"] == "compile_fail" and enc.attempts[1]["answer_form"] == "edits"
    told = seen[1]["current_failure_evidence"]["previous_attempts"][0]["visible_feedback"]["message"]
    assert "find text not found" in told  # Blue learns why its edit did not apply
    assert "edits" in seen[0]["required_output_schema"] and "Keep the module names" in seen[0]["lens_instruction"]


def test_full_answer_format_keeps_the_original_request():
    from r3e.loop.blue_provider import SYSTEM_PROMPT, BlueProvider
    sent = {}

    class Client:
        def complete_json(self, *, messages, seed):
            sent["messages"] = messages
            return {"result": {"replacement_rtl": "module m(); endmodule", "edit": "x"}, "raw_response_hash": "h",
                    "input_tokens": 1, "output_tokens": 1, "request_hash": "r"}
    out = BlueProvider(Client()).generate_candidate(
        evidence={}, artifact={"buggy_rtl_source": "module m(); endmodule"}, slot={"candidate_seed": 1, "lens_id": "l"},
        lens_instruction="i", prompt_hash="p", candidate_id="c")
    user = json.loads(sent["messages"][1]["content"])
    assert sent["messages"][0]["content"] == SYSTEM_PROMPT and "edits" not in json.dumps(user["required_output_schema"])
    assert out["patch_payload"]["answer_form"] == "full"


def test_a_json_request_without_the_word_json_is_refused_before_any_call():
    sent = []
    budget = BudgetedClient(_client(lambda **r: sent.append(r) or {}), max_calls=5)
    with pytest.raises(ValueError, match="mention 'json'"):
        budget.complete_json(messages=[{"role": "system", "content": "Return {a, b}."},
                                       {"role": "user", "content": "{}"}], seed=1)
    assert not sent and budget.total_calls == 0


_COUNT = """module top(input clk, input rst, output reg [3:0] q, output hit);
  always @(posedge clk) q <= rst ? 4'd0 : q + 4'd1;
  assign hit = (q == 4'd%d);
endmodule
"""


@needs_icarus
def test_repair_verification_separates_passing_the_test_from_matching_the_reference(tmp_path):
    from experiments.loop_probes.repair_verify import bounded_equivalence, random_simulation, summarise
    carrier = Carrier(carrier_id="c", cluster_id="c", clean_rtl=_COUNT % 9, top_module="top", visible_tb=())
    same = (_COUNT % 9).replace("q + 4'd1", "4'd1 + q")  # rewritten, equivalent
    late = _COUNT % 11                                     # differs only when q reaches 9 or 11
    sim = Simulator(tmp_path / "sim")
    for name, cand, expected in (("same", same, "no_mismatch_found"), ("late", late, "mismatch_found")):
        work = tmp_path / name
        work.mkdir()
        methods = [random_simulation(carrier.clean_rtl, cand, carrier, sim, seeds=[1], cycles=40, work=work),
                   bounded_equivalence(carrier.clean_rtl, cand, carrier, depth=14, work=work)]
        assert summarise(methods) == expected, methods
        if expected == "mismatch_found":
            assert all(m["outcome"] == "mismatch" for m in methods)
            assert methods[0]["counterexample"]["signal"] == "hit"
        else:
            assert methods[1]["outcome"] == "no_mismatch_within_bound" and methods[1]["scope"]["steps"] == 14
    shallow = tmp_path / "shallow"
    shallow.mkdir()  # a bound shorter than the divergence finds nothing: the scope says how far it looked
    assert bounded_equivalence(carrier.clean_rtl, late, carrier, depth=5, work=shallow)["outcome"] == \
        "no_mismatch_within_bound"
