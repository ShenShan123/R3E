"""Continuous-loop Blue delivery (runner side) and snapshot-bound confirmation; fake clients only."""
import json
import shutil

import pytest

from experiments.loop_probes import continuous_measure as cm, red907_measure as rm, repair_feedback as rf
from r3e.loop import repo_blue as rb
from r3e.protocol.hashing import canonical_json, hash_payload
from tests.test_red907_measure import BAD, setup_task
from tests.test_repo_blue import WordCounter, runner, scripted

needs_icarus = pytest.mark.skipif(not shutil.which("iverilog"), reason="iverilog is required")
FIX = {"edits": [{"file": "rtl/toy.v", "find": "assign y = a;", "replace": "assign y = a + 1;"}], "edit": "e"}
POLICY = "sha256:retrieval-policy"


def envelope(task_dir, entries=(), **override):
    payload = {"version": rb.EXPERIENCE_VERSION, "notice": rb.EXPERIENCE_NOTICE,
               "entries": [{"entry_hash": f"sha256:e{i}", "content": {
                   "validation": rb.EXPERIENCE_LABEL, "specification_excerpt": "toy spec",
                   "applied_patch": patch, "public_rows": {"W_bug": "visible_pass"}}} for i, patch in enumerate(entries)]}
    env = {"version": rb.DELIVERY_VERSION, "snapshot_id": "B_1", "snapshot_hash": "sha256:snap",
           "state_hash": "sha256:state", "task_hash": rb.public_task_hash(rb.RepoTask.load(task_dir)),
           "retrieval_policy_hash": POLICY, "payload": payload,
           "selected_entry_hashes": [e["entry_hash"] for e in payload["entries"]],
           "source_record_hashes": [f"sha256:src{i}" for i in range(len(entries))], "omissions": [],
           "payload_hash": hash_payload(payload)}
    return {**env, **override}


def run(tmp_path, replies, env, *, cfg=rb.RepoBlueConfig(budget_k=2, max_infra_retries=0), journal="j.jsonl"):
    task_dir = tmp_path / "task"
    transport, sent = scripted(replies)
    r, client = runner(transport, cfg=cfg, max_calls=4, archive=tmp_path / "archive")
    task = rb.RepoTask.load(task_dir)
    s = rb.setup(task, WordCounter(), cfg)
    result = r.run(task, s, seed=3, experience=env, experience_journal=tmp_path / journal if journal else None)
    return result, sent


def messages(sent_row):
    return [{"role": "system", "content": sent_row["system"]}, {"role": "user", "content": canonical_json(sent_row["user"])}]


@needs_icarus
def test_legacy_and_empty_selection_keep_the_no_experience_prompt(tmp_path):
    task_dir, _ = setup_task(tmp_path)
    legacy, sent_legacy = run(tmp_path, [FIX], None, journal=None)
    empty, sent_empty = run(tmp_path, [FIX], envelope(task_dir))
    strip = lambda u: {k: v for k, v in u.items() if k != "current_failure_evidence"}  # its logs carry temp paths
    assert "experience" not in legacy and strip(sent_legacy[0]["user"]) == strip(sent_empty[0]["user"])
    assert "repair_experience" not in sent_empty[0]["user"]
    (row,) = [json.loads(l) for l in (tmp_path / "j.jsonl").read_text().splitlines()]
    assert row["delivered"] is False and row["experience_tokens"] == 0 and row["event"] == "request"
    assert row["request_hash"] == hash_payload(messages(sent_empty[0]))
    assert empty["experience"]["delivered"] is False


@needs_icarus
def test_nonempty_delivery_is_fixed_from_attempt_one_and_journaled_before_each_request(tmp_path):
    task_dir, _ = setup_task(tmp_path)
    env = envelope(task_dir, ["--- a\n+++ b\n-x\n+y\n"])
    result, sent = run(tmp_path, [BAD, FIX], env)
    assert result["outcome"] == "fixed" and len(sent) == 2
    assert all(r["user"]["repair_experience"] == env["payload"] for r in sent)
    rows = [json.loads(l) for l in (tmp_path / "j.jsonl").read_text().splitlines()]
    assert [(r["attempt"], r["request_ordinal"], r["sequence"]) for r in rows] == [(1, 1, 0), (2, 2, 1)]
    assert [r["request_hash"] for r in rows] == [hash_payload(messages(x)) for x in sent]
    assert rows[0]["payload_bytes"] == canonical_json(env["payload"]) and rows[0]["delivered"]
    assert rows[0]["experience_tokens"] == WordCounter().count(canonical_json({"repair_experience": env["payload"]}))
    assert result["experience"]["delivery_hash"] == hash_payload(env)


