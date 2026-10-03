"""A structural view of the failing outputs, for Blue's evidence.

Structure as an input, not a guess from tokens: for each output the visible
test reports wrong, the view lists where it is assigned and what drives it,
from our own RTL analysis (``structure.py``):
- its backward logic cone up to ``max_depth`` signal hops: the signals at
  each depth, and the registers, state registers and inputs in it;
- the lines of the statements assigning the signals of that cone.

Each statement (line, text, block kind, the signals it reads and the signals
guarding it) is listed once under ``statements``, even when several failing
outputs share it.

It states facts about the current design only and gives no advice.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Iterable

from .structure import analyze_rtl


def _line_of(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def build_structural_view(source: str, failing: Iterable[str], *, max_depth: int = 3,
                          max_statements: int = 16) -> dict[str, Any]:
    structure = analyze_rtl(source)
    if not structure.parsed:
        return {}
    failing = [s for s in dict.fromkeys(failing) if s in structure.drivers]
    if not failing:
        return {}

    def statement(signal: str, driver) -> dict[str, Any]:
        text = " ".join(source[driver.start:driver.end].split())
        return {"line": _line_of(source, driver.start), "assigns": signal, "statement": text[:160],
                "block": driver.kind, "reads": sorted(driver.data_deps)[:12],
                "guarded_by": sorted(driver.control_deps)[:12]}

    view: dict[str, Any] = {"failing_outputs": {}, "statements": []}
    listed: set[tuple[int, str]] = set()
    for out in failing:
        depth = {out: 0}
        queue = deque([out])
        while queue:
            sig = queue.popleft()
            if depth[sig] >= max_depth:
                continue
            for d in structure.drivers.get(sig, []):
                for dep in sorted(d.data_deps | d.control_deps):
                    if dep not in depth and dep not in structure.parameters:
                        depth[dep] = depth[sig] + 1
                        queue.append(dep)
        lines = []
        for sig in sorted(depth, key=lambda s: (depth[s], s)):
            for d in structure.drivers.get(sig, []):
                entry = statement(sig, d)
                key = (entry["line"], entry["statement"])
                if key not in listed and len(listed) < max_statements:
                    listed.add(key)
                    view["statements"].append(entry)
                if key in listed and entry["line"] not in lines:
                    lines.append(entry["line"])
        kinds = {d.kind for d in structure.drivers[out]}
        view["failing_outputs"][out] = {
            "driven_by": kinds.pop() if len(kinds) == 1 else "mixed",
            "cone_by_depth": {str(k): sorted(s for s, v in depth.items() if v == k)
                              for k in sorted(set(depth.values())) if k > 0},
            "registers_in_cone": sorted(s for s in depth if structure.is_register(s)),
            "state_registers_in_cone": sorted(s for s in depth if structure.is_state_like(s)),
            "inputs_in_cone": sorted(s for s in depth if s in structure.inputs),
            "statement_lines": lines,
        }
    return view
