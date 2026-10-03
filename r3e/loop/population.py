"""Population ledger: every repair attempt as a complete, verifiable record.

Memory is not only for retrieval. Each attempt, including failed branches, is
kept as a tuple a policy can later learn from, without training any model:

- ``state``: the challenge, its design, the buggy-design hash, the failure
  profile, and how many attempts came before;
- ``action``: the candidate's patch (a diff against the buggy design), its
  hash, Blue's edit description, and the memory offered with it, if any;
- ``evidence``: the execution result (verdict tier, hidden-test outcome,
  wrong-cycle counts);
- ``reward``: a scalar from the evidence (definition below), with its parts;
- ``structure``: the patch's structural identity (change type, scope, block,
  assigned role) and a name-independent signature of the change;
- ``age``: the round, added by the loop.

Reward: 1.0 when the candidate passes the visible test and the hidden test
passes or does not exist; 0.5 when it passes the visible test but fails the
hidden one; up to 0.25 for a visible failure, scaled by the share of cycles
that are still right; 0 for a compile failure or no answer.

``eligible`` decides which records may influence a policy. Holdout
evaluation never does (that would leak the held-out designs), nor do provider
failures. Records older than ``max_age`` rounds age out. Of several records
with the same state and the same structural signature, only the first
counts, so structurally diverse branches are what remain. A policy learned
from eligible records is activated only through the fork-paired gate
(``qualification.py``), like any memory item.
"""
from __future__ import annotations

import difflib
from typing import Any, Iterable, Mapping

from r3e.knowledge import classify_repair
from r3e.knowledge.anonymize import anonymize_pair
from r3e.knowledge.structure import analyze_rtl
from r3e.protocol.hashing import hash_payload


EXCLUDED_PHASES = {"evaluation", "cross_model"}  # holdout data; other models' behaviour


def reward(tier: str, hidden: str | None, window: Mapping[str, Any] | None) -> float:
    if tier in {"visible_pass", "hidden_pass"}:
        return 0.5 if hidden == "fail" else 1.0
    if tier == "visible_fail":
        wrong = (window or {}).get("wrong_cycles") or {}
        total = (window or {}).get("cycles_compared") or 0
        worst = max((v.get("count", 0) for v in wrong.values()), default=0)
        return round(0.25 * (1 - worst / total), 4) if total else 0.0
    return 0.0


def _patch(buggy: str, candidate: str, limit: int = 40) -> list[str]:
    diff = [l for l in difflib.unified_diff(buggy.splitlines(), candidate.splitlines(), lineterm="", n=0)
            if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    return diff[:limit]


def _structure(buggy: str, candidate: str) -> dict[str, Any]:
    cls = classify_repair(buggy, candidate)
    before, after, _ = anonymize_pair(cls.pop("before_lines", []), cls.pop("after_lines", []), analyze_rtl(buggy))
    return {"change_type": cls.get("bug_type"), "edit_scope": cls.get("edit_scope"),
            "block_kind": cls.get("block_kind"), "assigned_signal_role": cls.get("assigned_signal_role"),
            "signature": hash_payload({"before": before, "after": after})}


def attempt_record(*, challenge, profile: Mapping[str, Any], record: Mapping[str, Any], rtl: str | None,
                   verdict, history_len: int, seed: int, delivered: list[str]) -> dict[str, Any]:
    """One attempt as a population tuple (``rtl``/``verdict`` are None when there was no answer)."""
    window = getattr(verdict, "window", None) if verdict is not None else None
    tier = record.get("verdict_tier")
    out = {
        "phase": record.get("phase"),
        "state": {"challenge_id": challenge.challenge_id, "origin": challenge.origin,
                  "design_cluster": challenge.carrier.cluster_id, "buggy_hash": hash_payload(challenge.buggy_rtl),
                  "profile": dict(profile), "attempts_before": history_len, "seed": seed},
        "action": None,
        "evidence": {"tier": tier, "hidden": record.get("hidden"),
                     "wrong_cycles": {k: v.get("count") for k, v in ((window or {}).get("wrong_cycles") or {}).items()},
                     "cycles_compared": (window or {}).get("cycles_compared")},
        "reward": reward(tier, record.get("hidden"), window),
        "structure": None,
    }
    if rtl is not None:
        out["action"] = {"candidate_hash": hash_payload(rtl), "patch": _patch(challenge.buggy_rtl, rtl),
                         "edit": record.get("edit"), "memory_delivered": list(delivered)}
        if rtl.strip() != challenge.buggy_rtl.strip():
            out["structure"] = _structure(challenge.buggy_rtl, rtl)
    elif delivered:
        out["action"] = {"memory_delivered": list(delivered)}
    return out


def eligible(records: Iterable[Mapping[str, Any]], *, now_round: int, max_age: int = 6) -> list[dict[str, Any]]:
    """Records that may influence a policy (see module docstring)."""
    kept, seen = [], set()
    for rec in records:
        if rec.get("phase") in EXCLUDED_PHASES or rec["evidence"]["tier"] == "provider_fail":
            continue
        if now_round - int(rec.get("round", now_round)) > max_age:
            continue
        signature = (rec["structure"] or {}).get("signature") or rec["evidence"]["tier"]
        key = (rec["state"]["buggy_hash"], signature)
        if key in seen:
            continue
        seen.add(key)
        kept.append(dict(rec))
    return kept
