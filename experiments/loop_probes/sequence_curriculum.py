"""Prepare one historical mechanism for continuous rounds; offline only.

prepare freezes specs/checkers before creating variants. acceptance exercises
the real scheduler and simulators with scripted replies, never credentials.
report classifies endpoints only after collection and keeps probes out of state.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import tempfile

from experiments.loop_probes import carrier_task, continuous_loop as loop, repair_feedback, witness_matrix as wm
from r3e.loop import continuous_state as state, red_continuous as red, repo_blue as rb
from r3e.loop.budget import BudgetedClient
from r3e.loop.env import build_client
from r3e.loop.fakes import _reply
from r3e.loop.red_regression import save_new
from r3e.loop.token_count import TokenCounter
from r3e.protocol.hashing import hash_payload

ROOT = loop.ROOT
ASSETS = ROOT / "r3e/loop/assets/prob003"
CASE = "Prob003_non-overlapping_sequence_detect"
CHALLENGE = "RC_406d811b4d61a2"
PRIMARY = "rtl/sequence.sv"
MECHANISM = "group_boundary_consumption"


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _replace(clean, old, new):
    if clean.count(old) != 1:
        raise ValueError("source no longer has the unique expected transition")
    return clean.replace(old, new)


def _source_commit():
    upstream = ROOT / "datasets/external/chipbench"
    return subprocess.run(["git", "-C", str(upstream), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()


def training_edits(clean):
    """Scripted acceptance fixtures only; paid Red generates its own proposals."""
    matched = "        s6:\n            nstate=data?sf1:s1;"
    failed = "        sf6:\n\t\t\tnstate=data?sf1:s1;"
    if clean.count(matched) != 1 or clean.count(failed) != 1:
        raise ValueError("unexpected source transition layout")
    return [
        [{"find": matched, "replace": matched.replace("data?sf1:s1", "data?sf1:idle")},
         {"find": failed, "replace": failed.replace("data?sf1:s1", "data?sf1:idle")}],
        [{"find": matched, "replace": matched.replace("data?sf1:s1", "data?idle:s1")}],
        [{"find": failed, "replace": failed.replace("data?sf1:s1", "data?idle:s1")}],
    ]


def prepare(out: Path, history: Path) -> dict:
    """Use the converted ChipBench source and reviewed seed; no hidden-run selection."""
    rows = [json.loads(l) for l in (ROOT / "datasets/manifests/chipbench89.jsonl").read_text().splitlines()]
    case = next(x for x in rows if x["case_id"] == f"chipbench:assignment:{CASE}" and x["eligible"])
    clean = (ROOT / case["golden_rtl"]).read_text()
    record_path, screen_path, decision_path = (history / n for n in ("red.jsonl", "screens.jsonl", "hit_decisions.json"))
    records = [json.loads(l) for l in record_path.read_text().splitlines()]
    candidates = [row["chosen"] for row in records if row.get("challenge_id") == CHALLENGE and row.get("admitted")]
    if len(candidates) != 1:
        raise ValueError("historical seed must identify exactly one materialized candidate")
    seed = candidates[0]
    screen, = [json.loads(l) for l in screen_path.read_text().splitlines() if json.loads(l).get("challenge_id") == CHALLENGE]
    if json.loads(decision_path.read_text())[CHALLENGE]["decision"] != "PASS":
        raise ValueError("seed is not a reviewed development case")
    provenance = {"clean_hash": hash_payload(clean), "seed_hash": hash_payload(seed["buggy_rtl"]),
                  "record_hashes": {n: hash_payload((history / n).read_text())
                                    for n in ("red.jsonl", "screens.jsonl", "hit_decisions.json")}}
    focus = {"version": "continuous_mechanism_seed_v1", "carrier_id": "chipbench_prob003_group_boundary_v1",
             "mechanism": "Consume each bit exactly once across six-bit group boundaries; preserve output phase and reset.",
             "selection": "human_selected_development_seed", "seed_edits": seed["edits"],
             "history": {"challenge_id": CHALLENGE, "runs": screen["runs"], "unrepaired": screen["fails"],
                         "label": "historical_budgeted_failure_not_current_confirmation"}, "provenance": provenance}
    # Authenticate reconstruction before creating any new package.
    reconstructed = red.materialize({"edits": focus["seed_edits"], "violated_requirement": "group boundary",
                                    "spec_basis": "six-bit groups"}, {"clean_rtl": clean, "top_module": "TopModule"})
    if reconstructed.get("buggy_rtl_hash") != provenance["seed_hash"]:
        raise ValueError("historical edits do not reconstruct the seed against the clean carrier")
    out.mkdir(parents=True, exist_ok=False)
    pkg = out / "package"
    _write(pkg / "src" / PRIMARY, clean)
    spec = (ROOT / case["spec"]).read_text() + "\n\n## Prospective development timing contract\n\n" + (ASSETS / "contract.md").read_text().split("W_bug uses")[0].split("\n\n", 1)[1]
    _write(pkg / "spec.md", spec)
    _write(pkg / "suites/CONTRACT.md", (ASSETS / "contract.md").read_text())
    license_path = ROOT / "datasets/licenses/ChipBench-MIT.txt"
    if not license_path.exists():
        license_path = ROOT / "datasets/chipbench/licenses/ChipBench-MIT.txt"
    _write(pkg / "licenses/ChipBench-MIT.txt", license_path.read_text())
    commit = _source_commit()
    man = {"carrier_id": focus["carrier_id"], "top_module": "TopModule", "rtl": [PRIMARY], "editable": [PRIMARY],
           "spec": "spec.md", "parameters": {}, "tool": "icarus", "defines": {}, "include_dirs": [],
           "pass_marker": "TESTBENCH PASS", "fail_marker": "CHECK_FAIL", "build_timeout": 60, "sim_timeout": 30,
           "source": {"repository": "https://github.com/zhongkaiyu/ChipBench", "commit": commit, "license": "MIT",
                      "family": f"chipbench_{CASE}"},
           "suites": {s: {"files": ["tb.sv"], "top": "tb"} for s in ("W_bug", "W_preserve")},
           "variants": {"clean": {"role": "clean"}}}
    for suite in man["suites"]:
        _write(pkg / "suites" / suite / "tb.sv", (ASSETS / f"{suite}.sv").read_text())
    save_new(pkg / "carrier.json", man)
    freeze = {"files": {k: v for k, v in wm.package_files(pkg).items() if k != "carrier.json"},
              "frozen_manifest_fields_hash": hash_payload(wm.frozen_fields(man))}
    save_new(out / "suite_freeze.json", freeze)
    evaluator = (ASSETS / "evaluator.sv").read_text()
    _write(out / "evaluator/tb.sv", evaluator)
    ef = {"files": {"evaluator/tb.sv": hash_payload(evaluator)},
          "run": {"top": "evaluate_sequence", "rtl": [PRIMARY], "pass_marker": "TESTBENCH PASS", "fail_marker": "CHECK_FAIL"}}
    save_new(out / "evaluator_freeze.json", ef)
    # Freeze and qualify the clean contract before any mutant is materialized.
    clean_rows = {s: wm.run_cell(pkg, man, s, "clean") for s in man["suites"]}
    if any(c["tier"] != "pass" for c in clean_rows.values()):
        raise ValueError("clean source does not satisfy the prospective public contract")
    with tempfile.TemporaryDirectory() as tmp:
        task_path = carrier_task.export(pkg, "clean", Path(tmp) / "task", freeze)
        check = loop.measure._evaluate(rb.RepoTask.load(task_path), {}, ef, out / "evaluator")
    if check["result"] != "pass":
        raise ValueError("clean source does not satisfy the frozen evaluator contract")
    clean_rows["evaluator"] = check
    save_new(out / "clean_qualification.json", clean_rows)
    variants = {"defect": seed["buggy_rtl"],
                "reset_control": _replace(clean, "always @(posedge clk or negedge rst_n)", "always @(posedge clk)"),
                "eval_match_branch": _replace(clean, "        s6:\n            nstate=data?sf1:s1;",
                                              "        s6:\n            nstate=data?s1:sf1;"),
                "eval_miss_branch": _replace(clean, "        sf6:\n\t\t\tnstate=data?sf1:s1;",
                                             "        sf6:\n\t\t\tnstate=data?s1:sf1;")}
    for name, rtl in variants.items():
        _write(pkg / "variants" / name / PRIMARY, rtl)
        man["variants"][name] = {"role": "shortcut" if name == "reset_control" else "defect"}
    (pkg / "carrier.json").write_text(json.dumps(man, indent=2))
    qualification = wm.qualify(pkg, freeze)
    save_new(out / "qualification.json", qualification)
    if not qualification["qualified"]:
        raise ValueError(f"public witness qualification failed: {qualification['problems']}")
    carrier = {"package": str(pkg.resolve()), "suite_freeze": str((out / "suite_freeze.json").resolve()),
               "evaluator_freeze": str((out / "evaluator_freeze.json").resolve()), "evaluator_root": str(out.resolve())}
    anchors = []
    for name in ("eval_match_branch", "eval_miss_branch"):
        task = carrier_task.export(pkg, name, out / "reserved" / name, freeze)
        anchors.append({"task_dir": str(task.resolve()), "carrier_index": 0})
    red.validate_focus(focus, loop.red907.public_material(pkg, freeze))
    materials = {"carriers": [carrier], "anchors": anchors, "mechanism_focus": focus,
                 "qualification_order": "public_and_evaluator_frozen_before_variants",
                 "scope": "new variants on one historically inspected development design; not cross-design transfer"}
    save_new(out / "materials.json", materials)
    return materials


def acceptance(materials, out, counter):
    """Scripted clean repairs test plumbing, not actual Red/Blue capability."""
    pkg = Path(materials["carriers"][0]["package"])
    clean = (pkg / "src" / PRIMARY).read_text()
    fixtures = iter(training_edits(clean))

    def transport(**request):
        user = json.loads(request["messages"][-1]["content"])
        if "carrier" in user:
            result = {"edits": next(fixtures), "violated_requirement": "consume every group-boundary bit",
                      "spec_basis": "six-bit groups with no idle gap"}
        else:
            current = user["files"][PRIMARY]["content"]
            result = {"edits": [{"file": PRIMARY, "find": current, "replace": clean}], "edit": "scripted clean repair"}
            assert "mechanism_focus" not in user and "carrier" not in user
        return _reply(result, 1)

    inner = build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=transport, maximum_output_tokens=65536)
    plan = loop.make_plan(materials["carriers"], materials["anchors"], counter=counter,
        client_config=inner.config.public_identity, mechanism_focus=materials["mechanism_focus"],
        anchor_repeats=2, red_output_tokens=16384)
    result = loop.collect(plan, out, client=BudgetedClient(inner, max_calls=plan["max_calls"]), counter=counter)
    return {**result, "model_calls": 0, "scripted_requests": result["calls"], "stand_in_only": True}


def report(run: Path) -> dict:
    """Three questions with full scheduled denominators, after collection only."""
    plan = loop.read(run / "plan.json")
    loop.validate_plan(plan)
    if loop.read(run / "collection.json")["status"] != "complete":
        raise ValueError("deferred evaluation requires complete collection")
    if (run / "report.json").exists():
        raise FileExistsError(run / "report.json")
    frozen = plan["carriers"][0]
    ef, root = loop.read(frozen["evaluator_freeze"]), Path(frozen["evaluator_root"])

    def classify(trial, task):
        blue = Path(trial["directory"]) / "blue"
        if not (blue / "encounters.jsonl").exists():
            return {"category": trial["status"]}
        return repair_feedback.classify_trials(blue, Path(task), ef, root)["encounters"][0]

    online = []
    for row in (json.loads(l) for l in (run / "slots.jsonl").read_text().splitlines()):
        base = run / "rounds" / str(row["round"]) / row["slot_id"]
        item = {"round": row["round"], "slot_id": row["slot_id"], "admission": row["admission"], "reason": row["reason"]}
        if row["admission"] == "admitted":
            proposal = loop.read(base / "admission/proposal.json")
            item["mutant_hash"] = proposal["buggy_rtl_hash"]
            item["normalized_identity"] = loop.mutant_identity(loop.red907.public_material(Path(frozen["package"]),
                loop.read(frozen["suite_freeze"])), proposal["buggy_rtl"])
            item["changed_fraction"] = proposal["edit_budget"]["all_code"]["changed_fraction"]
            item["repeats_historical_seed"] = proposal["buggy_rtl_hash"] == plan["mechanism_focus"]["provenance"]["seed_hash"]
            for role in ("online", "reference"):
                item[role] = classify(row["trials"][role], base / "admission/blue_task")
        online.append(item)
    probes = []
    for row in (json.loads(l) for l in (run / "probes.jsonl").read_text().splitlines()):
        probes.append({"snapshot": row["snapshot"], "anchor_index": row["anchor_index"], "repeat": row["repeat"],
                       "endpoint": classify(row, plan["anchors"][row["anchor_index"]]["task_dir"])})
    snapshots = [{"snapshot": f"B_{r}", "scheduled": len(plan["anchors"]) * plan["anchor_repeats"],
                  "categories": dict(Counter(p["endpoint"]["category"] for p in probes if p["snapshot"] == r))}
                 for r in range(plan["rounds"] + 1)]
    result = {"scope": "single design, human-selected mechanism, deferred unseen-variant probes",
              "scheduled_generation": len(plan["slots"]), "admitted": sum(r["admission"] == "admitted" for r in online),
              "distinct_admitted": len({r["normalized_identity"] for r in online if "normalized_identity" in r}),
              "online_and_frozen_reference": online, "reserved_variants_by_snapshot": snapshots, "probes": probes,
              "confirmation": "not scheduled; single-sample failures require separate reviewed confirmation",
              "limits": "Two reserved variants and two samples each per snapshot are diagnostic, not evidence of mastery or transfer."}
    save_new(run / "report.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "acceptance", "report"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--materials", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        if args.history is None:
            parser.error("prepare needs --history")
        prepare(args.out, args.history)
        print("PASS: frozen public contract, qualified development seed, two reserved variants; no model calls")
    elif args.action == "acceptance":
        if args.materials is None:
            parser.error("acceptance needs --materials")
        result = acceptance(loop.read(args.materials), args.out, TokenCounter())
        print(json.dumps(result))
    else:
        result = report(args.out)
        print(json.dumps({k: result[k] for k in ("scheduled_generation", "admitted", "distinct_admitted")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
