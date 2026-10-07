"""Supplementary verification of Blue's repository repairs (HWE): candidate vs reference at a declared boundary.

Evaluator-side only; never a Blue input. For a task, the **reference** is the
baseline with the oracle fix applied (only its hunks on files of the visible
build's RTL closure; the patch's other files are listed as uncovered). A
**candidate** is an archived Blue candidate (``repo_blue`` archive, hash checked).

Boundary (``verification_boundaries.json`` next to the tasks, reviewed per task):
the visible build's top module, or for a testbench top a generated wrapper that
instantiates the design the testbench drives with the testbench's own parameter
block. Elaboration: Yosys with the slang frontend, the build's ``-I``/``-D``,
assertions ignored (listed as uncovered), memories mapped to logic (``memory``;
the solver cannot read memory cells), ``async2sync``.

Methods, each recorded with its scope:
- ``bounded_equivalence``: a miter of reference and candidate, ``sat -seq K``
  from an all-zero state with reset asserted in the first step, all inputs free
  afterwards, reference X ignored. Guarded: the reference must first be
  equivalent to itself, else not applicable. A bounded check, not a proof.
- ``random_simulation``: the same miter written as a netlist and simulated with
  Icarus: reset for 2 cycles, then every input uniformly random each cycle (no
  protocol constraints), ``seeds x cycles``, mismatch checked after both edges.

Status per candidate: ``mismatch_found`` (with seed/cycle or the solver's
counterexample; needs review: random inputs may violate the real protocol, and
the reference may be wrong), ``no_mismatch_found`` (every method completed,
within its recorded scope), ``no_mismatch_found_partial`` (some method did not
complete; listed in ``incomplete_methods``), ``incomplete_or_not_applicable``.
A random-simulation seed completes only if it exits normally and prints its
completion marker. Every record lists what is not covered.

Usage: python -m experiments.loop_probes.repo_verify TASK_DIR --candidate-hash H --archive DIR [--depth 12]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from experiments.loop_probes.repair_verify import summarise
from r3e.loop import repo_blue as rb
from r3e.protocol.hashing import hash_payload

VERSION = "repo_verify_v1"
UNCOVERED_ALWAYS = [
    "SVA assertions (ignored by the formal frontend)",
    "parameter configurations other than the recorded one",
    "behaviour beyond the bounded depth that random stimulus does not reach",
    "input protocols: random inputs are unconstrained, so some mismatches may need protocol review",
]


def reference_overlay(task: rb.RepoTask, closure: list[str]) -> tuple[dict[str, str], list[str]]:
    """Baseline + oracle fix, restricted to closure files; returns (overlay, patch files outside the closure)."""
    patch = (task.task_dir / "oracle" / "fix.patch").read_text()
    touched = re.findall(r"(?m)^\+\+\+ b/(\S+)", patch)
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        shutil.copytree(task.repo, repo, symlinks=True)
        subprocess.run(["patch", "-p1", "-s", "-i", str(task.task_dir / "oracle" / "fix.patch")], cwd=repo,
                       check=True, capture_output=True)
        overlay = {f: (repo / f).read_text(errors="replace") for f in touched if f in closure}
    return overlay, [f for f in touched if f not in closure]


def materialise(task: rb.RepoTask, overlay: dict[str, str], dest: Path) -> Path:
    shutil.copytree(task.repo, dest, symlinks=True)
    for f, text in overlay.items():
        (dest / f).write_text(text)
    return dest


def _wrapper(task: rb.RepoTask, spec: dict, repo: Path, frontend: str, work: Path) -> tuple[str, str]:
    """A module instantiating the testbench's design instance with the testbench's parameter block."""
    tb = task.harness[spec["harness"]]
    head = tb.split("\n", 1)[1]
    # localparam blocks may span lines: take the text from the first import to the line before the first
    # non-parameter declaration
    lines = head.splitlines()
    block, inside = [], False
    for l in lines:
        if re.match(r"\s*(import |localparam )", l):
            inside = True
        elif inside and re.match(r"\s*(logic|reg|wire|int|integer|function|task|initial|always|bit|\w+_t\s)", l) \
                and not l.strip().startswith(("'", "}", "{")):
            break
        if inside:
            block.append(l)
    params = "\n".join(block)
    inst = re.search(rf"{spec['wrapper_of']}\s*#\s*\((.*?)\)\s*{spec['instance']}\s*\(", tb, re.S).group(1)
    ports_json = work / "ports.json"
    run_yosys(f"{frontend} --top {spec['wrapper_of']}; hierarchy -top {spec['wrapper_of']}; write_json {ports_json}",
              work)
    ports = json.loads(ports_json.read_text())["modules"][spec["wrapper_of"]]["ports"]
    unpacked = unpacked_ports(task, spec["wrapper_of"], repo) & set(ports)  # only ports elaborated in this build
    kept = {n: p for n, p in ports.items() if n not in unpacked}
    decl = ",\n".join(f"  {p['direction']} logic [{len(p['bits']) - 1}:0] {n}" for n, p in kept.items())
    conn = ", ".join([f".{n}({n})" for n in kept] + [f".{n}()" for n in unpacked])
    text = (f"module r3e_boundary (\n{decl}\n);\n{params}\n  {spec['wrapper_of']} #({inst}) u ({conn});\nendmodule\n")
    return text, hash_payload(text), sorted(unpacked)


def unpacked_ports(task: rb.RepoTask, module: str, repo: Path) -> set[str]:
    """Ports declared with an unpacked dimension after the name (e.g. ``logic [63:0] x [2]``)."""
    for f in repo.rglob("*.sv"):
        text = f.read_text(errors="replace")
        m = re.search(rf"\bmodule\s+{module}\b.*?\)\s*;", text, re.S)
        if m:
            header = re.sub(r"//[^\n]*", "", m.group(0))
            return set(re.findall(r"\b(?:input|output|inout)\b[^,;()]*?\b(\w+)\s*\[[^\]]*\]\s*(?=[,)\n])", header))
    return set()


def run_yosys(script: str, work: Path, timeout: float = 900) -> tuple[int | None, str]:
    rc, out = rb._run(["yosys", "-m", "slang", "-q", "-p", script], work, timeout)
    return rc, out


def frontend(task: rb.RepoTask, files: list[str], repo: Path, extra: list[str] = ()) -> str:
    argv = task.build["argv"]
    incs = " ".join(f"-I {repo / a[2:]}" for a in argv if a.startswith("-I"))
    defs = " ".join(f"-D {a[2:]}" for a in argv if a.startswith("-D"))
    srcs = " ".join(str(repo / f) for f in files)
    return f"read_slang --ignore-assertions {incs} {defs} {srcs} {' '.join(map(str, extra))}"


def sources(task: rb.RepoTask, closure: list[str]) -> list[str]:
    """The build's listed RTL plus closure files that declare modules or packages (Verilator found them
    through -I as a library path; slang needs them listed). Pure header/macro files stay includes."""
    listed = [a for a in task.build["argv"] if a.endswith((".sv", ".v")) and not a.startswith("{harness}")]
    extra = [f for f in closure if f.endswith((".sv", ".v")) and f not in listed
             and re.search(r"^\s*(module|package|interface)\s+\w+", (task.repo / f).read_text(errors="replace"), re.M)]
    return listed + extra


def build_miter(task, spec, closure_files, gold_repo, gate_repo, work: Path) -> tuple[str | None, dict]:
    """Yosys script prefix producing ``miter``; returns (script or None, scope)."""
    scope = {"boundary": spec.get("top") or f"wrapper of {spec['wrapper_of']} ({spec['instance']} in {spec['harness']})",
             "defines": [a[2:] for a in task.build["argv"] if a.startswith("-D")]}
    # harness SV other than a testbench top (e.g. primitive stubs) is part of the design under test
    stubs = []
    for n, text in task.harness.items():
        if n.endswith(".sv") and n != spec.get("harness"):
            (work / n).write_text(text)
            stubs.append(work / n)
    scope["harness_sources"] = [p.name for p in stubs]
    parts = []
    for name, repo in (("gold", gold_repo), ("gate", gate_repo)):
        fe = frontend(task, closure_files, repo, stubs)
        top = spec.get("top")
        if top is None:
            text, h, unconnected = _wrapper(task, spec, repo, fe, work)
            wf = work / f"wrapper_{name}.sv"
            wf.write_text(text)
            scope["wrapper_hash"] = h
            scope["unconnected_unpacked_ports"] = unconnected
            fe, top = fe + f" {wf}", "r3e_boundary"
        parts += [f"{fe} --top {top}", f"hierarchy -top {top}", "proc", "memory", "flatten", "async2sync", "opt_clean",
                  f"rename {top} {name}", f"design -stash {name}"]
    parts += ["design -copy-from gold -as gold gold", "design -copy-from gate -as gate gate",
              "miter -equiv -flatten -make_outputs -ignore_gold_x gold gate miter", "hierarchy -top miter"]
    return "; ".join(parts), scope


def bounded(script: str, spec: dict, depth: int, work: Path, timeout: float) -> dict:
    rst = f" -set-at 1 in_{spec['reset']} {spec['reset_active']}"
    rc, out = run_yosys(f"{script}; sat -verify -seq {depth} -set-init-zero{rst} -prove-skip 1 -prove trigger 0 "
                        f"-show-inputs miter", work, timeout)
    if rc is None:
        return {"outcome": "timeout"}
    if rc == 0:
        return {"outcome": "no_mismatch_within_bound"}
    if "proof did fail" in out:
        return {"outcome": "mismatch", "counterexample": out[-6000:]}
    return {"outcome": "incomplete", "reason": re.sub(r"\s+", " ", out[-400:])}


def random_sim(script: str, spec: dict, seeds, cycles: int, work: Path) -> dict:
    net = work / "miter.v"
    rc, out = run_yosys(f"{script}; opt_clean; write_verilog -noattr {net}; write_json {work / 'miter.json'}", work)
    if rc != 0:
        return {"outcome": "incomplete", "reason": re.sub(r"\s+", " ", out[-300:])}
    ports = json.loads((work / "miter.json").read_text())["modules"]["miter"]["ports"]
    ins = {n: len(p["bits"]) for n, p in ports.items() if p["direction"] == "input"}
    clk, rst = f"in_{spec['clock']}", f"in_{spec['reset']}"
    for seed in seeds:
        rand = "\n".join(f"      {n} = {{{', '.join(['$random(s)'] * (-(-w // 32)))}}};"
                         for n, w in ins.items() if n not in (clk, rst))
        tb = (f"module tb;\n" + "\n".join(f"  reg [{w - 1}:0] {n};" for n, w in ins.items())
              + f"\n  wire trigger;\n  integer s, i;\n  miter dut({', '.join(f'.{n}({n})' for n in ins)}, .trigger(trigger));\n"
              f"  initial begin\n    s = {seed}; {clk} = 0; {rst} = {spec['reset_active']};\n{rand}\n"
              f"    repeat (2) begin #1 {clk} = 1; #1 {clk} = 0; end\n    {rst} = {1 - spec['reset_active']};\n"
              f"    for (i = 0; i < {cycles}; i = i + 1) begin\n{rand}\n"
              f"      #1 {clk} = 1; #1 if (trigger === 1'b1) begin $display(\"MISMATCH %0d\", i); $finish; end\n"
              f"      {clk} = 0; #1 if (trigger === 1'b1) begin $display(\"MISMATCH %0d\", i); $finish; end\n"
              f"    end\n    $display(\"NO_MISMATCH\");\n    $finish;\n  end\nendmodule\n")
        (work / "tb.v").write_text(tb)
        rc, out = rb._run(["iverilog", "-g2012", "-o", "sim", "miter.v", "tb.v"], work, 600)
        if rc != 0:
            return {"outcome": "incomplete", "reason": re.sub(r"\s+", " ", out[-300:])}
        rc, out = rb._run(["vvp", "sim"], work, 1800)
        if rc is None:
            return {"outcome": "timeout", "seed": seed}
        m = re.search(r"(?<!NO_)MISMATCH (\d+)", out)
        if m:
            return {"outcome": "mismatch", "counterexample": {"seed": seed, "cycle": int(m.group(1)),
                                                              "testbench_hash": hash_payload(tb)}}
        if rc != 0 or "NO_MISMATCH" not in out:  # a seed counts only if it ran to its end and said so
            return {"outcome": "incomplete", "seed": seed,
                    "reason": f"simulation exit {rc}, completion marker {'present' if 'NO_MISMATCH' in out else 'missing'}: "
                              + re.sub(r"\s+", " ", out[-200:])}
    return {"outcome": "no_mismatch"}


def verify(task_dir: Path, overlay: dict[str, str], candidate_hash: str, *, depth=12, seeds=(11, 23, 37),
           cycles=1000, timeout=1200) -> dict:
    task = rb.RepoTask.load(task_dir)
    spec = json.loads((task_dir.parent / "verification_boundaries.json").read_text())[task.task["task_id"]]
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        base = rb.execute(task, {})  # closure of the visible build
        closure = [f for f in base.closure if not f.startswith("harness:")]
        ref, outside = reference_overlay(task, closure)
        listed = sources(task, closure)
        gold = materialise(task, ref, work / "gold")
        gate = materialise(task, overlay, work / "gate")
        record = {"verifier": VERSION, "task_id": task.task["task_id"], "candidate_hash": candidate_hash,
                  "reference": {"oracle_patch_files_outside_closure": outside, "files": sorted(ref)},
                  "uncovered": UNCOVERED_ALWAYS + [f"oracle patch file outside the visible build: {f}" for f in outside]}
        script, scope = build_miter(task, spec, listed, gold, gate, work)
        self_script, _ = build_miter(task, spec, listed, gold, gold, work)
        depth = spec.get("depth", depth)  # a per-task bound, declared in the boundary file
        own = bounded(self_script, spec, depth, work, timeout)
        methods = []
        b_scope = {**scope, "steps": depth, "start": "all-zero state, reset asserted in step 1",
                   "inputs": "free after step 1", "abstraction": "async resets made synchronous; X in the reference ignored"}
        if own["outcome"] != "no_mismatch_within_bound":
            methods.append({"method": "bounded_equivalence", "scope": b_scope, "outcome": "not_applicable",
                            "reason": f"reference not equivalent to itself here ({own['outcome']}: "
                                      f"{str(own.get('reason') or '')[:300]})"})
        else:
            methods.append({"method": "bounded_equivalence", "scope": b_scope,
                            **bounded(script, spec, depth, work, timeout)})
        methods.append({"method": "random_simulation",
                        "scope": {**scope, "seeds": list(seeds), "cycles_per_seed": cycles,
                                  "input_constraints": "reset 2 cycles, then all inputs uniformly random each cycle"},
                        **random_sim(script, spec, seeds, cycles, work)})
    status = summarise(methods)
    incomplete = [m["method"] for m in methods if m["outcome"] not in ("mismatch", "no_mismatch", "no_mismatch_within_bound")]
    record.update(status=status, methods=methods, incomplete_methods=incomplete,
                  **({"review": "needed: check the counterexample against the input protocol and the reference "
                                "against the spec"} if status == "mismatch_found" else {}))
    return record


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("task_dir", type=Path)
    p.add_argument("--candidate-hash", required=True)
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--depth", type=int, default=12)
    args = p.parse_args()
    task_id = rb.RepoTask.load(args.task_dir).task["task_id"]
    rec = next(r for r in (json.loads(f.read_text()) for f in args.archive.glob("*.json"))
               if r["candidate_hash"] == args.candidate_hash and r["task_id"] == task_id)
    if hash_payload(rec["files"]) != args.candidate_hash:
        raise SystemExit("archived candidate does not match its hash")
    print(json.dumps(verify(args.task_dir, rec["files"], args.candidate_hash, depth=args.depth), indent=1)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
