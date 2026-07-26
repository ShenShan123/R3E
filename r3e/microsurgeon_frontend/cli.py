#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from microsurgeon_frontend.schemas import load_designs_jsonl
from microsurgeon_frontend.frontend_gate import run_frontend_gate
from microsurgeon_frontend.handoff_writer import write_backend_handoff


def main():
    parser = argparse.ArgumentParser("MicroSurgeon frontend rebuild v0")
    parser.add_argument("--designs", required=True, help="Input design JSONL")
    parser.add_argument("--out-dir", required=True, help="Output directory")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    logs_dir = out_dir / "logs"
    results_dir = out_dir / "results"

    logs_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    designs = load_designs_jsonl(args.designs)

    results = []

    for i, d in enumerate(designs, 1):
        print("=" * 100)
        print(f"[FRONTEND v0] {i}/{len(designs)} {d.design_name}")
        print(f"top={d.top_module}, clk={d.resolved_clock_port}, files={len(d.rtl_files)}")

        r, _meta = run_frontend_gate(d, logs_dir)
        results.append(r)

        print(
            f"file={r.file_exists_ok} "
            f"iverilog={r.iverilog_ok} "
            f"yosys={r.yosys_ok} "
            f"contract={r.backend_contract_ok} "
            f"class={r.failure_class}"
        )

    summary_path = results_dir / "frontend_gate_summary.jsonl"
    with summary_path.open("w") as f:
        for r in results:
            f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")

    handoff_path = results_dir / "backend_handoff_manifest.jsonl"
    handoff_count = write_backend_handoff(designs, results, handoff_path)

    counter = Counter(r.failure_class for r in results)

    report_path = out_dir / "reports" / "frontend_gate_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    with report_path.open("w") as f:
        f.write("# Frontend Rebuild v0 Gate Report\n\n")
        f.write(f"- Input designs: {len(designs)}\n")
        f.write(f"- Backend handoff ready: {handoff_count}\n\n")
        f.write("## Failure Classes\n\n")
        for k, v in counter.most_common():
            f.write(f"- {k}: {v}\n")

    print("\nDone.")
    print(f"summary: {summary_path}")
    print(f"handoff: {handoff_path}")
    print(f"report : {report_path}")
    print(f"handoff_ready = {handoff_count}/{len(designs)}")


if __name__ == "__main__":
    main()
