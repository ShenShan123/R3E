"""Assemble the knowledge bundle handed to the Blue agent for one case.

Four modes support the C2 experience-value ablation (spec §3.4, C2-A):

- ``none``: no knowledge;
- ``static``: one fixed set of items for every case, regardless of the failure;
- ``matched``: items selected by the matcher for this failure;
- ``shuffled``: the same number of items ``matched`` would show, drawn
  deterministically from the pool *excluding* the matched ones. This keeps the
  activation rate the same while breaking the item-to-failure link.

Executor, budget and prompt structure are identical across the four modes.
Only the knowledge content changes.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from r3e.protocol.hashing import hash_payload

from .matcher import KnowledgeMatcher
from .schema import KnowledgeBundle, KnowledgeItem, ObservableProfile


USAGE_NOTE = (
    "Reference cases, not instructions. Each is a verified repair of a different design whose "
    "failure looked similar to this one: what was observed, what was faulty, how it was fixed "
    "(with the repairer's own explanation), and what did not work. Diagnose the current failure "
    "from its own evidence first. Use a case only as an idea of where to look or what kind of "
    "change to try, and only if its causal chain fits what you see here; "
    "fit_with_current_failure lists where it agrees and where it differs. Ignore cases that do "
    "not fit. The current test feedback always decides."
)
MAX_ITEM_CHARS = 6000


def fit_notes(result, profile: ObservableProfile) -> dict[str, Any]:
    """Where a retrieved item agrees and differs with the current failure (from the matcher)."""
    if result is None:
        return {}
    wanted = result.item.applicability_profile

    def value(p: ObservableProfile, label: str):
        group, key = label.split(".", 1)
        return (p.status if group == "status" else p.causal).get(key)

    return {
        "agrees_on": sorted(result.matched),
        "partly_agrees_on": sorted(result.partial),
        "differs_on": {label: {"case": value(wanted, label), "current": value(profile, label)}
                       for label in sorted(result.mismatched)},
    }


def render_item(item: KnowledgeItem, fit: Mapping[str, Any] | None = None,
                track_record: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The prompt-facing view of one item: its past cases, how it fits, and its track record."""
    body = item.to_dict()
    rendered = {
        "reference_id": f"{item.item_id}@v{item.version}",
        "support": body["evidence"].get("support"),
        "past_cases": list(body["card"]["cases"]),
        "fit_with_current_failure": dict(fit or {}),
    }
    if track_record is not None:
        rendered["track_record"] = dict(track_record)
    while len(repr(rendered)) > MAX_ITEM_CHARS and len(rendered["past_cases"]) > 1:
        rendered["past_cases"] = rendered["past_cases"][:-1]
    return rendered


def build_bundle(
    mode: str,
    *,
    profile: ObservableProfile,
    type_posterior: Mapping[str, float],
    pool: Sequence[KnowledgeItem],
    matcher: KnowledgeMatcher,
    case_id: str,
    seed: int = 0,
    static_items: Sequence[KnowledgeItem] = (),
    track_records: Mapping[str, Mapping[str, Any]] | None = None,
) -> KnowledgeBundle:
    detail: dict[str, Any] = {"matcher": matcher.config(), "pool_size": len(pool)}
    matches = []
    shown: list[KnowledgeItem] = []
    if mode == "none":
        pass
    elif mode == "static":
        shown = list(static_items)[: matcher.top_k]
        detail["static_item_ids"] = [i.item_id for i in shown]
    elif mode in {"matched", "shuffled"}:
        results = matcher.match(pool, profile, type_posterior)
        matches = [r.record() for r in results]
        if mode == "matched":
            shown = [r.item for r in results]
        else:
            excluded = {(r.item.item_id, r.item.version) for r in results}
            candidates = [i for i in pool if (i.item_id, i.version) not in excluded]
            candidates.sort(key=lambda i: hash_payload({
                "seed": int(seed), "case_id": case_id, "item": i.item_hash,
            }))
            shown = candidates[: len(results)]
            detail["shuffled_requested"] = len(results)
            detail["shuffled_available"] = len(candidates)
    else:
        raise ValueError(f"unknown knowledge mode: {mode}")
    detail["shown_item_ids"] = [f"{i.item_id}@v{i.version}" for i in shown]
    fits = [fit_notes(matcher.score(item, profile, type_posterior), profile) for item in shown]
    return KnowledgeBundle(
        mode=mode,
        items=tuple(render_item(item, fit, None if track_records is None else
                                track_records.get(f"{item.item_id}@v{item.version}", {"offered_before": 0}))
                    for item, fit in zip(shown, fits)),
        matches=tuple(matches),
        query_profile_hash=profile.profile_hash,
        type_posterior=dict(type_posterior),
        usage_note=USAGE_NOTE,
        selection_detail=detail,
    )
