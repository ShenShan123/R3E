"""GRD-8 / ACP-7 pilot configuration gates."""

__all__ = [
    "assess_pilot_readiness",
    "assess_shadow_admission_readiness",
    "assess_policy_promotion_readiness",
]


def __getattr__(name):
    if name in {
        "assess_pilot_readiness",
        "assess_shadow_admission_readiness",
        "assess_policy_promotion_readiness",
    }:
        # Keep ``python -m r3e.pilot.readiness`` free of runpy's eager-import
        # warning while preserving the package-level compatibility export.
        from .readiness import (
            assess_pilot_readiness,
            assess_shadow_admission_readiness,
        )
        from .promotion_readiness import assess_policy_promotion_readiness

        return {
            "assess_pilot_readiness": assess_pilot_readiness,
            "assess_shadow_admission_readiness": (
                assess_shadow_admission_readiness
            ),
            "assess_policy_promotion_readiness": (
                assess_policy_promotion_readiness
            ),
        }[name]
    raise AttributeError(name)
