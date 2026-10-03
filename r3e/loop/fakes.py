"""Scripted local transports for dry runs and tests. They never call a model.

Both fakes plug into ``OpenAICompatibleJSONClient(transport=...)``, so every
hash binding, conformance check and budget count is exercised exactly as in a
real run.

Results produced with these transports are plumbing checks only. They are
never experimental evidence.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from r3e.protocol.hashing import hash_payload


def _reply(payload: Mapping[str, Any], counter: int) -> dict[str, Any]:
    return {
        "content": json.dumps(payload),
        "input_tokens": 100,
        "output_tokens": 40,
        "provider_request_id": f"fake-{counter}",
    }


class FakeBlueTransport:
    """Return a scripted repair for each buggy design.

    ``solutions`` maps ``hash_payload(buggy_rtl)`` to a repaired RTL string.
    ``succeed(user_payload, attempt_number)`` decides whether this attempt
    returns the solution. By default an attempt succeeds when the knowledge it
    received names the ``required_bug_type``, which lets tests make knowledge
    matter. Otherwise the buggy RTL is returned unchanged.
    """

    def __init__(
        self,
        solutions: Mapping[str, str] | Callable[[str], str | None],
        *,
        succeed: Callable[[Mapping[str, Any], int], bool] | None = None,
    ):
        self.solutions = solutions if callable(solutions) else dict(solutions)
        self.succeed = succeed or (lambda user, n: True)
        self.requests: list[dict[str, Any]] = []
        self._attempts: dict[str, int] = {}

    def __call__(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        user = json.loads(request["messages"][-1]["content"])
        buggy = user["current_buggy_rtl"]
        key = hash_payload(buggy)
        # attempt index within the current encounter (runner encodes it in the seed)
        n = int(user.get("candidate_seed", 0)) % 100
        self._attempts[key] = self._attempts.get(key, 0) + 1
        fixed = self.solutions(buggy) if callable(self.solutions) else self.solutions.get(key)
        ok = fixed is not None and self.succeed(user, n)
        return _reply({
            "replacement_rtl": fixed if ok else buggy,
            "edit": "scripted repair" if ok else "scripted no-op",
        }, len(self.requests))


def nearest_clean_resolver(clean_designs: list[str]) -> Callable[[str], str | None]:
    """Dry-run oracle: map a buggy design back to its closest clean carrier.

    This deliberately cheats and exists only to exercise loop plumbing.
    """
    from difflib import SequenceMatcher

    def resolve(buggy: str) -> str | None:
        best = max(clean_designs, key=lambda clean: SequenceMatcher(None, clean, buggy).quick_ratio(),
                   default=None)
        return best

    return resolve


def knowledge_types(user: Mapping[str, Any]) -> set[str]:
    """Reference ids of the memory cases a Blue request carried (empty: none)."""
    items = (user.get("reference_cases") or {}).get("items") or []
    return {str(item.get("reference_id")) for item in items}


class FakeRedTransport:
    """Pick sites deterministically: the ``choice``-th site of an operator.

    Returns the candidate format (one candidate with one edit, or ``edits``
    edits on distinct sites).
    """

    def __init__(self, *, operator: str | None = None, choice: int = 0, edits: int = 1,
                 follow: Callable[[Mapping[str, Any]], str | None] | None = None):
        self.operator = operator
        self.choice = choice
        self.edits = edits
        # curriculum: ``follow(weak_point_view)`` returns a direction to pursue
        # the first offered weak point, or None to explore
        self.follow = follow
        self.requests: list[dict[str, Any]] = []

    def __call__(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        user = json.loads(request["messages"][-1]["content"])
        offered = (user.get("experience_base") or {}).get("open_weak_points") or []
        if "candidates" in user:  # curriculum over real bugs: choose one offered bug
            return _reply(self._choose(user["candidates"], offered), len(self.requests))
        target = offered[0] if offered and self.follow else None
        direction = self.follow(target) if target else None
        operators = set(target["operators"]) if direction else ({self.operator} if self.operator else None)
        sites = [s for s in user["sites"] if operators is None or s["operator"] in operators] or user["sites"]
        first = (self.choice + len(self.requests) - 1) % len(sites)
        n_edits = 1 if direction == "simpler" else self.edits
        picked = [sites[(first + k) % len(sites)] for k in range(min(n_edits, len(sites)))]
        reply: dict[str, Any] = {"candidates": [{
            "hypothesis": "scripted weakness hypothesis",
            "edits": [{"site_id": s["site_id"], "option": s["options"][0]} for s in picked],
            "expected_symptom": "scripted",
        }]}
        if "experience_base" in user:
            reply["decision"] = {"lineage_id": target["lineage_id"] if direction else None,
                                 "direction": direction or "explore", "reason": "scripted"}
        return _reply(reply, len(self.requests))


    def _choose(self, candidates: list[dict[str, Any]], offered: list[dict[str, Any]]) -> dict[str, Any]:
        """Pursue the first weak point a candidate can continue (``follow``), else explore."""
        for lin in offered if self.follow else []:
            direction = self.follow(lin)
            for c in candidates:
                if direction and f"type:{c['fix_type']}" in lin["operators"] and c["design"] not in lin["designs_used"]:
                    return {"decision": {"lineage_id": lin["lineage_id"], "direction": direction,
                                         "reason": "scripted"}, "choice": c["candidate_id"]}
        return {"decision": {"lineage_id": None, "direction": "explore", "reason": "scripted"},
                "choice": candidates[0]["candidate_id"]}
