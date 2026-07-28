"""Conservative control-delta conflict detection."""
from __future__ import annotations

from typing import Iterable

from .schema import ControlMemory


def conflict_fields(memories: Iterable[ControlMemory]) -> set[str]:
    values: dict[str, object] = {}
    conflicts: set[str] = set()
    enabled: set[str] = set()
    disabled: set[str] = set()
    for memory in memories:
        for key, value in memory.control_delta.items():
            if key == "enable_analyzers":
                enabled.update(value)
                continue
            if key == "disable_analyzers":
                disabled.update(value)
                continue
            if key in values and values[key] != value:
                conflicts.add(key)
            else:
                values[key] = value
    if enabled & disabled:
        conflicts.add("analyzer_enable_disable")
    return conflicts
