"""持久 openroad 进程会话(批跑性能使能器).

现状每候选 eval 重 spawn openroad + 读 8 LEF + 29 lib + read_db(~7.4s, 占 99%),
实际 swap+STA 仅 ~0.1s. PdkSession 开一次/载一次, 之后每候选在同一进程内增量评估:
  eval_swap: replace_cell → (estimate) → worst_slack → replace_cell 撤销  (~0.1s)
  commit_swap: replace_cell(不撤销, base 推进)
预期 ~40-50× 提速. 与 spawn 模式(pc.apply_and_eval/eval_wns) WNS 必须一致(正确性).

I/O 协议: openroad 交互模式读 stdin; 每命令后追加 `puts <MARKER>`, 读 stdout 到 MARKER 止.
VT-swap 同 footprint, 线 RC 不变 → estimate_parasitics 是否需重跑由 ESTIMATE_AFTER_SWAP 控制
(实测校准: 与 spawn 模式比对决定).
"""
from __future__ import annotations

import os
import re
import subprocess
import uuid
from pathlib import Path

from microsurgeon_flow import backend_eco_oneshot as _be
from microsurgeon_flow import pdk_config as _pc

_WS_RE = re.compile(r"WS_VAL:\s*([-+]?\d+\.?\d*(?:[eE][-+]?\d+)?)")
_REF_RE = re.compile(r"REF:\s*(\S+)")
# VT-swap 不改线 RC; sizing(跨族)可能改 load. 默认 swap 后重 estimate 保正确性,
# 校准证明可省则置 False 提速.
ESTIMATE_AFTER_SWAP = True


class PdkSession:
    def __init__(self, pdk, odb, sdc, timeout: int = 300):
        self.pdk = pdk
        self.timeout = timeout
        env = {**os.environ, "IN_NIX_SHELL": "1"}
        self._p = subprocess.Popen(
            [_be.OPENROAD, "-no_splash"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
        self._raw(_pc.preamble(pdk, Path(odb), Path(sdc)))   # 载一次, 不 exit
        self._ready()                                        # 等加载完成

    # ── 底层 I/O ──────────────────────────────────────────────────────────
    def _raw(self, tcl: str):
        self._p.stdin.write(tcl if tcl.endswith("\n") else tcl + "\n")
        self._p.stdin.flush()

    def _read_until(self, marker: str) -> str:
        # openroad 交互模式回显命令(`openroad> puts "X"`), 回显含 'puts'+marker/值会污染.
        # 真 puts 输出行不含 'puts'. 故: marker 只认非回显真输出; 跳过 puts 回显行.
        lines = []
        while True:
            line = self._p.stdout.readline()
            if line == "":
                raise RuntimeError("openroad session 进程意外退出:\n" + "".join(lines[-20:]))
            if marker in line and "puts" not in line:
                return "".join(lines)
            if "puts" in line:          # 跳过 puts 命令回显(防 REF/WS 解析撞回显)
                continue
            lines.append(line)

    def _ready(self):
        marker = f"__READY_{uuid.uuid4().hex}__"
        self._raw(f'puts "{marker}"')
        self._read_until(marker)

    def _send(self, tcl: str) -> str:
        marker = f"__DONE_{uuid.uuid4().hex}__"
        self._raw(tcl + f'\nputs "{marker}"')
        return self._read_until(marker)

    # ── 业务 API ──────────────────────────────────────────────────────────
    def _worst_slack(self, estimate: bool) -> float | None:
        pre = "estimate_parasitics -placement\n" if estimate else ""
        out = self._send(pre + 'puts "WS_VAL: [sta::worst_slack -max]"')
        m = _WS_RE.search(out)
        return float(m.group(1)) if m else None

    def worst_slack(self) -> float | None:
        return self._worst_slack(estimate=True)

    def report_path(self) -> str:
        return self._send("report_checks -path_delay max -format full")

    def ref_name(self, inst: str) -> str | None:
        out = self._send(f'puts "REF: [get_property [get_cells {{{inst}}}] ref_name]"')
        m = _REF_RE.search(out)
        return m.group(1) if m else None

    def eval_swap(self, inst: str, new_master: str) -> float | None:
        """transient: 换→评→撤销. base 不变. 返回换后 WNS."""
        old = self.ref_name(inst)
        if not old:
            return None
        self._send(f"replace_cell {{{inst}}} {new_master}")
        ws = self._worst_slack(ESTIMATE_AFTER_SWAP)
        self._send(f"replace_cell {{{inst}}} {old}")        # 撤销
        if ESTIMATE_AFTER_SWAP:
            self._send("estimate_parasitics -placement")    # 恢复 base parasitics
        return ws

    def commit_swap(self, inst: str, new_master: str):
        """永久施加(base 推进), 不撤销."""
        self._send(f"replace_cell {{{inst}}} {new_master}")
        if ESTIMATE_AFTER_SWAP:
            self._send("estimate_parasitics -placement")

    def save_odb(self, path):
        self._send(f"write_db {path}")
        return str(path)

    def close(self):
        try:
            self._raw("exit")
            self._p.wait(timeout=30)
        except Exception:
            self._p.kill()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
