from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from scripts import extract_generated_specs as extractor
from r3e.knowledge.schema import VisibleFeedback
from r3e.loop.blue import BlueRunner
from r3e.loop.carriers import DEFAULT_CARRIER_MANIFEST, load_carrier_manifest
from r3e.loop.corpus import Challenge
from r3e.loop.env import build_client
from r3e.loop.sim import Verdict


@pytest.fixture
def mini_corpus(tmp_path, monkeypatch):
    clean = tmp_path / "design" / "clean.sv"
    clean.parent.mkdir()
    clean.write_text("module TopModule(input a, output b); assign b=a; endmodule\n")
    row = {"carrier_id": "test", "cluster_id": "test", "top_module": "TopModule",
           "clean_rtl": "design/clean.sv", "visible_tb": [], "hidden_tb": [],
           "source": {"dataset": "verilog-eval-v2", "problem": "Example", "commit": "a" * 40},
           "files": [{"path": "design/clean.sv", "sha256": extractor.sha256(clean.read_bytes())}],
           "user_metadata": {"keep": True}}
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps(row) + "\n")
    raw = b"I would like you to implement TopModule. Output b follows a; the reset value is 13.\n"
    monkeypatch.setattr(extractor, "pinned_description", lambda *args: raw)
    return manifest, row, raw


def test_attach_is_idempotent_and_preserves_metadata(mini_corpus, tmp_path):
    manifest, original, raw = mini_corpus
    roots = {"verilog-eval-v2": tmp_path}
    report = extractor.attach_specs(manifest, roots, repo=tmp_path)
    assert report["specs"] == report["preserved_files"] == 1
    row = json.loads(manifest.read_text())
    for key, value in original.items():
        if key != "files":
            assert row[key] == value
    assert row["files"][0] == original["files"][0]
    assert (tmp_path / row["spec"]).read_bytes() == raw
    assert row["spec_source"]["sha256"] == extractor.sha256(raw)
    first = manifest.read_bytes()
    assert extractor.attach_specs(manifest, roots, repo=tmp_path) == report
    assert manifest.read_bytes() == first


def test_all_rows_validated_before_writing(mini_corpus, tmp_path, monkeypatch):
    manifest, row, raw = mini_corpus
    bad = copy.deepcopy(row)
    bad["carrier_id"] = "bad"
    bad["source"]["problem"] = "UnknownHDL"
    manifest.write_text(json.dumps(row) + "\n" + json.dumps(bad) + "\n")
    before = manifest.read_bytes()
    monkeypatch.setattr(extractor, "pinned_description", lambda root, commit, path:
                        b"Implement this: assign y = a;" if "UnknownHDL" in path else raw)
    with pytest.raises(ValueError, match="HDL"):
        extractor.attach_specs(manifest, {"verilog-eval-v2": tmp_path}, repo=tmp_path)
    assert manifest.read_bytes() == before
    assert not (tmp_path / "design/spec.txt").exists()


def test_existing_file_hash_mismatch_stops_extraction(mini_corpus, tmp_path):
    manifest, row, _ = mini_corpus
    (tmp_path / row["clean_rtl"]).write_text("changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        extractor.attach_specs(manifest, {"verilog-eval-v2": tmp_path}, repo=tmp_path)
    assert not (tmp_path / "design/spec.txt").exists()


def test_concurrent_manifest_edit_is_preserved(mini_corpus, tmp_path, monkeypatch):
    manifest, _, raw = mini_corpus
    concurrent = b'{"another_writer": true}\n'

    def read_and_modify(*args):
        manifest.write_bytes(concurrent)
        return raw

    monkeypatch.setattr(extractor, "pinned_description", read_and_modify)
    with pytest.raises(RuntimeError, match="manifest changed"):
        extractor.attach_specs(manifest, {"verilog-eval-v2": tmp_path}, repo=tmp_path)
    assert manifest.read_bytes() == concurrent
    assert not (tmp_path / "design/spec.txt").exists()


def test_rtllm_original_text_and_actual_top_name():
    raw = b"Please implement the original task.\nModule name:\n  multi_pipe_4bit\nLatency: 4 cycles.\n"
    spec, edits = extractor.extract_description(raw, {"dataset": "rtllm-v2"}, "verified_multi_pipe")
    assert spec.startswith(raw.decode())
    assert "verified_multi_pipe" in spec and "Latency: 4 cycles." in spec
    assert edits == [{"kind": "top_module_note", "original_module": "multi_pipe_4bit",
                      "corpus_module": "verified_multi_pipe"}]
    unchanged, edits = extractor.extract_description(raw, {"dataset": "rtllm-v2"}, "multi_pipe_4bit")
    assert unchanged.encode() == raw and edits == []


def test_reviewed_prompt_changes_require_review():
    with pytest.raises(ValueError, match="needs review"):
        extractor.extract_description(b"A changed prompt", {"dataset": "verilog-eval-v2",
                                      "problem": "Prob062_bugs_mux2"}, "TopModule")


def test_pinned_source_ignores_dirty_working_copy(tmp_path):
    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                              capture_output=True).stdout.strip()
    git("init")
    path = tmp_path / "prompt.txt"
    path.write_bytes(b"Original requirement: count to 9.\n")
    git("add", "prompt.txt")
    git("-c", "user.name=Test", "-c", "user.email=anonymous@users.noreply.github.com",
        "-c", "commit.gpgsign=false", "commit", "-m", "fixture")
    commit = git("rev-parse", "HEAD").decode()
    path.write_bytes(b"Uncommitted, inconsistent requirement")
    assert extractor.pinned_description(tmp_path, commit, "prompt.txt") == b"Original requirement: count to 9.\n"


