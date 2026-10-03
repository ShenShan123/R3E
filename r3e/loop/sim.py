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

from r3e.knowledge.feedback import compare_traces, compile_failure, evidence_window
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

    def verdict(self, rtl: str, carrier: Carrier) -> Verdict:
        observed, err = self.run(rtl, carrier, carrier.visible_tb)
        if observed is None:
            return Verdict("compile_fail", compile_failure(err))
        feedback = compare_traces(self.expected(carrier, "visible"), observed)
        if feedback.divergences:
            window = evidence_window(self.expected(carrier, "visible"), observed,
                                     self._stimulus.get(carrier.carrier_id))
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
