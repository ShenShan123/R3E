"""Measurement side of the repair-feedback protocol (r3e/loop/RED_REPAIR_FEEDBACK_PROTOCOL.md).

- ``build_packet`` / ``build_bank``: allowlisted PUBLIC feedback from feedback-arm trials whose
  one Blue answer passes the full public suite on replay. Never reads evaluator material.
- ``append_event`` / ``record_delivery``: append-only run journal. Each Red request's exact
  bank bytes, hashes and request hash are persisted before the request, after the round barrier.
- ``classify_trials``: broad functional endpoints for one-attempt encounters (screen and
  confirmation), separating a valid no-op from a missing answer, with masked review items.
- ``confirmation_manifest`` / ``run_confirmation``: separately approved fresh encounters for
  BOTH members of each eligible round-2 pair, frozen before the first call.

No model calls here except through ``run_confirmation``'s explicitly supplied client.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from experiments.loop_probes import red907_measure as measure
from r3e.loop import repo_blue as rb
from r3e.loop.blue_provider import apply_text_edits
from r3e.protocol.hashing import canonical_json, hash_payload

PACKET_VERSION = "repair_feedback_packet_v1"
RELEASE_LABEL = "public_pass_single_sample_hidden_unknown"
PACKET_TOKENS, BANK_PACKETS = 4096, 2
CONFIRM_ENCOUNTERS = 3
FUNCTIONAL = ("public_functional_failure", "valid_no_op", "public_pass_hidden_fail")
DECISION = "FUNCTIONAL_FAILURE"  # masked reviewer verdict: legal, spec-grounded functional failure


def _one_attempt(blue_dir: Path, encounters: int) -> dict[str, Any]:
    config = json.loads((blue_dir / "blue_config.json").read_text())
    runner = config["runner"]
    if (config["encounters"] != encounters or runner["budget_k"] != 1 or runner["max_infra_retries"] != 0
            or not config.get("direct_only")):
        raise measure.MeasurementRefused("not a one-attempt, direct, no-retry Blue record")
    bound = config.get("experience_delivery_hash")  # continuous mode: every encounter used this delivery
    if any(e["result"].get("experience", {}).get("delivery_hash") != bound for e in _encounters(blue_dir)) and bound:
        raise measure.MeasurementRefused("an encounter used a different experience delivery than recorded")
    return config


def _encounters(blue_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in (blue_dir / "encounters.jsonl").read_text().splitlines()]


def _archive(blue_dir: Path) -> dict[str, dict[str, Any]]:
    return {r["candidate_hash"]: r for r in (json.loads(f.read_text()) for f in (blue_dir / "archive").glob("*.json"))}


# ----------------------------------------------------------------------- feedback packets


def build_packet(blue_dir: Path, task_dir: Path, *, mutation_edits: list[dict[str, str]], source: dict[str, Any],
                 counter) -> dict[str, Any]:
    """One feedback-arm round-1 trial -> {"packet", "tokens"} or {"omitted", "source"}.

    Releasable only if the single materialized answer passes the full public suite on replay.
    Contents: Red's actual mutation edits, Blue's applied patch, public suite rows and the public
    evidence with Blue's own formatter and bound. No hidden material, Blue description or reasoning.
    """
    if source.get("arm") != "feedback" or source.get("round") != 1:
        raise ValueError("only feedback-arm round-1 trials can supply feedback")
    config = _one_attempt(blue_dir, 1)
    exported = measure._check_export(task_dir)
    task = rb.RepoTask.load(task_dir)
    (rel,) = task.task["editable"]
    mutant = (task.repo / rel).read_text()
    if apply_text_edits((task_dir / "oracle" / "clean" / rel).read_text(), mutation_edits) != mutant:
        raise measure.MeasurementRefused("mutation edits do not reproduce the exported mutant")
    (enc,) = _encounters(blue_dir)
    res = enc["result"]
    endpoint = res["final_candidate_hash"]
    if res["outcome"] != "fixed" or endpoint is None:
        return {"omitted": "not_public_pass", "source": source}
    record = _archive(blue_dir)[endpoint]
    replay = rb.execute(task, record["files"])
    if replay.tier != "visible_pass":
        return {"omitted": "public_replay_disagrees", "source": source}
    packet = {"version": PACKET_VERSION, "label": RELEASE_LABEL, "source": source,
              "mutation": {"file": rel, "edits": mutation_edits, "mutant_hash": hash_payload(mutant)},
              "blue_patch": record["patch"], "candidate_hash": endpoint,
              "public_rows": {k: v["result"] for k, v in replay.evidence["suites"].items()},
              "public_evidence": json.dumps(replay.evidence)[: config["runner"]["max_evidence_chars"]],
              "provenance": {"encounter_record": hash_payload(enc), "archive_record": hash_payload(record),
                             "export": hash_payload(exported)}}
    tokens = counter.count(canonical_json(packet))
    if tokens > PACKET_TOKENS:  # whole-packet omission; never cut hunks or summarize
        return {"omitted": "packet_over_token_limit", "tokens": tokens, "source": source}
    return {"packet": packet, "tokens": tokens}


def build_bank(built: list[dict[str, Any]], carrier_order: list[str]) -> dict[str, Any]:
    """Deterministic bank: frozen carrier order, at most one trial per carrier and two packets."""
    built = sorted(built, key=lambda b: carrier_order.index(b["source"]["carrier_id"]))
    carriers = [b["source"]["carrier_id"] for b in built]
    if len(set(carriers)) != len(carriers) or len(built) > BANK_PACKETS:
        raise ValueError("at most one round-1 feedback trial per carrier, two in total")
    return {"version": PACKET_VERSION, "packets": [b["packet"] for b in built if "packet" in b],
            "omissions": [{k: v for k, v in b.items() if k != "packet"} for b in built if "packet" not in b]}


EMPTY_BANK = {"version": PACKET_VERSION, "packets": [], "omissions": []}


# ----------------------------------------------------------------------- delivery journal


def _events(journal: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in journal.read_text().splitlines()] if journal.exists() else []


def append_event(journal: Path, event: dict[str, Any]) -> int:
    """Append-only journal with sequence numbers (e.g. trial_complete, round_barrier)."""
    seq = len(_events(journal))
    with journal.open("a") as f:
        f.write(json.dumps({"seq": seq, **event}) + "\n")
        f.flush()
    return seq


def record_delivery(journal: Path, *, round_index: int, arm: str, carrier_id: str,
                    messages: list[dict[str, str]], bank: dict[str, Any]) -> dict[str, Any]:
    """Persist exactly what one Red request receives, BEFORE the request is sent.

    Refuses feedback in round 1, in the independent arm, before the previous round's barrier,
    or from any trial that completed after that barrier.
    """
    events = _events(journal)
    sources = [p["source"] for p in bank["packets"]] + [o["source"] for o in bank["omissions"]]
    if (round_index == 1 or arm == "independent") and (bank["packets"] or bank["omissions"]):
        raise ValueError("round-1 and independent requests receive the explicit empty bank")
    if sources:
        barrier = next((e["seq"] for e in events if e["event"] == "round_barrier"
                        and e["round"] == round_index - 1), None)
        if barrier is None:
            raise ValueError("feedback before the previous round's barrier")
        for src in sources:
            done = [e["seq"] for e in events if e["event"] == "trial_complete" and e["slot_id"] == src["slot_id"]]
            if (src["arm"] != "feedback" or src["round"] >= round_index or not done or done[-1] > barrier):
                raise ValueError(f"feedback source {src['slot_id']} is not a completed earlier feedback trial")
    text = canonical_json(bank)
    row = {"event": "feedback_delivery", "round": round_index, "arm": arm, "carrier_id": carrier_id,
           "request_hash": hash_payload(messages), "bank_bytes": text,
           "bank_sha256": hashlib.sha256(text.encode()).hexdigest(),
           "packet_hashes": [hash_payload(p) for p in bank["packets"]],
           "source_record_hashes": [p["provenance"] for p in bank["packets"]], "omissions": bank["omissions"]}
    row["seq"] = append_event(journal, row)
    return row


# ----------------------------------------------------------------------- broad functional endpoints


def _endpoint(task: rb.RepoTask, res: dict[str, Any], archive: dict, evaluator: dict, eval_dir: Path) -> dict[str, Any]:
    outcome = res["outcome"]
    if outcome in ("inconclusive", "inconclusive_infrastructure"):
        return {"category": "infrastructure"}
    if outcome in ("request_too_large", "rtl_context_limit"):
        return {"category": "context_limit"}
    attempts = [a for a in res["attempts"] if not a.get("infra_failure")]
    last = attempts[-1] if attempts else {}
    if last.get("tier") in ("no_answer", "invalid_edit"):  # output cutoff; malformed or inapplicable edits
        return {"category": last["tier"], "feedback": last.get("feedback")}
    endpoint = res["final_candidate_hash"]
    files = archive[endpoint]["files"] if endpoint else {}
    if endpoint is None and (last.get("changed_files") != [] or last.get("tier") is None):
        return {"category": "no_candidate"}
    public = measure._public(task, files)
    row: dict[str, Any] = {"endpoint_candidate": endpoint, "endpoint_public": public,
                           "patch_vs_task": archive[endpoint]["patch"] if endpoint else ""}
    if public["tier"] != last.get("tier"):
        return {**row, "category": "replay_mismatch"}
    suites = public["suites"]
    checker = {k for k, v in suites.items() if v["result"] == "visible_fail" and v["checker_violation"]}
    if public["tier"] == "compile_fail":
        return {**row, "category": "compile_fail"}
    if checker:
        return {**row, "category": "valid_no_op" if endpoint is None else "public_functional_failure",
                "original_defect_persists": "W_bug" in checker, "new_regression": "W_preserve" in checker}
    if public["tier"] != "visible_pass":  # timeout or failure without a checker marker
        return {**row, "category": "public_" + public["tier"] + "_without_checker"}
    row["endpoint_evaluator"] = measure._evaluate(task, files, evaluator, eval_dir)
    verdict = row["endpoint_evaluator"]
    hidden = ("public_pass_hidden_pass" if verdict["result"] == "pass" else
              "public_pass_hidden_fail" if verdict["result"] == "fail" and verdict.get("failures") else
              "public_pass_hidden_inconclusive")
    return {**row, "category": hidden}


def classify_trials(blue_dir: Path, task_dir: Path, evaluator_freeze: dict[str, Any], carriers_root: Path, *,
                    encounters: int = 1) -> dict[str, Any]:
    """Offline, after collection: endpoints, masked review items and (for 3 encounters) confirmation."""
    if encounters not in (1, CONFIRM_ENCOUNTERS):
        raise ValueError("one screening encounter or three confirmation encounters")
    changed = [r for r, h in evaluator_freeze["files"].items() if hash_payload((carriers_root / r).read_text()) != h]
    if changed:
        raise measure.MeasurementRefused(f"evaluator changed since its freeze: {changed}")
    eval_dir = carriers_root / Path(next(iter(evaluator_freeze["files"]))).parent
    _one_attempt(blue_dir, encounters)
    measure._check_export(task_dir)
    task = rb.RepoTask.load(task_dir)
    archive = _archive(blue_dir)
    rows = [{"encounter": e["encounter"], **_endpoint(task, e["result"], archive, evaluator_freeze, eval_dir)}
            for e in _encounters(blue_dir)]
    if len(rows) != encounters:
        raise measure.MeasurementRefused("encounter count differs from the frozen protocol")
    items = [{"item": hash_payload([task.task["task_id"], r["encounter"], r.get("endpoint_candidate")])[7:23],
              "task": task.task["task_id"], "encounter": r["encounter"], "category": r["category"],
              "patch_vs_task": r["patch_vs_task"], "public": r["endpoint_public"],
              "evaluator": r.get("endpoint_evaluator")}  # masked: no arm, no Red text, no feedback
             for r in rows if r["category"] in FUNCTIONAL]
    decisions_path = blue_dir / "review_decisions.json"
    decisions = json.loads(decisions_path.read_text()) if decisions_path.exists() else {}
    for item in items:
        row = rows[item["encounter"]]
        row["functional_failure_candidate"] = True
        row["review"] = decisions.get(item["item"], {"decision": "PENDING"})
    reviewed = [r for r in rows if r.get("review", {}).get("decision") == DECISION]
    summary = {"task_id": task.task["task_id"], "encounters": encounters,
               "categories": {c: sum(r["category"] == c for r in rows) for c in sorted({r["category"] for r in rows})},
               "functional_failure_candidates": len(items),
               "pending_review": sum(r.get("review", {}).get("decision") == "PENDING" for r in rows),
               "reviewed_functional_failures": len(reviewed),
               "original_defect_persists": sum(bool(r.get("original_defect_persists")) for r in rows),
               "new_regression": sum(bool(r.get("new_regression")) for r in rows),
               "mechanism_907_exploratory": sum(bool(r["review"].get("mechanism_907")) for r in reviewed)}
    if encounters == CONFIRM_ENCOUNTERS:
        kinds = {"hidden" if r["category"] == "public_pass_hidden_fail" else "public" for r in reviewed}
        summary["confirmed_within_carrier_difficulty"] = len(reviewed) >= 2  # operational rule, not a probability
        summary["mixed_confirmation"] = len(kinds) > 1
    (blue_dir / "review_items.json").write_text(json.dumps(items, indent=1))
    out = {"summary": summary, "encounters": rows}
    (blue_dir / "feedback_classification.json").write_text(json.dumps(out, indent=1))
    return out


# ----------------------------------------------------------------------- separately approved confirmation


def confirmation_manifest(round2_pairs: list[dict[str, Any]], approved_calls: int) -> dict[str, Any]:
    """Eligible pairs in frozen order: BOTH members of every round-2 pair where either arm has a
    reviewed functional failure. Refuses, rather than selects, if the approval cannot cover all."""
    if any(p["round"] != 2 or len(p["members"]) != 2 for p in round2_pairs):
        raise ValueError("confirmation inspects complete round-2 pairs only")
    eligible = [p for p in round2_pairs if any(m["reviewed_functional_failures"] > 0 for m in p["members"])]
    calls = 2 * CONFIRM_ENCOUNTERS * len(eligible)
    if calls > approved_calls:
        raise ValueError(f"approved {approved_calls} calls cannot cover the eligible paired manifest ({calls})")
    return {"protocol": "repair_feedback_confirmation_v1", "encounters_per_member": CONFIRM_ENCOUNTERS,
            "blue_attempts": 1, "provider_retries": 0, "calls": calls,
            "pairs": [{"pair_id": p["pair_id"], "carrier_id": p["carrier_id"],
                       "members": [{"arm": m["arm"], "task_dir": str(m["task_dir"]), "seed": m["seed"]}
                                   for m in p["members"]]} for p in eligible]}


def run_confirmation(manifest: dict[str, Any], out: Path, *, client, counter) -> Path:
    """Freeze the manifest, then run fresh memory-free encounters for every member in order."""
    if client.max_calls - client.total_calls < manifest["calls"]:
        raise measure.MeasurementRefused("the whole eligible manifest must be reserved before the first call")
    out.mkdir(parents=True, exist_ok=False)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    cfg = rb.RepoBlueConfig(budget_k=1, max_infra_retries=0)
    for pair in manifest["pairs"]:
        for m in pair["members"]:
            measure.run_blue(Path(m["task_dir"]), out / pair["pair_id"] / m["arm"], client=client, counter=counter,
                             cfg=cfg, seed=m["seed"], encounters=CONFIRM_ENCOUNTERS, direct_only=True,
                             endpoint_protocol="confirmation")
    return out
