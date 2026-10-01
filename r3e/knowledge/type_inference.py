"""Soft bug-type inference at repair time.

While Blue is repairing, the true bug type is unknown, and Red's label is off
limits. This model estimates P(bug_type | observable profile) with a smoothed
naive-Bayes count model:

- The counts come from **verified episodes**: the bug type of Blue's own
  passing repair, paired with the profile observed before that repair.
- A small, documented prior (``PRIOR_PSEUDO_COUNTS``) keeps the estimate
  defined before any experience exists. Its total weight is deliberately small
  (``prior_weight``), so accumulated experience dominates quickly.

The posterior is used only as a *soft* matching feature. It never routes Blue
on its own.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Iterable, Mapping

from .schema import BUG_TYPES, ObservableProfile, RepairEpisode


INFERENCE_FEATURES = (
    "status.symptom",
    "status.delta_bucket",
    "status.onset_bucket",
    "causal.output_driver_kind",
    "causal.register_distance",
    "causal.cone_has_counter",
    "causal.cone_has_state_machine",
    "causal.cone_has_reset_logic",
)

# Weak initial prior: (feature, value) -> {bug_type: pseudo-count}.
PRIOR_PSEUDO_COUNTS: Mapping[tuple[str, object], Mapping[str, float]] = {
    ("status.symptom", "constant_offset"): {
        "constant_value": 2, "operator_compare": 2, "width_or_index": 1,
        "operator_arith": 1,
    },
    ("status.symptom", "step_rate_mismatch"): {
        "operator_arith": 2, "constant_value": 2, "operator_shift": 1,
    },
    ("status.symptom", "observed_leads_one_cycle"): {
        "assignment_kind": 2, "state_transition": 2, "signal_reference": 1,
    },
    ("status.symptom", "observed_lags_one_cycle"): {
        "assignment_kind": 1, "signal_reference": 2, "missing_or_extra_logic": 1,
    },
    ("status.symptom", "stuck_value"): {
        "condition_expression": 2, "reset_or_enable": 2, "sensitivity_list": 1,
        "assignment_target": 1,
    },
    ("status.symptom", "x_or_unknown"): {
        "reset_or_enable": 2, "missing_or_extra_logic": 1, "width_or_index": 1,
    },
    ("status.symptom", "single_cycle_glitch"): {
        "condition_expression": 1, "operator_compare": 1, "state_transition": 1,
    },
    ("status.onset_bucket", "cycle0"): {"reset_or_enable": 2, "constant_value": 1},
    ("causal.cone_has_state_machine", True): {"state_transition": 2},
    ("causal.cone_has_counter", True): {"operator_compare": 1, "constant_value": 1},
    ("causal.output_driver_kind", "combinational"): {
        "operator_logic": 1, "operator_compare": 1, "signal_reference": 1,
    },
}


class BugTypeInference:
    def __init__(self, *, alpha: float = 0.5, prior_weight: float = 0.5):
        self.alpha = float(alpha)
        self.prior_weight = float(prior_weight)
        self.type_counts: Counter[str] = Counter()
        self.feature_counts: dict[tuple[str, object], Counter[str]] = defaultdict(Counter)
        self.observed_values: dict[str, set[object]] = defaultdict(set)
        self.fitted_episode_hashes: list[str] = []

    @staticmethod
    def _features(profile: ObservableProfile) -> dict[str, object]:
        flat = profile.flat()
        return {key: flat[key] for key in INFERENCE_FEATURES if key in flat}

    def fit(self, episodes: Iterable[RepairEpisode]) -> "BugTypeInference":
        for episode in episodes:
            bug_type = episode.verified_bug_type
            if bug_type not in BUG_TYPES:
                continue
            self.type_counts[bug_type] += 1
            for key, value in self._features(episode.profile).items():
                self.feature_counts[(key, value)][bug_type] += 1
                self.observed_values[key].add(value)
            self.fitted_episode_hashes.append(episode.episode_hash)
        return self

    def posterior(self, profile: ObservableProfile) -> dict[str, float]:
        features = self._features(profile)
        total = sum(self.type_counts.values())
        logs: dict[str, float] = {}
        for bug_type in BUG_TYPES:
            log_p = math.log(
                (self.type_counts[bug_type] + self.alpha)
                / (total + self.alpha * len(BUG_TYPES))
            )
            for key, value in features.items():
                observed = self.feature_counts.get((key, value), Counter())[bug_type]
                prior = self.prior_weight * float(
                    PRIOR_PSEUDO_COUNTS.get((key, value), {}).get(bug_type, 0.0)
                )
                values = max(2, len(self.observed_values.get(key, ())) + 1)
                log_p += math.log(
                    (observed + prior + self.alpha)
                    / (self.type_counts[bug_type] + self.prior_weight * 2 + self.alpha * values)
                )
            logs[bug_type] = log_p
        peak = max(logs.values())
        weights = {key: math.exp(value - peak) for key, value in logs.items()}
        norm = sum(weights.values())
        return {
            key: round(value / norm, 6)
            for key, value in sorted(weights.items(), key=lambda kv: -kv[1])
        }

    def summary(self) -> dict[str, object]:
        return {
            "fitted_episodes": len(self.fitted_episode_hashes),
            "type_counts": dict(sorted(self.type_counts.items())),
            "alpha": self.alpha,
            "prior_weight": self.prior_weight,
        }
