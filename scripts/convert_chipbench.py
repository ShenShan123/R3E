#!/usr/bin/env python3
"""Convert ChipBench zero-shot debugging cases to the R3E public manifest.

Run from any directory: python scripts/convert_chipbench.py
Validation is on by default; --skip-validation only extracts the inputs.
Source RTL is preserved; upstream failures are reported, never filtered out.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COUNTS = {"assignment": 30, "timing": 29, "arithmetic": 24, "state_machine": 6}
TRACE_NAME = "trace_visible.txt"
STIMULUS_NAME = "stimulus_visible.txt"  # current R3E runner's discovery prefix
STIMULATION_NAME = "stimulation_visible.txt"  # requested name; identical log

# Preserve these exact upstream tasks, but prevent incomplete/inconsistent
# reference designs from entering the repair pool. Buggy compile failures
# alone are valid repair tasks and must not make a case ineligible.
INELIGIBLE_CASES = {
    **{f"chipbench:{kind}:Prob007_data_accumulation_output":
       "reference_spec_conflict: under backpressure the first accepted input of a new four-input group "
       "is added to the previous sum; the specification requires the sum of that group's four inputs"
       for kind in ("assignment", "timing", "arithmetic")},
    **{f"chipbench:{kind}:Prob033_traffic_lights":
       "reference_spec_conflict: reset sets all lamps off and enters idle; the specification requires red on"
       for kind in ("assignment", "timing", "arithmetic", "state_machine")},
    **{f"chipbench:{kind}:Prob006_data_serial-to-parallel_circuit":
       "reference_spec_conflict: after five accepted bits, a paused input asserts valid_b with stale data_b; "
       "the specification requires six received bits before a valid parallel output"
       for kind in ("assignment", "timing", "arithmetic")},
    **{f"chipbench:{kind}:Prob013_least_common_multiple":
       "reference_self_check_inconsistent: the native XOR checker rejects matching high-impedance (Z) outputs"
       for kind in ("assignment", "timing", "arithmetic")},
    **{f"chipbench:{kind}:Prob{number}_{design}":
       "reference_incomplete: dual_port_RAM is missing from the reference"
       for kind in ("assignment", "arithmetic")
       for number, design in (("021", "asynchronous_FIFO"), ("022", "synchronous_FIFO"))},
    **{f"chipbench:timing:Prob{number}_{design}":
       "reference_incomplete: duplicated dual_port_RAM declarations when golden and reference are compiled together"
       for number, design in (("021", "asynchronous_FIFO"), ("022", "synchronous_FIFO"))},
    "chipbench:timing:Prob018_submodules_to_implement_comparison_of_three_input_numbers":
        "reference_incomplete: slave_mod is missing from the reference",
    "chipbench:arithmetic:Prob019_implement_full_subtractor_using_three_to_eight_decoder":
        "reference_incomplete: decoder_38 is missing from the reference",
}
COMPILE_REPAIR_CASES = {"chipbench:timing:Prob016_odd-number_division_with_a_duty_cycle_of_half"}


def mask_comments(text: str) -> str:
    """Keep offsets stable while excluding comments from structural matches."""
    return re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/',
                  lambda m: m[0] if m[0].startswith('"') else
                  re.sub(r"[^\n]", " ", m[0]), text)


def module_span(text: str, name: str) -> tuple[int, int]:
    masked = mask_comments(text)
    declarations = list(re.finditer(r"\bmodule\s+" + re.escape(name) + r"\b", masked))
    if len(declarations) != 1:
        raise ValueError(f"expected exactly one module {name}, found {len(declarations)}")
    start = declarations[0].start()
    end = re.search(r"\bendmodule\b", masked[declarations[0].end():])
    if end is None:
        raise ValueError(f"module {name} has no endmodule")
    return start, declarations[0].end() + end.start()


def bug_marker(prompt: str) -> re.Match:
    markers = list(re.finditer(r"^.*the\s+code\s+below\b[^\n]*\bhas\s+bug[^\n]*", prompt, re.M | re.I))
    if len(markers) != 1:
        raise ValueError("expected one 'the code below ... has bug' marker (zero-shot prompts only)")
    return markers[0]


def validate_spec(spec: str) -> None:
    """Reject code fences and recognizable HDL, keeping prose/literals intact.

    Inline identifiers, numeric literals and equations are specification data,
    not implementation snippets. This is a conservative text check, not an
    arbitrary-language code classifier. Unexpected HDL is rejected, never
    silently rewritten into an inferred behavioral specification.
    """
    if not spec.strip():
        raise ValueError("empty specification")
    if re.search(r"`{3,}|~{3,}", spec):
        raise ValueError("specification still contains a code fence")
    patterns = (
        r"\bmodule\s+[A-Za-z_]\w*\s*(?:#\s*\(|\(|;)",
        r"\bendmodule\b|\bendfunction\b|\bendtask\b",
        r"\balways(?:_ff|_comb|_latch)?\s*(?:@|begin\b)",
        r"\bassign\s+[^\n;]+=[^\n;]*;",
        r"\b(?:initial|generate)\s+begin\b",
        r"(?m)^[ \t]*(?:input|output|inout|wire|reg|logic|parameter|localparam)\b[^\n]*;",
        r"`(?:timescale|define|include|default_nettype)\b",
        r"\$(?:display|finish|fopen|fwrite|fdisplay|random|urandom)\s*\(",
        r"(?m)^[ \t]*[A-Za-z_]\w*(?:\[[^\n]+?\])?\s*(?:<=|=)[^\n]*;",
    )
    texts = [spec, *re.findall(r"`([^`\n]+)`", spec)]
    for pattern in patterns:
        if any(re.search(pattern, text) for text in texts):
            raise ValueError("specification contains recognizable HDL/code outside a removed fence")


def extract_spec(prompt: str) -> tuple[str, list[dict]]:
    """Prose before the marker, with code sanitation recorded at source offsets.

    Provided helper implementations are removed. Standalone numeric parameter
    declarations become prose carrying the same name, literal and comment.
    Other unfenced implementation code is rejected.
    """
    prefix = prompt[:bug_marker(prompt).start()]
    opening = re.compile(r"(?m)^[ ]{0,3}(`{3,}|~{3,})[^\n]*$")
    edits = []
    position = 0
    while match := opening.search(prefix, position):
        fence = match[1]
        closing = re.compile(r"(?m)^[ ]{0,3}" + re.escape(fence[0])
                             + "{" + str(len(fence)) + r",}[ \t]*\r?$")
        end = closing.search(prefix, match.end())
        if end is None:
            raise ValueError("unterminated fenced block in specification")
        edits.append((match.start(), end.end(), "", "fenced_code_block"))
        position = end.end()
    numeric = r"(?:\d+)?'[sS]?[bBdDhHoO][0-9a-fA-FxXzZ?_]+|[+-]?\d[\d_]*"
    parameters = re.compile(r"(?m)^[ \t]*parameter\s+([A-Za-z_]\w*)\s*=\s*("
                            + numeric + r")[ \t]*;[ \t]*(?://([^\n]*))?\r?$")
    for match in parameters.finditer(prefix):
        if any(start <= match.start() < stop for start, stop, _, _ in edits):
            continue
        name, literal, comment = match.groups()
        prose = f"- Parameter `{name}`: `{literal}`."
        if comment and comment.strip():
            prose += " " + comment.strip()
        edits.append((match.start(), match.end(), prose, "numeric_parameter_to_prose"))
    parts = []
    audit = []
    position = 0
    for start, stop, replacement, reason in sorted(edits):
        parts.extend((prefix[position:start], replacement))
        audit.append({"start_line": prefix.count("\n", 0, start) + 1,
                      "end_line": prefix.count("\n", 0, stop) + 1,
                      "sha256": sha256(prefix[start:stop].encode()), "reason": reason})
        position = stop
    parts.append(prefix[position:])
    spec = "".join(parts).strip() + "\n"
    validate_spec(spec)
    return spec, audit


def extract_buggy(prompt: str) -> str:
    tail = prompt[bug_marker(prompt).end():]
    opening = re.search(r"^[ \t]*```(?:verilog|systemverilog|sv)?[ \t]*\r?$", tail, re.M | re.I)
    if opening is None:
        raise ValueError("missing Verilog code fence after 'has bug'")
    remaining = tail[opening.end():]
    closing = re.search(r"^[ \t]*```[ \t]*\r?$", remaining, re.M)
    if closing is None:
        raise ValueError("unterminated buggy code fence")
    code = remaining[:closing.start()].lstrip("\r\n").rstrip("\r\n") + "\n"
    module_span(code, "TopModule")
    return code


def rename_reference(reference: str) -> str:
    start, _ = module_span(reference, "RefModule")
    match = re.match(r"module\s+(RefModule)\b", reference[start:])
    assert match is not None
    lo, hi = start + match.start(1), start + match.end(1)
    return reference[:lo] + "TopModule" + reference[hi:]


def trace_columns(testbench: str) -> list[tuple[str, str]]:
    """(CSV column, SV expression), in declaration order and MSB first."""
    start, end = module_span(testbench, "tb")
    body = mask_comments(testbench[start:end])
    instance = re.search(r"\bTopModule\s+top_module1\s*\(([\s\S]*?)\)\s*;", body)
    if instance is None:
        raise ValueError("missing TopModule top_module1 instance in tb")
    connections = dict(re.findall(r"\.([A-Za-z_]\w*)\s*\(\s*([A-Za-z_]\w*_dut)\s*\)", instance[1]))
    if not connections or any(wire != port + "_dut" for port, wire in connections.items()):
        raise ValueError("expected named DUT output connections <port>_dut")
    wanted = set(connections.values())
    seen: set[str] = set()
    columns: list[tuple[str, str]] = []
    pattern = r"\b(?:wire|logic|reg)\s+(?:signed\s+)?(?:\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*)?([A-Za-z_]\w*_dut)\s*;"
    for match in re.finditer(pattern, body):
        left, right, signal = match.groups()
        if signal not in wanted:
            raise ValueError(f"unconnected DUT output {signal}")
        if signal in seen:
            raise ValueError(f"duplicate DUT output declaration {signal}")
        seen.add(signal)
        port = signal[:-4]
        if left is None:
            columns.append((port, signal))
        else:
            lo, hi = int(left), int(right)
            step = -1 if lo >= hi else 1
            columns.extend((f"{port}[{bit}]", f"{signal}[{bit}]")
                           for bit in range(lo, hi + step, step))
    if seen != wanted:
        raise ValueError(f"unsupported/missing DUT output declarations: {sorted(wanted - seen)}")
    return columns


def stimulus_columns(testbench: str) -> list[tuple[str, str, int]]:
    """(input port, connected TB signal, width), including clocks and resets.

    Use the testbench connections and concrete declarations rather than buggy
    RTL, which may have invalid/missing identifiers or parameterized widths.
    One binary string per input bus matches the existing stimulus parser.
    """
    start, end = module_span(testbench, "tb")
    body = mask_comments(testbench[start:end])
    instance = re.search(r"\bTopModule\s+top_module1\s*\(([\s\S]*?)\)\s*;", body)
    if instance is None:
        raise ValueError("missing TopModule top_module1 instance in tb")
    declarations = {}
    pattern = (r"\b(?:wire|logic|reg)\s+(?:signed\s+)?"
               r"(?:\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*)?"
               r"([A-Za-z_]\w*)\s*(?:=\s*[^;]+)?;")
    for match in re.finditer(pattern, body):
        left, right, signal = match.groups()
        declarations[signal] = abs(int(left) - int(right)) + 1 if left is not None else 1
    columns = []
    seen = set()
    for connection in instance[1].split(","):
        match = re.fullmatch(r"\s*\.([A-Za-z_]\w*)\s*(?:\(\s*([A-Za-z_]\w*)\s*\))?\s*", connection)
        if match is None:
            raise ValueError(f"unsupported DUT connection: {connection.strip()}")
        port, signal = match[1], match[2] or match[1]
        if port in seen:
            raise ValueError(f"duplicate DUT port {port}")
        seen.add(port)
        if signal.endswith("_dut"):
            continue
        if signal not in declarations:
            raise ValueError(f"unsupported/missing TB input declaration: {signal}")
        columns.append((port, signal, declarations[signal]))
    if not columns:
        raise ValueError("no DUT inputs found for stimulus log")
    return columns


def inject_trace(testbench: str) -> str:
    # Upstream dumpvars references tb_mismatch before its declaration under
    # default_nettype none, rejected by Icarus 13. Waveforms are unnecessary
    # for the CSV oracle. Remove only these two diagnostic system calls.
    testbench = re.sub(r'\$(?:dumpfile|dumpvars)\s*\([^;]*\)\s*;',
                       '// R3E: waveform dump disabled; use the visible CSV trace.', testbench)
    _, end = module_span(testbench, "tb")
    columns = trace_columns(testbench)
    inputs = stimulus_columns(testbench)
    body_start, _ = module_span(testbench, "tb")
    body = mask_comments(testbench[body_start:end])
    if "r3e_trace_fd" in body or re.search(r'\$fopen\s*\(', body):
        raise ValueError("testbench already contains trace/file instrumentation")
    # Match the upstream checker cadence, including both edges. $fstrobe runs
    # in the postponed region after all active/inactive/NBA updates settle.
    if not re.search(r"always\s*@\s*\(\s*posedge\s+clk\s*(?:,|or)\s*negedge\s+clk\s*\)", body):
        raise ValueError("unsupported checker clock; expected both edges of tb.clk")
    header = ",".join(["time", *(name for name, _ in columns)])
    fmt = ",".join(["%0t", *("%b" for _ in columns)])
    expressions = ", ".join(["$time", *(expr for _, expr in columns)])
    input_header = ",".join(["time", *(port for port, _, _ in inputs)])
    input_fmt = ",".join(["%0t", *("%b" for _ in inputs)])
    input_expressions = ", ".join(["$time", *(signal for _, signal, _ in inputs)])
    note = "# Inputs sampled after NBA on both tb.clk edges, row-aligned with trace_visible.txt; clocks and resets included"
    instrumentation = f'''
    // R3E visible output trace; one binary digit per DUT output bit.
    // Preserve upstream stimuli/checker. Sample after NBA updates settle.
    integer r3e_trace_fd, r3e_stimulus_fd, r3e_stimulation_fd;
    initial begin
        r3e_trace_fd = $fopen("{TRACE_NAME}", "w");
        if (r3e_trace_fd == 0) $fatal(1, "Cannot open {TRACE_NAME}");
        $fdisplay(r3e_trace_fd, "{header}");
        r3e_stimulus_fd = $fopen("{STIMULUS_NAME}", "w");
        r3e_stimulation_fd = $fopen("{STIMULATION_NAME}", "w");
        if (r3e_stimulus_fd == 0 || r3e_stimulation_fd == 0)
            $fatal(1, "Cannot open visible input logs");
        $fdisplay(r3e_stimulus_fd, "{note}");
        $fdisplay(r3e_stimulation_fd, "{note}");
        $fdisplay(r3e_stimulus_fd, "{input_header}");
        $fdisplay(r3e_stimulation_fd, "{input_header}");
    end
    always @(posedge clk or negedge clk) begin
        $fstrobe(r3e_trace_fd, "{fmt}", {expressions});
        $fstrobe(r3e_stimulus_fd, "{input_fmt}", {input_expressions});
        $fstrobe(r3e_stimulation_fd, "{input_fmt}", {input_expressions});
    end
    // Simulator exit flushes/closes the file. Closing it in a final block
    // could discard a pending postponed-region sample on the last edge.

'''
    return testbench[:end] + instrumentation + testbench[end:]


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def plan_conversion(source: Path, repo: Path, limit: int | None = None) -> tuple[list[dict], dict[Path, bytes]]:
    files: dict[Path, bytes] = {}
    rows: list[dict] = []
    for bug_type, expected in EXPECTED_COUNTS.items():
        folder = source / "Verilog Debugging" / f"dataset_debug_zero_shot_{bug_type}"
        prompts = sorted(folder.glob("Prob*_prompt.txt"))
        if len(prompts) != expected:
            raise ValueError(f"{folder.name}: expected {expected} prompts, found {len(prompts)}")
        cases = {p.name.removesuffix("_prompt.txt") for p in prompts}
        for suffix in ("_ref.sv", "_test.sv"):
            counterparts = {p.name.removesuffix(suffix) for p in folder.glob("Prob*" + suffix)}
            if cases != counterparts:
                raise ValueError(f"{folder.name}: unmatched {suffix} files")
        for prompt_path in prompts:
            problem = prompt_path.name.removesuffix("_prompt.txt")
            relative = Path("datasets/chipbench") / bug_type / problem
            try:
                prompt = prompt_path.read_text(encoding="utf-8")
                buggy = extract_buggy(prompt)
                spec, _ = extract_spec(prompt)
                reference_bytes = (folder / f"{problem}_ref.sv").read_bytes()
                reference = reference_bytes.decode("utf-8")
                testbench = (folder / f"{problem}_test.sv").read_text(encoding="utf-8")
                generated = {"buggy.sv": buggy.encode(), "golden.sv": rename_reference(reference).encode(),
                             "ref.sv": reference_bytes, "tb_visible.sv": inject_trace(testbench).encode(),
                             "spec.txt": spec.encode()}
            except ValueError as exc:
                raise ValueError(f"{bug_type}/{problem}: {exc}") from exc
            row = {
                "case_id": f"chipbench:{bug_type}:{problem}", "benchmark": "chipbench",
                "design": f"chipbench_{problem}__{bug_type}", "bug_type": bug_type,
                "golden_rtl": (relative / "golden.sv").as_posix(),
                "buggy_rtl": (relative / "buggy.sv").as_posix(),
                "spec": (relative / "spec.txt").as_posix(),
                "tb_sources": [(relative / "ref.sv").as_posix(), (relative / "tb_visible.sv").as_posix()],
                "top_module": "TopModule", "deps": [],
                "eligible": f"chipbench:{bug_type}:{problem}" not in INELIGIBLE_CASES,
            }
            if not row["eligible"]:
                row["ineligible_reason"] = INELIGIBLE_CASES[row["case_id"]]
            rows.append(row)
            files.update({repo / relative / name: payload for name, payload in generated.items()})
    found_ids = {row["case_id"] for row in rows}
    missing_exceptions = (set(INELIGIBLE_CASES) | COMPILE_REPAIR_CASES) - found_ids
    if missing_exceptions:
        raise ValueError(f"upstream case identity drift: {sorted(missing_exceptions)}")
    if limit is not None:
        rows = rows[:limit]
        keep = {repo / row[key] for row in rows for key in ("buggy_rtl", "golden_rtl", "spec")}
        keep.update(repo / name for row in rows for name in row["tb_sources"])
        files = {path: data for path, data in files.items() if path in keep}
    files[repo / "datasets/licenses/ChipBench-MIT.txt"] = (source / "LICENSE").read_bytes()
    return rows, files


def write_conversion(files: dict[Path, bytes], *, overwrite: bool) -> None:
    # Check every collision before writing any generated inputs.
    for path, payload in files.items():
        if path.exists() and path.read_bytes() != payload and not overwrite:
            raise ValueError(f"refusing to replace modified output: {path}; use --overwrite explicitly")
    for path, payload in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_bytes() != payload:
            path.write_bytes(payload)


def run_native(row: dict, repo: Path, rtl_key: str, cell: Path, timeout: float, repeat: int = 1) -> dict:
    cell.mkdir(parents=True)
    names = []
    for relative in (row[rtl_key], *row["tb_sources"]):
        path = repo / relative
        shutil.copyfile(path, cell / path.name)
        names.append(path.name)
    result: dict[str, Any] = {"ok": False}
    try:
        compile_run = subprocess.run(["iverilog", "-g2012", "-o", "a.out", *names],
                                     cwd=cell, capture_output=True, text=True, timeout=timeout)
        result["compile_warnings"] = compile_run.stderr
        if compile_run.returncode:
            result["error"] = "compile_error"
            return result
        traces = []
        stimuli = []
        mismatch_counts = []
        for _ in range(repeat):
            trace_path = cell / TRACE_NAME
            trace_path.unlink(missing_ok=True)
            for name in (STIMULUS_NAME, STIMULATION_NAME):
                (cell / name).unlink(missing_ok=True)
            run = subprocess.run(["vvp", "a.out"], cwd=cell, capture_output=True, text=True, timeout=timeout)
            result["stdout"] = run.stdout
            result["stderr"] = run.stderr
            if run.returncode or "TIMEOUT" in run.stdout:
                result["error"] = "simulation_error_or_upstream_timeout"
                return result
            counts = re.findall(r"^Mismatches:\s*(\d+)\s+in\s+(\d+)\s+samples", run.stdout, re.M)
            if len(counts) != 1 or not trace_path.is_file():
                result["error"] = "missing_mismatch_summary_or_trace"
                return result
            mismatches, samples = map(int, counts[0])
            trace = trace_path.read_text()
            lines = trace.splitlines()
            expected_header = ["time", *(c for c, _ in trace_columns((repo / row["tb_sources"][1]).read_text()))]
            if len(lines) < 3 or lines[0].split(",") != expected_header:
                result["error"] = "invalid_trace_header_or_too_few_rows"
                return result
            for line in lines[1:]:
                cells = line.split(",")
                if (len(cells) != len(expected_header) or not cells[0].isdigit()
                        or any(c.lower() not in {"0", "1", "x", "z"} for c in cells[1:])):
                    result["error"] = "invalid_trace_row"
                    return result
            stimulus_path = cell / STIMULUS_NAME
            alias_path = cell / STIMULATION_NAME
            if not stimulus_path.is_file() or not alias_path.is_file():
                result["error"] = "missing_stimulus_log"
                return result
            stimulus = stimulus_path.read_text()
            if stimulus != alias_path.read_text():
                result["error"] = "stimulus_alias_mismatch"
                return result
            input_lines = [line for line in stimulus.splitlines() if line and not line.startswith("#")]
            inputs = stimulus_columns((repo / row["tb_sources"][1]).read_text())
            if (len(input_lines) != len(lines)
                    or input_lines[0].split(",") != ["time", *(port for port, _, _ in inputs)]):
                result["error"] = "stimulus_header_or_length_mismatch"
                return result
            for output_line, input_line in zip(lines[1:], input_lines[1:]):
                cells = input_line.split(",")
                if (len(cells) != len(inputs) + 1 or cells[0] != output_line.split(",")[0]
                        or any(len(value) != width or not re.fullmatch(r"[01xz]+", value, re.I)
                               for value, (_, _, width) in zip(cells[1:], inputs))):
                    result["error"] = "invalid_or_unaligned_stimulus_row"
                    return result
            traces.append(trace)
            stimuli.append(stimulus)
            mismatch_counts.append(mismatches)
            result.update(mismatches=mismatches, checker_samples=samples, trace_rows=len(lines) - 1,
                          trace_sha256=sha256(trace.encode()), stimulus_rows=len(input_lines) - 1,
                          stimulus_sha256=sha256(stimulus.encode()),
                          stimulus_inputs=[port for port, _, _ in inputs])
        if repeat > 1:
            result["deterministic"] = (len(set(traces)) == 1 and len(set(stimuli)) == 1
                                       and len(set(mismatch_counts)) == 1)
        result["ok"] = True
        result["trace"] = traces[0]
        result["stimulus"] = stimuli[0]
    except subprocess.TimeoutExpired:
        result["error"] = "process_timeout"
    return result


def validate(rows: list[dict], repo: Path, manifest: Path, timeout: float) -> dict:
    for tool in ("iverilog", "vvp"):
        if shutil.which(tool) is None:
            raise ValueError(f"validation requires {tool}; install it or use --skip-validation")
    sys.path.insert(0, str(ROOT))
    from r3e.loop.corpus import load_public_manifest, split_by_cluster
    from r3e.loop.sim import Simulator
    from r3e.knowledge.feedback import compare_traces

    spec_hashes = {}
    for row in rows:
        path = repo / row["spec"]
        validate_spec(path.read_text(encoding="utf-8"))
        spec_hashes[row["case_id"]] = sha256(path.read_bytes())
    carriers, challenges = load_public_manifest(manifest, repo)
    eligible_rows = [row for row in rows if row["eligible"]]
    if (len(carriers) != len(eligible_rows)
            or {c.challenge_id for c in challenges} != {row["case_id"] for row in eligible_rows}):
        raise ValueError("R3E loader did not load exactly the eligible cases")
    split_membership = {c.carrier_id: split for split, members in split_by_cluster(carriers).items() for c in members}
    families: dict[str, set[str]] = {}
    for carrier in carriers:
        families.setdefault(carrier.cluster_id, set()).add(split_membership[carrier.carrier_id])
    if any(len(splits) != 1 for splits in families.values()):
        raise ValueError("design family leaked across splits")
    by_id = {c.challenge_id: c for c in challenges}
    results = []
    with tempfile.TemporaryDirectory(prefix="chipbench_validate_") as work:
        workspace = Path(work)
        simulator = Simulator(workspace / "runner")
        for index, row in enumerate(rows):
            if not row["eligible"]:
                results.append({"case_id": row["case_id"], "bug_type": row["bug_type"],
                                "eligible": False, "ineligible_reason": row["ineligible_reason"],
                                "status": "excluded", "passed": None, "errors": [],
                                "spec_verified": True, "spec_sha256": spec_hashes[row["case_id"]]})
                print(f"[{index + 1}/{len(rows)}] EXCLUDED {row['case_id']}: {row['ineligible_reason']}", flush=True)
                continue
            golden = run_native(row, repo, "golden_rtl", workspace / f"{index}_golden", timeout, repeat=2)
            buggy = run_native(row, repo, "buggy_rtl", workspace / f"{index}_buggy", timeout)
            compile_repair = row["case_id"] in COMPILE_REPAIR_CASES
            errors = []
            if not golden["ok"]:
                errors.append("golden_" + golden["error"])
            elif golden["mismatches"] != 0:
                errors.append("golden_has_native_mismatches")
            if golden["ok"] and not golden["deterministic"]:
                errors.append("golden_trace_nondeterministic")
            if compile_repair and not buggy["ok"] and buggy["error"] == "compile_error":
                buggy["expected_compile_failure"] = True
            elif not buggy["ok"]:
                errors.append("buggy_" + buggy["error"])
            elif buggy["mismatches"] <= 0:
                errors.append("buggy_has_no_native_mismatches")
            if golden["ok"] and buggy["ok"]:
                if not compare_traces(golden["trace"], buggy["trace"]).divergences:
                    errors.append("bug_not_visible_to_R3E_trace_comparator")
                if golden["stimulus"] != buggy["stimulus"]:
                    errors.append("stimulus_depends_on_DUT")
            runner_verdicts = {}
            # A native XOR checker can reject matching high-impedance outputs.
            # Still inspect the real R3E verdict when both traces are usable;
            # native gate failures remain failures in the qualification report.
            inputs_logged = False
            evidence_inputs_logged = False
            if golden["ok"] and golden["deterministic"] and (buggy["ok"] or buggy.get("expected_compile_failure")):
                challenge = by_id[row["case_id"]]
                try:
                    runner_verdicts["golden"] = simulator.verdict(challenge.carrier.clean_rtl, challenge.carrier).tier
                    verdict = simulator.verdict(challenge.buggy_rtl, challenge.carrier)
                    runner_verdicts["buggy"] = verdict.tier
                    expected_buggy_tier = "compile_fail" if compile_repair else "visible_fail"
                    if runner_verdicts != {"golden": "visible_pass", "buggy": expected_buggy_tier}:
                        errors.append("unexpected_R3E_runner_verdict")
                    inputs_logged = (simulator._stimulus.get(challenge.carrier.carrier_id) == golden["stimulus"])
                    if not inputs_logged:
                        errors.append("R3E_runner_did_not_read_stimulus")
                    evidence_inputs_logged = bool(verdict.window.get("inputs_logged"))
                    if not compile_repair and (not evidence_inputs_logged or
                                              not all(r.get("inputs") for r in verdict.window.get("rows", []))):
                        errors.append("R3E_evidence_window_missing_inputs")
                except (ValueError, RuntimeError) as exc:
                    errors.append(f"R3E_runner_error: {exc}")
            golden.pop("trace", None)
            buggy.pop("trace", None)
            golden.pop("stimulus", None)
            buggy.pop("stimulus", None)
            result = {"case_id": row["case_id"], "bug_type": row["bug_type"], "eligible": True,
                      "status": "passed" if not errors else "failed", "passed": not errors,
                      "task_type": "compile_repair" if compile_repair else "functional_repair",
                      "errors": errors, "golden": golden, "buggy": buggy, "runner_verdicts": runner_verdicts,
                      "spec_verified": True, "spec_sha256": spec_hashes[row["case_id"]],
                      "inputs_logged": inputs_logged, "evidence_inputs_logged": evidence_inputs_logged}
            results.append(result)
            print(f"[{index + 1}/{len(rows)}] {'PASS' if not errors else 'FAIL'} {row['case_id']}"
                  + (f": {', '.join(errors)}" if errors else ""), flush=True)
    per_type = {kind: {"total": sum(r["bug_type"] == kind for r in results),
                       "eligible": sum(r["bug_type"] == kind and r["eligible"] for r in results),
                       "passed": sum(r["bug_type"] == kind and r["passed"] is True for r in results)}
                for kind in EXPECTED_COUNTS}
    return {"validated": True, "total": len(rows), "eligible": len(eligible_rows),
            "ineligible": len(rows) - len(eligible_rows), "passed": sum(r["passed"] is True for r in results),
            "spec_files_verified": len(spec_hashes), "eligible_specs_verified": len(eligible_rows),
            "input_logs_verified": sum(r.get("inputs_logged", False) for r in results),
            "evidence_windows_with_inputs": sum(r.get("evidence_inputs_logged", False) for r in results),
            "per_bug_type": per_type, "design_families": len(families), "family_split_check": "passed",
            "manifest_sha256": sha256(manifest.read_bytes()), "cases": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT / "datasets/external/chipbench",
                        help="upstream ChipBench checkout, containing LICENSE and Verilog Debugging")
    parser.add_argument("--repo-root", type=Path, default=ROOT, help="output repository root")
    parser.add_argument("--limit", type=int, help="extract a deterministic prefix into chipbench_subset.jsonl")
    parser.add_argument("--skip-validation", action="store_true", help="only extract; no correctness claim")
    parser.add_argument("--overwrite", action="store_true", help="replace previously generated files that differ")
    parser.add_argument("--timeout", type=float, default=20.0, help="seconds per native compile/simulation")
    parser.add_argument("--report", type=Path, help="validation JSON (default artifacts/chipbench_conversion/validation.json)")
    args = parser.parse_args(argv)
    if (args.limit is not None and not 1 <= args.limit <= 89) or args.timeout <= 0:
        parser.error("--limit must be 1..89 and --timeout must be positive")
    repo = args.repo_root.resolve()
    try:
        if not args.skip_validation and not all(shutil.which(t) for t in ("iverilog", "vvp")):
            raise ValueError("validation requires iverilog and vvp; use --skip-validation for extraction only")
        rows, files = plan_conversion(args.source_root.resolve(), repo, args.limit)
        manifest = repo / "datasets/manifests" / ("chipbench_subset.jsonl" if args.limit else "chipbench89.jsonl")
        files[manifest] = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode()
        write_conversion(files, overwrite=args.overwrite)
        print(f"Converted {len(rows)} cases: {dict(Counter(row['bug_type'] for row in rows))}", flush=True)
        print(f"Manifest: {manifest}", flush=True)
        report = validate(rows, repo, manifest, args.timeout) if not args.skip_validation else {
            "validated": False, "total": len(rows), "eligible": sum(row["eligible"] for row in rows),
            "ineligible": sum(not row["eligible"] for row in rows),
            "manifest_sha256": sha256(manifest.read_bytes())}
        report["schema"] = "r3e-chipbench-conversion-validation-v3"
        report["task_inputs"] = ["spec", "buggy_rtl", "test_evidence"]
        report["output_sha256"] = {path.relative_to(repo).as_posix(): sha256(payload) for path, payload in files.items()}
        source = args.source_root.resolve()
        inputs = {source / "LICENSE"}
        spec_audit = []
        for row in rows:
            problem = row["case_id"].split(":", 2)[2]
            folder = source / "Verilog Debugging" / f"dataset_debug_zero_shot_{row['bug_type']}"
            inputs.update(folder / (problem + suffix) for suffix in ("_prompt.txt", "_ref.sv", "_test.sv"))
            spec, audit = extract_spec((folder / (problem + "_prompt.txt")).read_text(encoding="utf-8"))
            spec_audit.append({"case_id": row["case_id"], "eligible": row["eligible"], "spec": row["spec"],
                               "spec_sha256": sha256(spec.encode()),
                               "removed_code_blocks": [a for a in audit if a["reason"] == "fenced_code_block"],
                               "parameter_declarations_as_prose": [a for a in audit if a["reason"] == "numeric_parameter_to_prose"]})
        report["spec_audit"] = spec_audit
        report["input_sha256"] = {path.relative_to(source).as_posix(): sha256(path.read_bytes()) for path in sorted(inputs)}
        report_name = "validation_subset.json" if args.limit else "validation.json"
        report_path = args.report or repo / "artifacts/chipbench_conversion" / report_name
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Report: {report_path}")
        if report["validated"]:
            print(f"Validation: {report['passed']}/{report['eligible']} eligible cases passed; "
                  f"{report['ineligible']} excluded; {report['input_logs_verified']} input logs verified; "
                  f"{report['spec_files_verified']} specifications verified; "
                  "all cases retained in manifest")
            return 0 if report["passed"] == report["eligible"] else 1
        print("Validation skipped; generated cases have not been qualified")
        return 0
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
