"""Durable protocol checks; real local tools, injected transports, no paid calls."""
import json

import pytest

from experiments.loop_probes import red907, witness_matrix as wm
from r3e.loop.red_regression import (RegressionGenerator, MANUAL_SUMMARY, arm_context, materialize,
                                     rtl_language_review)
from r3e.loop.budget import BudgetedClient, CallBudgetExceeded
from r3e.loop.env import build_client
from r3e.protocol.hashing import hash_payload
from tests.test_repo_blue import WordCounter, scripted
from tests.test_witness_matrix import package, CLEAN, ROW_Y, ROW_Z


def fixture(tmp_path):
    pkg = package(tmp_path)
    man = json.loads((pkg / "carrier.json").read_text())
    man.update(top_module="toy", spec="spec.md", parameters={})
    (pkg / "carrier.json").write_text(json.dumps(man))
    freeze = {"files": {r: h for r, h in wm.package_files(pkg).items()
                        if not r.startswith("variants/") and r != "carrier.json"},
              "frozen_manifest_fields_hash": hash_payload(wm.frozen_fields(man))}
    return pkg, freeze


def raw(predicted=ROW_Z):
    return {"buggy_rtl": ROW_Y, "hypothesis": "a local fix may change another output",
            "spec_basis": "toy output obligations", "original_defect_obligation": "increment y",
            "preserved_obligations": ["z follows a"], "predicted_repair_regression": "z becomes constant",
            "predicted_wrong_repair_edits": [{"find": ROW_Y, "replace": predicted}]}


def header_fixture(tmp_path):
    from tests.test_carrier_task import header_carrier
    pkg, man, freeze = header_carrier(tmp_path)
    # A parameter-list fragment, like Hazard3's config header, cannot be compiled
    # as a standalone unit. Keep a nested dependency as well.
    header = "parameter WIDTH = 4\n"
    (pkg / "src/config.vh").write_text(header)
    primary = pkg / "src/rtl/toy.v"
    primary.write_text(primary.read_text().replace(
        "module toy(", 'module toy #(\n`include "config.vh"\n) ('))
    man["header_dependencies"].append("config.vh")
    (pkg / "carrier.json").write_text(json.dumps(man))
    freeze = {"files": {r: h for r, h in wm.package_files(pkg).items()
                        if not r.startswith("variants/") and r != "carrier.json"},
              "frozen_manifest_fields_hash": hash_payload(wm.frozen_fields(man))}
    return pkg, freeze


def test_red_headers_survive_admission_and_export_without_budget_padding(tmp_path):
    from r3e.loop import repo_blue as rb
    from r3e.loop.corpus import Carrier
    from r3e.loop.red_design import EditBudget, design_constraints

    pkg, freeze = header_fixture(tmp_path)
    public = red907.public_material(pkg, freeze)
    clean = public["clean_rtl"]
    defect = clean.replace("a + STEP", "a")
    shortcut = clean.replace("assign z = a;", "assign z = 0;")
    reply = {**raw(), "buggy_rtl": defect,
             "predicted_wrong_repair_edits": [{"find": defect, "replace": shortcut}]}
    gen, sent = generator([reply], clean=clean)
    result = red907.generate_slot(gen, pkg, freeze, arm_context("ordinary"),
                                 tmp_path / "slot", seed=7, seen=set())
    assert result["status"] == "admitted" and len(sent) == 1
    assert sent[0]["user"]["carrier"]["dependencies"] == {
        h: (pkg / "src" / h).read_text() for h in ("rtl/inc.vh", "config.vh")}
    assert public["edit_budget"] == design_constraints(Carrier("toy", "toy", clean, "toy", ()), EditBudget())
    rec = json.loads((tmp_path / "slot/candidate_0/admission.json").read_text())
    assert rec["shortcut_diagnostic"]["matrix_supported"]
    assert rec["baseline"]["structural"]["status"] == rec["structural"]["status"] == "ok"
    assert ".vh" not in rec["structural"]["command"]
    task_dir = tmp_path / "slot/candidate_0/blue_task"
    build = json.loads((task_dir / "public/visible/build.json").read_text())
    assert build["rtl"] == ["rtl/toy.v"] and build["headers"] == ["rtl/inc.vh", "config.vh"]
    for h in build["headers"]:
        for root in ("public/repo", "oracle/clean"):
            assert (task_dir / root / h).read_bytes() == (pkg / "src" / h).read_bytes()
    task = rb.RepoTask.load(task_dir)
    assert rb.execute(task, {}).tier == "visible_fail"
    assert rb.execute(task, {"rtl/toy.v": clean}).tier == "visible_pass"
    assert task.task["editable"] == ["rtl/toy.v"]


