"""GRD-8 / ACP-7 pilot configuration gates."""

__all__ = ["assess_pilot_readiness"]


def __getattr__(name):
    if name == "assess_pilot_readiness":
        # Keep ``python -m r3e.pilot.readiness`` free of runpy's eager-import
        # warning while preserving the package-level compatibility export.
        from .readiness import assess_pilot_readiness

        return assess_pilot_readiness
    raise AttributeError(name)
