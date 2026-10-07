from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from experiments.loop_probes import memory_eval as me
from r3e.knowledge import analyze_rtl, build_profile
from r3e.knowledge.schema import KnowledgeItem
from r3e.loop.blue import BlueConfig
from r3e.loop.corpus import load_public_manifest
from r3e.loop.env import build_client
from r3e.loop.fakes import FakeBlueTransport, knowledge_types, nearest_clean_resolver
from r3e.loop.sim import Simulator
from r3e.protocol.hashing import hash_payload
from r3e.providers.openai_compatible import OpenAICompatibleProviderViolation

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "datasets/manifests/chipbench89.jsonl"
needs_icarus = pytest.mark.skipif(not all(shutil.which(n) for n in ("iverilog", "vvp")),
                                  reason="Icarus is unavailable")
CFG = BlueConfig(budget_k=3, escalation_k=0, register_trace=True, answer_format="full")


@pytest.fixture(scope="module")
def bugs():
    _, chs = load_public_manifest(ROOT / MANIFEST, ROOT)
    picked, seen = [], set()
    for ch in chs:  # two bugs on two different problems
        if ch.carrier.cluster_id not in seen:
            picked.append(ch)
            seen.add(ch.carrier.cluster_id)
        if len(picked) == 2:
            return picked


def _case():
    return {"observed_failure": {}, "fault": {"faulty_lines": ["assign y = a;"]},
            "repair": {"fixed_lines": ["assign y = ~a;"], "explanation_by_repairer": "inverted"},
            "identifier_roles": {}, "did_not_work": [], "verification": {"result": "visible_pass"}}


def _item(applicability):
    return KnowledgeItem.create(item_id="K_test", version=1, applicability=applicability, card={"cases": [_case()]},
                                example=None, evidence={"support": 1, "source_episode_hashes": ["sha256:" + "a" * 64]},
                                author={"author_id": "t"})


def _item_matching(bug, tmp):
    v = Simulator(tmp / "isim").verdict(bug.buggy_rtl, bug.carrier, registers=True)
    prof = build_profile(v.feedback, analyze_rtl(bug.buggy_rtl))
    return _item({"bug_type": "condition_expression", "status": prof.status, "causal": prof.causal})


def _execute(tmp, bugs, items, transport, *, max_calls=200, repeats=1, frozen=None):
    frozen = frozen or {"eval_bugs": len(bugs), "repeats": repeats, "test": 1}
    factory = lambda: build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=transport)
    return me.execute(out=tmp / "out", bugs=bugs, items=items, client_factory=factory, max_calls=max_calls,
                      repeats=repeats, seed=7, cfg=CFG, frozen=frozen)


def _fake(bugs, succeed):
    fake = FakeBlueTransport(nearest_clean_resolver([b.carrier.clean_rtl for b in bugs]), succeed=succeed)
    sent = []

    def transport(**request):
        sent.append(json.loads(request["messages"][-1]["content"]))
        return fake(**request)
    return transport, sent


def _units(tmp):
    return [json.loads(l) for l in (tmp / "out" / "units.jsonl").read_text().splitlines()]


def _calls(tmp):
    return len((tmp / "out" / "calls.jsonl").read_text().splitlines())


def test_worst_case_reservation_follows_the_configured_retries():
    assert me.worst_case(CFG) == (1 + 2) + 2 * (2 + 2) == 11
    assert me.worst_case(BlueConfig(budget_k=3, max_infra_retries=0)) == 1 + 2 * 2


@needs_icarus
def test_all_first_attempts_pass_no_forks_and_conditional_rates_not_applicable(tmp_path, bugs):
    transport, _ = _fake(bugs, lambda u, n: True)
    _execute(tmp_path, bugs, [_item_matching(bugs[0], tmp_path)], transport, repeats=2)
    assert {u["outcome"] for u in _units(tmp_path)} == {"first_attempt_passed"}
    assert _calls(tmp_path) == 4  # one shared call per unit, nothing else
    rep = me.report(tmp_path / "out")["primary_complete_repeats"]
    assert rep["forks"] == 0 and rep["all_units_pass_rate"] == {"without_memory": 1.0, "with_memory": 1.0}
    assert rep["fork_recovery_rate"] == {a: "not applicable" for a in me.ARMS}
    assert rep["memory_delivery_rate"] == "not applicable"


