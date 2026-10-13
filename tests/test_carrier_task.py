import json
import shutil

import pytest

from experiments.loop_probes import carrier_task as ct
from experiments.loop_probes import witness_matrix as wm
from r3e.loop import repo_blue as rb
from r3e.protocol.hashing import hash_payload
from tests.test_repo_blue import WordCounter, runner, scripted
from tests.test_witness_matrix import ROW_Y, package

needs_icarus = pytest.mark.skipif(not shutil.which("iverilog"), reason="iverilog is required")
DEP = "module dep(input a, output b); assign b = a; endmodule\n"


def carrier(tmp_path):
    """Toy package with a read-only dependency, a defect variant, and its suite freeze."""
    pkg = package(tmp_path, variants={"clean": ("clean", None), "defect": ("defect", ROW_Y)})
    (pkg / "src" / "rtl" / "dep.v").write_text(DEP)
    man = json.loads((pkg / "carrier.json").read_text())
    man.update(rtl=["rtl/toy.v", "rtl/dep.v"], spec="spec.md", top_module="toy")
    (pkg / "carrier.json").write_text(json.dumps(man))
    freeze = {"files": {r: h for r, h in wm.package_files(pkg).items() if not r.startswith("variants/")},
              "frozen_manifest_fields_hash": hash_payload(wm.frozen_fields(man))}
    return pkg, freeze


def test_a_changed_package_is_refused(tmp_path):
    pkg, freeze = carrier(tmp_path)
    (pkg / "suites" / "W_preserve" / "tb.sv").write_text("// edited after the freeze\n")
    with pytest.raises(wm.FreezeMismatch, match="suites/W_preserve/tb.sv"):
        ct.export(pkg, "defect", tmp_path / "task", freeze)
    with pytest.raises(wm.FreezeMismatch):
        wm.qualify(pkg, freeze)


@needs_icarus
def test_blue_on_an_exported_carrier_sees_both_suites_and_only_the_editable_file(tmp_path):
    pkg, freeze = carrier(tmp_path)
    task_dir = ct.export(pkg, "defect", tmp_path / "task", freeze)
    assert (task_dir / "oracle" / "clean" / "rtl" / "toy.v").read_text() != (task_dir / "public" / "repo" / "rtl" / "toy.v").read_text()
    task = rb.RepoTask.load(task_dir)
    s = rb.setup(task, WordCounter(), rb.RepoBlueConfig())
    assert s.route == "direct" and s.repo_files == ["rtl/toy.v", "rtl/dep.v"]
    assert {k: v["result"] for k, v in s.baseline.evidence["suites"].items()} == {
        "W_bug": "visible_fail", "W_preserve": "visible_pass"}
    touch_dep = {"edits": [{"file": "rtl/dep.v", "find": "assign b = a;", "replace": "assign b = !a;"}], "edit": "dep"}
    fix = {"edits": [{"file": "rtl/toy.v", "find": "assign y = a;", "replace": "assign y = a + 1;"}], "edit": "add one"}
    transport, sent = scripted([touch_dep, fix])
    r, client = runner(transport, archive=tmp_path / "archive")
    res = r.run(task, s, seed=1)
    assert res["outcome"] == "fixed" and res["calls"] == 2
    assert res["attempts"][0]["tier"] == "invalid_edit" and "not an editable file" in res["attempts"][0]["feedback"]
    files = sent[0]["user"]["files"]
    assert files["rtl/dep.v"]["editable"] is False and files["rtl/toy.v"]["editable"] is True
    assert set(sent[0]["user"]["visible_test_sources"]) == {"W_bug_tb.sv", "W_preserve_tb.sv"}
    assert sent[0]["user"]["current_failure_evidence"]["suites"]["W_bug"]["result"] == "visible_fail"
    assert all(f.startswith("public/") for f in res["inputs_read"])
    rec = json.loads(next((tmp_path / "archive").glob("*.json")).read_text())
    assert rec["candidate_hash"] == res["final_candidate_hash"] and list(rec["files"]) == ["rtl/toy.v"]


