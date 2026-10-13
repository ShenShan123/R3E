"""Scheduler mechanics on synthetic carriers; no model calls or transfer claims."""
import json

import pytest

from experiments.loop_probes import red907_schedule as sched, witness_matrix as wm
from r3e.loop.budget import BudgetedClient, CallBudgetExceeded
from r3e.loop.env import build_client
from r3e.loop.fakes import _reply
from r3e.protocol.hashing import hash_payload
from tests.test_red_regression import packet, abstraction
from tests.test_repo_blue import WordCounter
from tests.test_witness_matrix import package


def carriers(tmp_path):
    items = []
    for i in range(3):
        root = tmp_path / f"case{i}"
        pkg = package(root)
        # Renaming here ONLY separates synthetic test fixtures. It is not a
        # qualification of independent real designs or ancestry.
        for f in pkg.rglob("*.v"):
            f.write_text(f.read_text().replace("module toy(", f"module toy{i}("))
        for f in pkg.rglob("*.sv"):
            f.write_text(f.read_text().replace("toy dut", f"toy{i} dut"))
        man = json.loads((pkg / "carrier.json").read_text())
        man.update(carrier_id=f"fixture{i}", top_module=f"toy{i}", spec="spec.md", parameters={},
                   source={"repository": f"fixture/{i}", "commit": "synthetic", "license": "test-only",
                           "family": f"synthetic-family-{i}"})
        (pkg / "carrier.json").write_text(json.dumps(man))
        freeze = {"files": {r: h for r, h in wm.package_files(pkg).items()
                            if r != "carrier.json" and not r.startswith("variants/")},
                  "frozen_manifest_fields_hash": hash_payload(wm.frozen_fields(man))}
        (root / "suite_freeze.json").write_text(json.dumps(freeze))
        ev = root / "evaluator"
        ev.mkdir()
        text = f'''module tb; reg [3:0] a; wire [3:0] y,z; toy{i} dut(a,y,z);
initial begin a=9; #1; if (y != 10 || z != 9) $display("CHECK_FAIL");
else $display("TESTBENCH PASS"); $finish; end endmodule'''
        (ev / "tb.sv").write_text(text)
        ef = {"files": {"evaluator/tb.sv": hash_payload(text)},
              "run": {"top": "tb", "rtl": ["rtl/toy.v"], "pass_marker": "TESTBENCH PASS", "fail_marker": "CHECK_FAIL"}}
        (root / "evaluator_freeze.json").write_text(json.dumps(ef))
        items.append({"package": pkg, "suite_freeze": root / "suite_freeze.json",
                      "evaluator_freeze": root / "evaluator_freeze.json", "evaluator_root": root})
    p = tmp_path / "source_packet.json"
    p.write_text(json.dumps(packet()))
    return items, p


def client(cap, fail=False):
    sent = []

    def transport(**request):
        user = json.loads(request["messages"][-1]["content"])
        sent.append(user)
        if fail:
            raise ValueError("configuration error")
        if "carrier" in user:
            assert request["max_tokens"] == 65536
            clean = user["carrier"]["clean_rtl"]
            mutant = clean.replace("assign y = a + 1;", "assign y = a;")
            result = {"edits": [{"find": clean, "replace": mutant}], "hypothesis": "restore y, damage z", "spec_basis": "toy",
                      "original_defect_obligation": "y increments a", "preserved_obligations": ["z follows a"],
                      "predicted_repair_regression": "z becomes constant",
                      "predicted_wrong_repair_edits": [{"find": mutant, "replace": clean.replace("assign z = a;", "assign z = 0;")}]}
        elif "files" in user:
            assert request["max_tokens"] == 32768
            assert "source_packet" not in user
            result = {"edits": [{"file": "rtl/toy.v", "find": "assign y = a;", "replace": "assign y = a + 1;"}],
                      "edit": "scripted fix"}
        else:
            assert request["max_tokens"] == 32768
            result = abstraction()
        return _reply(result, len(sent))

    inner = build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=transport, maximum_output_tokens=65536)
    return BudgetedClient(inner, max_calls=cap), sent


def plan_for(tmp_path, cap=None):
    items, packet_path = carriers(tmp_path)
    c, _ = client(1000)
    return sched.make_plan(items, packet_path, counter=WordCounter(), client_config=c.config.public_identity,
                           seed=7, per_arm_cap=cap)