@needs_icarus
def test_forks_share_one_first_attempt_and_memory_reaches_only_its_arm(tmp_path, bugs):
    # memory "helps": an attempt succeeds only when memory was injected into its request
    transport, sent = _fake(bugs, lambda u, n: bool(knowledge_types(u)))
    _execute(tmp_path, bugs, [_item_matching(b, tmp_path) for b in bugs[:1]], transport)
    units = _units(tmp_path)
    forked = [u for u in units if u["outcome"] == "forked"]
    assert forked
    for u in forked:
        assert u["without_memory"]["tiers"][0] == u["with_memory"]["tiers"][0] == u["first_attempt_tier"]
    # both branches' second attempts saw the identical first attempt and feedback
    seconds = [s for s in sent if s["candidate_seed"] % 100 == 1]
    by_case = {}
    for s in seconds:
        by_case.setdefault(s["current_buggy_rtl"], []).append(s["current_failure_evidence"]["previous_attempts"])
    assert all(len(v) == 2 and v[0] == v[1] for v in by_case.values())
    assert not any(knowledge_types(s) for s in sent if s["candidate_seed"] % 100 == 0)  # first attempts: no memory
    # the shared first call is counted once: ledger = shared + both branches' own calls
    assert _calls(tmp_path) == sum(u["shared_first"]["accounting"]["llm_calls"] + sum(
        u[a]["accounting"]["llm_calls"] for a in me.ARMS if a in u) for u in units)
    rep = me.report(tmp_path / "out")["primary_complete_repeats"]
    matched = [u for u in forked if u["with_memory"]["memory_injected"]]
    assert rep["helped"] == len(matched) >= 1 and rep["harmed"] == 0
    assert rep["memory_delivery_rate"] == round(len(matched) / len(forked), 3)


@needs_icarus
def test_both_arms_exhausting_their_attempts_are_recorded_as_wrong_repairs(tmp_path, bugs):
    transport, _ = _fake(bugs, lambda u, n: False)
    _execute(tmp_path, bugs, [], transport)
    for u in _units(tmp_path):
        assert u["outcome"] == "forked"
        for a in me.ARMS:
            assert not u[a]["solved"] and u[a]["wrong_repair"] == 2 and u[a]["no_answer"] == 0
    assert _calls(tmp_path) == 5 * len(bugs)
    rep = me.report(tmp_path / "out")["primary_complete_repeats"]
    assert rep["fork_recovery_rate"] == {"without_memory": 0.0, "with_memory": 0.0}


@needs_icarus
def test_retry_exhaustion_makes_the_unit_inconclusive_within_the_reserved_calls(tmp_path, bugs):
    def transport(**request):
        raise OpenAICompatibleProviderViolation("provider down")
    _execute(tmp_path, bugs, [], transport)
    units = _units(tmp_path)
    assert {u["outcome"] for u in units} == {"inconclusive_first_attempt"}
    assert _calls(tmp_path) == 3 * len(bugs)  # three failed calls each: within the 1 + R reserved for it
    rep = me.report(tmp_path / "out")["primary_complete_repeats"]
    assert rep["inconclusive_units"] == len(bugs) and rep["valid_units"] == 0


@needs_icarus
def test_budget_reservation_stops_before_a_unit_and_never_reports_a_full_schedule(tmp_path, bugs):
    transport, _ = _fake(bugs, lambda u, n: True)  # each unit costs 1 call, but 11 must stay free
    status = _execute(tmp_path, bugs, [], transport, max_calls=13, repeats=3)
    assert status["stopped"]["reason"] == "budget_reservation" and status["calls_used"] == 3
    rep = me.report(tmp_path / "out")
    assert rep["repeats_completed"] == "1 of 3"
    assert rep["unfinished_repeats"] == {1: {**rep["unfinished_repeats"][1], "units": 1, "of": 2}}
    assert rep["all_completed_units_disclosed"] == 3


