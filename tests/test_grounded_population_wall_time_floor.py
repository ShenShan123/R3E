from __future__ import annotations

from r3e.red.grounded.population_scheduler import _assignment_budget


def test_real_provider_wall_time_floor_applies_without_changing_legacy_budget():
    intent = {"difficulty_target": {"difficulty_band": "D0"}}
    legacy = {
        "max_input_tokens": 2048,
        "max_output_tokens": 1024,
        "max_wall_time_ms": 60000,
    }
    real_shadow = {
        **legacy,
        "minimum_wall_time_ms": 60000,
    }
    assert _assignment_budget(intent, legacy) == (2048, 1024, 10000)
    assert _assignment_budget(intent, real_shadow) == (2048, 1024, 60000)