@pytest.mark.parametrize("damage", ["changed", "missing", "unfrozen", "declaration", "editable"])
def test_red_header_integrity_refused_before_generation(tmp_path, damage):
    pkg, freeze = header_fixture(tmp_path)
    header = pkg / "src/config.vh"
    if damage == "changed":
        header.write_text("parameter WIDTH = 8\n")
    elif damage == "missing":
        header.unlink()
    elif damage == "unfrozen":
        del freeze["files"]["src/config.vh"]
    else:
        man = json.loads((pkg / "carrier.json").read_text())
        if damage == "declaration":
            man["header_dependencies"].remove("config.vh")
        else:
            man["editable"].append("config.vh")
        (pkg / "carrier.json").write_text(json.dumps(man))
    gen, sent = generator([raw()])
    with pytest.raises((wm.FreezeMismatch, ValueError)):
        red907.generate_slot(gen, pkg, freeze, arm_context("ordinary"),
                             tmp_path / "refused", seed=7, seen=set())
    assert not sent and not (tmp_path / "refused").exists()


def generator(replies, cap=4, clean=CLEAN):
    replies = [{**{k: v for k, v in r.items() if k != "buggy_rtl"},
                "edits": [{"find": clean, "replace": r["buggy_rtl"]}]}
               if isinstance(r, dict) and "buggy_rtl" in r else r for r in replies]
    transport, sent = scripted(replies)
    client = BudgetedClient(build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=transport), max_calls=cap)
    return RegressionGenerator(client, WordCounter(), output_tokens=client.config.maximum_output_tokens), sent


def packet():
    return {"version": "test", "source_task": "source", "source_candidate_hash": "hash",
            "evidence": [{"id": "e1", "kind": "blue_edit", "content": "HISTORY_SENTINEL",
                          "content_hash": hash_payload("HISTORY_SENTINEL")}]}


def abstraction():
    return {"evidence_ids": ["e1"], "attempted_decision": "relax admission",
            "violated_obligation": "finite capacity", "preserved_obligations": ["progress"],
            "applicability_preconditions": ["finite buffering"], "predicted_misleading_repair": "ignore buffered responses"}


def test_first_abstraction_is_frozen_and_ordinary_has_no_history(tmp_path):
    gen, sent = generator([abstraction(), raw(), raw(), raw()])
    p, freeze = fixture(tmp_path)
    a = gen.extract(packet(), seed=1, directory=tmp_path / "extract")
    assert a["status"] == "frozen"
    with pytest.raises(FileExistsError):
        gen.extract(packet(), seed=1, directory=tmp_path / "extract")
    public = red907.public_material(p, freeze)
    for arm in ("ordinary", "automatic", "manual"):
        kw = {} if arm == "ordinary" else {"packet": packet()}
        if arm == "automatic":
            kw["abstraction"] = a
        gen.propose(public, arm_context(arm, **kw), seed=2, directory=tmp_path / arm)
    ordinary = json.dumps(sent[1])
    assert "HISTORY_SENTINEL" not in ordinary and MANUAL_SUMMARY not in ordinary
    assert "HISTORY_SENTINEL" in json.dumps(sent[2]) and MANUAL_SUMMARY not in json.dumps(sent[2])
    assert MANUAL_SUMMARY in json.dumps(sent[3])
    assert "variants" not in sent[1]["user"]["carrier"]
    with pytest.raises(ValueError, match="cannot receive"):
        arm_context("ordinary", packet=packet())
    assert gen.client.total_calls == 4