@needs_icarus
def test_resume_skips_completed_units_and_refuses_an_interrupted_one(tmp_path, bugs):
    transport, _ = _fake(bugs, lambda u, n: True)
    _execute(tmp_path, bugs, [], transport, max_calls=13, repeats=2)  # stops after 2 units
    _execute(tmp_path, bugs, [], transport, max_calls=200, repeats=2)  # resumes the rest
    keys = [u["unit"] for u in _units(tmp_path)]
    assert len(keys) == len(set(keys)) == 4 and _calls(tmp_path) == 4  # no unit ran twice
    with pytest.raises(me.ResumeRefused, match="configuration differs"):
        _execute(tmp_path, bugs, [], transport, repeats=2, frozen={"eval_bugs": 2, "repeats": 2, "test": 2})

    calls = {"n": 0}

    def crashing(**request):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt  # the process dies mid-unit
        return transport(**request)
    other = tmp_path / "crash"
    other.mkdir()
    fail_transport, _ = _fake(bugs, lambda u, n: False)
    with pytest.raises(KeyboardInterrupt):
        _execute(other, bugs, [], lambda **r: (crashing(**r) if calls["n"] < 2 else fail_transport(**r)))
    with pytest.raises(me.ResumeRefused, match="started without completion"):
        _execute(other, bugs, [], fail_transport)


def test_frozen_inputs_are_recomputed_not_trusted(tmp_path):
    _, chs = load_public_manifest(ROOT / MANIFEST, ROOT)
    bug = chs[0]
    plan = tmp_path / "plan"
    plan.mkdir()
    (plan / "eval_set.json").write_text(json.dumps({
        "manifest": MANIFEST, "manifest_sha256": hash_payload((ROOT / MANIFEST).read_text()),
        "bugs": [{"challenge_id": bug.challenge_id, "buggy_rtl_hash": bug.buggy_hash,
                  "reference_hash": hash_payload(bug.carrier.clean_rtl), "spec_hash": hash_payload(bug.carrier.spec)}]}))
    item = _item({"bug_type": "condition_expression", "status": {}, "causal": {}})
    snap = {"matcher": {"weights": me.KnowledgeMatcher().config()["weights"], **me.MATCHER},
            "items": [{"item": item.to_dict()}]}
    (plan / "memory_primary.json").write_text(json.dumps(snap))
    good = hash_payload([item.item_hash])
    assert len(me.load_frozen(plan, good)[1]) == 1
    with pytest.raises(me.ResumeRefused, match="snapshot hash"):
        me.load_frozen(plan, "sha256:" + "0" * 64)
    tampered = json.loads(json.dumps(snap))
    tampered["items"][0]["item"]["card"]["cases"][0]["repair"]["fixed_lines"] = ["edited"]  # hash kept
    (plan / "memory_primary.json").write_text(json.dumps(tampered))
    with pytest.raises(Exception):
        me.load_frozen(plan, good)
    (plan / "memory_primary.json").write_text(json.dumps(snap))
    rows = json.loads((plan / "eval_set.json").read_text())
    rows["bugs"][0]["buggy_rtl_hash"] = "sha256:" + "1" * 64
    (plan / "eval_set.json").write_text(json.dumps(rows))
    with pytest.raises(me.ResumeRefused, match="does not match its frozen hashes"):
        me.load_frozen(plan, good)


@needs_icarus
def test_every_passing_candidate_is_verified_and_reported_per_arm(tmp_path, bugs):
    transport, _ = _fake(bugs, lambda u, n: n >= 1)  # first attempt fails, the next one returns the reference
    _execute(tmp_path, bugs, [], transport)
    rows = me.verify(tmp_path / "out", bugs)
    assert len(rows) == 2 * len(bugs) and {r["arm"] for r in rows} == set(me.ARMS)
    assert all(r["supplementary"]["status"] == "no_mismatch_found" for r in rows)  # the fake returns the reference
    summary = me.report(tmp_path / "out")["supplementary_verification"]
    assert summary["independent_check_records"] == {"total": 2 * len(bugs), **{a: {"no_mismatch_found": len(bugs)}
                                                                               for a in me.ARMS}}
