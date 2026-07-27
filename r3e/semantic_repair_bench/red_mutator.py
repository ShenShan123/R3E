"""Deprecated compatibility facade for the legacy red mutator.

Whole-Policy Evolution uses :mod:`r3e.red.generator`,
:mod:`r3e.red.operators`, and :mod:`r3e.red.validity`.  Historical scripts
may continue importing ``mutate_once`` and ``generate_repairable_poison`` from
this path, but those functions have no formal runtime authority.
"""
from __future__ import annotations

import warnings

warnings.warn(
    "semantic_repair_bench.red_mutator is legacy-only; use r3e.red.*",
    DeprecationWarning,
    stacklevel=2,
)

from r3e.legacy.red_mutator import *  # noqa: F401,F403,E402
from r3e.legacy.red_mutator import _main  # noqa: E402


if __name__ == "__main__":
    _main()