def test_malformed_extraction_has_no_manual_fallback(tmp_path):
    gen, _ = generator([{**abstraction(), "evidence_ids": ["invented"]}])
    a = gen.extract(packet(), seed=1, directory=tmp_path / "extract")
    assert a["status"] == "malformed"
    with pytest.raises(ValueError, match="frozen abstraction"):
        arm_context("automatic", packet=packet(), abstraction=a)


@pytest.mark.parametrize("predicted", [ROW_Z, CLEAN, ROW_Y, "module toy(;"])
def test_shortcut_diagnostic_never_gates_admission(tmp_path, predicted):
    pkg, freeze = fixture(tmp_path)
    proposal = materialize(raw(predicted), CLEAN, "toy")
    rec = red907.check_proposal(pkg, freeze, proposal, tmp_path / "check", seen=set())
    assert rec["status"] == "admitted"
    diag = rec["shortcut_diagnostic"]
    assert diag["affects_admission"] is False
    assert diag.get("matrix_supported", False) == (predicted == ROW_Z)
    blue = tmp_path / "check/blue_task/public"
    assert "hypothesis" not in (blue / "task.json").read_text()
    assert (blue / "repo/rtl/toy.v").read_text() == ROW_Y
    assert (blue / "spec.txt").read_text() == "toy\n"


def test_admitted_first_proposal_stops_even_with_bad_shortcut_and_exports_replayable_task(tmp_path):
    from r3e.loop import repo_blue as rb
    gen, sent = generator([raw(CLEAN)])
    pkg, freeze = fixture(tmp_path)
    result = red907.generate_slot(gen, pkg, freeze, arm_context("ordinary"), tmp_path / "slot", seed=7, seen=set())
    assert result["status"] == "admitted" and result["proposals"] == 1 and len(sent) == 1
    task = rb.RepoTask.load(tmp_path / "slot/candidate_0/blue_task")
    baseline = rb.execute(task, {})
    assert baseline.tier == "visible_fail"
    assert rb.execute(task, {"rtl/toy.v": CLEAN}).tier == "visible_pass"
    with pytest.raises(FileExistsError):
        red907.generate_slot(gen, pkg, freeze, arm_context("ordinary"), tmp_path / "slot", seed=7, seen=set())
    assert len(sent) == 1


def test_rejected_proposal_can_use_only_one_more_call_and_all_records_survive(tmp_path):
    gen, sent = generator([{**raw(), "buggy_rtl": CLEAN}, raw()])
    pkg, freeze = fixture(tmp_path)
    res = red907.generate_slot(gen, pkg, freeze, arm_context("ordinary"), tmp_path / "slot", seed=2, seen=set())
    assert res["status"] == "admitted" and res["proposals"] == 2
    assert sent[1]["user"]["feedback"]["public_check"]["reason"] == "no_token_change"
    assert (tmp_path / "slot/candidate_0/admission.json").exists()


def test_abstention_and_budget_exhaustion_do_not_create_retries(tmp_path):
    pkg, freeze = fixture(tmp_path)
    gen, sent = generator([{"abstain": "no applicable construction"}])
    r = red907.generate_slot(gen, pkg, freeze, arm_context("ordinary"), tmp_path / "abstain", seed=1, seen=set())
    assert r["status"] == "abstained" and len(sent) == 1
    gen, sent = generator([raw()], cap=0)
    with pytest.raises(CallBudgetExceeded):
        red907.generate_slot(gen, pkg, freeze, arm_context("ordinary"), tmp_path / "cap", seed=1, seen=set())
    assert not sent and (tmp_path / "cap/call_0/response.json").exists()


def test_freeze_change_refused_before_calls_and_unfrozen_spec_cannot_be_swapped(tmp_path):
    pkg, freeze = fixture(tmp_path)
    gen, sent = generator([raw()])
    man = json.loads((pkg / "carrier.json").read_text())
    (pkg / "alternative.md").write_text("leak")
    man["spec"] = "alternative.md"
    (pkg / "carrier.json").write_text(json.dumps(man))
    with pytest.raises(ValueError, match="not covered"):
        red907.generate_slot(gen, pkg, freeze, arm_context("ordinary"), tmp_path / "slot", seed=1, seen=set())
    assert not sent


