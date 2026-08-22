"""Command-line entry point for the R³E-AIC competition version."""
from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .app.backend.health import health
from .services.artifact_service import ArtifactService
from .services.benchmark_service import BenchmarkService
from .services.case_service import CaseCatalog
from .services.diagnosis_service import DiagnosisService
from .services.repair_service import RepairService
from .services.verification_service import VerificationService


ROOT = Path(__file__).resolve().parents[1]


def output_root(value: str | None, prefix: str) -> Path:
    if value:
        return Path(value).resolve()
    return Path(tempfile.mkdtemp(prefix=prefix))


def run_one(root: Path, case_id: str, mode: str, out: Path) -> dict[str, Any]:
    repair = RepairService(root, out)
    verifier = VerificationService(root, out)
    proposals = repair.generate(case_id, mode=mode)
    verified = []
    for candidate in proposals["candidates"]:
        verified.append({
            "candidate": candidate,
            "verification": verifier.verify(
                case_id,
                candidate["replacement_rtl"],
                run_id=f"{mode}-{candidate['id']}",
            ),
        })
    return {"proposals": proposals, "verified_candidates": verified}


def summary_payload(mode: str, out: Path, runs: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for run in runs:
        proposals = run["proposals"]
        accepted = [
            item for item in run["verified_candidates"]
            if item["verification"]["accepted"]
        ]
        rows.append({
            "case_id": proposals["case_id"],
            "mode": mode,
            "provider": proposals["provider"],
            "accepted_candidate_ids": [item["candidate"]["id"] for item in accepted],
            "candidate_results": [
                {
                    "candidate_id": item["candidate"]["id"],
                    "lens": item["candidate"]["lens"],
                    "source_kind": item["candidate"]["source_kind"],
                    "accepted": item["verification"]["accepted"],
                    "result_hash": item["verification"]["result_hash"],
                    "case_evidence": item["verification"]["case_evidence"],
                    "toolchain": item["verification"]["toolchain"],
                }
                for item in run["verified_candidates"]
            ],
        })
    return {
        "schema_version": "r3e-aic-raw-run-summary-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "case_count": len(rows),
        "runs": rows,
        "output_root": str(out),
    }


def print_result(value: Any, full: bool = False) -> None:
    if full:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if "runs" in value:
        print(json.dumps({
            "mode": value["mode"],
            "case_count": value["case_count"],
            "cases": [
                {
                    "case_id": row["case_id"],
                    "accepted_candidate_ids": row["accepted_candidate_ids"],
                    "all_gates_passed_for_one_candidate": bool(row["accepted_candidate_ids"]),
                }
                for row in value["runs"]
            ],
            "raw_summary": value["output_root"] + "/summary.json",
        }, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(prog="r3e-aic")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health")
    sub.add_parser("cases")
    diagnose = sub.add_parser("diagnose")
    diagnose.add_argument("case_id")
    run_case = sub.add_parser("run-case")
    run_case.add_argument("case_id")
    run_case.add_argument("--mode", choices=("demo", "live"), default="demo")
    run_case.add_argument("--output-root")
    run_case.add_argument("--full", action="store_true")
    demo = sub.add_parser("run-demo")
    demo.add_argument("--mode", choices=("demo", "live"), default="demo")
    demo.add_argument("--output-root")
    demo.add_argument("--full", action="store_true")
    sub.add_parser("benchmark")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--output-root")
    args = parser.parse_args()

    if args.command == "health":
        print_result(health(ROOT), full=True)
        return 0
    if args.command == "cases":
        print_result({"cases": CaseCatalog(ROOT).summaries()}, full=True)
        return 0
    if args.command == "benchmark":
        print_result(BenchmarkService(ROOT).dashboard(), full=True)
        return 0
    if args.command == "diagnose":
        result = DiagnosisService(ROOT, output_root(None, "r3e-aic-diagnosis-")).diagnose(args.case_id)
        print_result(result, full=True)
        return 0
    if args.command == "serve":
        from .app.backend.api import main as serve_main
        import sys
        old = sys.argv
        sys.argv = [old[0], "--host", args.host, "--port", str(args.port)]
        if args.output_root:
            sys.argv.extend(["--output-root", args.output_root])
        try:
            return serve_main()
        finally:
            sys.argv = old

    out = output_root(args.output_root, "r3e-aic-demo-")
    if args.command == "run-case":
        result = run_one(ROOT, args.case_id, args.mode, out)
        full_payload = {"mode": args.mode, "output_root": str(out), "run": result}
        ArtifactService(out).record_run(args.case_id, result)
        (out / "summary.json").write_text(json.dumps(full_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print_result(full_payload, full=args.full)
        return 0 if any(item["verification"]["accepted"] for item in result["verified_candidates"]) else 1

    runs = [run_one(ROOT, case.case_id, args.mode, out) for case in CaseCatalog(ROOT).all()]
    payload = summary_payload(args.mode, out, runs)
    for run in runs:
        ArtifactService(out).record_run(run["proposals"]["case_id"], run)
    (out / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print_result(payload, full=args.full)
    return 0 if all(row["accepted_candidate_ids"] for row in payload["runs"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
