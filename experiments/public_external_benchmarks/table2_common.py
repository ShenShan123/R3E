#!/usr/bin/env python3
"""Shared, state-free primitives for the frozen Table-2 comparison runs."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
TABLE2_SCHEMA = "r3e-table2-public-comparison-v1"
VCS_PTHREAD_SHIM_SOURCE = Path(__file__).resolve().parent / "compat" / "vcs_pthread_yield_shim.c"
VCS_PTHREAD_SHIM_OBJECT = ROOT / ".cross_benchmark_r3e_202607" / "qualification" / "vcs" / "vcs_pthread_yield_shim.o"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def sha256_payload(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode())


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def git_state(path: Path = ROOT) -> dict:
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=path, text=True).strip())
    return {"commit": commit, "dirty_tree": dirty}


def file_record(role: str, path: Path) -> dict:
    path = path.resolve()
    return {
        "role": role,
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def assert_no_state_features() -> None:
    forbidden = {
        "ALLOW_REAL_MEMORY_WRITE": "1",
        "PUBLIC_EXTERNAL_NO_MEMORY_WRITE": "0",
    }
    bad = [name for name, value in forbidden.items() if os.environ.get(name) == value]
    if bad:
        raise RuntimeError(f"stateful feature enabled: {bad}")
    os.environ.update({
        "ALLOW_REAL_MEMORY_WRITE": "0",
        "PUBLIC_EXTERNAL_NO_MEMORY_WRITE": "1",
        "TABLE2_RED_ENABLED": "0",
        "TABLE2_MEMORY_RETRIEVAL": "0",
        "TABLE2_REGISTRY_ENABLED": "0",
        "TABLE2_TEMPLATE_ENABLED": "0",
        "TABLE2_PROMOTION_ENABLED": "0",
        "TABLE2_CROSS_CASE_STATE": "0",
    })


def _run(command: list[str], cwd: Path, timeout: float, log_path: Path) -> dict:
    started = time.time()
    try:
        cp = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        output = (cp.stdout or "") + (cp.stderr or "")
        log_path.write_text(output)
        return {
            "ok": cp.returncode == 0,
            "exit_code": cp.returncode,
            "timed_out": False,
            "latency_sec": round(time.time() - started, 3),
            "log_path": str(log_path),
            "log_excerpt": output[-2000:],
        }
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or "") if isinstance(exc.stdout, str) else "") + ((exc.stderr or "") if isinstance(exc.stderr, str) else "")
        log_path.write_text(output + "\nTIMEOUT\n")
        return {
            "ok": False,
            "exit_code": 124,
            "timed_out": True,
            "latency_sec": round(time.time() - started, 3),
            "log_path": str(log_path),
            "log_excerpt": (output + "\nTIMEOUT")[-2000:],
        }


def ensure_vcs_compat_object() -> Path:
    """Build the audited one-function compatibility object for VCS 2018."""
    VCS_PTHREAD_SHIM_OBJECT.parent.mkdir(parents=True, exist_ok=True)
    needs_build = (not VCS_PTHREAD_SHIM_OBJECT.is_file() or
                   VCS_PTHREAD_SHIM_OBJECT.stat().st_mtime < VCS_PTHREAD_SHIM_SOURCE.stat().st_mtime)
    if needs_build:
        cp = subprocess.run(
            ["gcc", "-c", "-fPIC", "-o", str(VCS_PTHREAD_SHIM_OBJECT), str(VCS_PTHREAD_SHIM_SOURCE)],
            capture_output=True, text=True,
        )
        if cp.returncode != 0:
            raise RuntimeError(f"failed to build VCS compatibility object: {cp.stderr[-2000:]}")
    return VCS_PTHREAD_SHIM_OBJECT


def parse_csv(text: str) -> tuple[list[str], list[list[str]]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return [], []
    return ([x.strip().strip('"') for x in lines[0].split(",")],
            [[x.strip().strip('"') for x in line.split(",")] for line in lines[1:]])


def compare_outputs(golden: Path, candidate: Path) -> dict:
    if not golden.is_file() or not candidate.is_file():
        return {"ok": False, "reason": "missing_output", "mismatches": 0}
    gh, gr = parse_csv(golden.read_text(errors="replace"))
    ch, cr = parse_csv(candidate.read_text(errors="replace"))
    if gh != ch:
        return {"ok": False, "reason": "header_diff", "mismatches": 1}
    mismatches: list[str] = []
    for row_index, (grow, crow) in enumerate(zip(gr, cr)):
        for col_index, (expected, actual) in enumerate(zip(grow, crow)):
            if expected.lower() != "x" and expected.lower() != actual.lower():
                name = gh[col_index] if col_index < len(gh) else str(col_index)
                mismatches.append(f"{name}@{row_index}:{actual}!={expected}")
                if len(mismatches) >= 6:
                    break
        if len(mismatches) >= 6:
            break
    if len(cr) < len(gr):
        mismatches.append(f"output_truncated:{len(cr)}<{len(gr)}")
    return {
        "ok": not mismatches,
        "reason": "" if not mismatches else "oracle_mismatch",
        "golden_rows": len(gr),
        "candidate_rows": len(cr),
        "mismatches": len(mismatches),
        "mismatch_examples": mismatches,
    }


def simulate_literature_case(case: dict, rtl_path: Path, run_dir: Path, tag: str) -> dict:
    """Run one Literature-32 candidate with its frozen native simulator.

    RTL-Repair's published validation flow uses VCS as the primary simulator and
    treats Icarus as a best-effort secondary compatibility check.  Keeping the
    simulator selectable here is an evaluator adapter; candidate generation is
    unaffected.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    source_paths = [rtl_path] + [Path(path) for path in case.get("deps", [])]
    tb_paths = [Path(path) for path in case.get("tb_sources", [])]
    assets = [Path(path) for path in case.get("sim_assets", [])]
    local_sources: list[str] = []
    for src in source_paths + tb_paths:
        dst = run_dir / src.name
        shutil.copy2(src, dst)
        local_sources.append(dst.name)
    for asset in assets:
        shutil.copy2(asset, run_dir / asset.name)
    simulator = case.get("simulator", "iverilog")
    compile_timeout = float(case.get("compile_timeout", 600.0))
    sim_timeout = float(case.get("sim_timeout", 10.0))
    if simulator == "vcs":
        executable = f"{tag}_simv"
        shim = ensure_vcs_compat_object()
        compile_command = [
            "vcs", "-sverilog", "-full64", "-timescale=1ns/1ps",
            "-LDFLAGS", str(shim), "-o", executable, *local_sources,
        ]
        simulation_command = [f"./{executable}"]
    elif simulator == "iverilog":
        executable = f"{tag}.out"
        compile_command = ["iverilog", "-g2012", "-o", executable, *local_sources]
        simulation_command = ["vvp", executable]
    else:
        raise ValueError(f"unsupported frozen simulator: {simulator}")
    compile_result = _run(
        compile_command, run_dir, compile_timeout, run_dir / f"{tag}_compile.log",
    )
    if not compile_result["ok"]:
        return {**compile_result, "stage": "compile", "compile_ok": False, "simulation_ok": False, "oracle_ok": False}
    sim_result = _run(
        simulation_command, run_dir, sim_timeout, run_dir / f"{tag}_sim.log",
    )
    output = run_dir / case["tb_output"]
    return {
        **sim_result,
        "stage": "simulation" if not sim_result["ok"] else "produced_output",
        "compile_ok": True,
        "simulation_ok": bool(sim_result["ok"] and output.is_file()),
        "oracle_ok": False,
        "simulator": simulator,
        "output_path": str(output),
    }


def differential_gate(case: dict, candidate_rtl: Path, work_dir: Path) -> dict:
    golden_dir = work_dir / "golden"
    candidate_dir = work_dir / "candidate"
    golden = simulate_literature_case(case, Path(case["golden_rtl"]), golden_dir, "golden")
    if not golden["simulation_ok"]:
        return {**golden, "stage": "golden_gate", "failure_reason": golden.get("log_excerpt") or "golden_failed"}
    candidate = simulate_literature_case(case, candidate_rtl, candidate_dir, "candidate")
    if not candidate["simulation_ok"]:
        return {**candidate, "stage": "candidate_gate", "failure_reason": candidate.get("log_excerpt") or "candidate_failed"}
    compare = compare_outputs(Path(golden["output_path"]), Path(candidate["output_path"]))
    return {
        "ok": compare["ok"],
        "compile_ok": True,
        "simulation_ok": True,
        "oracle_ok": compare["ok"],
        "stage": "compare",
        "oracle_compare": compare,
        "failure_reason": None if compare["ok"] else canonical_json(compare),
        "golden_log_path": golden["log_path"],
        "candidate_log_path": candidate["log_path"],
    }


def temporary_dir(parent: Path, prefix: str) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