def test_dedup_is_within_arm_only(tmp_path):
    pkg, freeze = fixture(tmp_path)
    seen = set()
    p = materialize(raw(), CLEAN, "toy")
    assert red907.check_proposal(pkg, freeze, p, tmp_path / "one", seen=seen)["status"] == "admitted"
    assert red907.check_proposal(pkg, freeze, p, tmp_path / "two", seen=seen)["reason"] == "duplicate_within_arm"
    assert red907.check_proposal(pkg, freeze, p, tmp_path / "other_arm", seen=set())["status"] == "admitted"


def test_source_export_is_allowlisted_and_checks_provenance(tmp_path):
    pub = tmp_path / "public"
    (pub / "repo").mkdir(parents=True)
    (pub / "visible").mkdir()
    task = {"task_id": "source", "source_root": "repo", "spec": "spec.txt", "visible_test": "visible/build.json"}
    build = {"source_files": ["tb.sv"]}
    (pub / "task.json").write_text(json.dumps(task))
    (pub / "spec.txt").write_text("source spec")
    (pub / "visible/build.json").write_text(json.dumps(build))
    (pub / "visible/tb.sv").write_text("public test")
    (pub / "repo/a.v").write_text(CLEAN)
    files = {"a.v": ROW_Z}
    h = hash_payload(files)
    arc = {"task_id": "source", "candidate_hash": h, "files": files, "patch": "actual Blue patch",
           "baseline_hashes": {"a.v": hash_payload(CLEAN)}}
    enc = {"result": {"task_id": "source", "final_candidate_hash": h, "closure": ["a.v"],
                      "attempts": [{"candidate_hash": h, "edit": "actual description", "tier": "visible_pass", "stage": "simulation"}]}}
    rep = {"candidate_hash": h, "closure_baseline_hashes": arc["baseline_hashes"],
           "spec_hash": hash_payload("source spec"), "original_visible_harness_hash": hash_payload("public test"),
           "build_config": build, "guard_stimulus": {"reset": 2},
           "review_prose": "DO_NOT_EXPORT_REVIEW", "oracle_patch_hash": "DO_NOT_EXPORT_ORACLE",
           "results": [{"suite": "W_preserve", "variant": "reference", "result": "DO_NOT_EXPORT_REFERENCE"},
                       {"suite": "W_preserve", "variant": "blue_repeat_1",
                        "result": {"tier": "visible_fail", "evidence": {"log": "observed transaction trace"}}}]}
    p, provenance = red907.source_packet(tmp_path, enc, arc, rep)
    assert "DO_NOT_EXPORT" not in json.dumps(p)
    assert "observed transaction trace" in json.dumps(p)
    assert provenance["packet_hash"] == hash_payload(p)
    (pub / "repo/a.v").write_text(ROW_Y)
    with pytest.raises(ValueError, match="baseline hash mismatch"):
        red907.source_packet(tmp_path, enc, arc, rep)


def test_output_authority_is_checked_before_ledger_or_transport(tmp_path):
    gen, sent = generator([raw()])
    with pytest.raises(ValueError, match="authority"):
        RegressionGenerator(gen.client, WordCounter(), output_tokens=gen.client.config.maximum_output_tokens + 1)
    assert not sent and gen.client.total_calls == 0


def test_compilation_error_is_a_rejected_proposal_not_an_infrastructure_stop(tmp_path):
    pkg, freeze = fixture(tmp_path)
    p = materialize({**raw(), "buggy_rtl": ROW_Y.replace("assign y = a;", "assign y = missing;")}, CLEAN, "toy")
    rec = red907.check_proposal(pkg, freeze, p, tmp_path / "bad", seen=set())
    assert rec["status"] == "rejected" and rec["suites"]["W_bug"]["tier"] == "compile_fail"
    assert not (tmp_path / "bad/blue_task").exists()


