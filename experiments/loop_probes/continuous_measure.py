"""Snapshot-bound confirmation for the continuous loop (r3e/loop/CONTINUOUS_BLUE_INTERFACE.md).

A confirmation member restores the exact Blue snapshot and task-specific delivery used online,
never the latest Blue. The manifest is frozen, and all its calls reserved, before the first call.
Classification reuses ``repair_feedback.classify_trials(..., encounters=3)``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from experiments.loop_probes import red907_measure as measure
from r3e.loop import repo_blue as rb
from r3e.protocol.hashing import hash_payload

ENCOUNTERS = 3
CONFIG = rb.RepoBlueConfig(budget_k=1, max_infra_retries=0)
BINDING = ("task_hash", "snapshot_id", "snapshot_hash", "state_hash")


def confirmation_manifest(members: list[dict[str, Any]], approved_calls: int, *, retrieval_policy_hash: str) -> dict:
    """members: [{member_id, task_dir, envelope_path, delivery_hash, task_hash, snapshot_id, snapshot_hash,
    state_hash, seed, online_seed}] in frozen order. Refuses, rather than selects, if approval is short."""
    calls = ENCOUNTERS * len(members)
    if calls > approved_calls:
        raise ValueError(f"approved {approved_calls} calls cannot cover the frozen manifest ({calls})")
    rows = []
    for m in members:
        envelope = json.loads(Path(m["envelope_path"]).read_text())
        if hash_payload(envelope) != m["delivery_hash"] or any(envelope[k] != m[k] for k in BINDING):
            raise ValueError(f"{m['member_id']}: archived delivery differs from the online binding")
        labels = [m["seed"] * 10 + i for i in range(ENCOUNTERS)]
        # online_seed may be the plan's run_blue seed or the trial record's label (that seed * 10)
        if {m["online_seed"], m["online_seed"] * 10} & set(labels):
            raise ValueError(f"{m['member_id']}: confirmation seeds must be fresh")
        rows.append({**{k: str(v) if isinstance(v, Path) else v for k, v in m.items()}, "seed_labels": labels})
    return {"protocol": "continuous_confirmation_v1", "encounters_per_member": ENCOUNTERS, "calls": calls,
            "runner_config": CONFIG.__dict__, "retrieval_policy_hash": retrieval_policy_hash, "members": rows}


def run_confirmation(manifest: dict[str, Any], out: Path, *, client, counter) -> Path:
    """Restore each member's snapshot-bound delivery and run fresh one-attempt encounters."""
    if client.max_calls - client.total_calls < manifest["calls"]:
        raise measure.MeasurementRefused("the whole manifest must be reserved before the first call")
    checked = []
    for m in manifest["members"]:  # every binding is verified before any directory or call exists
        envelope = json.loads(Path(m["envelope_path"]).read_text())
        task = rb.RepoTask.load(Path(m["task_dir"]))
        if (hash_payload(envelope) != m["delivery_hash"] or any(envelope[k] != m[k] for k in BINDING)
                or rb.public_task_hash(task) != m["task_hash"]
                or envelope["retrieval_policy_hash"] != manifest["retrieval_policy_hash"]):
            raise measure.MeasurementRefused(f"{m['member_id']}: task, snapshot, policy or payload changed")
        checked.append((m, envelope))
    out.mkdir(parents=True, exist_ok=False)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    for m, envelope in checked:
        member = out / m["member_id"]
        measure.run_blue(Path(m["task_dir"]), member / "blue", client=client, counter=counter, cfg=CONFIG,
                         seed=m["seed"], encounters=ENCOUNTERS, direct_only=True, endpoint_protocol="confirmation",
                         experience=envelope, experience_journal=member / "experience_journal.jsonl",
                         retrieval_policy_hash=manifest["retrieval_policy_hash"])
    return out
