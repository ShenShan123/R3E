"""Measurement side of the repair-feedback protocol: packets, journal, endpoints, confirmation."""
import json
import shutil

import pytest

from experiments.loop_probes import red907, repair_feedback as rf, red907_measure as rm
from r3e.loop import repo_blue as rb
from r3e.loop.red_regression import materialize
from tests.test_red907_measure import TARGET, BAD, setup_task
from tests.test_red_regression import fixture, raw
from tests.test_repo_blue import WordCounter, runner, scripted
from tests.test_witness_matrix import CLEAN, ROW_Y

needs_icarus = pytest.mark.skipif(not shutil.which("iverilog"), reason="iverilog is required")
ONE = rb.RepoBlueConfig(budget_k=1, max_infra_retries=0)
FIX = {"edits": [{"file": "rtl/toy.v", "find": "assign y = a;", "replace": "assign y = a + 1;"}], "edit": "SECRET-DESCRIPTION"}
CONST = {"edits": [{"file": "rtl/toy.v", "find": "assign y = a;", "replace": "assign y = 4'd6;"}], "edit": "e"}  # public pass, hidden fail
NOOP = {"edits": [{"file": "rtl/toy.v", "find": "assign y = a;", "replace": "assign y = a;"}], "edit": "e"}
MUTATION = [{"find": "assign y = a + 1;", "replace": "assign y = a;"}]
SOURCE = {"round": 1, "arm": "feedback", "slot_id": "r1_c0_feedback", "carrier_id": "toy", "public_hash": "sha256:p"}


def trial(tmp_path, reply, name="blue", encounters=1):
    task_dir, ev = setup_task(tmp_path) if not (tmp_path / "task").exists() else (tmp_path / "task", None)
    transport, sent = scripted([reply] * encounters if not isinstance(reply, list) else reply)
    _, client = runner(transport, cfg=ONE, max_calls=encounters)
    rm.run_blue(task_dir, tmp_path / name, client=client, counter=WordCounter(), cfg=ONE, seed=5,
                encounters=encounters, direct_only=True, endpoint_protocol="screening" if encounters == 1 else "confirmation")
    return task_dir, sent


def evaluator(tmp_path):
    from tests.test_red907_measure import EVAL
    from r3e.protocol.hashing import hash_payload
    return {"files": {"evaluator/toy/eval_tb.sv": hash_payload(EVAL)},
            "run": {"top": "tb", "rtl": ["rtl/toy.v", "rtl/dep.v"], "pass_marker": "TESTBENCH PASS", "fail_marker": "CHECK_FAIL"}}


@needs_icarus
def test_packet_is_public_only_and_releases_only_public_passes(tmp_path):
    task_dir, _ = trial(tmp_path, FIX)
    (tmp_path / "evaluator/toy/eval_tb.sv").write_text("// HIDDEN-SENTINEL\n")  # the builder must never read it
    built = rf.build_packet(tmp_path / "blue", task_dir, mutation_edits=MUTATION, source=SOURCE, counter=WordCounter())
    packet = built["packet"]
    text = json.dumps(packet)
    assert packet["label"] == "public_pass_single_sample_hidden_unknown"
    assert packet["public_rows"] == {"W_bug": "visible_pass", "W_preserve": "visible_pass"}
    assert "assign y = a + 1;" in packet["blue_patch"] and packet["mutation"]["edits"] == MUTATION
    assert len(packet["public_evidence"]) <= ONE.max_evidence_chars
    assert not any(s in text for s in ("HIDDEN-SENTINEL", "SECRET-DESCRIPTION", "evaluator", "oracle"))
    with pytest.raises(rm.MeasurementRefused, match="do not reproduce"):
        rf.build_packet(tmp_path / "blue", task_dir, mutation_edits=[{"find": "assign z = a;", "replace": "assign z = 0;"}],
                        source=SOURCE, counter=WordCounter())
    with pytest.raises(ValueError, match="feedback-arm round-1"):
        rf.build_packet(tmp_path / "blue", task_dir, mutation_edits=MUTATION, source={**SOURCE, "arm": "independent"},
                        counter=WordCounter())

    class Huge(WordCounter):
        def count(self, text):
            return rf.PACKET_TOKENS + 1

    over = rf.build_packet(tmp_path / "blue", task_dir, mutation_edits=MUTATION, source=SOURCE, counter=Huge())
    assert over["omitted"] == "packet_over_token_limit" and "packet" not in over
    trial(tmp_path, TARGET, name="blue_fail")
    miss = rf.build_packet(tmp_path / "blue_fail", task_dir, mutation_edits=MUTATION, source=SOURCE, counter=WordCounter())
    assert miss == {"omitted": "not_public_pass", "source": SOURCE}
    bank = rf.build_bank([miss], ["toy"])
    assert bank["packets"] == [] and bank["omissions"][0]["omitted"] == "not_public_pass"


