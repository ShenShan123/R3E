#!/usr/bin/env python3
"""
report_normalizer.py — Phase 2 核心
=============================
将 OpenROAD 输出的海量日志 / 时序报告解析为结构化 JSON。

支持解析内容：
  • WNS / TNS 汇总数字
  • 各条违例路径的 startpoint / endpoint / slack
  • 阶段标签（synth / place / cts / route）

典型输入（from OpenROAD v2.0 route report）：
    Startpoint: dpath.a_reg.out[8]$_DFFE_PP_
                (rising edge-triggered flip-flop clocked by core_clock)
    Endpoint: resp_msg[9] (output port clocked by core_clock)
    Path Group: core_clock
    Path Type: max
      ...
             -0.22   slack (VIOLATED)
    openroad> report_wns
    wns -0.22
    openroad> report_tns
    tns -10.09

输出 JSON：
{
  "stage": "route",
  "timing": { "wns": -0.22, "tns": -10.09 },
  "critical_paths": [
    {"start": "dpath.a_reg.out[8]", "end": "resp_msg[9]", "slack": -0.22}
  ]
}
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


# ------------------------------------------------------------------
# Dataclasses
# ------------------------------------------------------------------

@dataclass
class CriticalPath:
    """单条时序路径"""
    start: str
    end:   str
    slack: float   # 负数 = 违例

    def asdict(self) -> dict:
        return asdict(self)


@dataclass
class TimingSummary:
    """解析后的时序元数据"""
    stage:          str
    wns:            float
    tns:            float
    critical_paths: list[CriticalPath]
    drc_count:      int = 0   # DRC violation count (default 0 — set by adapter if available)
    num_setup:      int = 0   # number of setup (max) violations — equals len(critical_paths)

    def asdict(self) -> dict:
        return {
            "stage": self.stage,
            "timing": {
                "wns": self.wns,
                "tns": self.tns,
            },
            "critical_paths": [p.asdict() for p in self.critical_paths],
        }

    def to_json(self, **kwargs) -> str:
        return json.dumps(self.asdict(), indent=2, **kwargs)

    @property
    def is_violated(self) -> bool:
        return self.wns < 0.0


# ------------------------------------------------------------------
# 正则模式（Strategy 1: fast per-line scan）
# ------------------------------------------------------------------

# WNS: 两种格式都支持
#  1. "Worst Negative Slack (WNS)           :  -0.224"
#  2. "wns -0.22"
_WNS_RE = re.compile(
    r"(?:Worst\s+Negative\s+Slack\s+\(WNS\)\s*[^\d-]*|wns\s+)([-+]?\d+\.?\d*)",
    re.IGNORECASE,
)
_TNS_RE = re.compile(
    r"(?:Worst\s+Negative\s+Slack\s+\(TNS\)\s*[^\d-]*|tns\s+)([-+]?\d+\.?\d*)",
    re.IGNORECASE,
)
_STAGE_RE = re.compile(
    r"(synthesis|synth|placement|floorplan|clock[\s_-]tree|cts|"
    r"global\s*route|detailed[\s_-]?route|route)",
    re.IGNORECASE,
)

# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------

def _normalize_val(s: str) -> float:
    """字符串 → float，空值默认返回 0.0"""
    s = s.strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _clean_node(name: str) -> str:
    """
    清理节点名：
      - 去掉寄存器后缀 _C / _R / _Q / _DFFE_PP_
      - 去掉括号内描述 (如 "(output port ...)")
    例: "dpath.a_reg.out[8]$_DFFE_PP_" → "dpath.a_reg.out[8]"
    """
    name = re.sub(r"\s*\(.*?\)", "", name)          # 去掉 (FD1) / (output port ...)
    name = re.sub(r"_(C|R|Q|DFFE_PP_)$", "", name)   # 去掉 _C / _DFFE_PP_ 后缀
    return name.strip()


def _extract_stage(text: str) -> str:
    """根据日志内容推断当前 EDA 阶段"""
    m = _STAGE_RE.search(text)
    if not m:
        return "unknown"
    raw = m.group(1).lower().replace(" ", "")
    if "synth"  in raw: return "synth"
    if "floorplan" in raw: return "floorplan"
    if "place"  in raw: return "place"
    if "clock"  in raw or "cts" in raw: return "cts"
    if "global" in raw: return "groute"
    if "detail" in raw or "route" in raw: return "route"
    return raw


# ------------------------------------------------------------------
# Strategy 2: 行级解析器（对 OpenROAD v2.0 格式更鲁棒）
# ------------------------------------------------------------------

def _extract_paths(text: str) -> list[CriticalPath]:
    """
    逐行扫描，提取所有违例路径。

    适用于 OpenROAD v2.0 真实格式：
      Startpoint: dpath.a_reg.out[8]$_DFFE_PP_
                  (rising edge-triggered ...)
      Endpoint: resp_msg[9] (output port ...)
      ...
                -0.22   slack (VIOLATED)
    """
    paths: list[CriticalPath] = []
    seen: set[tuple[str, str]] = set()

    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # 触发点：识别 Startpoint 行
        if line.startswith("Startpoint:"):
            start_raw = line[len("Startpoint:"):].strip()

            # Advance: skip the description line "(rising edge-triggered ...)"
            # 它可能和 Startpoint 在同一行（inline），也可能单独一行
            j = i + 1
            if j < len(lines) and lines[j].strip().startswith("("):
                # Description on next line — skip
                j += 1

            # Now find Endpoint
            endpoint_line = ""
            while j < len(lines):
                if lines[j].strip().startswith("Endpoint:"):
                    endpoint_line = lines[j].strip()
                    break
                j += 1

            if not endpoint_line:
                i += 1
                continue

            # Extract endpoint name: "Endpoint: resp_msg[9] (output port ...)"
            # Strip "Endpoint:" prefix
            end_raw = endpoint_line[len("Endpoint:"):].strip()
            # Remove inline parenthetical description
            end_raw = re.sub(r"\s*\(.*?$", "", end_raw).strip()

            # Now scan ahead for slack (VIOLATED) value
            # Note: OpenROAD format is: "   -0.22   slack (VIOLATED)"
            # The value comes BEFORE the word "slack"
            slack_val = None
            for k in range(i, min(i + 80, len(lines))):
                sl = lines[k].strip()
                # Pattern: "-0.22   slack (VIOLATED)" or similar
                m = re.search(r"^([-+]?\d+\.?\d*)\s+slack\s+\(?VIOLATED\)?", sl, re.IGNORECASE)
                if m:
                    slack_val = m.group(1)
                    break

            if slack_val is None:
                i += 1
                continue

            try:
                slack = float(slack_val)
            except ValueError:
                i += 1
                continue

            start = _clean_node(start_raw)
            end   = _clean_node(end_raw)
            key   = (start, end)
            if key in seen:
                i += 1
                continue
            seen.add(key)
            paths.append(CriticalPath(start=start, end=end, slack=slack))

        i += 1

    return paths


# ------------------------------------------------------------------
# 主解析函数
# ------------------------------------------------------------------

def parse_openroad_report(
    report_text: str | Path,
    *,
    stage_hint: Optional[str] = None,
    max_paths: int = 100,
) -> TimingSummary:
    """
    将 OpenROAD 文本报告解析为 TimingSummary。

    Parameters
    ----------
    report_text : str | Path
        原始日志内容，或指向 .rpt / .log 文件的路径。
    stage_hint : str, optional
        强制指定阶段标签（覆盖自动推断）。
    max_paths : int
        返回的 critical_paths 上限（避免超长日志导致内存爆炸）。

    Returns
    -------
    TimingSummary
        wns / tns 默认为 0.0（当报告不含违例时）。

    Raises
    ------
    FileNotFoundError
        当文件不存在时。
    UnicodeDecodeError
        当文件无法以 UTF-8 解码时。
    """
    if isinstance(report_text, Path):
        try:
            report_text = report_text.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            raise FileNotFoundError(f"Report file not found: {report_text}")

    # 1. WNS
    wns_m = _WNS_RE.search(report_text)
    wns = _normalize_val(wns_m.group(1)) if wns_m else 0.0

    # 2. TNS
    tns_m = _TNS_RE.search(report_text)
    tns = _normalize_val(tns_m.group(1)) if tns_m else 0.0

    # 3. 违例路径（行级解析，更鲁棒）
    paths = _extract_paths(report_text)[:max_paths]

    # 4. Stage
    stage = stage_hint if stage_hint else _extract_stage(report_text)

    # 5. DRC (TODO: extract from report if available)
    drc_count = 0

    # 6. num_setup = count of violating paths (setup = max delay paths)
    num_setup = len(paths)

    return TimingSummary(
        stage          = stage,
        wns            = wns,
        tns            = tns,
        critical_paths = paths,
        drc_count      = drc_count,
        num_setup      = num_setup,
    )


def parse_file(path: str | Path, **kwargs) -> TimingSummary:
    """parse_openroad_report 的别名，方便 CLI 调用"""
    return parse_openroad_report(Path(path), **kwargs)


# ------------------------------------------------------------------
# CLI 入口
# ------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: report_normalizer.py <openroad_report.log> [stage_hint]")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"[ERROR] File not found: {path}", file=sys.stderr)
        sys.exit(1)

    stage_hint = sys.argv[2] if len(sys.argv) > 2 else None
    summary = parse_openroad_report(path, stage_hint=stage_hint)
    print(summary.to_json())


if __name__ == "__main__":
    main()