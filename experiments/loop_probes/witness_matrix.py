"""Witness-suite qualification of one carrier package (measurement side; no model calls).

A carrier package is a directory::

    carrier.json            identity, provenance, top, files, editable scope, suites, variants
    spec.md                 the specification Blue and Red will see
    src/<rel>               the clean implementation (the files listed in ``rtl``) and any
                            ``header_dependencies``: included files, never compile units, never editable
    suites/<suite>/<file>   self-checking testbenches (W_bug, W_preserve)
    variants/<name>/<rel>   overlays of editable files (absent for ``clean``)

Every suite runs on every variant, twice (determinism). A cell is ``pass`` (exit 0, pass marker, no fail
marker), ``fail`` (fail marker), ``compile_fail``, ``timeout``, or ``inconclusive`` (anything else). The
package qualifies only if each variant shows its role's expected row::

    clean     W_bug pass  W_preserve pass
    defect    W_bug fail  W_preserve pass
    shortcut  W_bug pass  W_preserve fail

This checks the task construction and the verification chain only: it is no evidence about Red generation
or Blue weaknesses. The record hashes every input so later stages can refuse a changed package.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from r3e.loop.repo_blue import _run
from r3e.protocol.hashing import hash_payload

EXPECTED = {"clean": {"W_bug": "pass", "W_preserve": "pass"},
            "defect": {"W_bug": "fail", "W_preserve": "pass"},
            "shortcut": {"W_bug": "pass", "W_preserve": "fail"}}
VERSION = "witness_matrix_v1"


def _design_output(log: str) -> str:
    """The run's own output, without Verilator's end-of-run report (wall time, memory)."""
    return "\n".join(l for l in log.splitlines() if not l.startswith("- "))


FROZEN_FIELDS = ("carrier_id", "top_module", "rtl", "editable", "parameters", "tool", "defines", "include_dirs",
                 "pass_marker", "fail_marker", "build_timeout", "sim_timeout")


class FreezeMismatch(RuntimeError):
    pass


def frozen_fields(man: dict) -> dict[str, Any]:
    """The manifest fields a suite freeze covers (variants and status fields may change after it)."""
    out = {k: man.get(k) for k in FROZEN_FIELDS}
    if "header_dependencies" in man:  # only when declared, so packages without headers keep their freeze hashes
        out["header_dependencies"] = man["header_dependencies"]
    out["suites"] = {s: {k: v for k, v in man["suites"][s].items() if k in ("files", "top")} for s in man["suites"]}
    return out


def check_freeze(pkg: Path, freeze: dict[str, Any]) -> None:
    """Refuse a package whose frozen files or manifest fields changed since the suite freeze."""
    now = package_files(pkg)
    problems = [f"{rel} changed or missing" for rel, h in freeze["files"].items() if now.get(rel) != h]
    if hash_payload(frozen_fields(load(pkg))) != freeze["frozen_manifest_fields_hash"]:
        problems.append("frozen manifest fields changed")
    if problems:
        raise FreezeMismatch("; ".join(problems))


def package_files(pkg: Path) -> dict[str, str]:
    """Content hashes of every file in the package (the freeze identity)."""
    return {str(f.relative_to(pkg)): hash_payload(f.read_text(encoding="utf-8"))
            for f in sorted(pkg.rglob("*")) if f.is_file()}


def load(pkg: Path) -> dict[str, Any]:
    man = json.loads((pkg / "carrier.json").read_text())
    for rel in man["rtl"]:
        if not (pkg / "src" / rel).is_file():
            raise ValueError(f"listed RTL file missing: {rel}")
    if not set(man["editable"]) <= set(man["rtl"]):
        raise ValueError("the editable scope must be listed RTL files")
    headers = man.get("header_dependencies", [])
    if set(headers) & set(man["rtl"]) or len(set(headers)) != len(headers):
        raise ValueError("header dependencies are distinct from the compiled RTL files")
    for rel in headers:
        if not (pkg / "src" / rel).is_file():
            raise ValueError(f"declared header missing: {rel}")
    if set(man["suites"]) != {"W_bug", "W_preserve"}:
        raise ValueError("a carrier has exactly the suites W_bug and W_preserve")
    for name, v in man["variants"].items():
        if v["role"] not in EXPECTED:
            raise ValueError(f"{name}: unknown role {v['role']}")
        vdir = pkg / "variants" / name
        overlay = [str(f.relative_to(vdir)) for f in vdir.rglob("*") if f.is_file()] if vdir.exists() else []
        if v["role"] == "clean" and overlay:
            raise ValueError(f"{name}: the clean variant has no overlay")
        if v["role"] != "clean" and not overlay:
            raise ValueError(f"{name}: an empty overlay is the clean implementation")
        if set(overlay) - set(man["editable"]):
            raise ValueError(f"{name}: overlay outside the editable scope: {sorted(set(overlay) - set(man['editable']))}")
    return man


def _commands(man: dict, suite: dict, rtl: list[str], tb: list[str]) -> tuple[list[str], list[str]]:
    inc = [f"-I{d}" for d in man.get("include_dirs", [])]
    if man["tool"] == "icarus":
        defs = [f"-D{k}={v}" for k, v in man.get("defines", {}).items()]
        return (["iverilog", "-g2012", "-s", suite["top"], "-o", "sim.vvp", *inc, *defs, *rtl, *tb], ["vvp", "-n", "sim.vvp"])
    if man["tool"] == "verilator":
        defs = [f"+define+{k}={v}" for k, v in man.get("defines", {}).items()]
        return (["verilator", "--binary", "--timing", "-Wno-fatal", "--top-module", suite["top"], "-Mdir", "obj",
                 "-o", "sim", *inc, *defs, *rtl, *tb], ["obj/sim"])
    raise ValueError(f"unknown tool {man['tool']}")


def run_cell(pkg: Path, man: dict, suite_name: str, variant: str, *, runs: int = 2) -> dict[str, Any]:
    suite = man["suites"][suite_name]
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        shutil.copytree(pkg / "src", work, dirs_exist_ok=True)
        vdir = pkg / "variants" / variant
        if vdir.exists():
            shutil.copytree(vdir, work, dirs_exist_ok=True)
        tb = []
        for f in suite["files"]:
            shutil.copy2(pkg / "suites" / suite_name / f, work / f"tb_{f}")
            tb.append(f"tb_{f}")
        build, sim = _commands(man, suite, man["rtl"], tb)
        t0 = time.monotonic()
        rc, out = _run(build, work, man.get("build_timeout", 600))
        cell: dict[str, Any] = {"build_seconds": round(time.monotonic() - t0, 2)}
        if rc != 0:
            return {**cell, "tier": "timeout" if rc is None else "compile_fail", "log": out[-1500:]}
        tiers, logs = [], []
        for _ in range(runs):
            rc, out = _run(sim, work, man.get("sim_timeout", 120))
            passed = rc == 0 and man["pass_marker"] in out and man["fail_marker"] not in out
            tiers.append("timeout" if rc is None else "pass" if passed else
                         "fail" if man["fail_marker"] in out else "inconclusive")
            logs.append(out)
        same = len(set(tiers)) == 1 and len({hash_payload(_design_output(l)) for l in logs}) == 1
        return {**cell, "tier": tiers[0] if same else "nondeterministic", "runs": tiers,
                "log_hash": hash_payload(_design_output(logs[0])), "deterministic": same, "log": logs[0][-1500:]}


def tool_version(tool: str) -> str:
    cmd = ["iverilog", "-V"] if tool == "icarus" else ["verilator", "--version"]
    return subprocess.run(cmd, capture_output=True, text=True).stdout.splitlines()[0]


def qualify(pkg: Path, freeze: dict[str, Any] | None = None) -> dict[str, Any]:
    pkg = pkg.resolve()
    man = load(pkg)
    if freeze is not None:
        check_freeze(pkg, freeze)
    cells, problems = {}, []
    for variant, v in man["variants"].items():
        for suite in ("W_bug", "W_preserve"):
            cell = run_cell(pkg, man, suite, variant)
            cells[f"{variant}/{suite}"] = cell
            want = EXPECTED[v["role"]][suite]
            if cell["tier"] != want:
                problems.append(f"{variant} ({v['role']}) {suite}: expected {want}, got {cell['tier']}")
    roles = {v["role"] for v in man["variants"].values()}
    problems += [f"no {r} variant" for r in EXPECTED if r not in roles]
    return {"version": VERSION, "carrier_id": man["carrier_id"], "qualified": not problems, "problems": problems,
            "freeze_checked": freeze is not None,
            "single_file": len(man["editable"]) == 1, "tool": tool_version(man["tool"]),
            "package_files": package_files(pkg),
            "matrix": {k: {kk: vv for kk, vv in c.items() if kk != "log"} for k, c in cells.items()},
            "logs": {k: c["log"] for k, c in cells.items()}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("package", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--freeze", type=Path, help="suite freeze record; the package must still match it")
    args = ap.parse_args()
    rec = qualify(args.package, json.loads(args.freeze.read_text()) if args.freeze else None)
    args.out.write_text(json.dumps(rec, indent=1))
    print(json.dumps({k: rec[k] for k in ("carrier_id", "qualified", "problems", "single_file")}, indent=1))
    for k, c in rec["matrix"].items():
        print(f"{k:32s} {c['tier']}")
    return 0 if rec["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
