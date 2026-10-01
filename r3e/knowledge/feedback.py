"""Structured visible-test feedback from expected vs. observed output traces.

The trace format is the comma-separated, per-cycle output table that the
existing differential oracle uses (``semantic_repair_bench/oracle_gate.py``):
a header row, optionally starting with ``time``, then one row per sample.
Bit-split columns such as ``q[3],q[2],...`` are regrouped into buses.

The expected trace is the visible test's expected outputs. It is part of the
visible test the agent is allowed to see. Golden RTL source is never read here.
"""
from __future__ import annotations

import re
from typing import Sequence

from .schema import SignalDivergence, VisibleFeedback


_BUS_RE = re.compile(r"^(.*)\[(\d+)\]$")


def _parse_item(text: str) -> str:
    text = text.strip()
    if len(text) > 1 and text[0] == '"' and text[-1] == '"':
        return text[1:-1].strip()
    return text


def _parse_line(line: str) -> list[str]:
    return [_parse_item(item) for item in line.split(",")]


def parse_trace(text: str) -> tuple[list[str], list[list[str]]]:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if len(lines) < 2:
        return [], []
    header = _parse_line(lines[0])
    rows = [_parse_line(line) for line in lines[1:]]
    if header and header[0].lower() == "time":
        header = header[1:]
        rows = [row[1:] for row in rows]
    return header, rows


def bus_layout(header: Sequence[str]) -> list[tuple[str, list[int]]]:
    groups: dict[str, list[tuple[int, int]]] = {}
    order: list[str] = []
    for column, name in enumerate(header):
        match = _BUS_RE.match(name)
        base = match.group(1) if match else name
        bit = int(match.group(2)) if match else 0
        if base not in groups:
            groups[base] = []
            order.append(base)
        groups[base].append((bit, column))
    return [
        (base, [col for _, col in sorted(groups[base], key=lambda t: -t[0])])
        for base in order
    ]


def _bus_value(row: Sequence[str], cols: Sequence[int]) -> tuple[str, int | None]:
    bits = [row[c].lower() if c < len(row) else "?" for c in cols]
    text = "".join(bits)
    value = int(text, 2) if bits and all(bit in "01" for bit in bits) else None
    return text, value


def _diverges(expected: Sequence[str], observed: Sequence[str], cols: Sequence[int]) -> bool:
    for col in cols:
        want = expected[col].lower() if col < len(expected) else "?"
        got = observed[col].lower() if col < len(observed) else "?"
        if want != "x" and want != got:
            return True
    return False


def classify_symptom(
    expected_rows: Sequence[Sequence[str]],
    observed_rows: Sequence[Sequence[str]],
    cols: Sequence[int],
    first: int,
    *,
    window: int = 8,
) -> tuple[str, int | None]:
    """Classify the divergence window of one bus; return (symptom, first delta)."""
    stop = min(first + window, len(expected_rows), len(observed_rows))
    exp_vals: list[int] = []
    obs_vals: list[int] = []
    unknown = False
    diverging = 0
    for index in range(first, stop):
        _, want = _bus_value(expected_rows[index], cols)
        _, got = _bus_value(observed_rows[index], cols)
        if _diverges(expected_rows[index], observed_rows[index], cols):
            diverging += 1
        if want is None or got is None:
            unknown = unknown or got is None
            continue
        exp_vals.append(want)
        obs_vals.append(got)
    first_delta = (obs_vals[0] - exp_vals[0]) if exp_vals else None
    if unknown and not exp_vals:
        return "x_or_unknown", None
    if not exp_vals:
        return "variable_offset", None
    if diverging == 1 and stop - first > 1:
        return "single_cycle_glitch", first_delta
    deltas = [o - e for o, e in zip(obs_vals, exp_vals)]
    # On a ramp, a constant offset and a one-cycle shift look identical; the
    # value-level explanation is reported first and shifts only when the
    # offset itself varies.
    if len(set(deltas)) == 1 and not unknown:
        return "constant_offset", first_delta
    if len(exp_vals) >= 2:
        # Compare the observed trace against the expected trace shifted by
        # one sample, read from the full rows (not only the window).
        lead = all(
            _bus_value(observed_rows[i], cols)[1]
            == _bus_value(expected_rows[i + 1], cols)[1]
            for i in range(first, stop - 1)
        )
        # the very first sample of a lagging output holds its initial value
        lag_start = max(first, 1)
        lag = stop - lag_start >= 2 and all(
            _bus_value(observed_rows[i], cols)[1]
            == _bus_value(expected_rows[i - 1], cols)[1]
            for i in range(lag_start, stop)
        )
        if lead:
            return "observed_leads_one_cycle", first_delta
        if lag:
            return "observed_lags_one_cycle", first_delta
        if len(set(obs_vals)) == 1 and len(set(exp_vals)) > 1:
            return "stuck_value", first_delta
        exp_steps = {b - a for a, b in zip(exp_vals, exp_vals[1:])}
        obs_steps = {b - a for a, b in zip(obs_vals, obs_vals[1:])}
        if len(exp_steps) == 1 and len(obs_steps) == 1 and exp_steps != obs_steps:
            return "step_rate_mismatch", first_delta
    if len(set(deltas)) == 1:
        return "constant_offset", first_delta
    if unknown:
        return "x_or_unknown", first_delta
    return "variable_offset", first_delta


def compare_traces(
    expected_text: str,
    observed_text: str,
    *,
    max_signals: int = 4,
    window: int = 8,
) -> VisibleFeedback:
    """Build structured feedback from the visible expected/observed traces."""
    header, expected_rows = parse_trace(expected_text)
    obs_header, observed_rows = parse_trace(observed_text)
    if not header or not obs_header:
        return VisibleFeedback(
            oracle_stage="functional",
            compile_ok=True,
            divergences=(),
            total_cycles=0,
        )
    found: list[SignalDivergence] = []
    passing: list[str] = []
    for base, cols in bus_layout(header):
        first = next(
            (
                index
                for index in range(min(len(expected_rows), len(observed_rows)))
                if _diverges(expected_rows[index], observed_rows[index], cols)
            ),
            None,
        )
        if first is None:
            passing.append(base)
            continue
        symptom, delta = classify_symptom(
            expected_rows, observed_rows, cols, first, window=window
        )
        exp_bits, _ = _bus_value(expected_rows[first], cols)
        obs_bits, _ = _bus_value(observed_rows[first], cols)
        found.append(SignalDivergence(
            signal=base,
            width=len(cols),
            first_cycle=first,
            expected=exp_bits,
            observed=obs_bits,
            symptom=symptom,
            delta=delta,
        ))
    if len(expected_rows) != len(observed_rows) and not found:
        found.append(SignalDivergence(
            signal="<trace_length>",
            width=0,
            first_cycle=min(len(expected_rows), len(observed_rows)),
            expected=str(len(expected_rows)),
            observed=str(len(observed_rows)),
            symptom="variable_offset",
        ))
    found.sort(key=lambda item: (item.first_cycle, item.signal))
    return VisibleFeedback(
        oracle_stage="functional",
        compile_ok=True,
        divergences=tuple(found[:max_signals]),
        passing_outputs=tuple(sorted(passing)),
        total_cycles=min(len(expected_rows), len(observed_rows)),
    )


def compile_failure(message: str) -> VisibleFeedback:
    return VisibleFeedback(
        oracle_stage="compile",
        compile_ok=False,
        compile_message=str(message)[:2000],
    )
