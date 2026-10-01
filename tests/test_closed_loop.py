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
from r3e.loop.corpus import load_public_manifest, split_by_cluster
from r3e.loop.env import LlmEnvError, build_client, load_llm_env
from r3e.loop.evaluate import evaluate_holdout
from r3e.loop.fakes import (
    FakeBlueTransport,
    FakeRedTransport,
    knowledge_types,
    nearest_clean_resolver,
)
from r3e.loop.operators import apply_edit, enumerate_sites
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
        budget.complete_json(messages=[{"role": "user", "content": "{}"}], seed=1)
    with pytest.raises(CallBudgetExceeded):
        budget.complete_json(messages=[{"role": "user", "content": "{}"}], seed=1)
    assert len(sent) == 1
    assert budget.report()["learning"]["calls"] == 1


# ------------------------------------------------------------- red side


def test_operator_sites_apply_legal_edits(corpus):
    carrier = _carrier(corpus, "first_counter_overflow")
    sites = enumerate_sites(carrier)
    assert {"condition_negate", "compare_swap", "constant_shift", "assign_kind_swap"} <= {s.operator for s in sites}
    negate = next(s for s in sites if s.operator == "condition_negate")
    edited = apply_edit(carrier, negate, "negate")
    assert edited != carrier.clean_rtl and "(!(" in edited
    with pytest.raises(ValueError):
        apply_edit(carrier, negate, "not-an-option")


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
    splits = split_by_cluster(carriers, seed=0)
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
    loop, state, blue, splits, sim = _loop(corpus, tmp_path, objective="cost")
    loop.run()
    rounds = state.read("rounds")
    assert [r["round"] for r in rounds] == [0, 1]
    assert rounds[-1]["active"], "a knowledge item should be promoted under the cost objective"
    later = [row["encounter"] for row in state.read("encounters") if row["round"] == 1]
    assert any(e["shown_items"] for e in later), "promoted knowledge should reach later encounters"
    assert all(r["decision"] in {"active", "rejected"} for r in state.read("qualification"))
    calls_before = blue.budget.total_calls
    loop.run()  # resume: everything is complete, nothing re-runs
    assert blue.budget.total_calls == calls_before
    assert len(state.read("rounds")) == 2

    holdout = frozen_challenges(splits["holdout"], corpus[1], sim, per_carrier=1, seed=2000)[:3]
    report = evaluate_holdout(state, blue, holdout, modes=("none", "matched", "shuffled"), rounds=[1])
    summary = report["snapshots"][0]["summary"]
    assert set(summary) == {"none", "matched", "shuffled"}
    assert summary["none"]["negative_transfer"] == 0


@needs_icarus
def test_gate_rejects_knowledge_without_gain(corpus, tmp_path):
    loop, state, *_ = _loop(corpus, tmp_path, objective="solve_rate", rounds=1)
    loop.run()
    decisions = state.read("qualification")
    assert decisions and all(d["decision"] == "rejected" for d in decisions)
    assert {d["reason"] for d in decisions} <= {"no_net_gain", "no_coverage"}
    assert state.active_items() == []


@needs_icarus
def test_resume_refuses_changed_frozen_config(corpus, tmp_path):
    loop, state, *_ = _loop(corpus, tmp_path, rounds=1)
    loop.run()
    with pytest.raises(RuntimeError):
        _loop(corpus, tmp_path, rounds=2)


def test_cli_refuses_real_calls_without_explicit_approval(tmp_path):
    out = subprocess.run(
        [sys.executable, "-B", "-m", "r3e.loop.cli", "--state-dir", str(tmp_path / "s")], cwd=ROOT,
        capture_output=True, text=True, timeout=300,
    )
    assert out.returncode == 2 and "refusing" in out.stdout
