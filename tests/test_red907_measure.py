import json
import shutil

import pytest

from experiments.loop_probes import carrier_task as ct
from experiments.loop_probes import red907_measure as rm
from r3e.loop import repo_blue as rb
from r3e.protocol.hashing import hash_payload
from tests.test_carrier_task import carrier
from tests.test_repo_blue import WordCounter, runner, scripted

needs_icarus = pytest.mark.skipif(not shutil.which("iverilog"), reason="iverilog is required")
EVAL = """module tb; reg [3:0] a; wire [3:0] y, z; toy dut(.a(a), .y(y), .z(z));
initial begin a = 9; #1 if (y != 10 || z != 9) begin $display("CHECK_FAIL eval"); $finish; end
$display("TESTBENCH PASS"); $finish; end
endmodule
"""
TARGET = {"edits": [{"file": "rtl/toy.v", "find": "assign y = a; assign z = a;",
                     "replace": "assign y = a + 1; assign z = 0;"}], "edit": "e"}   # W_bug pass, W_preserve fail
CONST = {"edits": [{"file": "rtl/toy.v", "find": "assign y = a + 1; assign z = 0;",
                    "replace": "assign y = 4'd6; assign z = a;"}], "edit": "e"}     # public pass, evaluator fail
BAD = {"edits": [{"file": "rtl/toy.v", "find": "no such text", "replace": "x"}], "edit": "e"}


def setup_task(tmp_path):
    pkg, freeze = carrier(tmp_path)
    task_dir = ct.export(pkg, "defect", tmp_path / "task", freeze)
    (tmp_path / "evaluator" / "toy").mkdir(parents=True)
    (tmp_path / "evaluator" / "toy" / "eval_tb.sv").write_text(EVAL)
    ev = {"files": {"evaluator/toy/eval_tb.sv": hash_payload(EVAL)},
          "run": {"top": "tb", "rtl": ["rtl/toy.v", "rtl/dep.v"], "pass_marker": "TESTBENCH PASS", "fail_marker": "CHECK_FAIL"}}
    return task_dir, ev


@needs_icarus
def test_three_encounters_are_classified_by_endpoint(tmp_path):
    task_dir, ev = setup_task(tmp_path)
    replies = [TARGET, CONST,                       # encounter 0: intermediate target pattern, then public pass
               TARGET, BAD, BAD,                    # encounter 1: unrepaired, endpoint has the target pattern
               "NO_ANSWER", "NO_ANSWER", "NO_ANSWER"]  # encounter 2: nothing materialised
    transport, sent = scripted(replies)
    _, client = runner(transport, max_calls=15)
    rm.run_blue(task_dir, tmp_path / "blue", client=client, counter=WordCounter(), seed=3)
    assert client.total_calls == 8
    out = rm.classify(tmp_path / "blue", task_dir, ev, tmp_path)
    e0, e1, e2 = out["encounters"]
    assert e0["category"] == "public_pass_hidden_fail" and e0["target_review_eligible"]
    assert len(e0["intermediate_target_pattern"]) == 1
    assert e1["category"] == "public_unrepaired_at_budget" and e1["target_review_eligible"]
    assert e1["endpoint_public"]["suites"]["W_preserve"]["result"] == "visible_fail"
    assert e2["category"] == "no_candidate" and "target_review_eligible" not in e2
    s = out["summary"]
    assert s["target_review_eligible"] == 2 and s["pending_review"] == 2 and not s["confirmed_target_challenge"]
    items = json.loads((tmp_path / "blue" / "review_items.json").read_text())
    assert len(items) == 2 and not any(k in json.dumps(items) for k in ("arm", "ordinary", "automatic", "manual"))
    # one TARGET decision on the unrepaired endpoint is not a 2-of-3 confirmation; the hidden-only one never counts
    (tmp_path / "blue" / "review_decisions.json").write_text(json.dumps({i["item"]: {"decision": "TARGET"} for i in items}))
    s = rm.classify(tmp_path / "blue", task_dir, ev, tmp_path)["summary"]
    assert s["confirmed_public_target_endpoints"] == 1 and not s["confirmed_target_challenge"]


