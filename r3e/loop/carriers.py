"""Build carriers from golden-only designs by generating trace testbenches.

Many open designs (VerilogEval, RTLLM, ...) ship a golden module plus a
testbench that only prints a pass/fail summary. The loop needs a per-cycle
output trace, so for each design this module generates two testbenches:

- **visible**: random stimulus with seed A, which Blue's feedback is computed
  from;
- **hidden**: different random stimulus and a longer run, used only for the
  ``hidden_pass`` verdict tier.

Ports come from Yosys (``write_json``). The clock and reset are taken from the
design's edge-triggered blocks and from conventional names. A design becomes a
carrier only if it passes all of these gates:
- the clean design compiles under both testbenches;
- its trace is deterministic across two runs;
- no output is X/Z after reset;
- at least one output changes over the trace;
- it offers at least ``min_sites`` legal Red edit sites.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from r3e.knowledge.structure import analyze_rtl
from r3e.knowledge.verilog_utils import RESET_NAME_RE
from r3e.protocol.hashing import hash_payload

from .corpus import Carrier
from .operators import enumerate_sites
from .sim import Simulator


CLOCK_NAMES = {"clk", "clock", "clk_i", "i_clk", "aclk"}
ACTIVE_LOW = re.compile(r"(_n|n|_b|_l)$", re.IGNORECASE)


@dataclass(frozen=True)
class Port:
    name: str
    direction: str
    width: int


def yosys_ports(rtl_path: Path, top: str, workdir: Path) -> list[Port]:
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "ports.json"
    cmd = ["yosys", "-q", "-p",
           f"read_verilog -sv {rtl_path}; hierarchy -top {top}; proc; write_json {out}"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"yosys failed: {(proc.stderr or proc.stdout)[-200:]}")
    module = json.loads(out.read_text())["modules"][top]
    return [Port(name, p["direction"], len(p["bits"])) for name, p in module["ports"].items()]


def _decl(port: Port, kind: str) -> str:
    rng = f"[{port.width - 1}:0] " if port.width > 1 else ""
    return f"  {kind} {rng}{port.name};"


def generate_testbench(
    *, top: str, ports: list[Port], clock: str | None, reset: str | None,
    seed: int, cycles: int, output_name: str, stimulus_name: str | None = None,
) -> str:
    """Random-stimulus testbench writing one output row per cycle.

    With ``stimulus_name``, the inputs applied in each logged cycle are written
    to a second file (same row order), headed by a ``#`` note on the reset
    sequence. Only the visible testbench logs stimulus: it is evidence Blue may
    see. The output trace is unchanged by this option.
    """
    inputs = [p for p in ports if p.direction == "input" and p.name not in {clock, reset}]
    outputs = [p for p in ports if p.direction == "output"]
    columns = []
    for p in outputs:
        columns += [f"{p.name}[{b}]" for b in range(p.width - 1, -1, -1)] if p.width > 1 else [p.name]
    fmt = ",".join(["%0d"] + ["%b"] * len(columns))
    args = ", ".join(["i"] + [f"{p.name}[{b}]" if p.width > 1 else p.name
                               for p in outputs for b in (range(p.width - 1, -1, -1) if p.width > 1 else [0])])
    lines = ["`timescale 1ns/1ps", "module r3e_tb;"]
    if clock:
        lines.append(f"  reg {clock} = 0;")
    if reset:
        lines.append(f"  reg {reset};")
    lines += [_decl(p, "reg") for p in inputs] + [_decl(p, "wire") for p in outputs]
    conns = ", ".join(f".{p.name}({p.name})" for p in ports)
    lines += [f"  {top} dut({conns});", "  integer f, i, s;"]
    if clock:
        lines.append(f"  always #5 {clock} = ~{clock};")
    reset_on = "0" if reset and ACTIVE_LOW.search(reset) and not reset.lower().endswith(("en",)) else "1"
    reset_off = "1" if reset_on == "0" else "0"

    def drive() -> list[str]:
        # $random yields 32 bits; concatenate enough draws to cover wide inputs
        out = []
        for p in inputs:
            words = max(1, -(-p.width // 32))
            value = "$random(s)" if words == 1 else "{" + ", ".join(["$random(s)"] * words) + "}"
            out.append(f"      {p.name} = {value};")
        return out

    lines += ["  initial begin", f"    s = {seed};", f'    f = $fopen("{output_name}");',
              f'    $fdisplay(f, "time,{",".join(columns)}");']
    if stimulus_name:
        lines[lines.index("  integer f, i, s;")] = "  integer f, i, s, r3e_stim;"
        note = (f"#reset {reset} held at {reset_on} for 2 cycles before cycle 0" if reset and clock
                else f"#reset {reset} pulsed before cycle 0" if reset else "#no reset")
        lines += [f'    r3e_stim = $fopen("{stimulus_name}");', f'    $fdisplay(r3e_stim, "{note}");',
                  f'    $fdisplay(r3e_stim, "time,{",".join(p.name for p in inputs)}");']
    stim = ([f'      $fdisplay(r3e_stim, "{",".join(["%0d"] + ["%b"] * len(inputs))}", '
             f'{", ".join(["i"] + [p.name for p in inputs])});'] if stimulus_name else [])
    lines += [f"    {p.name} = 0;" for p in inputs]
    if reset:
        lines.append(f"    {reset} = {reset_on};")
    if clock:
        lines += [f"    repeat (2) @(negedge {clock});"]
        if reset:
            lines.append(f"    {reset} = {reset_off};")
        lines += [f"    for (i = 0; i < {cycles}; i = i + 1) begin", *drive(),
                  f"      @(negedge {clock});", f'      $fdisplay(f, "{fmt}", {args});', *stim, "    end"]
    else:
        if reset:
            lines += ["    #1;", f"    {reset} = {reset_off};"]
        lines += [f"    for (i = 0; i < {cycles}; i = i + 1) begin", *drive(),
                  "      #5;", f'      $fdisplay(f, "{fmt}", {args});', *stim, "    end"]
    lines += ["    $fclose(f);", *(["    $fclose(r3e_stim);"] if stimulus_name else []),
              "    $finish;", "  end", "endmodule", ""]
    return "\n".join(lines)


def detect_clock_reset(rtl: str, ports: list[Port]) -> tuple[str | None, str | None]:
    inputs = {p.name for p in ports if p.direction == "input" and p.width == 1}
    structure = analyze_rtl(rtl)
    edges = [b.edge_signals for b in structure.blocks if b.edge_signals]
    clock = next((e[0] for e in edges if e and e[0] in inputs), None)
    if clock is None:
        clock = next((n for n in inputs if n.lower() in CLOCK_NAMES), None)
    reset = next((s for e in edges for s in e[1:] if s in inputs), None)
    if reset is None:
        reset = next((n for n in sorted(inputs) if RESET_NAME_RE.match(n) or n.lower() in {"reset", "areset", "rst", "rst_n"}), None)
    return clock, reset


def build_carrier(
    *, rtl_path: Path, top: str, rename_top: str | None, dest: Path, cluster: str,
    source: dict[str, Any], visible_cycles: int = 64, hidden_cycles: int = 160, min_sites: int = 3,
) -> tuple[dict[str, Any] | None, str]:
    """Return (manifest row, reason). The row is None when a gate rejects the design."""
    dest.mkdir(parents=True, exist_ok=True)
    rtl = rtl_path.read_text(encoding="utf-8", errors="ignore")
    final_top = rename_top or top
    if rename_top:
        rtl = re.sub(rf"\bmodule\s+{re.escape(top)}\b", f"module {rename_top}", rtl, count=1)
    clean = dest / "clean.sv"
    clean.write_text(rtl, encoding="utf-8")
    try:
        ports = yosys_ports(clean, final_top, dest / "yosys")
    except Exception as exc:
        return None, f"ports:{str(exc)[:60]}"
    if not any(p.direction == "output" for p in ports) or any(p.direction == "inout" for p in ports):
        return None, "unsupported_ports"
    clock, reset = detect_clock_reset(rtl, ports)
    tbs = {}
    for which, seed, cycles in (("visible", 11, visible_cycles), ("hidden", 97, hidden_cycles)):
        tb = dest / f"tb_{which}.v"
        tb.write_text(generate_testbench(top=final_top, ports=ports, clock=clock, reset=reset, seed=seed,
                                         cycles=cycles, output_name=f"trace_{which}.txt",
                                         stimulus_name="stimulus_visible.txt" if which == "visible" else None),
                      encoding="utf-8")
        tbs[which] = (tb,)
    carrier = Carrier(
        carrier_id="CR_" + hash_payload({"src": source, "rtl": hash_payload(rtl)}).split(":", 1)[1][:12],
        cluster_id=cluster, clean_rtl=rtl, top_module=final_top,
        visible_tb=tbs["visible"], hidden_tb=tbs["hidden"], sim_timeout=10.0, source=source,
    )
    sim = Simulator(dest / "gate_sim")
    traces = []
    for _ in range(2):
        text, err = sim.run(rtl, carrier, carrier.visible_tb)
        if text is None:
            shutil.rmtree(dest / "gate_sim", ignore_errors=True)
            return None, f"clean_fails:{err[:60]}"
        traces.append(text)
    hidden, err = sim.run(rtl, carrier, carrier.hidden_tb)
    shutil.rmtree(dest / "gate_sim", ignore_errors=True)
    if hidden is None:
        return None, f"clean_fails_hidden:{err[:60]}"
    if traces[0] != traces[1]:
        return None, "nondeterministic"
    rows = [line.split(",")[1:] for line in traces[0].splitlines()[1:] if line.strip()]
    if not rows or any(bit.lower() in {"x", "z"} for row in rows[2:] for bit in row):
        return None, "x_or_z_outputs"
    if len({tuple(r) for r in rows}) < 2:
        return None, "constant_outputs"
    if len(enumerate_sites(carrier)) < min_sites:
        return None, "too_few_edit_sites"
    return {
        "carrier_id": carrier.carrier_id,
        "cluster_id": cluster,
        "top_module": final_top,
        "clean_rtl": str(clean),
        "visible_tb": [str(tbs["visible"][0])],
        "hidden_tb": [str(tbs["hidden"][0])],
        "deps": [],
        "sim_timeout": 10.0,
        "clock": clock,
        "reset": reset,
        "ports": [p.__dict__ for p in ports],
        "source": source,
    }, "admitted"


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CARRIER_MANIFEST = REPO_ROOT / "datasets/generated/corpus_v1/carriers.jsonl"


def load_carrier_manifest(path: Path) -> list[Carrier]:
    """Carriers from a manifest; relative paths are relative to the repository root."""
    resolve = lambda p: Path(p) if Path(p).is_absolute() else REPO_ROOT / p
    carriers = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        carriers.append(Carrier(
            carrier_id=row["carrier_id"],
            cluster_id=row["cluster_id"],
            clean_rtl=resolve(row["clean_rtl"]).read_text(encoding="utf-8"),
            top_module=row["top_module"],
            visible_tb=tuple(resolve(p) for p in row["visible_tb"]),
            hidden_tb=tuple(resolve(p) for p in row["hidden_tb"]),
            deps=tuple(resolve(p) for p in row.get("deps") or []),
            sim_timeout=float(row.get("sim_timeout", 10.0)),
            source=row.get("source") or {},
        ))
    return carriers


