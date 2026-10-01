"""Normalized observable profile: bug status plus causal relation.

The profile combines two inputs: the visible test feedback for the current
buggy design, and a structural model of that same buggy RTL. Every value is
bucketed or categorical, so profiles from different designs are comparable.
Signal names never appear in a profile.
"""
from __future__ import annotations

from .schema import ObservableProfile, VisibleFeedback
from .structure import RtlStructure


_TEMPORAL = {
    "observed_leads_one_cycle": "cycle_shift",
    "observed_lags_one_cycle": "cycle_shift",
    "stuck_value": "no_progress",
    "step_rate_mismatch": "accumulating",
    "single_cycle_glitch": "transient",
}


def _onset_bucket(cycle: int) -> str:
    if cycle <= 0:
        return "cycle0"
    if cycle <= 3:
        return "early"
    if cycle <= 15:
        return "mid"
    return "late"


def _delta_bucket(delta: int | None) -> str:
    if delta is None:
        return "n/a"
    if delta == 1:
        return "+1"
    if delta == -1:
        return "-1"
    if 0 < delta <= 8:
        return "small_pos"
    if -8 <= delta < 0:
        return "small_neg"
    return "large_pos" if delta > 0 else "large_neg"


def _count_bucket(count: int) -> str:
    if count <= 1:
        return "1"
    if count <= 3:
        return "2-3"
    return "4+"


def _width_bucket(width: int) -> str:
    if width <= 1:
        return "1bit"
    if width <= 8:
        return "narrow"
    return "wide"


def _size_bucket(size: int) -> str:
    if size <= 4:
        return "small"
    if size <= 15:
        return "medium"
    return "large"


def build_profile(feedback: VisibleFeedback, structure: RtlStructure) -> ObservableProfile:
    if not feedback.compile_ok:
        return ObservableProfile(
            status={"oracle_stage": "compile", "symptom": "compile_failure"},
            causal={},
        )
    primary = feedback.primary
    if primary is None:
        return ObservableProfile(
            status={"oracle_stage": "functional", "symptom": "no_divergence"},
            causal={},
        )
    status = {
        "oracle_stage": "functional",
        "symptom": primary.symptom,
        "delta_bucket": _delta_bucket(primary.delta),
        "onset_bucket": _onset_bucket(primary.first_cycle),
        "failing_output_bucket": _count_bucket(len(feedback.divergences)),
        "partial_failure": bool(feedback.passing_outputs),
        "x_involved": any(
            ch in "xz?" for d in feedback.divergences for ch in d.observed.lower()
        ),
        "output_width_bucket": _width_bucket(primary.width),
    }
    causal: dict[str, object] = {
        "temporal_relation": _TEMPORAL.get(primary.symptom, "same_cycle"),
    }
    cone = structure.cone(primary.signal) if structure.parsed else {"known": False}
    drivers = structure.drivers.get(primary.signal, []) if structure.parsed else []
    if drivers:
        kinds = {d.kind for d in drivers}
        causal["output_driver_kind"] = (
            "sequential" if kinds == {"sequential"}
            else "combinational" if kinds == {"combinational"}
            else "mixed"
        )
    else:
        causal["output_driver_kind"] = "unknown"
    if cone.get("known"):
        stage = cone["nearest_register_stage"]
        causal["register_distance"] = (
            "combinational_only" if stage is None
            else "0" if stage == 0
            else "1" if stage == 1
            else "2+"
        )
        causal["cone_size_bucket"] = _size_bucket(int(cone["size"]))
        causal["cone_has_counter"] = bool(cone["has_counter"])
        causal["cone_has_state_machine"] = bool(cone["has_state_machine"])
        causal["cone_has_reset_logic"] = bool(cone["has_reset_logic"])
        causal["cone_has_control_dependency"] = bool(cone["has_control_dependency"])
    else:
        causal["register_distance"] = "unknown"
    return ObservableProfile(status=status, causal=causal)
