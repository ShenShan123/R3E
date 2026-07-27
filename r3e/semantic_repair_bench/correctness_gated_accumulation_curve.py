"""Deprecated compatibility alias for the legacy accumulation experiment."""
from __future__ import annotations

import sys
import warnings

from experiments.legacy import correctness_gated_accumulation_curve as _implementation


warnings.warn(
    "semantic_repair_bench.correctness_gated_accumulation_curve is legacy-only; "
    "use r3e.arena.runner for Whole-Policy Evolution",
    DeprecationWarning,
    stacklevel=2,
)

if __name__ == "__main__":
    _implementation.main()
else:
    # Preserve historical imports and monkeypatch behavior by making the old
    # module name an alias of the relocated implementation module.
    sys.modules[__name__] = _implementation
