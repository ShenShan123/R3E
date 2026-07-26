"""Deprecated compatibility import for the legacy skill registry.

Whole-Policy Evolution formal runtime uses :mod:`r3e.policy.registry_v2`.
"""
from __future__ import annotations

import warnings

warnings.warn(
    "semantic_repair_bench.skill_registry is legacy-only; use r3e.policy.registry_v2",
    DeprecationWarning,
    stacklevel=2,
)

from r3e.legacy.skill_registry import *  # noqa: F401,F403,E402
from r3e.legacy.skill_registry import _FAMILY_KEYWORDS, _REPAIR_HINTS  # noqa: F401,E402