def test_loader_optional_spec_and_invalid_spec(mini_corpus, tmp_path, monkeypatch):
    from r3e.loop import carriers
    monkeypatch.setattr(carriers, "REPO_ROOT", tmp_path)
    manifest, row, raw = mini_corpus
    assert load_carrier_manifest(manifest)[0].spec == ""
    row["spec"] = "design/spec.txt"
    manifest.write_text(json.dumps(row) + "\n")
    with pytest.raises(FileNotFoundError):
        load_carrier_manifest(manifest)
    path = tmp_path / row["spec"]
    path.write_bytes(raw)
    assert load_carrier_manifest(manifest)[0].spec == raw.decode().strip()
    for invalid in [" ", "module answer; endmodule", "```verilog\nanswer\n```"]:
        path.write_text(invalid)
        with pytest.raises(ValueError):
            load_carrier_manifest(manifest)
    row.update(eligible=False, ineligible_reason="test exclusion")
    manifest.write_text(json.dumps(row) + "\n")
    path.unlink()
    assert load_carrier_manifest(manifest) == []


@pytest.mark.parametrize("problem", sorted(extractor.INELIGIBLE_PROBLEMS))
def test_exclusions_survive_regeneration(mini_corpus, tmp_path, monkeypatch, problem):
    manifest, row, raw = mini_corpus
    row["source"]["problem"] = problem
    row["eligible"] = True
    manifest.write_text(json.dumps(row) + "\n")
    monkeypatch.setattr(extractor, "extract_description", lambda *args: (raw.decode(), []))
    roots = {"verilog-eval-v2": tmp_path}
    for _ in range(2):
        report = extractor.attach_specs(manifest, roots, repo=tmp_path)
        saved = json.loads(manifest.read_text())
        assert saved["eligible"] is False
        assert saved["ineligible_reason"] == extractor.INELIGIBLE_REASON
        assert report["eligible"] == 0 and report["ineligible"] == 1


def test_complete_corpus_specs_and_blue_requests(tmp_path):
    rows = [json.loads(line) for line in DEFAULT_CARRIER_MANIFEST.read_text().splitlines()]
    carriers = load_carrier_manifest(DEFAULT_CARRIER_MANIFEST)
    eligible = [r for r in rows if r.get("eligible") is not False]
    excluded = [r for r in rows if r.get("eligible") is False]
    assert len(rows) == 151 and len(eligible) == len(carriers) == 148
    assert {r["source"]["problem"] for r in excluded} == extractor.INELIGIBLE_PROBLEMS
    assert all(r["ineligible_reason"] for r in excluded)
    assert sum(r["source"]["dataset"] == "verilog-eval-v2" for r in rows) == 113
    assert sum(r["source"]["dataset"] == "rtllm-v2" for r in rows) == 38
    assert sum(r["source"]["dataset"] == "verilog-eval-v2" for r in eligible) == 110
    assert sum(r["source"]["dataset"] == "rtllm-v2" for r in eligible) == 38
    requests = []

    def local_transport(**request):
        payload = json.loads(request["messages"][-1]["content"])
        requests.append(payload)
        return {"content": json.dumps({"replacement_rtl": payload["current_buggy_rtl"], "edit": "test"}),
                "input_tokens": 1, "output_tokens": 1, "provider_request_id": "local-only"}

    verdict = Verdict("hidden_pass", VisibleFeedback(oracle_stage="visible", compile_ok=True))
    blue = BlueRunner(json_client=build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=local_transport),
                      simulator=SimpleNamespace(verdict=lambda *args, **kwargs: verdict),
                      project_root=extractor.ROOT, max_calls=149)
    for row in rows:
        path = extractor.ROOT / row["spec"]
        extractor.validate_spec(path.read_text())
        entries = [f for f in row["files"] if f["path"] == row["spec"]]
        assert len(entries) == 1 and entries[0]["sha256"] == extractor.sha256(path.read_bytes())
        assert row["spec_source"]["commit"] == row["source"]["commit"]
    for row, carrier in zip(eligible, carriers):
        assert carrier.carrier_id == row["carrier_id"]
        assert carrier.spec == (extractor.ROOT / row["spec"]).read_text().strip()
        challenge = Challenge("test_" + carrier.carrier_id, carrier, "module faulty; endmodule", "red")
        record, _, _ = blue._attempt(challenge, index=1, seed=0, phase="evaluation", initial=verdict, history=[])
        assert record["verdict_tier"] == "hidden_pass"
        assert requests[-1]["specification"] == carrier.spec
        assert requests[-1]["current_buggy_rtl"] == challenge.buggy_rtl
    # Compatibility: carriers from corpora without specs do not add an empty field.
    without_spec = replace(carriers[0], spec="")
    blue._attempt(Challenge("legacy", without_spec, "module faulty; endmodule", "red"),
                  index=1, seed=0, phase="evaluation", initial=verdict, history=[])
    assert "specification" not in requests[-1]
    assert len(requests) == 149