@needs_icarus
@pytest.mark.parametrize("mutate", [
    lambda e: {**e, "task_hash": "sha256:other"},
    lambda e: {**e, "payload_hash": "sha256:wrong"},
    lambda e: {**e, "extra": 1},
    lambda e: {**e, "selected_entry_hashes": list(reversed(e["selected_entry_hashes"]))},
    lambda e: {**e, "source_record_hashes": []},
    lambda e: {**e, "payload": {**e["payload"], "entries": e["payload"]["entries"] * 2}},
])
def test_inconsistent_envelopes_are_refused_before_any_request(tmp_path, mutate):
    task_dir, _ = setup_task(tmp_path)
    env = mutate(envelope(task_dir, ["p0", "p1"]))
    with pytest.raises(rb.RepoTaskError, match="envelope rejected"):
        run(tmp_path, [FIX], env)
    assert not (tmp_path / "j.jsonl").exists()


@needs_icarus
def test_size_limits_journal_failures_and_context_preflight(tmp_path):
    task_dir, _ = setup_task(tmp_path)
    env = envelope(task_dir, ["p" * 10])

    class Huge(WordCounter):
        def count(self, text):
            return rb.EXPERIENCE_ENTRY_TOKENS + 1 if "entry_hash" in text else super().count(text)

    task = rb.RepoTask.load(task_dir)
    with pytest.raises(rb.RepoTaskError, match="token limit"):
        rb.check_delivery(env, task, Huge())
    with pytest.raises(rb.RepoTaskError, match="journal"):
        run(tmp_path, [FIX], env, journal=None)
    (tmp_path / "dir_journal").mkdir()  # an unwritable journal stops the request
    with pytest.raises(OSError):
        run(tmp_path, [FIX], env, journal="dir_journal")
    tiny = rb.RepoBlueConfig(budget_k=1, max_infra_retries=0, context_tokens=100, output_reserve=50)
    result, sent = run(tmp_path, [FIX], env, cfg=tiny)
    assert result["outcome"] == "request_too_large" and not sent
    (row,) = [json.loads(l) for l in (tmp_path / "j.jsonl").read_text().splitlines()]
    assert row["event"] == "preflight_refused" and row["request_ordinal"] == 0


@needs_icarus
def test_snapshot_bound_confirmation(tmp_path):
    task_dir, ev = setup_task(tmp_path)
    env = envelope(task_dir, ["--- a\n+++ b\n"])
    (tmp_path / "env.json").write_text(json.dumps(env))
    member = {"member_id": "m0", "task_dir": task_dir, "envelope_path": tmp_path / "env.json",
              "delivery_hash": hash_payload(env), "seed": 40, "online_seed": 7,
              **{k: env[k] for k in cm.BINDING}}
    with pytest.raises(ValueError, match="cannot cover"):
        cm.confirmation_manifest([member], 2, retrieval_policy_hash=POLICY)
    with pytest.raises(ValueError, match="fresh"):
        cm.confirmation_manifest([{**member, "online_seed": 401}], 3, retrieval_policy_hash=POLICY)
    with pytest.raises(ValueError, match="fresh"):  # plan seed 40 screened as label 400
        cm.confirmation_manifest([{**member, "online_seed": 40}], 3, retrieval_policy_hash=POLICY)
    with pytest.raises(ValueError, match="differs from the online binding"):
        cm.confirmation_manifest([{**member, "snapshot_id": "B_2"}], 3, retrieval_policy_hash=POLICY)
    manifest = cm.confirmation_manifest([member], 3, retrieval_policy_hash=POLICY)
    transport, sent = scripted([FIX, BAD, FIX])
    _, client = runner(transport, cfg=cm.CONFIG, max_calls=3)
    bad_policy = {**manifest, "retrieval_policy_hash": "sha256:latest"}
    with pytest.raises(rm.MeasurementRefused, match="changed"):
        cm.run_confirmation(bad_policy, tmp_path / "c0", client=client, counter=WordCounter())
    assert not sent and not (tmp_path / "c0").exists()
    cm.run_confirmation(manifest, tmp_path / "c1", client=client, counter=WordCounter())
    assert client.total_calls == 3 and all(r["user"]["repair_experience"] == env["payload"] for r in sent)
    journal = [json.loads(l) for l in (tmp_path / "c1/m0/experience_journal.jsonl").read_text().splitlines()]
    assert len(journal) == 3 and {r["snapshot_id"] for r in journal} == {"B_1"}
    out = rf.classify_trials(tmp_path / "c1/m0/blue", task_dir, ev, tmp_path, encounters=3)
    assert out["summary"]["categories"] == {"invalid_edit": 1, "public_pass_hidden_pass": 2}
    with pytest.raises(rm.MeasurementRefused, match="retrieval policy"):
        rm.run_blue(task_dir, tmp_path / "x", client=client, counter=WordCounter(), cfg=cm.CONFIG, seed=1,
                    encounters=1, direct_only=True, experience=env, experience_journal=tmp_path / "x.jsonl",
                    retrieval_policy_hash="sha256:other")
