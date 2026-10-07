"""The design's own register values at each sample of the visible test.

Blue sees the inputs and outputs of each sampled cycle, not the internal
state. To explain a wrong output of a sequential design it otherwise has to
replay the design from reset in its head. The register trace gives that state
directly: the values of the registers that drive the failing outputs, taken
from simulating the same (buggy or candidate) design. These are facts anyone
can get by simulating the design; the reference design's internals are never
shown.

Values are read from a VCD dump of the design under test and aligned with the
trace by sample time. The alignment is checked, not assumed: the dumped
failing outputs must equal the logged ones at every sample, otherwise no
register values are given (a testbench that samples before register updates
would make them misleading).
"""
from __future__ import annotations

import bisect
import re
from collections import deque
from typing import Iterable, Mapping

from .structure import analyze_rtl

VCD_NAME = "r3e_registers.vcd"
_LITERAL = re.compile(r"^(?:(\d+)?\s*'\s*([sS]?)([bBoOdDhH])\s*([0-9a-fA-F_xXzZ]+)|(\d+))$")
_PARAM = re.compile(r"\b(?:parameter|localparam)\b([^;]*);")


def cone_registers(source: str, failing: Iterable[str], *, limit: int = 6) -> tuple[list[str], set[str]]:
    """Registers in the backward cone of the failing outputs (state registers first,
    nearest first), and which of them are state registers."""
    structure = analyze_rtl(source)
    if not structure.parsed:
        return [], set()
    depth: dict[str, int] = {}
    queue = deque()
    for out in dict.fromkeys(failing):
        if out in structure.drivers:
            depth[out] = 0
            queue.append(out)
    while queue:
        sig = queue.popleft()
        for d in structure.drivers.get(sig, []):
            for dep in sorted(d.data_deps | d.control_deps):
                if dep not in depth and dep not in structure.parameters and dep not in structure.inputs:
                    depth[dep] = depth[sig] + 1
                    queue.append(dep)
    regs = [s for s in depth if structure.is_register(s) and s not in structure.outputs]
    regs.sort(key=lambda s: (not structure.is_state_like(s), depth[s], s))
    regs = regs[:limit]
    return regs, {s for s in regs if structure.is_state_like(s)}


def inject_dump(source: str, top: str, signals: list[str], outputs: list[str]) -> str | None:
    """``source`` with a VCD dump of ``signals`` and ``outputs`` added to module ``top``."""
    m = re.search(rf"\bmodule\s+{re.escape(top)}\b", source)
    end = re.compile(r"\bendmodule\b").search(source, m.end()) if m else None
    if end is None:
        return None
    names = ", ".join(dict.fromkeys([*signals, *outputs]))
    dump = f'\ninitial begin $dumpfile("{VCD_NAME}"); $dumpvars(0, {names}); end\n'
    return source[:end.start()] + dump + source[end.start():]


def parse_vcd(text: str) -> dict[str, tuple[int, list[int], list[str]]]:
    """``{name: (width, change times, values)}``; values as binary strings of ``width`` bits."""
    codes: dict[str, tuple[str, int]] = {}
    signals: dict[str, tuple[int, list[int], list[str]]] = {}
    time = 0
    tokens = iter(text.split())
    for tok in tokens:
        if tok == "$var":
            _kind, width, code, name = next(tokens), int(next(tokens)), next(tokens), next(tokens)
            codes[code] = (name, width)
            signals.setdefault(name, (width, [], []))
        elif tok.startswith("#") and tok[1:].isdigit():
            time = int(tok[1:])
        elif tok[0] in "bB":
            code = next(tokens)
            if code in codes:
                _record(signals, codes[code], time, tok[1:].lower())
        elif tok[0] in "01xXzZ" and len(tok) > 1 and tok[1:] in codes:
            _record(signals, codes[tok[1:]], time, tok[0].lower())
    return signals


def _record(signals, ident, time, bits) -> None:
    name, width = ident
    fill = bits[0] if bits[0] in "xz" else "0"
    bits = bits[-width:].rjust(width, fill)
    _, times, values = signals[name]
    if times and times[-1] == time:
        values[-1] = bits
    else:
        times.append(time)
        values.append(bits)


def value_at(signal: tuple[int, list[int], list[str]], time: int) -> str | None:
    """Value at the end of time step ``time`` (after all its updates)."""
    width, times, values = signal
    i = bisect.bisect_right(times, time) - 1
    return values[i] if i >= 0 else None


def _literal(text: str) -> int | None:
    m = _LITERAL.match(text.strip())
    if not m:
        return None
    if m.group(5):
        return int(m.group(5))
    digits = m.group(4).replace("_", "")
    if any(c in "xXzZ" for c in digits):
        return None
    return int(digits, {"b": 2, "o": 8, "d": 10, "h": 16}[m.group(3).lower()])


def parameter_values(source: str) -> dict[int, list[str]]:
    """Numeric-literal parameters by value (to name the values of state registers)."""
    by_value: dict[int, list[str]] = {}
    for decl in _PARAM.findall(source):
        decl = re.sub(r"\[[^\]]*\]", " ", decl)
        for part in decl.split(","):
            if "=" in part:
                name, value = (x.strip() for x in part.split("=", 1))
                name = name.split()[-1] if name.split() else ""
                v = _literal(value)
                if name and v is not None:
                    by_value.setdefault(v, []).append(name)
    return by_value


def show(bits: str, names: Mapping[int, list[str]] | None = None) -> str:
    """Binary up to 8 bits, hex above; a state value also gets its parameter name when unique."""
    if any(b not in "01" for b in bits):
        return bits
    shown = bits if len(bits) <= 8 else "0x" + format(int(bits, 2), f"0{-(-len(bits) // 4)}x")
    named = (names or {}).get(int(bits, 2), [])
    return f"{named[0]} ({shown})" if len(named) == 1 else shown
