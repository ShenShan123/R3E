"""Public repair episodes, immutable Blue snapshots and deterministic retrieval.

No provider or hidden-evaluator calls. The scheduler authenticates source records
and committed snapshot hashes before using these pure state transitions.
"""
from __future__ import annotations

from copy import deepcopy
import re

from . import repo_blue as rb
from r3e.protocol.hashing import canonical_json, hash_payload

LEARNING = {"version": "continuous_episode_learning_v1", "sources": "online_public_replay_pass",
            "extractor": "deterministic", "spec_chars": 1024, "dedup": "task_mutant_candidate",
            "hidden_filter": False, "qualification": "provisional_public_only"}
RETRIEVAL = {"version": "continuous_spec_jaccard_v1", "terms": "[A-Za-z_][A-Za-z_0-9]*",
             "lowercase": True, "tie": "entry_hash_ascending", "skip_zero": True,
             "entry_tokens": 2048, "wrapper_tokens": 4096, "max_entries": 2}


def terms(text):
    return sorted(set(re.findall(RETRIEVAL["terms"], text.lower())))


def visible_entry(entry):
    return {"entry_hash": entry["entry_hash"], "content": deepcopy(entry["body"]["content"])}


def episode_entry(source, spec, archive, public_rows):
    if set(public_rows) != {"W_bug", "W_preserve"} or set(public_rows.values()) != {"visible_pass"}:
        raise ValueError("only fully public-passing online repairs can teach Blue")
    body = {"version": "continuous_repair_episode_v1", "created_round": source["round"],
            "source": deepcopy(source), "retrieval_terms": terms(spec),
            "content": {"validation": rb.EXPERIENCE_LABEL, "specification_excerpt": spec[:1024],
                        "applied_patch": archive["patch"], "public_rows": dict(public_rows)}}
    return {"entry_hash": hash_payload(body), "body": body}


def validate_entry(entry):
    if (set(entry) != {"entry_hash", "body"} or entry["entry_hash"] != hash_payload(entry["body"])
            or set(entry["body"]) != {"version", "created_round", "source", "retrieval_terms", "content"}
            or entry["body"]["version"] != "continuous_repair_episode_v1"
            or entry["body"]["created_round"] != entry["body"]["source"]["round"]):
        raise ValueError("entry body or hash mismatch")
    content = entry["body"]["content"]
    if (set(content) != rb.CONTENT_FIELDS or content["validation"] != rb.EXPERIENCE_LABEL
            or set(content["public_rows"]) != {"W_bug", "W_preserve"}
            or set(content["public_rows"].values()) != {"visible_pass"}):
        raise ValueError("entry content mismatch")


def snapshot(round_index, blue_config, entries=(), parent=None):
    if (round_index == 0 and (parent is not None or entries)) or (
            round_index > 0 and (parent is None or parent["committed_round"] != round_index - 1)):
        raise ValueError("snapshot parent/round mismatch")
    for entry in entries:
        validate_entry(entry)
        if not 0 < entry["body"]["created_round"] <= round_index:
            raise ValueError("future entry in snapshot")
    state = {"blue_config_hash": hash_payload(blue_config), "learning_policy_hash": hash_payload(LEARNING),
             "retrieval_policy_hash": hash_payload(RETRIEVAL), "entries": deepcopy(list(entries))}
    return {"version": "continuous_blue_snapshot_v1", "snapshot_id": f"B_{round_index}",
            "parent_snapshot_hash": hash_payload(parent) if parent is not None else None,
            "committed_round": round_index, **state, "state_hash": hash_payload(state)}


def validate_snapshot(snap, blue_config, parent=None):
    expected = snapshot(snap["committed_round"], blue_config, snap["entries"], parent)
    if expected != snap:
        raise ValueError("snapshot state or policy hash mismatch")


def update(incoming, candidates, blue_config, counter):
    entries, decisions = deepcopy(incoming["entries"]), []
    key = lambda e: tuple(e["body"]["source"][k] for k in ("task_hash", "mutant_hash", "candidate_hash"))
    seen = {key(e) for e in entries}
    r = incoming["committed_round"] + 1
    for entry in candidates:
        validate_entry(entry)
        if entry["body"]["created_round"] != r:
            raise ValueError("update sources must belong to the completed round")
        reason = "accepted"
        if key(entry) in seen:
            reason = "duplicate_source"
        elif counter.count(canonical_json(visible_entry(entry))) > RETRIEVAL["entry_tokens"]:
            reason = "entry_too_large"
        else:
            entries.append(deepcopy(entry))
            seen.add(key(entry))
        decisions.append({"entry_hash": entry["entry_hash"], "reason": reason})
    return snapshot(r, blue_config, entries, incoming), decisions


def select_experience(snap, spec, counter):
    """Shared spec-only selection for Blue delivery and Red's incoming-state view."""
    wanted = set(terms(spec))
    payload = {"version": rb.EXPERIENCE_VERSION, "notice": rb.EXPERIENCE_NOTICE, "entries": []}
    ranked, omissions, selected = [], [], []
    for entry in snap["entries"]:
        validate_entry(entry)
        src = set(entry["body"]["retrieval_terms"])
        union = wanted | src
        score = len(wanted & src) / len(union) if union else 0
        ranked.append((-score, entry["entry_hash"], entry))
    for negative, identity, entry in sorted(ranked):
        item = visible_entry(entry)
        reason = None
        if negative == 0:
            reason = "zero_overlap"
        elif counter.count(canonical_json(item)) > RETRIEVAL["entry_tokens"]:
            reason = "entry_too_large"
        elif len(selected) == RETRIEVAL["max_entries"]:
            reason = "entry_count_limit"
        elif counter.count(canonical_json({"repair_experience": {**payload, "entries": [*payload["entries"], item]}})) > RETRIEVAL["wrapper_tokens"]:
            reason = "delivery_token_limit"
        if reason:
            omissions.append({"entry_hash": identity, "reason": reason})
        else:
            payload["entries"].append(item)
            selected.append(entry)
    return payload, selected, omissions


def target_view(snap, spec, blue_config, counter):
    """Project authenticated state, never evaluator results or hypothetical repairs.

    Selection is a preview for this unchanged public spec, not a task-bound Blue
    delivery receipt. The scheduler authenticates the full snapshot beforehand.
    """
    payload, selected, omissions = select_experience(snap, spec, counter)
    view = {"version": "continuous_red_target_v1", "snapshot_id": snap["snapshot_id"],
            "committed_round": snap["committed_round"], "snapshot_hash": hash_payload(snap),
            "state_hash": snap["state_hash"], "repair_budget": deepcopy(blue_config["runner"]),
            "learning_policy": deepcopy(LEARNING), "retrieval_policy": deepcopy(RETRIEVAL),
            "eligible_entry_count": len(snap["entries"]), "spec_selected_experience": payload}
    return view, omissions


def delivery(snap, task, counter):
    """Select once from the initial public spec; no mutation spans or outcomes in ranking."""
    payload, selected, omissions = select_experience(snap, task.spec, counter)
    envelope = {"version": rb.DELIVERY_VERSION, "snapshot_id": snap["snapshot_id"],
                "snapshot_hash": hash_payload(snap), "state_hash": snap["state_hash"],
                "task_hash": rb.public_task_hash(task), "retrieval_policy_hash": hash_payload(RETRIEVAL),
                "payload": payload, "selected_entry_hashes": [e["entry_hash"] for e in selected],
                "source_record_hashes": [e["body"]["source"]["encounter_hash"] for e in selected],
                "omissions": omissions, "payload_hash": hash_payload(payload)}
    rb.check_delivery(envelope, task, counter)
    return envelope
