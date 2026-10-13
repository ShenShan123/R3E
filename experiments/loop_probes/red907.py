"""Single-file Red generation -> public admission -> repository Blue task.

Offline helpers and callable integration, not a paid experiment launcher. The
experiment scheduler owns arm interleaving, whole-slot call reservations and
three independent Blue encounters. No hidden evaluator is read here.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
import tempfile
import time

from experiments.loop_probes import carrier_task, witness_matrix as wm
from r3e.loop.red_regression import (VERSION, RegressionGenerator, save_new, validate_packet,
                                      rtl_language_review)
from r3e.loop.red_design import EditBudget, design_constraints
from r3e.loop.corpus import Carrier
from r3e.loop.repo_blue import _run
from r3e.knowledge.verilog_utils import safe_tokenize
from r3e.protocol.hashing import hash_payload


def _inside(root: Path, rel: str) -> Path:
    if not isinstance(rel, str) or Path(rel).is_absolute() or any(x in {"", ".", ".."} for x in rel.split("/")):
        raise ValueError("invalid package path")
    path = root / rel
    if root.resolve() not in path.resolve().parents or path.is_symlink():
        raise ValueError("path outside package or symlink")
    return path


def public_material(pkg: Path, freeze: dict) -> dict:
    wm.check_freeze(pkg, freeze)
    man = wm.load(pkg)
    if (len(man["editable"]) != 1 or man["tool"] != "icarus"
            or man.get("defines") or man.get("include_dirs")):
        raise ValueError("v1 supports one editable file, Icarus and no defines/include paths")

    def read(rel):
        content = _inside(pkg, rel).read_text()
        if freeze["files"].get(rel) != hash_payload(content):
            raise ValueError(f"input not covered by suite freeze: {rel}")
        return content

    primary = man["editable"][0]
    top = man["top_module"]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top):
        raise ValueError("unsupported top name")
    for key, value in man.get("parameters", {}).items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", key) or type(value) is not int:
            raise ValueError("v1 requires integer parameters")
    clean = read("src/" + primary)
    constraints = design_constraints(Carrier(man["carrier_id"], man["carrier_id"], clean, top, ()), EditBudget())
    return {"carrier_id": man["carrier_id"], "top_module": top, "editable_file": primary,
            "clean_rtl": clean, "specification": read(man["spec"]), "edit_budget": constraints,
            # Included fragments are immutable context, not standalone compile units
            # or padding for the primary-file edit budget.
            "dependencies": {f: read("src/" + f)
                             for f in [*man["rtl"], *man.get("header_dependencies", [])] if f != primary},
            "public_suites": {name: {f: read(f"suites/{name}/{f}") for f in suite["files"]}
                              for name, suite in man["suites"].items()},
            "parameters": man.get("parameters", {}), "syntax_fraction_ceiling": 0.4,
            "manifest_hash": hash_payload(wm.frozen_fields(man)), "freeze_hash": hash_payload(freeze)}


def source_packet(task_dir: Path, encounter: dict, archive: dict, replay: dict) -> tuple[dict, dict]:
    """Mechanical export from a selected encounter; never copy review/reference rows.

    The reviewed regression stimulus and the candidate trace are explicitly
    declassified. Replay reference results and oracle patch hashes are excluded.
    Missing historical feedback stays missing; fresh replay is labeled as such.
    """
    pub = task_dir / "public"
    task = json.loads(_inside(pub, "task.json").read_text())
    result = encounter["result"]
    identity = archive["candidate_hash"]
    if (archive["task_id"] != task["task_id"] or result["task_id"] != task["task_id"]
            or identity != result["final_candidate_hash"] or identity != replay["candidate_hash"]
            or hash_payload(archive["files"]) != identity):
        raise ValueError("source task/candidate identity mismatch")
    evidence, provenance = [], {}

    def add(eid, kind, content, origin):
        evidence.append({"id": eid, "kind": kind, "content": content, "content_hash": hash_payload(content)})
        provenance[eid] = {"origin": origin, "content_hash": hash_payload(content)}

    add("task", "public_task", {k: task[k] for k in ("task_id", "source_root", "spec", "visible_test")}, "public/task.json")
    spec = _inside(pub, task["spec"]).read_text()
    if hash_payload(spec) != replay["spec_hash"]:
        raise ValueError("source spec hash mismatch")
    add("spec", "public_spec", spec, "public/" + task["spec"])
    build = json.loads(_inside(pub, task["visible_test"]).read_text())
    if build != replay["build_config"]:
        raise ValueError("source build changed since reviewed replay")
    add("build", "public_build", build, "public/" + task["visible_test"])
    closure = [f for f in result["closure"] if not f.startswith("harness:")]
    if set(archive["files"]) - set(closure):
        raise ValueError("candidate outside recorded public closure")
    for i, rel in enumerate(closure):
        text = _inside(pub, task["source_root"] + "/" + rel).read_text()
        expected = replay["closure_baseline_hashes"].get(rel)
        if expected != hash_payload(text) or (rel in archive["files"] and archive["baseline_hashes"].get(rel) != expected):
            raise ValueError("source baseline hash mismatch")
        add(f"rtl_{i}", "public_rtl", {"file": rel, "text": text}, "public/" + task["source_root"] + "/" + rel)
    for i, rel in enumerate(build["source_files"]):
        path = str(Path(task["visible_test"]).parent / rel)
        text = _inside(pub, path).read_text()
        if len(build["source_files"]) != 1 or hash_payload(text) != replay["original_visible_harness_hash"]:
            raise ValueError("source visible harness hash mismatch or unsupported source layout")
        add(f"test_{i}", "public_test", {"file": rel, "text": text}, "public/" + path)
    attempt = next(a for a in result["attempts"] if a.get("candidate_hash") == identity)
    add("blue_edit", "blue_edit", {"description": attempt["edit"], "files": archive["files"],
                                   "patch": archive["patch"]}, "selected archived Blue candidate")
    add("visible_result", "visible_feedback", {"tier": attempt["tier"], "stage": attempt["stage"],
             "note": "Recorded original outcome; full original feedback not retained here."}, "selected encounter")
    regression = next(r for r in replay["results"] if r["suite"] == "W_preserve" and r["variant"] == "blue_repeat_1")
    add("regression", "development_regression", {"stimulus": replay["guard_stimulus"],
        "tier": regression["result"]["tier"], "trace": regression["result"]["evidence"]["log"],
        "scope": "Reviewed legal source stimulus, declassified for development; fresh offline replay, not an original Blue input."},
        "source replay: only candidate row, no reference or review prose")
    packet = {"version": VERSION, "source_task": task["task_id"], "source_candidate_hash": identity, "evidence": evidence}
    validate_packet(packet)
    return packet, {"packet_hash": hash_payload(packet), "sources": provenance,
                    "encounter_hash": hash_payload(encounter), "archive_hash": hash_payload(archive),
                    "replay_hash": hash_payload(replay)}


def _loop_check(pkg: Path, man: dict, variant: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        shutil.copytree(pkg / "src", work, dirs_exist_ok=True)
        overlay = pkg / "variants" / variant
        if overlay.exists():
            shutil.copytree(overlay, work, dirs_exist_ok=True)
        files = " ".join(json.dumps(f) for f in man["rtl"])
        params = " ".join(f"-chparam {k} {v}" for k, v in man.get("parameters", {}).items())
        cmd = f"read_verilog -sv {files}; hierarchy -check -top {man['top_module']} {params}; proc; flatten; check -assert"
        rc, log = _run(["yosys", "-Q", "-T", "-p", cmd], work, man.get("build_timeout", 120))
        status = "logic_loop" if "logic loop" in log.lower() else "ok" if rc == 0 else "analysis_unavailable"
        return {"status": status, "returncode": rc, "log": log[-3000:], "command": cmd}


def check_proposal(pkg: Path, freeze: dict, proposal: dict, out: Path, *, seen: set[str]) -> dict:
    """Archive all materialized candidates. Functional admission ignores shortcut outcome.

    ``seen`` belongs to ONE ARM. Equal proposals in different arms are permitted.
    The result is public functional admission only; hidden suites and reviewed
    causal attribution remain on the measurement side.
    """
    started = time.monotonic()
    public = public_material(pkg, freeze)
    out.mkdir(parents=True, exist_ok=False)
    save_new(out / "proposal.json", proposal)
    if proposal["status"] != "proposal":
        record = {"status": proposal["status"], "reason": proposal.get("reason")}
        save_new(out / "admission.json", record)
        return record
    language = rtl_language_review(public["clean_rtl"], proposal["buggy_rtl"])
    (out / "candidate.v").write_text(proposal["buggy_rtl"])
    if not language["comments_unchanged"]:
        record = {"version": VERSION, "status": "rejected", "reason": "comments_changed",
                  "language_review": language, "tools_seconds": time.monotonic() - started}
        save_new(out / "admission.json", record)
        return record
    # Recompute boundaries even when called directly with a saved proposal.
    from r3e.loop.red_design import validate_design
    budget = validate_design(public["clean_rtl"], proposal["buggy_rtl"], public["top_module"])
    fingerprint = hash_payload({"carrier": public["carrier_id"],
                                "tokens": [t.value for t in safe_tokenize(proposal["buggy_rtl"])]})
    record = {"version": VERSION, "status": "rejected", "candidate_hash": hash_payload(proposal["buggy_rtl"]),
              "fingerprint": fingerprint, "edit_budget": budget, "freeze_hash": hash_payload(freeze),
              "language_review": language}
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "package"
        # Only frozen files, no existing variants/evaluator materials, enter the scratch package.
        man = deepcopy(wm.load(pkg))
        copied = {man["spec"], *("src/" + f for f in [*man["rtl"], *man.get("header_dependencies", [])]),
                  *(f"suites/{s}/{f}" for s, v in man["suites"].items() for f in v["files"])}
        # Additional frozen public license/contract files are needed by check_freeze.
        copied |= {f for f in freeze["files"] if f.startswith("licenses/") or f == "suites/CONTRACT.md"}
        if set(freeze["files"]) - copied:
            raise ValueError("freeze contains files outside the public package allowlist")
        for rel in sorted(copied):
            dest = _inside(stage, rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_inside(pkg, rel), dest)
        man["variants"] = {"clean": {"role": "clean"}, "candidate": {"role": "defect"}}
        rel = man["editable"][0]
        for name, rtl in (("candidate", proposal["buggy_rtl"]),
                          ("predicted", proposal.get("predicted", {}).get("rtl"))):
            if rtl is None:
                continue
            dest = stage / "variants" / name / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(rtl)
            (out / f"{name}.v").write_text(rtl)
        (stage / "carrier.json").write_text(json.dumps(man))
        # Freeze files excludes manifest in production; reject changed manifests in older freezes.
        wm.check_freeze(stage, freeze)
        baseline = {s: wm.run_cell(stage, man, s, "clean") for s in ("W_bug", "W_preserve")}
        clean_loop = _loop_check(stage, man, "clean")
        record["baseline"] = {"suites": baseline, "structural": clean_loop}
        if any(c["tier"] != "pass" for c in baseline.values()) or clean_loop["status"] != "ok":
            record.update(status="inconclusive", reason="clean_baseline_not_qualified")
        else:
            cells = {s: wm.run_cell(stage, man, s, "candidate") for s in ("W_bug", "W_preserve")}
            loop = _loop_check(stage, man, "candidate")
            record.update(suites=cells, structural=loop)
            if fingerprint in seen:
                record["reason"] = "duplicate_within_arm"
            elif loop["status"] == "logic_loop":
                record["reason"] = loop["status"]
            elif {s: c["tier"] for s, c in cells.items()} != wm.EXPECTED["defect"]:
                record["reason"] = "outside_public_admission_matrix"
            elif loop["status"] == "analysis_unavailable":
                record.update(status="inconclusive", reason="structural_check_unavailable")
            else:
                record.update(status="admitted", reason="public_matrix_and_structure_pass")
                seen.add(fingerprint)
            if proposal.get("predicted", {}).get("status") == "materialized":
                predicted_cells = {s: wm.run_cell(stage, man, s, "predicted") for s in ("W_bug", "W_preserve")}
                predicted_loop = _loop_check(stage, man, "predicted")
                record["shortcut_diagnostic"] = {"suites": predicted_cells, "structural": predicted_loop,
                    "matrix_supported": predicted_loop["status"] == "ok" and
                        {s: c["tier"] for s, c in predicted_cells.items()} == wm.EXPECTED["shortcut"],
                    "mechanism_review": "pending", "affects_admission": False}
            elif "predicted" in proposal:  # a no-prediction schema gets no shortcut record, not a placeholder
                record["shortcut_diagnostic"] = {**proposal["predicted"], "affects_admission": False}
            if record["status"] == "admitted":
                carrier_task.export(stage, "candidate", out / "blue_task", freeze)
                # Task identity must distinguish candidates, independently of the arm.
                task_file = out / "blue_task/public/task.json"
                task = json.loads(task_file.read_text())
                task["task_id"] = public["carrier_id"] + "__" + record["candidate_hash"][7:23]
                task_file.write_text(json.dumps(task))
                export_file = out / "blue_task/export.json"
                exported = json.loads(export_file.read_text())
                exported.update(task_id=task["task_id"], package="frozen scratch reconstruction",
                                candidate_hash=record["candidate_hash"])
                exported["public_files"]["public/task.json"] = hash_payload(task_file.read_text())
                export_file.write_text(json.dumps(exported, indent=2))
                record["blue_task"] = "blue_task"
    record["tools_seconds"] = time.monotonic() - started
    save_new(out / "admission.json", record)
    return record


def generate_slot(generator: RegressionGenerator, pkg: Path, freeze: dict, context: dict,
                  out: Path, *, seed: int, seen: set[str], proposal_calls: int = 2) -> dict:
    """At most two proposals, no replacement slot. Caller reserves Blue's budget separately.

    First admitted mutant stops generation even if its shortcut diagnostic fails.
    Only this slot's public checks can feed its second proposal. No other arm's
    results or actual Blue outcomes enter generation.
    """
    if type(proposal_calls) is not int or not 1 <= proposal_calls <= 2:
        raise ValueError("proposal_calls must be 1 or 2")
    public = public_material(pkg, freeze)
    out.mkdir(parents=True, exist_ok=False)
    save_new(out / "slot.json", {"version": VERSION, "seed": seed, "context": context,
             "public_hash": hash_payload(public), "freeze_hash": hash_payload(freeze), "proposal_calls": proposal_calls})
    feedback, records = None, []
    for index in range(proposal_calls):
        # Detect concurrent material changes before each call.
        if public_material(pkg, freeze) != public:
            raise ValueError("carrier changed during slot")
        proposal = generator.propose(public, context, seed=seed + index,
                                     directory=out / f"call_{index}", feedback=feedback)
        record = check_proposal(pkg, freeze, proposal, out / f"candidate_{index}", seen=seen)
        records.append(record)
        if record["status"] in {"admitted", "abstained", "inconclusive", "no_answer", "request_too_large"}:
            break
        # The clean source is already in the request. Submitted edits reconstruct
        # both variants without repeating full materialized RTL and duplicate diffs.
        feedback = {"previous_proposal": proposal.get("submitted_answer", proposal), "public_check": record}
    result = {"arm": context["arm"], "status": records[-1]["status"], "proposals": len(records),
              "admitted_candidate": len(records) - 1 if records[-1]["status"] == "admitted" else None}
    save_new(out / "result.json", result)
    return result


def main() -> int:
    """Replay a saved Red JSON proposal without constructing a provider client."""
    import argparse
    from r3e.loop.red_regression import materialize

    ap = argparse.ArgumentParser(description="Offline Red proposal admission and Blue task export")
    ap.add_argument("package", type=Path)
    ap.add_argument("--freeze", type=Path, required=True)
    ap.add_argument("--proposal", type=Path, required=True, help="raw Red JSON, not a judged proposal")
    ap.add_argument("--out", type=Path, required=True, help="new output directory")
    args = ap.parse_args()
    freeze = json.loads(args.freeze.read_text())
    public = public_material(args.package, freeze)
    proposal = materialize(json.loads(args.proposal.read_text()), public["clean_rtl"], public["top_module"])
    record = check_proposal(args.package, freeze, proposal, args.out, seen=set())
    print(json.dumps({"status": record["status"], "reason": record.get("reason"), "model_calls": 0}))
    return 0 if record["status"] == "admitted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