def header_carrier(tmp_path):
    """Toy package whose compiled file includes a declared, read-only header (never a compile unit)."""
    pkg = package(tmp_path, variants={"clean": ("clean", None), "defect": ("defect", ROW_Y)})
    (pkg / "src" / "rtl" / "inc.vh").write_text("localparam STEP = 1;\n")
    for f in (pkg / "src" / "rtl" / "toy.v", pkg / "variants" / "defect" / "rtl" / "toy.v"):
        f.write_text(f.read_text().replace("assign y", '\n`include "rtl/inc.vh"\nassign y').replace("a + 1", "a + STEP"))
    man = json.loads((pkg / "carrier.json").read_text())
    man.update(spec="spec.md", top_module="toy", header_dependencies=["rtl/inc.vh"])
    (pkg / "carrier.json").write_text(json.dumps(man))
    freeze = {"files": {r: h for r, h in wm.package_files(pkg).items() if not r.startswith("variants/")},
              "frozen_manifest_fields_hash": hash_payload(wm.frozen_fields(man))}
    return pkg, man, freeze


def test_header_declarations_are_validated_and_frozen_only_when_present(tmp_path):
    pkg, man, freeze = header_carrier(tmp_path)
    without = {k: v for k, v in man.items() if k != "header_dependencies"}
    assert "header_dependencies" not in wm.frozen_fields(without)  # older packages keep their freeze hashes
    assert wm.frozen_fields(man)["header_dependencies"] == ["rtl/inc.vh"]
    for bad, match in ((["rtl/toy.v"], "distinct"), (["rtl/missing.vh"], "header missing")):
        (pkg / "carrier.json").write_text(json.dumps({**man, "header_dependencies": bad}))
        with pytest.raises(ValueError, match=match):
            wm.load(pkg)
    (pkg / "carrier.json").write_text(json.dumps(man))
    (pkg / "src" / "rtl" / "inc.vh").write_text("localparam STEP = 2;\n")
    with pytest.raises(wm.FreezeMismatch, match="src/rtl/inc.vh"):
        ct.export(pkg, "defect", tmp_path / "task", freeze)


@needs_icarus
def test_exported_headers_are_in_blue_closure_but_read_only(tmp_path):
    pkg, man, freeze = header_carrier(tmp_path)
    rec = wm.qualify(pkg, freeze)
    assert rec["matrix"]["clean/W_bug"]["tier"] == "pass" and rec["matrix"]["defect/W_bug"]["tier"] == "fail"
    task_dir = ct.export(pkg, "defect", tmp_path / "task", freeze)
    build = json.loads((task_dir / "public" / "visible" / "build.json").read_text())
    assert build["rtl"] == ["rtl/toy.v"] and build["headers"] == ["rtl/inc.vh"]
    for root in ("public/repo", "oracle/clean"):
        assert (task_dir / root / "rtl" / "inc.vh").read_text() == (pkg / "src" / "rtl" / "inc.vh").read_text()
    task = rb.RepoTask.load(task_dir)
    s = rb.setup(task, WordCounter(), rb.RepoBlueConfig())
    assert s.repo_files == ["rtl/toy.v", "rtl/inc.vh"]
    touch = {"edits": [{"file": "rtl/inc.vh", "find": "STEP = 1", "replace": "STEP = 0"}], "edit": "header"}
    fix = {"edits": [{"file": "rtl/toy.v", "find": "assign y = a;", "replace": "assign y = a + STEP;"}], "edit": "fix"}
    transport, sent = scripted([touch, fix])
    r, _ = runner(transport, archive=tmp_path / "archive")
    res = r.run(task, s, seed=1)
    assert res["attempts"][0]["tier"] == "invalid_edit" and "not an editable file" in res["attempts"][0]["feedback"]
    assert res["outcome"] == "fixed"
    files = sent[0]["user"]["files"]
    assert files["rtl/inc.vh"] == {"editable": False, "content": "localparam STEP = 1;\n"}
    assert sent[0]["user"]["build_configuration"]["headers"] == ["rtl/inc.vh"]
