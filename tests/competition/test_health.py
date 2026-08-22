from __future__ import annotations

from pathlib import Path

from competition.app.backend.health import health


ROOT = Path(__file__).resolve().parents[2]


def test_health_reports_required_tool_readiness():
    result = health(ROOT)
    assert set(result["required_tools"]) == {"iverilog", "vvp", "yosys"}
    assert result["required_tools_ready"] is True
    assert result["status"] == "pass"


def test_provider_readiness_is_not_claimed_by_health():
    assert health(ROOT)["provider_ready"] is None
