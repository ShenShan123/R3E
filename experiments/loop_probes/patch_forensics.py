"""Rebuild what Blue changed in each attempt of a probe, from its simulation workspace.

Every simulated candidate is kept under ``<probe>/sim/run_*/src/r3e_dut.v`` (or
``<probe>/sim_workspace``). Each run is matched to its bug, and its diff against
the buggy design is printed. A run equal to the buggy design is the initial
check; one equal to the clean design right after it is the expected-trace run.
No model calls.

Usage: python -m experiments.loop_probes.patch_forensics PROBE_DIR --source-run RUN --tests RC_a RC_b
"""
from __future__ import annotations

import argparse
import difflib
from pathlib import Path

from .common import all_carriers, rebuild, red_rows


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("probe_dir", type=Path)
    p.add_argument("--source-run", type=Path, required=True)
    p.add_argument("--tests", nargs="+", required=True)
    args = p.parse_args()
    cars, rows = all_carriers(), red_rows(args.source_run)
    cases = {cid: rebuild(rows[cid], cars) for cid in args.tests}
    workspace = next((d for d in (args.probe_dir / "sim", args.probe_dir / "sim_workspace") if d.is_dir()), None)
    if workspace is None:
        print("no simulation workspace found")
        return 1
    for run in sorted(workspace.glob("run_*")):
        dut = (run / "src" / "r3e_dut.v").read_text()
        cid = max(cases, key=lambda c: difflib.SequenceMatcher(None, cases[c].buggy_rtl, dut).quick_ratio())
        buggy, clean = cases[cid].buggy_rtl, cases[cid].carrier.clean_rtl
        tb = sorted(f.name for f in (run / "sim").glob("tb_*"))
        if dut == buggy:
            print(f"\n{run.name} {cid} {tb}: initial check of the buggy design")
            continue
        print(f"{run.name} {cid} {tb}{' == CLEAN' if dut.strip() == clean.strip() else ''}")
        diff = [l for l in difflib.unified_diff(buggy.splitlines(), dut.splitlines(), lineterm="", n=0)
                if l[:1] in "+-" and not l.startswith(("+++", "---"))]
        for line in diff[:8]:
            print("     ", line[:150])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