def test_full_schedule_uses_one_shared_ledger_and_evaluates_only_after_collection(tmp_path, monkeypatch):
    plan = plan_for(tmp_path)
    assert plan["per_arm_cap"] == 79 and plan["max_calls"] == 237
    pairs = {(s["arm"], s["carrier_id"]) for s in plan["slots"]}
    assert len(pairs) == 9
    c, sent = client(plan["max_calls"])
    out = tmp_path / "run"
    result = sched.collect(plan, out, client=c, counter=WordCounter())
    assert result["status"] == "complete" and not result["evaluator_executed"]
    assert c.total_calls == 37 == len(sent)  # 1 extraction + 9 proposals + 27 first-pass repairs
    assert not list(out.rglob("classification.json"))
    ledger = [json.loads(l) for l in (out / "calls.jsonl").read_text().splitlines()]
    assert len(ledger) == 37 and sum(r["stage"] == "abstraction" for r in ledger) == 1
    slots = [json.loads(l) for l in (out / "slots.jsonl").read_text().splitlines()]
    assert sum(r["slot_calls"] for r in slots) == 36 and sum(r["extraction_calls"] for r in slots) == 1
    assert sum(r["calls"] for r in slots) == 37
    assert sum(r["total_calls"] for r in result["per_arm"].values()) == 37
    classified = sched.classify_collection(out)
    assert len(classified) == 9
    assert all(x["measurement_summary"]["categories"] == {"public_pass_hidden_pass": 3} for x in classified)
    with pytest.raises(ValueError, match="fresh client"):
        sched.collect(plan, out, client=c, counter=WordCounter())
    assert c.total_calls == 37
    # An evaluator without a checker verdict is classified inconclusive, never a functional hidden failure.
    monkeypatch.setattr(sched.measure, "_evaluate", lambda *args: {"result": "timeout", "failures": []})
    classified = sched.classify_collection(out)
    assert all(x["measurement_summary"]["categories"] == {"public_pass_hidden_inconclusive": 3} for x in classified)
    assert all(x["measurement_summary"]["target_review_eligible"] == 0 for x in classified)
    assert c.total_calls == 37


def test_equal_caps_reserve_whole_slots_without_spending_leftovers_on_more_trials(tmp_path):
    plan = plan_for(tmp_path, cap=27)
    c, _ = client(plan["max_calls"])
    result = sched.collect(plan, tmp_path / "run", client=c, counter=WordCounter())
    rows = [json.loads(l) for l in (tmp_path / "run/slots.jsonl").read_text().splitlines()]
    assert sum(r["status"] == "admitted" for r in rows) == 3
    assert sum(r["status"] == "not_started_budget" for r in rows) == 6
    assert c.total_calls == 13 and result["recorded_slots"] == 9


def test_changed_material_cap_and_incomplete_plans_refused_without_calls(tmp_path):
    plan = plan_for(tmp_path)
    c, sent = client(plan["max_calls"] + 1)
    with pytest.raises(ValueError, match="frozen call cap"):
        sched.collect(plan, tmp_path / "run", client=c, counter=WordCounter())
    c.max_calls = plan["max_calls"]
    runtime = plan["runtime"]
    plan["runtime"] = {**runtime, "python_version": "different interpreter"}
    with pytest.raises(ValueError, match="Python runtime changed"):
        sched.collect(plan, tmp_path / "wrong_runtime", client=c, counter=WordCounter())
    plan["runtime"] = runtime
    assert not sent and not (tmp_path / "wrong_runtime").exists()
    p = plan_for(tmp_path / "other")
    p["ready_for_collection"] = False
    with pytest.raises(ValueError, match="three qualified"):
        sched.collect(p, tmp_path / "no", client=c, counter=WordCounter())
    root = plan["carriers"][0]["evaluator_root"]
    from pathlib import Path
    (Path(root) / "evaluator/extra.sv").write_text("unfrozen")
    with pytest.raises(ValueError, match="file set"):
        sched.collect(plan, tmp_path / "changed", client=c, counter=WordCounter())
    assert not sent


def test_arm_budget_cannot_borrow_and_keeps_provider_failures_in_root_ledger():
    shared, sent = client(5, fail=True)
    arm = sched.ArmBudget(shared, 1)
    with pytest.raises(ValueError, match="configuration error"):
        arm.complete_json(messages=[{"role": "user", "content": '{"json":true}'}], seed=1)
    assert arm.total_calls == shared.total_calls == 1
    with pytest.raises(CallBudgetExceeded, match="per-arm"):
        arm.complete_json(messages=[{"role": "user", "content": '{"json":true}'}], seed=1)
    assert len(sent) == 1


@pytest.mark.parametrize("message,local", [
    ("baseline no longer shows the admission row: {'W_bug': 'visible_pass'}", True),
    ("exported task changed since export: ['public/spec.md']", False),
    ("needs 15 calls reserved, 14 left", False),
])
def test_blue_preflight_refusal_scope(tmp_path, monkeypatch, message, local):
    plan = plan_for(tmp_path)
    c, _ = client(plan["max_calls"])
    original = sched.measure.run_blue
    invocations = []

    def refuse_once(*args, **kwargs):
        invocations.append(1)
        if len(invocations) == 1:
            raise sched.measure.MeasurementRefused(message)
        return original(*args, **kwargs)

    monkeypatch.setattr(sched.measure, "run_blue", refuse_once)
    out = tmp_path / "run"
    if not local:
        with pytest.raises(sched.measure.MeasurementRefused, match=message.split(':')[0]):
            sched.collect(plan, out, client=c, counter=WordCounter())
        assert json.loads((out / "collection.json").read_text())["status"] == "aborted"
        assert len(invocations) == 1
        return
    result = sched.collect(plan, out, client=c, counter=WordCounter())
    rows = [json.loads(l) for l in (out / "slots.jsonl").read_text().splitlines()]
    assert result["status"] == "complete" and len(rows) == 9
    assert rows[0]["status"] == "blue_refused" and rows[0]["generation_status"] == "admitted"
    assert rows[0]["blue_calls"] == 0 and "blue_directory" not in rows[0]
    assert sum(r["status"] == "admitted" for r in rows) == 8
    assert c.total_calls == 34  # one refused slot saves exactly three scripted Blue calls
