"""Runner-owned simulation verdicts in tiers.

Expected traces come from simulating the carrier's clean RTL under its
testbench. The clean RTL is a runner asset; Blue never sees it. Blue sees only
the structured comparison (``VisibleFeedback``) for the *visible* testbench.

A hidden testbench, when present, is used only for the verdict tier. Its
feedback is never returned to Blue, memory qualification or stopping
decisions.

Tiers: ``compile_fail`` < ``visible_fail`` < ``visible_pass`` < ``hidden_pass``.
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from r3e.knowledge.feedback import _bus_value, bus_layout, compare_traces, compile_failure, evidence_window, parse_trace
from r3e.knowledge.register_trace import VCD_NAME, cone_registers, inject_dump, parameter_values, parse_vcd, show, value_at
from r3e.knowledge.schema import VisibleFeedback

from .corpus import Carrier
from .icarus import simulate


_FOPEN = re.compile(r'\$fopen\(\s*"([^"]+)"')
_STIMULUS = re.compile(r'\$fopen\(\s*"(stimulus_[^"]+)"')
STEALTH_MAX_CYCLES = 4000


@dataclass(frozen=True)
class Verdict:
    tier: str
    feedback: VisibleFeedback
    hidden: str = "not_available"  # not_available | pass | fail | error
    window: dict = field(default_factory=dict)  # visible-test cycles around the first mismatches

    @property
    def visible_ok(self) -> bool:
        return self.tier in {"visible_pass", "hidden_pass"}

    def record(self) -> dict[str, Any]:
        return {"tier": self.tier, "hidden": self.hidden,
                "feedback_hash": self.feedback.feedback_hash}


@dataclass
class Simulator:
    workspace: Path
    _expected: dict[tuple[str, str], str] = field(default_factory=dict)
    _stimulus: dict[str, str] = field(default_factory=dict)
    _counter: itertools.count = field(default_factory=itertools.count)

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def output_name(tb_files: tuple[Path, ...]) -> str:
        text = "".join(Path(p).read_text(encoding="utf-8", errors="ignore") for p in tb_files)
        match = _FOPEN.search(text)
        if not match:
            raise ValueError("testbench does not open an output trace file")
        return match.group(1)

    def run(self, rtl: str, carrier: Carrier, tb_files: tuple[Path, ...]) -> tuple[str | None, str]:
        index = next(self._counter)
        cell = self.workspace / f"run_{index:06d}"
        src_dir = cell / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        dut = src_dir / "r3e_dut.v"
        dut.write_text(rtl, encoding="utf-8")
        timeout = max(20.0, 3.0 * float(carrier.sim_timeout))
        self._last_cell = cell
        return simulate([dut, *carrier.deps], list(tb_files),
                         self.output_name(tb_files), cell / "sim", timeout)

    @staticmethod
    def stimulus_name(tb_files: tuple[Path, ...]) -> str | None:
        text = "".join(Path(p).read_text(encoding="utf-8", errors="ignore") for p in tb_files)
        match = _STIMULUS.search(text)
        return match.group(1) if match else None

    def expected(self, carrier: Carrier, which: str) -> str:
        key = (carrier.carrier_id, which)
        if key not in self._expected:
            tb = carrier.visible_tb if which == "visible" else carrier.hidden_tb
            text, err = self.run(carrier.clean_rtl, carrier, tb)
            if text is None:
                raise RuntimeError(f"clean carrier {carrier.carrier_id} fails its {which} testbench: {err[:120]}")
            self._expected[key] = text
            name = self.stimulus_name(tb) if which == "visible" else None
            stim = self._last_cell / "sim" / name if name else None
            if stim is not None and stim.is_file():  # inputs do not depend on the design under test
                self._stimulus[carrier.carrier_id] = stim.read_text(encoding="utf-8", errors="ignore")
        return self._expected[key]

    def stealth(self, rtl: str, carrier: Carrier) -> dict[str, Any]:
        """How visible a bug is on the visible test (runner-side; never shown to Blue).

        ``mismatch_fraction`` is the share of sampled cycles with any wrong
        output bit; a lower value and a later ``first_mismatch`` mean a
        stealthier bug.
        """
        observed, err = self.run(rtl, carrier, carrier.visible_tb)
        if observed is None:
            return {"ok": False}
        # Bounded sample: large testbenches (e.g. CirFix sdram) emit long traces,
        # and ranking candidates only needs a representative prefix.
        exp_lines = [l for l in self.expected(carrier, "visible").splitlines() if l.strip()][1:STEALTH_MAX_CYCLES + 1]
        obs_lines = [l for l in observed.splitlines() if l.strip()][1:STEALTH_MAX_CYCLES + 1]
        n = min(len(exp_lines), len(obs_lines))
        bad = []
        for i in range(n):
            if exp_lines[i] == obs_lines[i]:
                continue
            exp_cells = [c.strip().strip('"').lower() for c in exp_lines[i].split(",")]
            obs_cells = [c.strip().strip('"').lower() for c in obs_lines[i].split(",")]
            obs_cells += ["?"] * (len(exp_cells) - len(obs_cells))
            if any(e != "x" and e != o for e, o in zip(exp_cells[1:], obs_cells[1:])):
                bad.append(i)
        if len(exp_lines) != len(obs_lines) and n < STEALTH_MAX_CYCLES and not bad:
            bad.append(n)
        return {"ok": True, "cycles": n, "mismatch_cycles": len(bad),
                "mismatch_fraction": (len(bad) / n) if n else 1.0,
                "first_mismatch": bad[0] if bad else None,
                "sampled_prefix": n == STEALTH_MAX_CYCLES}

    def register_samples(self, rtl: str, carrier: Carrier, failing: list[str],
                         observed: str) -> dict[int, dict[str, str]]:
        """The design's register values per sample of ``observed`` (``register_trace.py``).

        Empty when there are no registers to show, the dump does not compile,
        or the dumped failing outputs disagree with the logged ones anywhere
        (sampling not aligned with register updates).
        """
        registers, state = cone_registers(rtl, failing)
        lines = [l for l in observed.splitlines() if l.strip()]
        if not registers or len(lines) < 2 or lines[0].split(",")[0].strip().lower() != "time":
            return {}
        try:
            times = [int(float(l.split(",")[0])) for l in lines[1:]]
        except ValueError:
            return {}
        header, rows = parse_trace(observed)
        layout = {name: cols for name, cols in bus_layout(header) if name in failing}
        dumped = inject_dump(rtl, carrier.top_module, registers, list(layout))
        if dumped is None:
            return {}
        text, _ = self.run(dumped, carrier, carrier.visible_tb)
        vcd = self._last_cell / "sim" / VCD_NAME
        if text is None or not vcd.is_file():
            return {}
        signals = parse_vcd(vcd.read_text(errors="ignore"))
        if not layout or any(name not in signals for name in layout) or any(r not in signals for r in registers):
            return {}
        # The trace's time unit may differ from the dump's resolution, and a
        # testbench logs either at the end of a time step ($fstrobe) or before
        # that step's updates ($fdisplay ahead of new inputs): the dump gives
        # the value at the end of a step, so "before" reads the previous step.
        # Take the first reading that reproduces every logged output exactly.
        def reading(scale: int, before: bool):
            return lambda signal, t: value_at(signal, t * scale - before)
        for at in (reading(scale, before) for before in (False, True)
                   for scale in (1, 10, 100, 1000, 10000, 100000, 1000000)):
            if all(at(signals[name], t) == _bus_value(row, cols)[0]
                   for t, row in zip(times, rows) for name, cols in layout.items()):
                break
        else:
            return {}
        names = parameter_values(rtl)
        return {i: {r: show(at(signals[r], t) or "x", names if r in state else None) for r in registers}
                for i, t in enumerate(times[:len(rows)])}

    def verdict(self, rtl: str, carrier: Carrier, *, registers: bool = False) -> Verdict:
        """``registers``: add the design's register values to the evidence window rows."""
        observed, err = self.run(rtl, carrier, carrier.visible_tb)
        if observed is None:
            return Verdict("compile_fail", compile_failure(err))
        feedback = compare_traces(self.expected(carrier, "visible"), observed)
        if feedback.divergences:
            samples = (self.register_samples(rtl, carrier, [d.signal for d in feedback.divergences], observed)
                       if registers else None)
            window = evidence_window(self.expected(carrier, "visible"), observed,
                                     self._stimulus.get(carrier.carrier_id), registers=samples)
            return Verdict("visible_fail", feedback, window=window)
        if not carrier.hidden_tb:
            return Verdict("visible_pass", feedback)
        hidden_obs, hidden_err = self.run(rtl, carrier, carrier.hidden_tb)
        if hidden_obs is None:
            return Verdict("visible_pass", feedback, hidden="error")
        hidden_fb = compare_traces(self.expected(carrier, "hidden"), hidden_obs)
        if hidden_fb.divergences:
            return Verdict("visible_pass", feedback, hidden="fail")
        return Verdict("hidden_pass", feedback, hidden="pass")
