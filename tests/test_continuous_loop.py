"""Three-round state transitions with fake models and real public Icarus/Yosys checks."""
from copy import deepcopy
import json
from pathlib import Path
import re

import pytest

from experiments.loop_probes import carrier_task, continuous_loop as loop
from r3e.loop import continuous_state as state, red_continuous as red, repo_blue as rb
from r3e.loop.budget import BudgetedClient
from r3e.loop.env import build_client
from r3e.loop.fakes import _reply
from r3e.protocol.hashing import hash_payload
from tests.test_red907_schedule import carriers
from tests.test_repo_blue import WordCounter


def fake_client(cap, hook=None):
    sent = []
    visits = {}

    def transport(**request):
        user = json.loads(request["messages"][-1]["content"])
        sent.append({"user": user, "seed": request["seed"]})
        if hook:
            replacement = hook(request, user)
            if replacement is not None:
                return _reply(replacement, len(sent))
        if "carrier" in user:
            assert request["max_tokens"] == 65536
            carrier = user["carrier"]["carrier_id"]
            visits[carrier] = visits.get(carrier, 1) + 1
            result = {"edits": [{"find": "assign y = a + 1;", "replace": f"assign y = a + {visits[carrier]};"}],
                      "violated_requirement": "y must increment a once", "spec_basis": "toy"}
        else:
            assert request["max_tokens"] == 32768
            text = user["files"]["rtl/toy.v"]["content"]
            before = re.search(r"assign y = [^;]+;", text).group()
            result = {"edits": [{"file": "rtl/toy.v", "find": before, "replace": "assign y = a + 1;"}],
                      "edit": "PUBLIC_BLUE_DESCRIPTION_NOT_LEARNING"}
        return _reply(result, len(sent))

    inner = build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=transport,
                         maximum_output_tokens=65536, require_seed=True)
    return BudgetedClient(inner, max_calls=cap), sent


def plan_for(root, **kwargs):
    # Preserve the original rotating fixtures used by measurement audit tests.
    kwargs.setdefault("carrier_mode", "rotate")
    items, _ = carriers(root)
    anchors = []
    for i, c in enumerate(items):
        target = root / f"anchor{i}"
        carrier_task.export(c["package"], "defect", target, loop.read(c["suite_freeze"]))
        anchors.append({"task_dir": target, "carrier_index": i})
    client, _ = fake_client(21)
    return loop.make_plan(items, anchors, counter=WordCounter(), client_config=client.config.public_identity, **kwargs)


def rows(path):
    return [json.loads(l) for l in path.read_text().splitlines()]


def test_three_rounds_real_public_checks_21_calls_and_sealed_measurement(tmp_path, monkeypatch):
    plan = plan_for(tmp_path / "inputs")
    assert plan["max_calls"] == 21
    # Evaluator execution must never be part of learning, reference or anchor collection.
    monkeypatch.setattr(loop.measure, "_evaluate", lambda *a, **kw: pytest.fail("hidden evaluation during collection"))
    client, sent = fake_client(21)
    out = tmp_path / "run"
    result = loop.collect(plan, out, client=client, counter=WordCounter())
    assert result["status"] == "complete" and result["committed_rounds"] == 3
    ledger = rows(out / "calls.jsonl")
    assert len(ledger) == client.total_calls == len(sent) == 21
    assert {role: sum(r["role"] == role for r in ledger) for role in ("red", "online", "reference", "anchor")} == {
        "red": 3, "online": 3, "reference": 3, "anchor": 12}
    commits = [loop.read(out / "rounds" / str(r) / "commit.json") for r in range(4)]
    assert [len(c["blue"]["entries"]) for c in commits] == [0, 1, 2, 3]
    for i, c in enumerate(commits):
        loop.authenticate(c["blue"], out, loop.blue_identity(plan), commits[i - 1]["blue"] if i else None)
    red_calls = [r["user"] for r in sent if "carrier" in r["user"]]
    assert [len(r["prior_public_observations"]) for r in red_calls] == [0, 1, 2]
    red_request = loop.read(out / "rounds/3/r3_s0/red/request.json")
    assert red_request["target_snapshot_hash"] == hash_payload(commits[2]["blue"])
    assert red_request["red_state_hash"] == hash_payload(commits[2]["red"])
    for r in (2, 3):
        delivered = rows(out / f"rounds/{r}/r{r}_s0/online/experience.jsonl")
        assert delivered[0]["delivered"] and delivered[0]["snapshot_id"] == f"B_{r-1}"
    for p in out.glob("rounds/*/*/reference/experience.jsonl"):
        assert all(r["snapshot_id"] == "B_0" and not r["delivered"] for r in rows(p))
    assert len(rows(out / "probes.jsonl")) == 12
    observations = json.dumps(commits[-1]["red"])
    assert "PUBLIC_BLUE_DESCRIPTION_NOT_LEARNING" not in observations
    assert "predicted" not in json.dumps(red_calls)
    sources = list((out / "sources").glob("*.json"))
    assert len(sources) == 3
    assert all("/online/" in loop.read(p)["archive_path"] for p in sources)
    assert all("shortcut_diagnostic" not in loop.read(p) for p in out.glob("rounds/*/*/admission/admission.json"))
    # Source or entry corruption must fail, even if the visible payload looks plausible.
    broken = deepcopy(commits[2]["blue"])
    broken["entries"][0]["body"]["content"]["applied_patch"] = "forged"
    with pytest.raises(ValueError, match="hash mismatch"):
        loop.authenticate(broken, out, loop.blue_identity(plan), commits[1]["blue"])
    source = loop.read(sources[0])
    archive_path = Path(source["archive_path"])
    archive = archive_path.read_text()
    archive_path.write_text(archive.replace("assign y", "assign x"))
    with pytest.raises(ValueError, match="provenance changed"):
        loop.authenticate(commits[3]["blue"], out, loop.blue_identity(plan), commits[2]["blue"])
    archive_path.write_text(archive)
    # Post-run audit results are not part of state: adding a mismatch report cannot rewrite it.
    (out / "source_audit.json").write_text('{"checker_mismatch":1}')
    assert loop.read(out / "rounds/3/commit.json") == commits[3]


