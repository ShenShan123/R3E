"""Match the current failure against stored knowledge.

The score has three parts, each in [0, 1]:

- **status**: weighted agreement on the bug-status features the item
  conditions on (``FIELD_WEIGHTS``);
- **causal**: weighted agreement on the causal-relation features it
  conditions on;
- **type**: the inferred probability of the item's bug type, relative to the
  most likely type.

An item conditions only on features that every one of its source episodes
shared. Ordinal buckets that are one step apart earn half credit. A mismatched
oracle stage (compile vs. functional) excludes the item. When nothing reaches
``threshold`` the matcher abstains. Each match records its rationale.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .schema import ORDINAL_BUCKETS, KnowledgeItem, ObservableProfile


@dataclass(frozen=True)
class MatchResult:
    item: KnowledgeItem
    score: float
    components: Mapping[str, float]
    matched: tuple[str, ...]
    partial: tuple[str, ...]
    mismatched: tuple[str, ...]

    def record(self) -> dict[str, Any]:
        return {
            "item_id": self.item.item_id,
            "item_version": self.item.version,
            "item_hash": self.item.item_hash,
            "score": round(self.score, 6),
            "components": {k: round(v, 6) for k, v in self.components.items()},
            "matched": list(self.matched),
            "partial": list(self.partial),
            "mismatched": list(self.mismatched),
        }


# Discriminating features dominate; near-universal ones (no X, one failing
# output, ...) carry little weight so they cannot produce a match on their own.
FIELD_WEIGHTS: Mapping[str, float] = {
    "status.symptom": 3.0,
    "status.delta_bucket": 1.0,
    "status.onset_bucket": 1.0,
    "status.failing_output_bucket": 0.5,
    "status.partial_failure": 0.5,
    "status.x_involved": 0.5,
    "status.output_width_bucket": 0.5,
    "causal.output_driver_kind": 2.0,
    "causal.register_distance": 2.0,
    "causal.temporal_relation": 2.0,
    "causal.cone_has_counter": 1.0,
    "causal.cone_has_state_machine": 1.0,
    "causal.cone_has_reset_logic": 1.0,
    "causal.cone_has_control_dependency": 1.0,
    "causal.cone_size_bucket": 0.5,
}


def _field_score(key: str, wanted: Any, actual: Any) -> float:
    if actual == wanted:
        return 1.0
    scale = ORDINAL_BUCKETS.get(key)
    if scale and wanted in scale and actual in scale:
        if abs(scale.index(wanted) - scale.index(actual)) == 1:
            return 0.5
    return 0.0


class KnowledgeMatcher:
    def __init__(
        self,
        *,
        weights: Mapping[str, float] | None = None,
        threshold: float = 0.6,
        top_k: int = 3,
    ):
        self.weights = dict(weights or {"status": 0.45, "causal": 0.35, "type": 0.20})
        if not 1 <= int(top_k) <= 3:
            raise ValueError("top_k must be between 1 and 3")
        self.threshold = float(threshold)
        self.top_k = int(top_k)

    def config(self) -> dict[str, Any]:
        return {"weights": dict(sorted(self.weights.items())),
                "threshold": self.threshold, "top_k": self.top_k}

    def score(
        self,
        item: KnowledgeItem,
        profile: ObservableProfile,
        type_posterior: Mapping[str, float],
    ) -> MatchResult | None:
        wanted = item.applicability_profile
        if wanted.status.get("oracle_stage", profile.status.get("oracle_stage")) != profile.status.get("oracle_stage"):
            return None
        matched: list[str] = []
        partial: list[str] = []
        mismatched: list[str] = []
        components: dict[str, float] = {}
        for group, want, have in (
            ("status", wanted.status, profile.status),
            ("causal", wanted.causal, profile.causal),
        ):
            keys = [k for k in want if k != "oracle_stage"]
            if not keys:
                continue
            total = 0.0
            weight_total = 0.0
            for key in keys:
                label = f"{group}.{key}"
                weight = FIELD_WEIGHTS.get(label, 1.0)
                value = _field_score(key, want[key], have.get(key))
                total += weight * value
                weight_total += weight
                (matched if value == 1.0 else partial if value > 0 else mismatched).append(label)
            components[group] = total / weight_total
        if type_posterior:
            peak = max(type_posterior.values()) or 1.0
            components["type"] = min(1.0, float(type_posterior.get(item.bug_type, 0.0)) / peak)
        weight_sum = sum(self.weights[k] for k in components) or 1.0
        score = sum(self.weights[k] * v for k, v in components.items()) / weight_sum
        return MatchResult(
            item=item,
            score=score,
            components=components,
            matched=tuple(matched),
            partial=tuple(partial),
            mismatched=tuple(mismatched),
        )

    def match(
        self,
        items: Iterable[KnowledgeItem],
        profile: ObservableProfile,
        type_posterior: Mapping[str, float],
    ) -> list[MatchResult]:
        scored = [
            result for item in items
            if (result := self.score(item, profile, type_posterior)) is not None
            and result.score >= self.threshold
        ]
        scored.sort(key=lambda r: (-r.score, r.item.item_id, r.item.version))
        return scored[: self.top_k]
