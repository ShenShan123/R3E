"""Red-only executable repair hypotheses on frozen visible tests.

This is a necessary observability check, not a Blue outcome or semantic proof
that a model-supplied repair leaves the claimed mechanism. No hidden tests,
duplicate state, scoring, or model calls are involved.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess

from r3e.protocol.hashing import hash_payload

TARGET_CHECK_VERSION = "predicted_repair_visible_v1"


class TargetObservabilityCheck:
    def __init__(self, simulator):
        self.simulator = simulator
        self._baselines = {}

    def _loop_check(self, rtl, carrier, directory):
        directory.mkdir(parents=True, exist_ok=True)
        source = directory / "dut.sv"
        source.write_text(rtl, encoding="utf-8")
        files = " ".join(json.dumps(str(Path(p).resolve())) for p in (source, *carrier.deps))
        command = f"read_verilog -sv {files}; hierarchy -check -top {carrier.top_module}; proc; flatten; check"
        # Do not treat unsupported syntax, missing tools, or timeouts as proof
        # of a functional residual. Infrastructure exceptions propagate.
        result = subprocess.run(["yosys", "-q", "-p", command], capture_output=True,
                                text=True, timeout=120)
        if result.returncode:
            return "analysis_unavailable"
        return "logic_loop" if "logic loop" in (result.stdout + result.stderr).lower() else "ok"

    def check(self, carrier, mutant, predicted):
        visible = replace(carrier, hidden_tb=())
        inputs = {"clean_rtl_hash": hash_payload(carrier.clean_rtl),
                  "spec_hash": hash_payload(carrier.spec), "top_module": carrier.top_module,
                  "visible_test_hashes": [hash_payload(p.read_text()) for p in carrier.visible_tb],
                  "dependency_hashes": [hash_payload(p.read_text()) for p in carrier.deps],
                  "sim_timeout": carrier.sim_timeout}
        baseline_key = hash_payload(inputs)
        identity = hash_payload({"inputs": inputs, "mutant": mutant, "predicted": predicted})
        root = self.simulator.workspace / "red_target_check"
        record = {"version": TARGET_CHECK_VERSION, "inputs": inputs,
                  "mutant_hash": hash_payload(mutant), "predicted_repair_hash": hash_payload(predicted),
                  "test_scope": "frozen_visible_only", "blue_calls": 0,
                  "interpretation": "hypothetical residual; not a Blue failure or confirmed mechanism"}
        if baseline_key not in self._baselines:
            loop = self._loop_check(carrier.clean_rtl, visible, root / baseline_key[7:] / "clean")
            tier = self.simulator.verdict(carrier.clean_rtl, visible).tier if loop == "ok" else None
            self._baselines[baseline_key] = {"loop_check": loop, "verdict": tier}
        record["baseline"] = self._baselines[baseline_key]
        if record["baseline"] != {"loop_check": "ok", "verdict": "visible_pass"}:
            return {**record, "status": "inconclusive", "reason": "target_check_baseline_unqualified"}
        loop = self._loop_check(predicted, visible, root / identity[7:] / "predicted")
        record["loop_check"] = loop
        if loop != "ok":
            return {**record, "status": "inconclusive" if loop == "analysis_unavailable" else "rejected",
                    "reason": "predicted_repair_" + loop}
        verdict = self.simulator.verdict(predicted, visible)
        record.update(verdict=verdict.tier, visible_feedback_hash=verdict.feedback.feedback_hash)
        if verdict.tier == "compile_fail":
            return {**record, "status": "rejected", "reason": "predicted_repair_not_executable"}
        if verdict.visible_ok:
            return {**record, "status": "rejected", "reason": "target_not_observable_after_predicted_repair"}
        if verdict.tier != "visible_fail":
            return {**record, "status": "inconclusive", "reason": "predicted_repair_unknown_verdict"}
        return {**record, "status": "residual_observed", "reason": "admitted"}