def test_anchor_collision_is_rejected_without_feedback_or_replacement(tmp_path):
    plan = plan_for(tmp_path / "inputs")

    def collide(req, user):
        if "carrier" in user:
            return {"edits": [{"find": "assign y = a + 1;", "replace": "assign y = a;"}],
                    "violated_requirement": "y", "spec_basis": "toy"}

    client, sent = fake_client(21, collide)
    result = loop.collect(plan, tmp_path / "run", client=client, counter=WordCounter())
    assert result["calls"] == 15  # three rejected Red calls + twelve anchors
    assert all(r["reason"] == "reserved_task_collision" for r in rows(tmp_path / "run/slots.jsonl"))
    states = [loop.read(tmp_path / f"run/rounds/{r}/commit.json")["blue"] for r in range(4)]
    assert len({s["state_hash"] for s in states}) == 1
    assert all(not r["user"]["prior_public_observations"] for r in sent if "carrier" in r["user"])


def test_functional_failure_is_observation_not_blue_advice(tmp_path):
    plan = plan_for(tmp_path / "inputs")
    online = {s["online_seed"] * 1000 + 50 for s in plan["slots"]}

    def regress(req, user):
        if req["seed"] in online:
            return {"edits": [{"file": "rtl/toy.v", "find": "assign y = a + 2;", "replace": "assign y = a + 3;"}],
                    "edit": "still incorrect"}

    client, sent = fake_client(21, regress)
    loop.collect(plan, tmp_path / "run", client=client, counter=WordCounter())
    c = loop.read(tmp_path / "run/rounds/3/commit.json")
    assert not c["blue"]["entries"]
    assert len(c["red"]["observations"]) == 3
    assert all(o["label"] == "public_functional_failure_single_sample_unconfirmed" for o in c["red"]["observations"])
    assert all("repair_experience" not in r["user"] for r in sent)


def test_global_error_leaves_no_partial_round_commit_or_extra_calls(tmp_path):
    plan = plan_for(tmp_path / "inputs")

    def stop(req, user):
        if "files" in user:
            raise ValueError("configuration error")

    client, _ = fake_client(21, stop)
    with pytest.raises(ValueError, match="configuration error"):
        loop.collect(plan, tmp_path / "run", client=client, counter=WordCounter())
    result = loop.read(tmp_path / "run/collection.json")
    assert result["status"] == "aborted" and result["committed_rounds"] == 0
    assert result["calls"] == 2 and not (tmp_path / "run/rounds/1/commit.json").exists()
    assert len(result["uncompleted_slot_ids"]) == 3


def test_strict_generator_schema_and_prior_round_allowlist():
    public = {"clean_rtl": "", "top_module": "toy"}
    assert red.materialize({"edits": [], "violated_requirement": "x", "spec_basis": "y", "predicted": {}}, public)["reason"] == "output_schema"
    with pytest.raises(ValueError, match="boundary"):
        red.observation_bank([{"hidden": "SECRET"}], 2, WordCounter())
    assert red.observation_bank([], 1, WordCounter()) == ([], [])


