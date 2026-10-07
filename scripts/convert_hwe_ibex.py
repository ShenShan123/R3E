#!/usr/bin/env python3
"""Extract three reviewed HWE Ibex development tasks and reproduce them offline.

Consumes an existing local git repository and HWE JSONL. No network, model calls,
upstream preparation scripts, or arbitrary shell scripts are executed. This is
a repository-task handoff, not a single-file R3E carrier or a Blue runner.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile


VERSION = "hwe_ibex_development_v1"
# Reviewed upstream test scripts. New scripts require explicit adapter review.
PROFILES = {
    1816: {
        "tb_sha256": "0eba51fc1c255a184caa1705f9aa97f97a9c1c2092d7e3e42627e5bc17533d17",
        "selection": "debug cause capture across cycles; single RTL module",
        "pass_marker": "SIM PASS: debug cause preserved as HALTREQ",
        "fail_marker": "ERROR: expected DBG_CAUSE_HALTREQ(3), got",
        "binary": "Vibex_controller",
    },
    907: {
        "tb_sha256": "998b3d67ba04ee87fac28e28e23ea208a754ba1c1eac32b294b2c784608c25c6",
        "selection": "prefetch and FIFO dependency; two RTL modules",
        "pass_marker": "TESTBENCH PASS",
        "fail_marker": "CHECK_FAIL expected immediate refill request for address 0x8",
        "binary": "Vibex_prefetch_buffer",
    },
    2232: {
        "tb_sha256": "9b6165f54efa1728ce5f0a3bd97bffb693fead112565c5b889854cf78f3aa7d7",
        "selection": "debug PMP access; interface propagation across multiple files",
        "pass_marker": "TB_PASS result=1",
        "fail_marker": "TB_FAIL timeout",
        "binary": "Vtb_pmp_debug_dm",
    },
}


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def git(repo: Path, *args: str, input_data: bytes | None = None) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], input=input_data,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=True, timeout=120).stdout


def tree_hashes(root: Path) -> dict[str, str]:
    hashes = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"symlink needs review: {path.relative_to(root)}")
        if path.is_file():
            hashes[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    return hashes


def extract_harness(script: str, profile: dict, visible: Path) -> dict:
    if hashlib.sha256(script.encode()).hexdigest() != profile["tb_sha256"]:
        raise ValueError("unreviewed upstream test script")
    # Copy heredoc bytes; do not execute upstream scripts or their installers.
    pattern = r'cat > "(?:\$\{TB_DIR\}|\$WORKDIR)/([^"/]+)" <<\'EOF\'\n(.*?)\nEOF'
    sources = re.findall(pattern, script, re.S)
    if not sources or len({name for name, _ in sources}) != len(sources):
        raise ValueError("missing or duplicate harness sources")
    for name, content in sources:
        if not re.fullmatch(r"[A-Za-z0-9_]+\.(?:sv|cpp)", name):
            raise ValueError("unsupported harness filename")
        (visible / name).write_text(content + "\n")
    logical_lines = script.replace("\\\n", " ").splitlines()
    lines = [line for line in logical_lines if re.match(
        r"^(?:timeout \d+s? )?verilator ", line)]
    if len(lines) != 1:
        raise ValueError("expected one reviewed Verilator build command")
    words = shlex.split(lines[0])
    words = words[words.index("verilator") + 1:]
    if ">" in words:
        words = words[:words.index(">")]
    args = [w.replace("${TB_DIR}", "{harness}").replace("$WORKDIR", "{harness}")
            for w in words]
    if any("$" in arg for arg in args):
        raise ValueError("unresolved shell expression in build command")
    return {"tool": "verilator", "argv": args, "binary": profile["binary"],
            "pass_marker": profile["pass_marker"], "fail_marker": profile["fail_marker"],
            "source_files": [name for name, _ in sources],
            "upstream_tb_sha256": digest(script.encode()),
            "adaptation": "extract literal heredocs and build argv; relocate harness; use local tool"}


def run_logged(argv: list[str], cwd: Path, path: Path, timeout: int) -> dict:
    # Own process group so a timeout also stops compiler/simulator children.
    import signal
    with path.open("wb") as log:
        process = subprocess.Popen(argv, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            rc = process.wait(timeout=timeout)
            return {"returncode": rc, "timed_out": False}
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            return {"returncode": process.returncode, "timed_out": True}


def reproduce(task: Path, recipe: dict, tool: str, timeout: int) -> dict:
    results = {}
    logs = task / "validation"
    logs.mkdir()
    for variant in ("baseline", "reference"):
        with tempfile.TemporaryDirectory(prefix="r3e-hwe-") as temp:
            root = Path(temp) / "repo"
            shutil.copytree(task / "public" / "repo", root)
            for name in ("test.patch", "fix.patch"):
                patch = task / "oracle" / name
                if name == "fix.patch" and variant == "baseline":
                    continue
                if patch.stat().st_size:
                    git(root, "apply", "--check", str(patch))
                    git(root, "apply", str(patch))
            harness = root / ".hwe_harness"
            shutil.copytree(task / "public" / "visible", harness)
            argv = [tool, *[arg.replace("{harness}", str(harness)) for arg in recipe["argv"]]]
            build = run_logged(argv, root, logs / f"{variant}.build.log", timeout)
            result = {"build": build}
            if build["returncode"] == 0 and not build["timed_out"]:
                sim_log = logs / f"{variant}.sim.log"
                sim = run_logged([str(harness / "obj_dir" / recipe["binary"])],
                                 root, sim_log, min(timeout, 60))
                log = sim_log.read_text(errors="replace")
                sim["pass_marker_found"] = recipe["pass_marker"] in log
                sim["fail_marker_found"] = recipe["fail_marker"] in log
                result["simulation"] = sim
            results[variant] = result
    baseline = results["baseline"].get("simulation", {})
    reference = results["reference"].get("simulation", {})
    reproduced = (baseline.get("returncode", 0) != 0 and
                  baseline.get("fail_marker_found", False) and
                  not baseline.get("pass_marker_found", False) and
                  not baseline.get("timed_out", True) and
                  reference.get("returncode") == 0 and
                  reference.get("pass_marker_found", False) and
                  not reference.get("fail_marker_found", False) and
                  not reference.get("timed_out", True))
    return {"reproduced": reproduced, "variants": results,
            "scope": "upstream directed reproducer only; not full repository regression or equivalence",
            "tool_version": subprocess.check_output([tool, "--version"], text=True).strip(),
            "logs": tree_hashes(logs)}


def convert(row: dict, args: argparse.Namespace) -> dict:
    number = row["number"]
    profile = PROFILES[number]
    task_id = f"lowRISC__ibex-{number}"
    task = args.output / task_id
    task.mkdir()
    public = task / "public"
    public.mkdir()
    visible = public / "visible"
    visible.mkdir()
    oracle = task / "oracle"
    oracle.mkdir()
    # HWE's per-task preparation overrides metadata.base; preserve both values.
    checkouts = re.findall(r"^git checkout ([0-9a-f]{40})$", row["prepare_script"], re.M)
    if len(checkouts) != 1:
        raise ValueError("expected one pinned checkout in reviewed preparation")
    base = checkouts[0]
    if git(args.repo, "rev-parse", f"{base}^{{commit}}").decode().strip() != base:
        raise ValueError("base commit identity mismatch")
    entries = git(args.repo, "ls-tree", "-r", base).decode()
    if any(line.startswith(("160000 ", "120000 ")) for line in entries.splitlines()):
        raise ValueError("submodule or symlink needs explicit dependency review")
    repo = public / "repo"
    repo.mkdir()
    archive = git(args.repo, "archive", base)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(repo, filter="data")
    spec = row["problem_statement"]
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("missing problem statement")
    (public / "spec.txt").write_text(spec)
    for field, name in (("fix_patch", "fix.patch"), ("test_patch", "test.patch")):
        (oracle / name).write_text(row[field])
    recipe = extract_harness(row["tb_script"], profile, visible)
    write_json(visible / "build.json", recipe)
    # Public task metadata intentionally excludes gold patches and changed-file lists.
    write_json(public / "task.json", {
        "schema": VERSION, "task_id": task_id, "repository": "lowRISC/ibex",
        "repository_family": "ibex", "split": "development_only",
        "spec": "spec.txt", "source_root": "repo", "visible_test": "visible/build.json",
        "editable_scope": "repository RTL; tests and oracle are not editable",
        "evidence_format": "upstream textual simulator logs; no cycle-trace schema yet",
    })
    manifest = {
        "schema": VERSION, "task_id": task_id, "split": "development_only",
        "repository_family": "ibex", "selection_reason": profile["selection"],
        "source_url": row["html_url"], "metadata_base": row["base"]["sha"],
        "effective_base": base, "base_discrepancy": base != row["base"]["sha"],
        "public_task": f"{task_id}/public/task.json",
        "source_record_hash": digest(json.dumps(row, sort_keys=True).encode()),
        "prepare_script_hash": digest(row["prepare_script"].encode()),
        "public_files": tree_hashes(public), "oracle_files": tree_hashes(oracle),
        "eligible": False, "validation_status": "not_run",
        "oracle_boundary": "Only public/ may be copied to Blue; manifest, oracle/ and validation/ are evaluator-only.",
    }
    if args.reproduce:
        try:
            validation = reproduce(task, recipe, args.verilator, args.timeout)
            manifest["eligible"] = validation["reproduced"]
            manifest["validation_status"] = "reproduced" if validation["reproduced"] else "not_reproduced"
        except (ValueError, subprocess.SubprocessError, OSError) as exc:
            validation = {"reproduced": False, "error": str(exc)}
            manifest["validation_status"] = "infrastructure_error"
        write_json(task / "validation.json", validation)
    write_json(task / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True, help="local Ibex git repository (bare is supported)")
    parser.add_argument("--output", type=Path, required=True, help="new evaluator-only artifact directory")
    parser.add_argument("--ids", type=int, nargs="+", default=[1816, 907, 2232])
    parser.add_argument("--reproduce", action="store_true")
    parser.add_argument("--verilator", default="verilator")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if len(set(args.ids)) != len(args.ids) or any(n not in PROFILES for n in args.ids):
        parser.error("choose unique IDs from the reviewed profiles: 1816 907 2232")
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    args.repo = args.repo.resolve()
    args.output = args.output.resolve()
    if args.output.exists():
        parser.error("output already exists; use a fresh directory")
    if args.reproduce:
        args.verilator = shutil.which(args.verilator)
        if not args.verilator:
            parser.error("Verilator is required for offline reproduction")
    rows = [json.loads(line) for line in args.dataset.read_text().splitlines() if line.strip()]
    chosen = {}
    for number in args.ids:
        matches = [r for r in rows if r["org"] == "lowRISC" and r["repo"] == "ibex" and r["number"] == number]
        if len(matches) != 1:
            parser.error(f"expected exactly one source record for {number}")
        chosen[number] = matches[0]
    args.output.mkdir(parents=True)
    manifest = []
    for number in args.ids:
        row = convert(chosen[number], args)
        manifest.append(row)
        print(f"{row['task_id']}: {row['validation_status']}", flush=True)
    with (args.output / "manifest.jsonl").open("w") as stream:
        for row in manifest:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
    write_json(args.output / "conversion.json", {
        "schema": VERSION, "dataset_hash": digest(args.dataset.read_bytes()),
        "converter_hash": digest(Path(__file__).read_bytes()), "task_count": len(manifest),
        "reproduced": sum(r["eligible"] for r in manifest), "model_calls": 0,
        "split_policy": "All selected Ibex tasks are development-only; no within-Ibex holdout claim.",
    })
    return 0 if not args.reproduce or all(r["eligible"] for r in manifest) else 1


if __name__ == "__main__":
    raise SystemExit(main())
