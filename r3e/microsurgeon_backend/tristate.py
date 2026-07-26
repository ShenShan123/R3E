from __future__ import annotations

from pathlib import Path


def _nonempty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def detect_flow_signals(results_root: Path, design: str, platform: str, variant: str = "base") -> tuple[bool, bool]:
    base = results_root / platform / design / variant
    route_completed = _nonempty(base / "5_route.odb") or _nonempty(base / "5_2_route.odb")
    gds_generated = _nonempty(base / "6_final.gds") or _nonempty(base / "6_1_merged.gds")
    return route_completed, gds_generated


def classify_tristate(
    final_wns: float | None,
    route_completed: bool,
    gds_generated: bool,
) -> str:
    if final_wns is None:
        return "failed"
    if final_wns >= 0:
        return "closed"
    if route_completed and gds_generated:
        return "routed_unclosed"
    return "failed"