@needs_icarus
def test_refusals_before_any_call_and_on_changed_material(tmp_path):
    task_dir, ev = setup_task(tmp_path)
    transport, sent = scripted([])
    _, client = runner(transport, max_calls=14)          # 3 encounters x (3 attempts + 2 retries) = 15 needed
    with pytest.raises(rm.MeasurementRefused, match="needs 15 calls"):
        rm.run_blue(task_dir, tmp_path / "blue", client=client, counter=WordCounter(), seed=1)
    assert not sent and not (tmp_path / "blue").exists()
    spec = task_dir / "public" / "spec.txt"
    spec.write_text(spec.read_text() + "hint\n")
    _, client = runner(transport, max_calls=15)
    with pytest.raises(rm.MeasurementRefused, match="spec.txt"):
        rm.run_blue(task_dir, tmp_path / "blue", client=client, counter=WordCounter(), seed=1)
    (tmp_path / "evaluator" / "toy" / "eval_tb.sv").write_text(EVAL + "// edited\n")
    with pytest.raises(rm.MeasurementRefused, match="evaluator changed"):
        rm.classify(tmp_path / "blue", task_dir, ev, tmp_path)


@needs_icarus
@pytest.mark.parametrize("evaluator, verdict", [
    ("module tb; initial begin $display(\"starting\"); $fatal(1); end endmodule\n", "unmarked_nonzero_exit"),
    ("module tb; this is not verilog\n", "compile_fail")])
def test_an_evaluator_without_a_checker_verdict_is_inconclusive_not_a_false_fix(tmp_path, evaluator, verdict):
    task_dir, ev = setup_task(tmp_path)
    (tmp_path / "evaluator" / "toy" / "eval_tb.sv").write_text(evaluator)
    ev["files"] = {"evaluator/toy/eval_tb.sv": hash_payload(evaluator)}
    fix = {"edits": [{"file": "rtl/toy.v", "find": "assign y = a;", "replace": "assign y = a + 1;"}], "edit": "e"}
    transport, _ = scripted([fix, fix, fix])
    _, client = runner(transport, max_calls=15)
    rm.run_blue(task_dir, tmp_path / "blue", client=client, counter=WordCounter(), seed=1)
    out = rm.classify(tmp_path / "blue", task_dir, ev, tmp_path)
    for row in out["encounters"]:
        assert row["category"] == "public_pass_hidden_inconclusive" and not row["target_review_eligible"]
        assert row["endpoint_evaluator"]["result"] == verdict
    assert out["summary"]["target_review_eligible"] == 0


@needs_icarus
def test_a_replay_that_disagrees_with_the_recorded_tier_is_kept_apart(tmp_path):
    task_dir, ev = setup_task(tmp_path)
    fix = {"edits": [{"file": "rtl/toy.v", "find": "assign y = a;", "replace": "assign y = a + 1;"}], "edit": "e"}
    transport, _ = scripted([fix, fix, fix])
    _, client = runner(transport, max_calls=15)
    rm.run_blue(task_dir, tmp_path / "blue", client=client, counter=WordCounter(), seed=1)
    log = tmp_path / "blue" / "encounters.jsonl"
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    rows[0]["result"]["outcome"] = "not_fixed"                 # as if collection had seen a different result
    rows[0]["result"]["attempts"][-1]["tier"] = "visible_fail"
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    out = rm.classify(tmp_path / "blue", task_dir, ev, tmp_path)
    e0 = out["encounters"][0]
    assert e0["category"] == "replay_mismatch" and not e0["target_review_eligible"]
    assert e0["recorded_tier"] == "visible_fail" and e0["endpoint_public"]["tier"] == "visible_pass"
    assert [r["category"] for r in out["encounters"][1:]] == ["public_pass_hidden_pass"] * 2


def test_only_a_checker_violation_in_w_preserve_is_the_target_pattern():
    def public(w_preserve):
        return {"tier": "visible_fail", "suites": {"W_bug": {"result": "visible_pass"}, "W_preserve": w_preserve}}
    assert rm._target_pattern(public({"result": "visible_fail", "checker_violation": True}))
    assert not rm._target_pattern(public({"result": "visible_fail", "checker_violation": False}))  # unmarked exit
    assert not rm._target_pattern(public({"result": "timeout", "checker_violation": False}))