def test_whole_entry_omissions_and_stable_empty_delivery(tmp_path):
    plan = plan_for(tmp_path / "inputs", rounds=1)
    task = rb.RepoTask.load(Path(plan["anchors"][0]["task_dir"]))
    entries = []
    for i in range(3):
        entries.append(state.episode_entry({"round": 1, "task_hash": str(i), "mutant_hash": str(i),
            "candidate_hash": str(i), "encounter_hash": f"source{i}"}, task.spec, {"patch": f"PATCH{i}"},
            {"W_bug": "visible_pass", "W_preserve": "visible_pass"}))
    b0 = state.snapshot(0, plan["blue_config"])
    b1, _ = state.update(b0, entries, plan["blue_config"], WordCounter())
    env = state.delivery(b1, task, WordCounter())
    assert len(env["payload"]["entries"]) == 2 and env["omissions"][0]["reason"] == "entry_count_limit"

    class Oversize(WordCounter):
        def count(self, text):
            return 3000 if "PATCH0" in text else super().count(text)

    env = state.delivery(b1, task, Oversize())
    assert any(o["reason"] == "entry_too_large" for o in env["omissions"])
    assert "PATCH0" not in json.dumps(env["payload"])
    assert state.delivery(b0, task, WordCounter())["payload"]["entries"] == []

    class WrapperLimit(WordCounter):
        def count(self, text):
            return 4097 if '"repair_experience"' in text and text.count('"entry_hash"') > 1 else super().count(text)

    env = state.delivery(b1, task, WrapperLimit())
    assert len(env["payload"]["entries"]) == 1
    assert all(o["reason"] == "delivery_token_limit" for o in env["omissions"])


def test_wrong_plan_cap_seed_order_and_snapshot_tamper_refused(tmp_path):
    plan = plan_for(tmp_path / "inputs")
    client, sent = fake_client(22)
    with pytest.raises(ValueError, match="frozen cap"):
        loop.collect(plan, tmp_path / "badcap", client=client, counter=WordCounter())
    assert not (tmp_path / "badcap").exists() and not sent
    bad = deepcopy(plan)
    bad["slots"][1]["online_seed"] = bad["slots"][0]["online_seed"]
    with pytest.raises(ValueError, match="schedule"):
        loop.validate_plan(bad)
    bad = deepcopy(plan)
    bad["anchor_probes"].reverse()
    with pytest.raises(ValueError, match="schedule"):
        loop.validate_plan(bad)

    def change_committed_state(req, user):
        if "carrier" in user:
            p = tmp_path / "tampered/rounds/0/commit.json"
            record = loop.read(p)
            record["red"]["observations"].append({"hidden": "never send"})
            p.write_text(json.dumps(record))

    client, _ = fake_client(21, change_committed_state)
    with pytest.raises(ValueError, match="committed state changed"):
        loop.collect(plan, tmp_path / "tampered", client=client, counter=WordCounter())
    assert client.total_calls == 1


def test_multiple_slots_share_one_incoming_state(tmp_path):
    plan = plan_for(tmp_path / "inputs", rounds=1, slots_per_round=2)
    client, sent = fake_client(plan["max_calls"])
    loop.collect(plan, tmp_path / "run", client=client, counter=WordCounter())
    online = list((tmp_path / "run/rounds/1").glob("*/online/delivery.json"))
    assert len(online) == 2
    assert all(not loop.read(p)["selected_entry_hashes"] and loop.read(p)["snapshot_id"] == "B_0" for p in online)
    assert all(not r["user"]["prior_public_observations"] for r in sent if "carrier" in r["user"])
    assert len(loop.read(tmp_path / "run/rounds/1/commit.json")["blue"]["entries"]) == 2


def test_valid_no_op_is_retained_as_unconfirmed_observation(tmp_path):
    plan = plan_for(tmp_path / "inputs", rounds=1)
    online_seed = plan["slots"][0]["online_seed"] * 1000 + 50

    def unchanged(req, user):
        if req["seed"] == online_seed:
            return {"edits": [{"file": "rtl/toy.v", "find": "assign y = a + 2;", "replace": "assign y = a + 2;"}],
                    "edit": "no change"}

    client, _ = fake_client(plan["max_calls"], unchanged)
    loop.collect(plan, tmp_path / "run", client=client, counter=WordCounter())
    commit = loop.read(tmp_path / "run/rounds/1/commit.json")
    assert not commit["blue"]["entries"]
    assert commit["red"]["observations"][0]["label"] == "valid_no_op_single_sample_unconfirmed"


