"""Round-boundary Red/Blue adaptation; explicit client injection, no paid CLI.

Collection never executes hidden suites. Reference and deferred anchor trials
are measurement-only. Fresh directories only; no implicit resume or refill.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import difflib
import json
import os
from pathlib import Path
import shutil

from experiments.loop_probes import red907, red907_measure as measure, red907_schedule as frozen
from experiments.loop_probes import witness_matrix as wm
from r3e.loop import continuous_state as state, red_continuous as red, repo_blue as rb
from r3e.loop.red_regression import save_new
from r3e.knowledge.verilog_utils import safe_tokenize
from r3e.protocol.hashing import atomic_write_json, hash_payload

VERSION = "continuous_loop_v4"
ROOT = Path(__file__).resolve().parents[2]
CONFIG = rb.RepoBlueConfig(budget_k=1, max_infra_retries=0)


def read(path):
    return json.loads(Path(path).read_text())


def append(path, row):
    with Path(path).open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def code_identity():
    names = set(frozen.code_identity()) | {
        str(Path(__file__).relative_to(ROOT)), "experiments/loop_probes/continuous_measure.py",
        "experiments/loop_probes/continuous_audit.py",
        "experiments/loop_probes/sequence_curriculum.py", "r3e/loop/RED_SEQUENCE_CURRICULUM.md",
        "experiments/loop_probes/continuous_launch.py",
        "experiments/loop_probes/repair_feedback.py", "r3e/loop/RED_CONTINUOUS_LOOP.md",
        "r3e/loop/CONTINUOUS_BLUE_INTERFACE.md"}
    names |= {str(p.relative_to(ROOT)) for p in (ROOT / "r3e/loop/assets/prob003").glob("*") if p.is_file()}
    return {name: frozen._sha(ROOT / name) for name in sorted(names)}


def task_identity(task_dir):
    measure._check_export(Path(task_dir))
    return rb.public_task_hash(rb.RepoTask.load(Path(task_dir)))


def mutant_identity(public, rtl):
    # Task IDs differ between exported anchors and generated mutants; compare
    # actual code/configuration instead. Ignore formatting/comments for exclusion.
    return hash_payload({"carrier": public["carrier_id"], "manifest": public["manifest_hash"],
                         "dependencies": public["dependencies"],
                         "tokens": [t.value for t in safe_tokenize(rtl)]})


def schedule(rounds, slots_per_round, carrier_count, anchor_count, seed, *, carrier_mode="fixed", fixed_carrier_index=0,
             anchor_repeats=1):
    if type(anchor_repeats) is not int or anchor_repeats < 1:
        raise ValueError("positive anchor repeat count required")
    if carrier_mode not in {"fixed", "rotate"} or type(fixed_carrier_index) is not int or not 0 <= fixed_carrier_index < carrier_count:
        raise ValueError("invalid carrier scheduling policy")
    # A new policy has its own seed namespace; labels are not provider determinism.
    base = int(hash_payload([VERSION, seed, carrier_mode, fixed_carrier_index, anchor_repeats])[7:19], 16)
    slots = [{"round": r, "slot_id": f"r{r}_s{s}", "carrier_index": (fixed_carrier_index if carrier_mode == "fixed"
                         else ((r - 1) * slots_per_round + s) % carrier_count),
              "red_seed": base + ((r - 1) * slots_per_round + s) * 3,
              "online_seed": base + ((r - 1) * slots_per_round + s) * 3 + 1,
              "reference_seed": base + ((r - 1) * slots_per_round + s) * 3 + 2}
             for r in range(1, rounds + 1) for s in range(slots_per_round)]
    probes = [{"snapshot": r, "anchor_index": a, "repeat": k,
               "seed": base + 3 * len(slots) + (r * anchor_count + a) * anchor_repeats + k}
              for r in range(rounds + 1) for a in range(anchor_count) for k in range(anchor_repeats)]
    return slots, probes


def blue_identity(plan):
    return {"runner": plan["blue_config"], "client": plan["client_config"], "tokenizer": plan["tokenizer"],
            "runner_code": plan["code"]["r3e/loop/repo_blue.py"]}


def make_plan(carriers, anchors, *, counter, client_config, rounds=3, slots_per_round=1, seed=700,
              carrier_mode="fixed", fixed_carrier_index=0, anchor_repeats=1, mechanism_focus=None,
              red_output_tokens=65536):
    """anchors: [{task_dir, carrier_index}]. Freeze all material before collection."""
    if type(rounds) is not int or type(slots_per_round) is not int or min(rounds, slots_per_round) < 1:
        raise ValueError("positive fixed round/slot counts required")
    if not carriers or not anchors or type(seed) is not int or seed < 0:
        raise ValueError("carriers, anchors and nonnegative seed required")
    if client_config["maximum_output_tokens"] < 65536:
        raise ValueError("client output authority below Red ceiling")
    if type(red_output_tokens) is not int or not 0 < red_output_tokens <= 65536:
        raise ValueError("invalid Red output ceiling")
    items = [{k: str(Path(v).resolve()) for k, v in c.items()} for c in carriers]
    if type(fixed_carrier_index) is not int or not 0 <= fixed_carrier_index < len(items):
        raise ValueError("invalid fixed carrier index")
    ids = [frozen._carrier_identity(c) for c in items]
    if mechanism_focus is not None:
        if carrier_mode != "fixed":
            raise ValueError("mechanism focus requires a fixed carrier")
        c = items[fixed_carrier_index]
        red.validate_focus(mechanism_focus, red907.public_material(Path(c["package"]), read(c["suite_freeze"])))
    if len({i["carrier_id"] for i in ids}) != len(ids):
        raise ValueError("duplicate carrier IDs")
    qualified = []
    for c in items:
        q = wm.qualify(Path(c["package"]), read(c["suite_freeze"]))
        if not q["qualified"]:
            raise ValueError("public carrier qualification failed")
        qualified.append({k: q[k] for k in ("package_files", "tool")})
    anchor_rows = []
    for a in anchors:
        idx = a["carrier_index"]
        c = items[idx]
        public = red907.public_material(Path(c["package"]), read(c["suite_freeze"]))
        task_dir = Path(a["task_dir"]).resolve()
        task = rb.RepoTask.load(task_dir)
        setup = rb.setup(task, counter, CONFIG)
        if (setup.route != "direct" or {k: v["result"] for k, v in setup.baseline.evidence.get("suites", {}).items()}
                != {"W_bug": "visible_fail", "W_preserve": "visible_pass"}):
            raise ValueError("anchor must be a direct, public-qualified defect")
        anchor_rows.append({"task_dir": str(task_dir), "carrier_index": idx,
                            "task_hash": task_identity(task_dir),
                            "mutant_identity": mutant_identity(public, (task.repo / public["editable_file"]).read_text())})
    if len({a["task_hash"] for a in anchor_rows}) != len(anchor_rows):
        raise ValueError("duplicate anchors")
    slots, probes = schedule(rounds, slots_per_round, len(items), len(anchors), seed,
                             carrier_mode=carrier_mode, fixed_carrier_index=fixed_carrier_index, anchor_repeats=anchor_repeats)
    return {"version": VERSION, "rounds": rounds, "slots_per_round": slots_per_round, "seed": seed,
            "carrier_mode": carrier_mode, "fixed_carrier_index": fixed_carrier_index,
            "anchor_repeats": anchor_repeats, "mechanism_focus": deepcopy(mechanism_focus),
            "path_policy": "absolute_paths_preserve_original_locations",
            "carriers": items, "identities": ids, "qualifications": qualified, "anchors": anchor_rows,
            "slots": slots, "anchor_probes": probes, "blue_config": asdict(CONFIG), "red_output_tokens": red_output_tokens,
            "client_config": client_config, "tokenizer": counter.identity, "runtime": frozen.runtime_identity(),
            "learning": state.LEARNING, "retrieval": state.RETRIEVAL, "red_bank_tokens": 8192,
            "max_calls": 3 * len(slots) + len(probes), "code": code_identity(),
            "authorization": "offline plan only; no paid launcher or monetary approval"}


def validate_plan(plan):
    if (plan["version"] != VERSION or plan["code"] != code_identity()
            or plan["runtime"] != frozen.runtime_identity() or plan["blue_config"] != asdict(CONFIG)
            or plan["learning"] != state.LEARNING or plan["retrieval"] != state.RETRIEVAL
            or type(plan["red_output_tokens"]) is not int or not 0 < plan["red_output_tokens"] <= 65536
            or plan["red_bank_tokens"] != 8192
            or plan["path_policy"] != "absolute_paths_preserve_original_locations"):
        raise ValueError("code/runtime/policy changed; create a fresh plan")
    if [frozen._carrier_identity(c) for c in plan["carriers"]] != plan["identities"]:
        raise ValueError("frozen carrier/evaluator material changed")
    if plan["mechanism_focus"] is not None:
        if plan["carrier_mode"] != "fixed":
            raise ValueError("mechanism focus requires a fixed carrier")
        c = plan["carriers"][plan["fixed_carrier_index"]]
        red.validate_focus(plan["mechanism_focus"], red907.public_material(Path(c["package"]), read(c["suite_freeze"])))
    for c, q in zip(plan["carriers"], plan["qualifications"], strict=True):
        if wm.package_files(Path(c["package"])) != q["package_files"]:
            raise ValueError("qualified package changed")
    for a in plan["anchors"]:
        if task_identity(a["task_dir"]) != a["task_hash"]:
            raise ValueError("anchor changed")
        item = plan["carriers"][a["carrier_index"]]
        public = red907.public_material(Path(item["package"]), read(item["suite_freeze"]))
        task = rb.RepoTask.load(Path(a["task_dir"]))
        if mutant_identity(public, (task.repo / public["editable_file"]).read_text()) != a["mutant_identity"]:
            raise ValueError("anchor exclusion identity changed")
    n = plan["rounds"] * plan["slots_per_round"]
    slots, probes = schedule(plan["rounds"], plan["slots_per_round"], len(plan["carriers"]),
                             len(plan["anchors"]), plan["seed"], carrier_mode=plan["carrier_mode"],
                             fixed_carrier_index=plan["fixed_carrier_index"], anchor_repeats=plan["anchor_repeats"])
    if (plan["slots"] != slots or plan["anchor_probes"] != probes
            or plan["max_calls"] != 3 * n + len(probes)):
        raise ValueError("schedule/call cap mismatch")


def authenticate(snap, run, blue_config, parent):
    state.validate_snapshot(snap, blue_config, parent)
    for entry in snap["entries"]:
        source = entry["body"]["source"]
        record = read(run / "sources" / (source["encounter_hash"][7:] + ".json"))
        task = rb.RepoTask.load(Path(record["task_dir"]))
        archive = read(record["archive_path"])
        encounter_rows = [json.loads(line) for line in
                          (Path(record["archive_path"]).parent.parent / "encounters.jsonl").read_text().splitlines()]
        if (source != record["source"] or hash_payload(record["encounter"]) != source["encounter_hash"]
                or encounter_rows != [record["encounter"]]
                or hash_payload(archive) != source["archive_hash"]
                or hash_payload(record["replay"]) != source["public_replay_hash"]
                or task_identity(record["task_dir"]) != source["task_hash"]
                or state.episode_entry(source, task.spec, archive, record["public_rows"]) != entry):
            raise ValueError("experience source provenance changed")


def online_evidence(task_dir, blue_dir, *, run, slot, proposal, incoming, carrier_id):
    """Allowlist real online endpoint evidence; no reference or evaluator inputs."""
    task = rb.RepoTask.load(task_dir)
    enc, = [json.loads(l) for l in (blue_dir / "encounters.jsonl").read_text().splitlines()]
    result = enc["result"]
    if result["experience"]["snapshot_hash"] != hash_payload(incoming):
        raise ValueError("online result is bound to a different snapshot")
    attempts = [a for a in result["attempts"] if not a.get("infra_failure")]
    last = attempts[-1] if attempts else {}
    endpoint = result["final_candidate_hash"]
    archives = [(p, read(p)) for p in (blue_dir / "archive").glob("*.json")]
    matches = [(p, a) for p, a in archives if a["task_id"] == task.task["task_id"] and a["candidate_hash"] == endpoint]
    noop = endpoint is None and last.get("changed_files") == [] and last.get("tier") == "visible_fail"
    if (not noop and len(matches) != 1) or last.get("tier") in {"invalid_edit", "no_answer"}:
        return None, None, {"reason": "no_usable_materialized_endpoint", "outcome": result["outcome"]}
    archive_path, archive = (None, {"files": {}, "baseline_hashes": {}, "patch": ""}) if noop else matches[0]
    if (not noop and hash_payload(archive["files"]) != endpoint) or any(
            hash_payload((task.repo / p).read_text()) != archive["baseline_hashes"][p] for p in archive["files"]):
        raise ValueError("online archive identity mismatch")
    expected_patch = "".join("".join(difflib.unified_diff((task.repo / p).read_text().splitlines(True),
                                text.splitlines(True), "a/" + p, "b/" + p)) for p, text in sorted(archive["files"].items()))
    if expected_patch != archive["patch"]:
        raise ValueError("archive patch does not reconstruct candidate")
    replay = rb.execute(task, archive["files"])
    if replay.tier != last.get("tier"):
        return None, None, {"reason": "public_replay_mismatch"}
    rows = {k: v["result"] for k, v in replay.evidence.get("suites", {}).items()}
    passed = replay.tier == "visible_pass"
    failed = (replay.tier == "visible_fail" and any(v.get("fail_marker_found") for v in replay.evidence["suites"].values()))
    if not (passed or failed):
        return None, None, {"reason": "nonfunctional_endpoint", "tier": replay.tier}
    source = {"run_id": run.name, "round": slot["round"], "slot_id": slot["slot_id"],
              "task_hash": rb.public_task_hash(task), "carrier_id": carrier_id,
              "mutant_hash": proposal["buggy_rtl_hash"], "candidate_hash": endpoint,
              "encounter_hash": hash_payload(enc), "archive_hash": hash_payload(archive),
              "public_replay_hash": hash_payload(asdict(replay))}
    obs = {"round": slot["round"], "slot_id": slot["slot_id"], "carrier_id": carrier_id,
           "target_snapshot_hash": hash_payload(incoming), "mutant_hash": source["mutant_hash"],
           "mutation_edits": proposal["mutation_edits"], "blue_patch": archive["patch"], "public_rows": rows,
           "public_evidence": json.dumps(replay.evidence)[:CONFIG.max_evidence_chars],
           "label": (rb.EXPERIENCE_LABEL if passed else "valid_no_op_single_sample_unconfirmed" if noop
                     else "public_functional_failure_single_sample_unconfirmed"),
           "source_hash": source["encounter_hash"]}
    save_new(run / "sources" / (source["encounter_hash"][7:] + ".json"), {
        "source": source, "encounter": enc, "archive_path": str(archive_path) if archive_path else None, "task_dir": str(task_dir),
        "replay": asdict(replay), "public_rows": rows, "mutation_edits": proposal["mutation_edits"]})
    return obs, state.episode_entry(source, task.spec, archive, rows) if passed else None, {"reason": obs["label"]}


def collect(plan, out, *, client, counter):
    validate_plan(plan)
    if (client.total_calls or client.max_calls != plan["max_calls"]
            or client.config.public_identity != plan["client_config"] or counter.identity != plan["tokenizer"]):
        raise ValueError("fresh client with exact frozen cap/configuration/tokenizer required")
    plan = deepcopy(plan)
    plan_hash = hash_payload(plan)
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    save_new(out / "plan.json", plan)
    save_new(out / "plan_hash.json", {"hash": plan_hash})
    for name, digest in plan["code"].items():
        dest = out / "code_snapshot" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, dest)
        if frozen._sha(dest) != digest:
            raise ValueError("code snapshot changed while copying")
    for name in ("calls", "slots", "probes", "events"):
        (out / f"{name}.jsonl").touch()
    client.output_caps["blue_inference"] = CONFIG.output_reserve
    label, commits, seen, completed_slots, completed_probes = {}, [], set(), set(), set()
    prior_callback = client.on_call

    def log_call(row):
        append(out / "calls.jsonl", {**row, **label})
        if prior_callback:
            prior_callback(row)

    client.on_call = log_call
    identity = blue_identity(plan)
    b0 = state.snapshot(0, identity)

    def commit(blue, observations, decisions):
        r = blue["committed_round"]
        record = {"round": r, "parent_commit_hash": hash_payload(commits[-1]) if commits else None,
                  "blue": blue, "red": {"round": r, "observations": deepcopy(observations)},
                  "update_decisions": decisions}
        atomic_write_json(out / "rounds" / str(r) / "commit.json", record)
        commits.append(record)
        append(out / "events.jsonl", {"event": "round_commit", "round": r, "commit_hash": hash_payload(record)})

    def check():
        validate_plan(plan)
        if hash_payload(read(out / "plan.json")) != plan_hash:
            raise ValueError("stored plan changed")
        for i, c in enumerate(commits):
            if read(out / "rounds" / str(i) / "commit.json") != c:
                raise ValueError("committed state changed")
            authenticate(c["blue"], out, identity, commits[i - 1]["blue"] if i else None)

    def blue_trial(task_dir, directory, snap, seed, role, slot_id):
        check()
        label.clear()
        label.update(role=role, slot_id=slot_id, snapshot_id=snap["snapshot_id"])
        task = rb.RepoTask.load(task_dir)
        envelope = state.delivery(snap, task, counter)
        directory.mkdir(parents=True, exist_ok=False)
        save_new(directory / "delivery.json", envelope)
        try:
            results = measure.run_blue(task_dir, directory / "blue", client=client, counter=counter,
                cfg=CONFIG, seed=seed, encounters=1, direct_only=True, endpoint_protocol="screening",
                experience=envelope, experience_journal=directory / "experience.jsonl",
                retrieval_policy_hash=hash_payload(state.RETRIEVAL))
            return {"status": results[0]["outcome"], "directory": str(directory),
                    "task_hash": envelope["task_hash"], "delivery_hash": hash_payload(envelope),
                    "snapshot_hash": hash_payload(snap), "online_seed": seed * 10}
        except measure.DirectContextLimit as exc:
            return {"status": "context_limit", "reason": str(exc), "directory": str(directory)}
        except measure.MeasurementRefused as exc:
            if not str(exc).startswith("baseline no longer shows the admission row:"):
                raise
            return {"status": "public_baseline_inconclusive", "reason": str(exc), "directory": str(directory)}

    status = "aborted"
    try:
        commit(b0, [], [])
        for r in range(1, plan["rounds"] + 1):
            check()
            # Reserve this and all remaining online work plus every deferred anchor.
            remaining = 3 * sum(s["round"] >= r for s in plan["slots"]) + len(plan["anchor_probes"])
            if client.max_calls - client.total_calls < remaining:
                raise ValueError("insufficient reservation for remaining schedule and probes")
            incoming = commits[-1]["blue"]
            observations = deepcopy(commits[-1]["red"]["observations"])
            added, candidates = [], []
            for slot in (s for s in plan["slots"] if s["round"] == r):
                check()
                item = plan["carriers"][slot["carrier_index"]]
                public = red907.public_material(Path(item["package"]), read(item["suite_freeze"]))
                directory = out / "rounds" / str(r) / slot["slot_id"]
                label.clear()
                label.update(role="red", slot_id=slot["slot_id"], snapshot_id=incoming["snapshot_id"])
                before = client.total_calls
                target, target_omissions = state.target_view(incoming, public["specification"], identity, counter)
                proposal = red.propose(public, observations, round_index=r, snapshot_hash=hash_payload(incoming),
                    red_state_hash=hash_payload(commits[-1]["red"]), seed=slot["red_seed"], directory=directory / "red",
                    client=client, counter=counter, output_tokens=plan["red_output_tokens"],
                    target_blue=target, target_omissions=target_omissions, mechanism_focus=plan["mechanism_focus"])
                excluded = {a["mutant_identity"]: "reserved_task_collision" for a in plan["anchors"]}
                if plan["mechanism_focus"] is not None:
                    from r3e.loop.blue_provider import apply_text_edits
                    seed_rtl = apply_text_edits(public["clean_rtl"], plan["mechanism_focus"]["seed_edits"])
                    excluded[mutant_identity(public, seed_rtl)] = "historical_seed_repeat"
                fingerprint = mutant_identity(public, proposal["buggy_rtl"]) if proposal["status"] == "proposal" else None
                if fingerprint in excluded:
                    admission = {"status": "rejected", "reason": excluded[fingerprint]}
                    save_new(directory / "admission" / "proposal.json", proposal)
                    save_new(directory / "admission" / "admission.json", admission)
                else:
                    admission = red907.check_proposal(Path(item["package"]), read(item["suite_freeze"]),
                                                      proposal, directory / "admission", seen=seen)
                row = {**slot, "carrier_id": public["carrier_id"], "incoming_snapshot_hash": hash_payload(incoming),
                       "generation_status": proposal["status"], "admission": admission["status"],
                       "reason": admission.get("reason"), "trials": {}}
                if admission["status"] == "admitted":
                    task_dir = directory / "admission" / "blue_task"
                    # Order is fixed from slot ordinal; both roles run even if one has an operational failure.
                    order = ("online", "reference") if (r + slot["carrier_index"]) % 2 else ("reference", "online")
                    for role in order:
                        row["trials"][role] = blue_trial(task_dir, directory / role,
                            incoming if role == "online" else b0, slot[role + "_seed"], role, slot["slot_id"])
                    if (directory / "online/blue/encounters.jsonl").exists():
                        obs, entry, decision = online_evidence(task_dir, directory / "online/blue", run=out,
                            slot=slot, proposal=proposal, incoming=incoming, carrier_id=public["carrier_id"])
                        row["learning_source"] = decision
                        if obs:
                            added.append(obs)
                        if entry:
                            candidates.append(entry)
                row["calls"] = client.total_calls - before
                append(out / "slots.jsonl", row)
                completed_slots.add(slot["slot_id"])
            blue, decisions = state.update(incoming, candidates, identity, counter)
            commit(blue, [*observations, *added], decisions)
        append(out / "events.jsonl", {"event": "online_collection_complete"})
        for probe in plan["anchor_probes"]:
            anchor = plan["anchors"][probe["anchor_index"]]
            name = f"b{probe['snapshot']}_a{probe['anchor_index']}"
            if plan["anchor_repeats"] > 1:
                name += f"_repeat{probe['repeat']}"
            result = blue_trial(Path(anchor["task_dir"]), out / "anchors" / name,
                                commits[probe["snapshot"]]["blue"], probe["seed"], "anchor", None)
            append(out / "probes.jsonl", {**probe, **result})
            completed_probes.add((probe["snapshot"], probe["anchor_index"], probe["repeat"]))
        status = "complete"
    finally:
        client.on_call = prior_callback
        summary = {"version": VERSION, "status": status, "plan_hash": plan_hash,
                   "committed_rounds": len(commits) - 1, "calls": client.total_calls,
                   "max_calls": plan["max_calls"], "scheduled_slots": len(plan["slots"]),
                   "completed_slots": len(completed_slots), "uncompleted_slot_ids":
                   [s["slot_id"] for s in plan["slots"] if s["slot_id"] not in completed_slots],
                   "scheduled_probes": len(plan["anchor_probes"]), "completed_probes": len(completed_probes),
                   "uncompleted_probes": [p for p in plan["anchor_probes"]
                                          if (p["snapshot"], p["anchor_index"], p["repeat"]) not in completed_probes],
                   "evaluator_executed": False, "confirmation_executed": False}
        atomic_write_json(out / "collection.json", summary)
    return summary
