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
    items = (user.get("retrieved_repair_knowledge") or {}).get("items") or []
    return {str(item.get("observed_under", {}).get("likely_bug_type")) for item in items}


class FakeRedTransport:
    """Pick a site deterministically: the ``choice``-th site of an operator."""

    def __init__(self, *, operator: str | None = None, choice: int = 0):
        self.operator = operator
        self.choice = choice
        self.requests: list[dict[str, Any]] = []

    def __call__(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        user = json.loads(request["messages"][-1]["content"])
        sites = [s for s in user["sites"] if self.operator in (None, s["operator"])] or user["sites"]
        site = sites[(self.choice + len(self.requests) - 1) % len(sites)]
        return _reply({
            "hypothesis": "scripted weakness hypothesis",
            "operator": site["operator"],
            "site_id": site["site_id"],
            "option": site["options"][0],
            "expected_symptom": "scripted",
        }, len(self.requests))
