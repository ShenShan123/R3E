"""Compile and run one design under one testbench with Icarus Verilog."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _set_pdeathsig() -> None:
    """Linux: the simulator is killed if its parent dies, so no orphaned ``vvp`` keeps running."""
    try:
        import ctypes
        import signal

        ctypes.CDLL("libc.so.6").prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG
    except Exception:  # noqa: BLE001 - best effort on non-Linux hosts
        pass


def simulate(dut_files, tb_files, output_name: str, work_dir: Path, timeout: float) -> tuple[str | None, str]:
    """Stage the files in ``work_dir``, compile with ``iverilog -g2012``, run ``vvp``.

    Returns ``(text of output_name, "")`` on success, or ``(None, error)``.
    ``output_name`` must be relative (the testbench writes it in ``work_dir``).
    """
    work_dir = Path(work_dir)
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_path = Path(output_name)
    if output_path.is_absolute() or ".." in output_path.parts:
        return None, f"unsafe_output_path({output_name})"

    names, seen, staged = [], set(), {}
    for f in [*dut_files, *tb_files]:
        f = Path(f)
        resolved = f.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        prior = staged.get(f.name)
        if prior is not None and prior != resolved:
            return None, f"basename_collision({f.name}): {prior} != {resolved}"
        staged[f.name] = resolved
        shutil.copy2(f, work_dir / f.name)
        names.append(f.name)

    try:
        compiled = subprocess.run(["iverilog", "-g2012", "-o", "a.out", *names], cwd=work_dir,
                                  capture_output=True, text=True, timeout=timeout, preexec_fn=_set_pdeathsig)
    except subprocess.TimeoutExpired:
        return None, "compile_timeout"
    if compiled.returncode != 0:
        return None, f"compile_err: {(compiled.stderr or compiled.stdout).strip()[:300]}"
    try:
        run = subprocess.run(["vvp", "a.out"], cwd=work_dir, capture_output=True, text=True,
                             timeout=timeout, preexec_fn=_set_pdeathsig)
    except subprocess.TimeoutExpired:
        return None, "sim_timeout"
    if run.returncode != 0:
        return None, f"sim_err({run.returncode}): {(run.stderr or run.stdout).strip()[:300]}"
    out = work_dir / output_path
    if not out.exists():
        return None, f"no_output({output_name})"
    return out.read_text(errors="ignore"), ""
