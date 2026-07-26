import json
import re
import shutil
from pathlib import Path
from typing import List, Tuple

from .schemas import DesignInput, FrontendGateResult
from .tool_runner import run_cmd
from .failure_classifier import classify_frontend_failure
from .backend_contract import check_backend_contract, infer_suspected_patterns
from .include_resolver import resolve_include_dirs


INCLUDE_RE = re.compile(r'^\s*`include\s+"([^"]+)"', re.M)


def find_design_roots(design: DesignInput) -> List[Path]:
    roots = []
    seen = set()

    for f in design.rtl_files:
        p = Path(f).resolve()

        candidates = [
            p.parent,
            p.parent.parent,
            p.parent.parent.parent,
        ]

        for c in candidates:
            if c.exists():
                key = str(c)
                if key not in seen:
                    roots.append(c)
                    seen.add(key)

    return roots


def extract_include_names(design: DesignInput) -> List[str]:
    includes = []
    seen = set()

    for f in design.rtl_files:
        p = Path(f)

        if not p.exists():
            continue

        text = p.read_text(errors="ignore")

        for m in INCLUDE_RE.finditer(text):
            name = m.group(1)

            if name not in seen:
                includes.append(name)
                seen.add(name)

    return includes


def get_compile_context(design: DesignInput) -> dict:
    rtl_file_dirs = []
    seen = set()

    for f in design.rtl_files:
        d = Path(f).resolve().parent

        key = str(d)
        if key not in seen:
            rtl_file_dirs.append(d)
            seen.add(key)

    include_names = extract_include_names(design)
    design_roots = find_design_roots(design)

    include_dirs, include_resolution, missing_includes = resolve_include_dirs(
        include_names=include_names,
        rtl_file_dirs=rtl_file_dirs,
        design_roots=design_roots,
        global_roots=[Path("/path/to/rtl-data")],
        design_name=design.design_name,
    )

    return {
        "include_dirs": include_dirs,
        "include_names": include_names,
        "include_resolution": include_resolution,
        "missing_includes": missing_includes,
    }


def rtl_include_dirs(design: DesignInput) -> List[str]:
    return get_compile_context(design)["include_dirs"]


def run_iverilog_check(design: DesignInput, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)

    output = out_dir / f"{design.design_name}.iverilog.out"

    include_args = []
    for d in rtl_include_dirs(design):
        include_args.append(f"-I{d}")

    cmd = [
        "iverilog",
        "-g2012",
        "-Wall",
    ]

    cmd.extend(include_args)

    cmd.extend(
        [
            "-s",
            design.top_module,
            "-o",
            str(output),
        ]
    )

    cmd.extend(design.rtl_files)

    return run_cmd(cmd, timeout_s=120)


def materialize_yosys_bundle(design: DesignInput, out_dir: Path):
    """
    Create a Yosys-safe compile bundle.

    Yosys 0.9 does not reliably parse quoted -I paths when paths contain
    spaces such as '/OPEN Cores/'. Therefore, frontend gate copies RTL and
    include files into a local path-safe bundle before calling Yosys.
    """
    bundle = out_dir / "yosys_bundle"
    rtl_dir = bundle / "rtl"
    inc_dir = bundle / "include"

    if bundle.exists():
        shutil.rmtree(bundle)

    rtl_dir.mkdir(parents=True, exist_ok=True)
    inc_dir.mkdir(parents=True, exist_ok=True)

    bundled_rtl = []
    used_rtl_names = set()

    for idx, src in enumerate(design.rtl_files):
        src_p = Path(src)

        name = src_p.name
        if name in used_rtl_names:
            name = f"{idx:03d}_{name}"

        used_rtl_names.add(name)

        dst = rtl_dir / name
        shutil.copy2(src_p, dst)
        bundled_rtl.append(str(dst))

    used_inc_names = set()

    for d in rtl_include_dirs(design):
        d_p = Path(d)

        if not d_p.exists():
            continue

        for pattern in ["*.v", "*.vh", "*.sv", "*.svh"]:
            for src_p in d_p.glob(pattern):
                name = src_p.name

                if name in used_inc_names:
                    continue

                used_inc_names.add(name)

                dst = inc_dir / name
                shutil.copy2(src_p, dst)

    return bundled_rtl, [str(inc_dir), str(rtl_dir)]