@pytest.mark.parametrize("hint", ["// RED: restore the threshold\n", "/* repair hint */\n"])
def test_added_comment_with_real_mutation_is_rejected_before_export(tmp_path, hint):
    pkg, freeze = fixture(tmp_path)
    p = materialize({**raw(), "buggy_rtl": hint + ROW_Y}, CLEAN, "toy")
    rec = red907.check_proposal(pkg, freeze, p, tmp_path / "hint", seen=set())
    assert rec["status"] == "rejected" and rec["reason"] == "comments_changed"
    assert not rec["language_review"]["comments_unchanged"]
    assert not (tmp_path / "hint/blue_task").exists()
    assert (tmp_path / "hint/candidate.v").read_text() == hint + ROW_Y


def test_comment_lexer_preserves_order_and_ignores_strings_and_escaped_names():
    clean = '// one\nwire \\name//not_comment ; string s="/*not*/ \\" //not"; /* two */\n'
    assert rtl_language_review(clean, clean.replace('wire', 'logic'))["comments_unchanged"]
    assert not rtl_language_review(clean, clean.replace('// one', '// changed'))["comments_unchanged"]
    assert not rtl_language_review('// a\n// b\n', '// b\n// a\n')["comments_unchanged"]
    assert not rtl_language_review('/* a */', '')["comments_unchanged"]


def test_new_identifiers_are_recorded_not_banned(tmp_path):
    pkg, freeze = fixture(tmp_path)
    clean = CLEAN.replace('assign y = a + 1;', 'wire [3:0] original_value = a; assign y = original_value + 1;')
    (pkg / 'src/rtl/toy.v').write_text(clean)
    freeze['files']['src/rtl/toy.v'] = hash_payload(clean)
    rtl = clean.replace('original_value + 1', 'original_value').replace('original_value', 'refill_value')
    p = materialize({**raw(), "buggy_rtl": rtl}, clean, "toy")
    rec = red907.check_proposal(pkg, freeze, p, tmp_path / "names", seen=set())
    assert rec["status"] == "admitted"
    assert rec["language_review"]["new_identifiers"] == ["refill_value"]


@pytest.mark.parametrize("content", ["", '{"abstain":"example"}'])
def test_output_cutoff_is_no_answer_and_does_not_get_another_proposal(tmp_path, content):
    pkg, freeze = fixture(tmp_path)
    sent = []

    def transport(**req):
        sent.append(req)
        return {"content": content, "finish_reason": "length", "input_tokens": 10, "output_tokens": 65536}

    client = BudgetedClient(build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=transport,
                                         maximum_output_tokens=65536), max_calls=10)
    gen = RegressionGenerator(client, WordCounter(), output_tokens=65536, abstraction_output_tokens=32768)
    result = red907.generate_slot(gen, pkg, freeze, arm_context("ordinary"), tmp_path / "slot", seed=1, seen=set())
    assert result["status"] == "no_answer" and result["proposals"] == 1
    assert client.total_calls == len(sent) == 1
    proposal = json.loads((tmp_path / "slot/call_0/proposal.json").read_text())
    assert proposal["reason"] == "output_budget"
    assert not (tmp_path / "slot/candidate_0/candidate.v").exists()


def test_stage_caps_and_edits_only_generation(tmp_path):
    from r3e.loop.fakes import _reply
    sent = []

    def transport(**req):
        sent.append(req)
        return _reply(abstraction() if len(sent) == 1 else raw(), len(sent))

    client = BudgetedClient(build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=transport,
                                         maximum_output_tokens=65536), max_calls=10)
    gen = RegressionGenerator(client, WordCounter(), output_tokens=65536, abstraction_output_tokens=32768)
    assert gen.extract(packet(), seed=1, directory=tmp_path / "extract")["status"] == "frozen"
    result = gen.propose({"clean_rtl": CLEAN, "top_module": "toy"}, arm_context("ordinary"),
                         seed=1, directory=tmp_path / "propose")
    assert result["reason"] == "edits_required"
    assert [r["max_tokens"] for r in sent] == [32768, 65536]


def test_context_limit_is_recorded_without_http(tmp_path):
    gen, sent = generator([])
    gen.context_tokens = gen.output_tokens + 1
    result = gen.propose({"clean_rtl": CLEAN, "top_module": "toy"}, arm_context("ordinary"),
                         seed=1, directory=tmp_path / "propose")
    assert result["status"] == "request_too_large" and not sent