def test_red_preflight_and_durable_request_before_transport(tmp_path):
    public = {"clean_rtl": "module toy(input a, output y); assign y=a; endmodule", "top_module": "toy"}
    snap = state.snapshot(0, {"runner": {}})
    target, _ = state.target_view(snap, "toy", {"runner": {}}, WordCounter())
    client, sent = fake_client(1)
    result = red.propose(public, [], round_index=1, snapshot_hash=hash_payload(snap), red_state_hash="r0", seed=1,
                         target_blue=target,
                         directory=tmp_path / "too_large", client=client, counter=WordCounter(), context_tokens=50)
    assert result["status"] == "request_too_large" and not sent
    request_path = tmp_path / "sent/request.json"

    def inspect(req, user):
        record = loop.read(request_path)
        assert record["request_hash"] == hash_payload(req["messages"])
        assert record["bank_hash"] == hash_payload([])
        assert record["target_view_hash"] == hash_payload(user["target_blue"])
        return {"abstain": "no applicable requirement"}

    client, _ = fake_client(1, inspect)
    result = red.propose(public, [], round_index=1, snapshot_hash=hash_payload(snap), red_state_hash="r0", seed=1,
                         target_blue=target,
                         directory=request_path.parent, client=client, counter=WordCounter())
    assert result["status"] == "abstained" and client.total_calls == 1


def test_fixed_carrier_revisits_same_design_with_updated_blue(tmp_path):
    from experiments.loop_probes.continuous_audit import exposure_audit

    plan = plan_for(tmp_path / "inputs", carrier_mode="fixed", fixed_carrier_index=1)
    assert [s["carrier_index"] for s in plan["slots"]] == [1, 1, 1]
    client, sent = fake_client(21)
    result = loop.collect(plan, tmp_path / "run", client=client, counter=WordCounter())
    assert result["calls"] == 21
    commits = [loop.read(tmp_path / f"run/rounds/{r}/commit.json") for r in range(4)]
    assert [len(c["blue"]["entries"]) for c in commits] == [0, 1, 2, 3]
    assert {e["body"]["source"]["carrier_id"] for e in commits[-1]["blue"]["entries"]} == {"fixture1"}
    red_calls = [r["user"] for r in sent if "carrier" in r["user"]]
    for r, request in enumerate(red_calls, 1):
        target = request["target_blue"]
        assert target["snapshot_id"] == f"B_{r-1}"
        assert target["snapshot_hash"] == hash_payload(commits[r-1]["blue"])
        assert target["eligible_entry_count"] == r-1
        delivery = loop.read(tmp_path / f"run/rounds/{r}/r{r}_s0/online/delivery.json")
        assert target["spec_selected_experience"] == delivery["payload"]
        assert target["learning_policy"]["qualification"] == "provisional_public_only"
    exposure = exposure_audit(tmp_path / "run")
    online = {r["slot_id"]: r for r in exposure["trials"] if r["role"] == "online"}
    assert online["r1_s0"]["store_group"] == "neither_observed"
    for r in (2, 3):
        assert online[f"r{r}_s0"]["store_group"] == "span_overlap_only"
        assert online[f"r{r}_s0"]["delivered_group"] == "span_overlap_only"
    # Counterfactual change of carrier mode must not silently reuse the frozen slots/seeds.
    bad = deepcopy(plan)
    bad["carrier_mode"] = "rotate"
    with pytest.raises(ValueError, match="schedule"):
        loop.validate_plan(bad)


def test_carrier_policy_is_explicit_validated_and_seed_namespaced():
    fixed, _ = loop.schedule(3, 1, 3, 3, 700)
    rotating, _ = loop.schedule(3, 1, 3, 3, 700, carrier_mode="rotate")
    assert [s["carrier_index"] for s in fixed] == [0, 0, 0]
    assert [s["carrier_index"] for s in rotating] == [0, 1, 2]
    assert {s["red_seed"] for s in fixed}.isdisjoint(s["red_seed"] for s in rotating)
    for mode, index in (("unknown", 0), ("fixed", 3), ("fixed", -1), ("fixed", True)):
        with pytest.raises(ValueError, match="scheduling policy"):
            loop.schedule(3, 1, 3, 3, 700, carrier_mode=mode, fixed_carrier_index=index)


def test_target_view_binding_and_size_fail_before_model_call(tmp_path):
    snap = state.snapshot(0, {"runner": {}})
    target, _ = state.target_view(snap, "spec", {"runner": {}}, WordCounter())
    client, sent = fake_client(1)
    kwargs = dict(round_index=1, snapshot_hash=hash_payload(snap), red_state_hash="r0", seed=1,
                  directory=tmp_path / "refused", client=client, counter=WordCounter())
    for bad in ({**target, "snapshot_hash": "wrong"}, {**target, "committed_round": 1},
                {**target, "hidden_results": []}):
        with pytest.raises(ValueError, match="target state"):
            red.propose({}, [], target_blue=bad, **kwargs)
    assert not sent and not kwargs["directory"].exists()

    class TooLarge(WordCounter):
        def count(self, text):
            return 7000 if "continuous_red_target_v1" in text else super().count(text)

    kwargs["counter"] = TooLarge()
    result = red.propose({}, [], target_blue=target, **kwargs)
    assert result == {"status": "request_too_large", "reason": "red_target_state_limit"}
    assert not sent and (kwargs["directory"] / "request.json").exists()
