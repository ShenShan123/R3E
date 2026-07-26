"""Deprecated compatibility import for legacy skill preflight."""
from __future__ import annotations

import warnings

warnings.warn(
    "semantic_repair_bench.skill_preflight is legacy-only; formal runtime uses PolicyState",
    DeprecationWarning,
    stacklevel=2,
)

from r3e.legacy.skill_preflight import *  # noqa: F401,F403,E402
