"""Mechanism seeding, reserved variants and continuous states with scripted models."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from experiments.loop_probes import continuous_audit, continuous_launch as launch, continuous_loop as loop, sequence_curriculum as seq
from r3e.loop import red_continuous as red
from r3e.loop.budget import BudgetedClient
from r3e.loop.env import build_client
from r3e.loop.fakes import _reply
from r3e.protocol.hashing import hash_payload
from tests.test_repo_blue import WordCounter


def fixture(tmp_path, monkeypatch):
    # Small specification-compliant FSM; no external checkout needed by regression tests.
    cases = ["idle: nstate=data?sf1:s1;", "s1: nstate=data?s2:sf2;", "s2: nstate=data?s3:sf3;",
             "s3: nstate=data?s4:sf4;", "s4: nstate=data?sf5:s5;", "s5: nstate=data?sf6:s6;",
             "        s6:\n            nstate=data?sf1:s1;"]
    cases += [f"sf{i}: nstate=sf{i+1};" for i in range(1, 6)]
    cases += ["        sf6:\n\t\t\tnstate=data?sf1:s1;", "default: nstate=idle;"]
    names = ["idle", *[f"s{i}" for i in range(1, 7)], *[f"sf{i}" for i in range(1, 7)]]
    clean = "module TopModule(input clk,rst_n,data,output reg match,not_match);\n"
    clean += "reg [3:0] pstate,nstate;\nparameter " + ",".join(f"{n}=4'd{i}" for i, n in enumerate(names)) + ";\n"
    clean += "always @(posedge clk or negedge rst_n) if(!rst_n) pstate<=idle; else pstate<=nstate;\n"
    clean += "always @(*) case(pstate)\n" + "\n".join(cases) + "\nendcase\n"
    clean += "always @(*) begin match=0; not_match=0; if(rst_n) begin if(pstate==s6) match=1; if(pstate==sf6) not_match=1; end end\nendmodule\n"
    source = tmp_path / "inputs"
    case = {"case_id": f"chipbench:assignment:{seq.CASE}", "eligible": True,
            "golden_rtl": "clean.sv", "spec": "spec.txt"}
    seq._write(source / "datasets/manifests/chipbench89.jsonl", json.dumps(case) + "\n")
    seq._write(source / "datasets/licenses/ChipBench-MIT.txt", "MIT fixture license\n")
    seq._write(source / "clean.sv", clean)
    seq._write(source / "spec.txt", "Consume non-overlapping groups of six bits and recognize 011100.\n")
    edits = [{"find": e["find"], "replace": e["find"].replace("data?sf1:s1", "idle")}
             for e in seq.training_edits(clean)[0]]
    mutant = clean
    for edit in edits:
        mutant = mutant.replace(edit["find"], edit["replace"])
    history = tmp_path / "history"
    seq._write(history / "red.jsonl", json.dumps({"challenge_id": seq.CHALLENGE, "admitted": True,
               "chosen": {"buggy_rtl": mutant, "edits": edits}}) + "\n")
    seq._write(history / "screens.jsonl", json.dumps({"challenge_id": seq.CHALLENGE, "runs": 3, "fails": 2}) + "\n")
    seq._write(history / "hit_decisions.json", json.dumps({seq.CHALLENGE: {"decision": "PASS"}}))
    monkeypatch.setattr(seq, "ROOT", source)
    monkeypatch.setattr(seq, "_source_commit", lambda: "fixture-commit")
    materials = seq.prepare(tmp_path / "material", history)
    return materials


def test_real_checker_matrix_and_reserved_mutations(tmp_path, monkeypatch):
    material = fixture(tmp_path, monkeypatch)
    q = loop.read(tmp_path / "material/qualification.json")
    assert q["qualified"]
    assert q["matrix"]["defect/W_bug"]["tier"] == "fail"
    assert q["matrix"]["defect/W_preserve"]["tier"] == "pass"
    assert q["matrix"]["reset_control/W_bug"]["tier"] == "pass"
    assert q["matrix"]["reset_control/W_preserve"]["tier"] == "fail"
    assert all(q["matrix"][f"{name}/W_bug"]["tier"] == "fail" for name in ("eval_match_branch", "eval_miss_branch"))
    assert material["mechanism_focus"]["history"]["label"] == "historical_budgeted_failure_not_current_confirmation"
    assert loop.read(tmp_path / "material/clean_qualification.json")["evaluator"]["result"] == "pass"


def test_25_requests_three_rounds_and_deferred_new_variants(tmp_path, monkeypatch):
    material = fixture(tmp_path, monkeypatch)
    # No hidden evaluator may be executed during collection, including anchors.
    original = loop.measure._evaluate
    monkeypatch.setattr(loop.measure, "_evaluate", lambda *a, **kw: pytest.fail("hidden during collection"))
    run = tmp_path / "run"
    result = seq.acceptance(material, run, WordCounter())
    assert result["status"] == "complete" and result["scripted_requests"] == 25 and result["model_calls"] == 0
    plan = loop.read(run / "plan.json")
    assert plan["anchor_repeats"] == 2 and plan["red_output_tokens"] == 16384
    assert len({s["seed"] for s in plan["anchor_probes"]}) == 16
    commits = [loop.read(run / f"rounds/{r}/commit.json") for r in range(4)]
    assert [len(c["blue"]["entries"]) for c in commits] == [0, 1, 2, 3]
    for r in range(1, 4):
        request = loop.read(run / f"rounds/{r}/r{r}_s0/red/request.json")
        user = json.loads(request["messages"][-1]["content"])
        assert user["mechanism_focus"] == material["mechanism_focus"]
        assert len(user["prior_public_observations"]) == r - 1
        assert user["target_blue"]["snapshot_id"] == f"B_{r-1}"
        assert "eval_match_branch" not in json.dumps(user) and "eval_miss_branch" not in json.dumps(user)
    assert not result["uncompleted_probes"]
    probes = [json.loads(l) for l in (run / "probes.jsonl").read_text().splitlines()]
    assert len({p["directory"] for p in probes}) == 16
    assert len(commits[-1]["red"]["observations"]) == 3  # reference/probe results do not teach Red
    before = hash_payload(commits)
    monkeypatch.setattr(loop.measure, "_evaluate", original)
    report = seq.report(run)
    assert report["scheduled_generation"] == report["admitted"] == report["distinct_admitted"] == 3
    assert not any(r["repeats_historical_seed"] for r in report["online_and_frozen_reference"])
    assert all(s["scheduled"] == 4 and s["categories"] == {"public_pass_hidden_pass": 4}
               for s in report["reserved_variants_by_snapshot"])
    assert hash_payload([loop.read(run / f"rounds/{r}/commit.json") for r in range(4)]) == before
    exposure = continuous_audit.exposure_audit(run)
    assert exposure is not None
    with pytest.raises(FileExistsError):
        seq.report(run)


@pytest.mark.parametrize("field", ["seed_hash", "clean_hash"])
def test_historical_seed_hash_mismatch_refused_before_requests(tmp_path, monkeypatch, field):
    materials = fixture(tmp_path, monkeypatch)
    public = loop.red907.public_material(Path(materials["carriers"][0]["package"]),
                                         loop.read(materials["carriers"][0]["suite_freeze"]))
    focus = deepcopy(materials["mechanism_focus"])
    focus["provenance"][field] = "forged"
    with pytest.raises(ValueError, match="provenance"):
        red.validate_focus(focus, public)


def test_repeat_and_seed_schedule_frozen(tmp_path, monkeypatch):
    materials = fixture(tmp_path, monkeypatch)
    seq.acceptance(materials, tmp_path / "run", WordCounter())
    plan = loop.read(tmp_path / "run/plan.json")
    plan["anchor_repeats"] = 1
    with pytest.raises(ValueError, match="schedule"):
        loop.validate_plan(plan)
    with pytest.raises(ValueError, match="repeat"):
        loop.schedule(3, 1, 1, 2, 1, anchor_repeats=0)


def test_historical_seed_repeat_is_not_a_new_challenge(tmp_path, monkeypatch):
    material = fixture(tmp_path, monkeypatch)
    clean = (Path(material["carriers"][0]["package"]) / "src" / seq.PRIMARY).read_text()

    def transport(**request):
        user = json.loads(request["messages"][-1]["content"])
        if "carrier" in user:
            raw = {"edits": material["mechanism_focus"]["seed_edits"], "violated_requirement": "group", "spec_basis": "six"}
        else:
            raw = {"edits": [{"file": seq.PRIMARY, "find": user["files"][seq.PRIMARY]["content"], "replace": clean}],
                   "edit": "scripted"}
        return _reply(raw, 1)

    inner = build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=transport, maximum_output_tokens=65536)
    plan = loop.make_plan(material["carriers"], material["anchors"], counter=WordCounter(),
        client_config=inner.config.public_identity, mechanism_focus=material["mechanism_focus"], anchor_repeats=2)
    result = loop.collect(plan, tmp_path / "run", client=BudgetedClient(inner, max_calls=25), counter=WordCounter())
    assert result["calls"] == 19  # three rejected proposals plus sixteen deferred probes
    rows = [json.loads(l) for l in (tmp_path / "run/slots.jsonl").read_text().splitlines()]
    assert len(rows) == 3 and all(r["reason"] == "historical_seed_repeat" for r in rows)
    assert not loop.read(tmp_path / "run/rounds/3/commit.json")["blue"]["entries"]


def test_sequence_launch_scope_and_cost_bound(tmp_path, monkeypatch):
    from decimal import Decimal

    material = fixture(tmp_path, monkeypatch)
    plan = loop.make_plan(material["carriers"], material["anchors"], counter=WordCounter(),
        client_config=launch.client_identity(), mechanism_focus=material["mechanism_focus"],
        anchor_repeats=2, red_output_tokens=16384)
    plan.update(launch_profile="sequence", cost_bound=launch.budget("sequence"), spending_envelope_CNY="12")
    launch.check_scope(plan)
    assert sum(r["calls"] for r in plan["cost_bound"]["rows"]) == 25
    assert Decimal(plan["cost_bound"]["maximum_CNY"]) < 12
    plan["red_output_tokens"] = 65536
    with pytest.raises(ValueError, match="25-call"):
        launch.check_scope(plan)
