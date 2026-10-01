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
    "These items summarize previously verified repairs on other designs. "
    "Each states a hypothesis and the conditions it was observed under. Use an "
    "item only if the current failure evidence is consistent with it; otherwise "
    "ignore it. The current test feedback always takes precedence."
)
MAX_ITEM_CHARS = 3000


def render_item(item: KnowledgeItem) -> dict[str, Any]:
    """The prompt-facing view of one item: card and example, no scores."""
    body = item.to_dict()
    app = body["applicability"]
    rendered = {
        "knowledge_id": f"{item.item_id}@v{item.version}",
        "observed_under": {
            "likely_bug_type": app["bug_type"],
            **{f"status.{k}": v for k, v in sorted(app["status"].items())},
            **{f"causal.{k}": v for k, v in sorted(app["causal"].items())},
        },
        **body["card"],
    }
    if body.get("example"):
        rendered["abstracted_example"] = body["example"]
    text = repr(rendered)
    if len(text) > MAX_ITEM_CHARS and "abstracted_example" in rendered:
        rendered.pop("abstracted_example")
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
    return KnowledgeBundle(
        mode=mode,
        items=tuple(render_item(item) for item in shown),
        matches=tuple(matches),
        query_profile_hash=profile.profile_hash,
        type_posterior=dict(type_posterior),
        usage_note=USAGE_NOTE,
        selection_detail=detail,
    )
