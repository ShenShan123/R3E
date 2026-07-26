import json
from pathlib import Path
from typing import Iterable
from .schemas import DesignInput
from .frontend_gate import FrontendGateResult


def write_backend_handoff(
    designs: Iterable[DesignInput],
    results: Iterable[FrontendGateResult],
    out_path: Path,
):
    by_name = {d.design_name: d for d in designs}

    out_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with out_path.open("w") as f:
        for r in results:
            if not r.handoff_ready:
                continue

            d = by_name[r.design_name]

            obj = {
                "design_name": d.design_name,
                "family": d.family,
                "top_module": d.top_module,
                "clock_port": d.resolved_clock_port,
                "clock_period": d.clock_period,
                "rtl_files": d.rtl_files,
                "status": "frontend_ready",
                "frontend_pass": True,
                "iverilog_ok": r.iverilog_ok,
                "yosys_ok": r.yosys_ok,
                "backend_contract_ok": r.backend_contract_ok,
                "suspected_patterns": r.suspected_patterns,
            }

            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
            count += 1

    return count
