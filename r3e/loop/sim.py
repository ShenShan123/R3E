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

from r3e.knowledge.feedback import compare_traces, compile_failure
from r3e.knowledge.schema import VisibleFeedback
from r3e.semantic_repair_bench.oracle_gate import _simulate

from .corpus import Carrier


_FOPEN = re.compile(r'\$fopen\(\s*"([^"]+)"')


@dataclass(frozen=True)
class Verdict:
    tier: str
    feedback: VisibleFeedback
    hidden: str = "not_available"  # not_available | pass | fail | error

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
        return _simulate([dut, *carrier.deps], list(tb_files),
                         self.output_name(tb_files), cell / "sim", timeout)

    def expected(self, carrier: Carrier, which: str) -> str:
        key = (carrier.carrier_id, which)
        if key not in self._expected:
            tb = carrier.visible_tb if which == "visible" else carrier.hidden_tb
            text, err = self.run(carrier.clean_rtl, carrier, tb)
            if text is None:
                raise RuntimeError(f"clean carrier {carrier.carrier_id} fails its {which} testbench: {err[:120]}")
            self._expected[key] = text
        return self._expected[key]

    def verdict(self, rtl: str, carrier: Carrier) -> Verdict:
        observed, err = self.run(rtl, carrier, carrier.visible_tb)
        if observed is None:
            return Verdict("compile_fail", compile_failure(err))
        feedback = compare_traces(self.expected(carrier, "visible"), observed)
        if feedback.divergences:
            return Verdict("visible_fail", feedback)
        if not carrier.hidden_tb:
            return Verdict("visible_pass", feedback)
        hidden_obs, hidden_err = self.run(rtl, carrier, carrier.hidden_tb)
        if hidden_obs is None:
            return Verdict("visible_pass", feedback, hidden="error")
        hidden_fb = compare_traces(self.expected(carrier, "hidden"), hidden_obs)
        if hidden_fb.divergences:
            return Verdict("visible_pass", feedback, hidden="fail")
        return Verdict("hidden_pass", feedback, hidden="pass")