def run_yosys_check(design: DesignInput, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)

    script = out_dir / f"{design.design_name}.ys"
    json_out = out_dir / f"{design.design_name}.yosys.json"

    bundled_rtl, bundled_inc_dirs = materialize_yosys_bundle(design, out_dir)

    include_flags = " ".join([f"-I{d}" for d in bundled_inc_dirs])
    rtl_files = " ".join(bundled_rtl)

    script_lines = [
        f"read_verilog -sv {include_flags} {rtl_files}",
        f"hierarchy -check -top {design.top_module}",
        "proc",
        "opt",
        "check",
        "stat",
        f"write_json {json_out}",
    ]

    script.write_text("\n".join(script_lines) + "\n")

    cmd = ["yosys", "-s", str(script)]
    return run_cmd(cmd, timeout_s=180)


def run_frontend_gate(design: DesignInput, out_root: Path) -> Tuple[FrontendGateResult, dict]:
    design_dir = out_root / design.design_name
    design_dir.mkdir(parents=True, exist_ok=True)

    result = FrontendGateResult(
        design_name=design.design_name,
        family=design.family,
        top_module=design.top_module,
        clock_port=design.resolved_clock_port,
        clock_period=design.clock_period,
        rtl_files=design.rtl_files,
    )

    missing_files = []
    for p in design.rtl_files:
        if not Path(p).exists():
            missing_files.append(p)

    result.file_exists_ok = len(missing_files) == 0

    if not result.file_exists_ok:
        result.failure_class = "F_FILE_MISSING"
        result.failure_reason = "; ".join(missing_files)

        (design_dir / "frontend_gate_result.json").write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
        )

        return result, {}

    contract_ok, contract_reasons = check_backend_contract(design)

    context = get_compile_context(design)
    (design_dir / "compile_context.json").write_text(
        json.dumps(context, indent=2, ensure_ascii=False)
    )

    iv = run_iverilog_check(design, design_dir)

    (design_dir / "iverilog.stdout.log").write_text(iv.stdout)
    (design_dir / "iverilog.stderr.log").write_text(iv.stderr)

    result.iverilog_ok = iv.ok

    ys = None

    if iv.ok:
        ys = run_yosys_check(design, design_dir)

        (design_dir / "yosys.stdout.log").write_text(ys.stdout)
        (design_dir / "yosys.stderr.log").write_text(ys.stderr)

        result.yosys_ok = ys.ok
    else:
        result.yosys_ok = False

    combined_log = iv.stdout + "\n" + iv.stderr

    if ys:
        combined_log += "\n" + ys.stdout + "\n" + ys.stderr

    result.failure_class = classify_frontend_failure(
        result.iverilog_ok,
        result.yosys_ok,
        combined_log,
        iverilog_returncode=iv.returncode,
        yosys_returncode=ys.returncode if ys else 0,
    )

    if result.failure_class == "PASS":
        result.failure_reason = ""
    else:
        result.failure_reason = combined_log[-2000:]

    result.backend_contract_ok = bool(
        contract_ok and result.iverilog_ok and result.yosys_ok
    )

    if result.iverilog_ok and result.yosys_ok and not contract_ok:
        result.failure_class = "F8_BACKEND_CONTRACT_FAILURE"
        result.failure_reason = "; ".join(contract_reasons)
        result.backend_contract_ok = False

    result.suspected_patterns = infer_suspected_patterns(combined_log)
    result.handoff_ready = bool(result.backend_contract_ok)

    tool_meta = {
        "iverilog": iv.__dict__,
        "yosys": ys.__dict__ if ys else None,
        "compile_context": context,
    }

    (design_dir / "frontend_gate_result.json").write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
    )

    return result, tool_meta
