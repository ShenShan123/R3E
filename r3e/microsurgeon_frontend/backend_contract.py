from pathlib import Path
from typing import Tuple, List
from .schemas import DesignInput


def check_backend_contract(design: DesignInput) -> Tuple[bool, List[str]]:
    reasons = []

    if not design.design_name:
        reasons.append("missing design_name")

    if not design.top_module:
        reasons.append("missing top_module")

    if not design.resolved_clock_port:
        reasons.append("missing clock_port")

    if not design.rtl_files:
        reasons.append("missing rtl_files")

    for p in design.rtl_files:
        if not Path(p).exists():
            reasons.append(f"missing rtl file: {p}")

    if design.clock_period <= 0:
        reasons.append("clock_period must be positive")

    return len(reasons) == 0, reasons


def infer_suspected_patterns(yosys_log: str) -> List[str]:
    t = yosys_log.lower()
    out = []

    if "xor" in t or "xnor" in t:
        out.append("xor_heavy")

    if "mux" in t:
        out.append("mux_heavy")

    if "assign" in t and "always" in t:
        out.append("combinational_driver_candidate")

    return sorted(set(out))