def test_delivery_journal_enforces_empty_control_and_round_barrier(tmp_path):
    journal = tmp_path / "journal.jsonl"
    packet = {"source": SOURCE, "provenance": {"x": "h"}}
    bank = {"version": rf.PACKET_VERSION, "packets": [packet], "omissions": []}
    msgs = [{"role": "user", "content": "json"}]
    rf.record_delivery(journal, round_index=1, arm="feedback", carrier_id="toy", messages=msgs, bank=rf.EMPTY_BANK)
    with pytest.raises(ValueError, match="empty bank"):
        rf.record_delivery(journal, round_index=1, arm="feedback", carrier_id="toy", messages=msgs, bank=bank)
    with pytest.raises(ValueError, match="empty bank"):
        rf.record_delivery(journal, round_index=2, arm="independent", carrier_id="toy", messages=msgs, bank=bank)
    rf.append_event(journal, {"event": "trial_complete", "slot_id": "r1_c0_feedback", "round": 1})
    with pytest.raises(ValueError, match="barrier"):
        rf.record_delivery(journal, round_index=2, arm="feedback", carrier_id="toy", messages=msgs, bank=bank)
    rf.append_event(journal, {"event": "round_barrier", "round": 1})
    row = rf.record_delivery(journal, round_index=2, arm="feedback", carrier_id="toy", messages=msgs, bank=bank)
    assert json.loads(row["bank_bytes"]) == bank and row["request_hash"].startswith("sha256:")
    late = {**SOURCE, "slot_id": "late"}
    rf.append_event(journal, {"event": "trial_complete", "slot_id": "late", "round": 1})
    with pytest.raises(ValueError, match="not a completed earlier feedback trial"):
        rf.record_delivery(journal, round_index=2, arm="feedback", carrier_id="toy", messages=msgs,
                           bank={**bank, "packets": [{"source": late, "provenance": {}}]})
    assert [json.loads(l)["seq"] for l in journal.read_text().splitlines()] == [0, 1, 2, 3, 4]


@needs_icarus
@pytest.mark.parametrize("reply,category,functional", [
    (NOOP, "valid_no_op", True),                      # applied, changed nothing: unrepaired, not a missing answer
    (BAD, "invalid_edit", False),
    ("NO_ANSWER", "no_answer", False),
    (TARGET, "public_functional_failure", True),      # new regression in W_preserve
    (CONST, "public_pass_hidden_fail", True),
    (FIX, "public_pass_hidden_pass", False),
])
def test_broad_functional_endpoints(tmp_path, reply, category, functional):
    task_dir, _ = trial(tmp_path, reply)
    out = rf.classify_trials(tmp_path / "blue", task_dir, evaluator(tmp_path), tmp_path)
    (row,) = out["encounters"]
    assert row["category"] == category and bool(row.get("functional_failure_candidate")) == functional
    items = json.loads((tmp_path / "blue/review_items.json").read_text())
    assert len(items) == int(functional) and "arm" not in json.dumps(items)
    if reply is NOOP:
        assert row["original_defect_persists"] and not row["new_regression"] and row["patch_vs_task"] == ""
    if reply is TARGET:
        assert row["new_regression"] and not row["original_defect_persists"]


@needs_icarus
def test_confirmation_runs_both_members_and_needs_two_reviewed_failures(tmp_path):
    task_dir, _ = setup_task(tmp_path)
    member = lambda arm, n: {"arm": arm, "task_dir": task_dir, "seed": 70 + len(arm), "reviewed_functional_failures": n}
    pairs = [{"round": 2, "pair_id": "p0", "carrier_id": "toy", "members": [member("feedback", 1), member("independent", 0)]},
             {"round": 2, "pair_id": "p1", "carrier_id": "toy", "members": [member("feedback", 0), member("independent", 0)]}]
    with pytest.raises(ValueError, match="cannot cover"):
        rf.confirmation_manifest(pairs, approved_calls=5)
    manifest = rf.confirmation_manifest(pairs, approved_calls=12)
    assert manifest["calls"] == 6 and [p["pair_id"] for p in manifest["pairs"]] == ["p0"]
    assert [m["arm"] for m in manifest["pairs"][0]["members"]] == ["feedback", "independent"]
    transport, sent = scripted([TARGET, CONST, FIX, FIX, FIX, NOOP])
    _, client = runner(transport, cfg=ONE, max_calls=6)
    out = rf.run_confirmation(manifest, tmp_path / "confirm", client=client, counter=WordCounter())
    assert client.total_calls == 6 and json.loads((out / "manifest.json").read_text()) == json.loads(json.dumps(manifest, default=str))
    fb = out / "p0" / "feedback"
    s = rf.classify_trials(fb, task_dir, evaluator(tmp_path), tmp_path, encounters=3)["summary"]
    assert s["functional_failure_candidates"] == 2 and not s["confirmed_within_carrier_difficulty"]
    items = json.loads((fb / "review_items.json").read_text())
    (fb / "review_decisions.json").write_text(json.dumps({i["item"]: {"decision": rf.DECISION} for i in items}))
    s = rf.classify_trials(fb, task_dir, evaluator(tmp_path), tmp_path, encounters=3)["summary"]
    assert s["confirmed_within_carrier_difficulty"] and s["mixed_confirmation"]
    with pytest.raises(rm.MeasurementRefused, match="one-attempt"):
        rf.classify_trials(fb, task_dir, evaluator(tmp_path), tmp_path, encounters=1)


@needs_icarus
def test_no_prediction_proposal_gets_no_shortcut_record(tmp_path):
    pkg, freeze = fixture(tmp_path)
    proposal = materialize(raw(CLEAN), CLEAN, "toy")
    proposal.pop("predicted")
    rec = red907.check_proposal(pkg, freeze, proposal, tmp_path / "check", seen=set())
    assert rec["status"] == "admitted" and "shortcut_diagnostic" not in rec
    assert (tmp_path / "check/blue_task/public/repo/rtl/toy.v").read_text() == ROW_Y
