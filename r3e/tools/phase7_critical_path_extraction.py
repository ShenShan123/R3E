#!/usr/bin/env python3
"""
phase7_critical_path_extraction.py — Phase 7: Real Critical Path Extraction
================================================================================
从 OpenROAD report_checks 输出中提取真实关键路径信息，为 LLM 生成精准修复代码提供物理上下文。

Usage:
    python3 phase7_critical_path_extraction.py --design gcd --period 0.18 --verify
"""

from __future__ import annotations

import argparse
import os
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


# ═══════════════════════════════════════════════════════════════════════════════
#  Task 1: Timing Report Parser (extract_critical_paths)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class CriticalPathSegment:
    """关键路径上的一个时序段（门或连线）"""
    instance_name: str          # 实例名（如 _637_）
    cell_type: str             # 单元类型（如 BUF_X4, NAND2_X2）
    pin_name: str              # 引脚名（如 Z, ZN, CK, D）
    delay: float               # 该段延迟（ns）
    cumulative_time: float     # 累积时间（ns）
    transition: str            # 转换方向（^ = rising, v = falling）


@dataclass
class CriticalPath:
    """一条完整的时序关键路径"""
    startpoint: str                    # 起点（触发器输出）
    endpoint: str                      # 终点（触发器输入）
    path_group: str                    # 路径组（如 core_clock）
    path_type: str                     # 路径类型（max/min）
    data_arrival_time: float           # 数据到达时间
    data_required_time: float          # 数据要求时间
    slack: float                       # 时序裕量（负值 = 违例）
    segments: list[CriticalPathSegment] = field(default_factory=list)  # 路径段

    def total_delay(self) -> float:
        """计算路径总延迟"""
        return sum(seg.delay for seg in self.segments)

    def get_key_instances(self, top_n: int = 5) -> list[tuple[str, float]]:
        """获取延迟贡献最大的前 N 个实例"""
        instances = [(seg.instance_name, seg.delay) for seg in self.segments if seg.instance_name]
        return sorted(instances, key=lambda x: x[1], reverse=True)[:top_n]


@dataclass
class TimingReport:
    """完整的时序报告"""
    wns: float                         # Worst Negative Slack
    tns: float                         # Total Negative Slack
    num_violations: int                # 违例数量
    critical_paths: list[CriticalPath] = field(default_factory=list)

    def worst_path(self) -> Optional[CriticalPath]:
        """返回最差路径"""
        if not self.critical_paths:
            return None
        return min(self.critical_paths, key=lambda p: p.slack)


def extract_critical_paths(report_text: str) -> TimingReport:
    """
    Task 1: 从 OpenROAD report_checks 输出中提取关键路径信息。

    解析字段：
    - Startpoint / Endpoint（起点/终点寄存器）
    - Data Arrival Time / Data Required Time（到达/要求时间）
    - Slack（时序裕量）
    - Path segments（路径上的门实例）

    Args:
        report_text: OpenROAD report_checks 的原始文本输出

    Returns:
        TimingReport: 结构化时序报告对象
    """
    lines = report_text.splitlines()
    paths: list[CriticalPath] = []
    current_path: Optional[CriticalPath] = None
    current_segments: list[CriticalPathSegment] = []

    i = 0
    while i < len(lines):
        raw_line = lines[i]

        # ── 识别新路径开始 ─────────────────────────────────────────────
        if raw_line.startswith("Startpoint:"):
            # 保存之前的路径
            if current_path is not None:
                current_path.segments = list(current_segments)
                paths.append(current_path)

            # 提取起点
            m = re.search(r"Startpoint:\s+(\S+)", raw_line)
            startpoint = m.group(1) if m else "unknown"

            # 跳过下一行（时钟描述）
            i += 2
            current_path = CriticalPath(
                startpoint=startpoint,
                endpoint="",
                path_group="",
                path_type="max",
                data_arrival_time=0.0,
                data_required_time=0.0,
                slack=0.0,
            )
            current_segments = []
            continue

        # ── 提取终点 ────────────────────────────────────────────────────
        if raw_line.startswith("Endpoint:") and current_path is not None:
            m = re.search(r"Endpoint:\s+(\S+)", raw_line)
            if m:
                current_path.endpoint = m.group(1)
            i += 2  # 跳过时钟描述行
            continue

        # ── 提取路径组 ───────────────────────────────────────────────────
        if raw_line.startswith("Path Group:") and current_path is not None:
            current_path.path_group = raw_line.split(":", 1)[1].strip()
            i += 1
            continue

        # ── 提取路径类型 ─────────────────────────────────────────────────
        if raw_line.startswith("Path Type:") and current_path is not None:
            current_path.path_type = raw_line.split(":", 1)[1].strip()
            i += 1
            continue

        # ── 提取路径段（门实例）─────────────────────────────────────────
        # 格式（精确）："   0.08    0.08 v dpath.a_reg.out[14]$_DFFE_PP_/Q (DFF_X1)"
        # 或："   0.03    0.11 v _637_/Z (BUF_X4)"
        if current_path is not None and len(raw_line) > 35:
            seg_match = re.match(
                r"\s{3}(?P<delay>[\d.]+)\s+(?P<cum_time>[\d.]+)\s+(?P<trans>[\^v])\s+(?P<pin>\S+)\s+\((?P<cell>[\w_]+)\)",
                raw_line,
            )
            if seg_match:
                pin_full = seg_match.group("pin")
                if "/" in pin_full:
                    instance_name, pin_name = pin_full.rsplit("/", 1)
                else:
                    instance_name, pin_name = pin_full, ""
                current_segments.append(CriticalPathSegment(
                    instance_name=instance_name,
                    cell_type=seg_match.group("cell"),
                    pin_name=pin_name,
                    delay=float(seg_match.group("delay")),
                    cumulative_time=float(seg_match.group("cum_time")),
                    transition=seg_match.group("trans"),
                ))
                i += 1
                continue

        # ── 提取到达时间 ─────────────────────────────────────────────────
        # 行如: "           0.39   data arrival time"
        if "data arrival time" in raw_line and current_path is not None:
            m = re.search(r"^\s+([\d.]+)\s+data arrival time", raw_line)
            if m:
                current_path.data_arrival_time = float(m.group(1))
            i += 1
            continue

        # ── 提取要求时间 ─────────────────────────────────────────────────
        # 行如: "           0.15   data required time"
        if "data required time" in raw_line and current_path is not None:
            if "library setup time" not in raw_line:
                m = re.search(r"^\s+([\d.]+)\s+data required time", raw_line)
                if m:
                    current_path.data_required_time = float(m.group(1))
            i += 1
            continue

        # ── 提取 Slack（违例判断）────────────────────────────────────────
        # 行如: "          -0.24   slack (VIOLATED)"
        if "slack (VIOLATED)" in raw_line and current_path is not None:
            m = re.search(r"^\s+([-\d.]+)\s+slack", raw_line)
            if m:
                current_path.slack = float(m.group(1))
            i += 1
            continue

        i += 1

    # 保存最后一条路径
    if current_path is not None:
        current_path.segments = list(current_segments)
        paths.append(current_path)

    # 计算 WNS / TNS（fallback：若路径解析失败，从 stdout 文本估算）
    wns = min((p.slack for p in paths if p.slack < 0), default=0.0)
    tns = sum((p.slack for p in paths if p.slack < 0), 0.0)
    num_violations = len([p for p in paths if p.slack < 0])

    # Fallback: 从原始文本中直接正则估算 WNS/TNS（report_checks 输出格式）
    if not paths:
        for line in report_text.splitlines():
            m = re.search(r"slack\s+-\s*([\d.]+)", line, re.IGNORECASE)
            if m and wns == 0.0:
                wns = -float(m.group(1))
            m = re.search(r"tns\s+([-\d.]+)", line, re.IGNORECASE)
            if m:
                tns = float(m.group(1))

    return TimingReport(
        wns=wns,
        tns=tns,
        num_violations=num_violations,
        critical_paths=paths,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Task 2: Context-Aware Prompt Generation (build_critical_path_prompt)
# ═══════════════════════════════════════════════════════════════════════════════

def build_critical_path_prompt(
    timing_report: TimingReport,
    design_name: str,
    clock_period_ns: float,
    available_skills: list[dict[str, Any]],
) -> str:
    """
    Task 2: 基于真实物理时序信息生成 LLM Prompt。

    将关键路径提取的信息与可用技能结合，生成可发送给大模型的修复提示。

    Args:
        timing_report: 解析后的时序报告
        design_name: 设计名称
        clock_period_ns: 时钟周期（ns）
        available_skills: 可用的修复技能列表

    Returns:
        str: 格式化后的 LLM Prompt 文本
    """
    worst_path = timing_report.worst_path()
    if not worst_path:
        return "# 错误：未找到关键路径"

    # 提取 Top 5 关键实例（延迟贡献最大）
    key_instances = worst_path.get_key_instances(top_n=5)

    # 构建技能建议
    skill_suggestions = []
    for skill in available_skills:
        strategy = skill.get("repair_strategy", "unknown")
        if strategy == "pipeline_stage":
            skill_suggestions.append(
                f"- **{strategy}**: 在路径 `{worst_path.startpoint}` → `{worst_path.endpoint}` 之间插入流水线寄存器，"
                f"将长组合逻辑分为两级（周期 {clock_period_ns}ns → 2×{clock_period_ns/2}ns）"
            )
        elif strategy == "gate_sizing_up":
            instances_str = ", ".join([f"`{inst}`" for inst, _ in key_instances[:3]])
            skill_suggestions.append(
                f"- **{strategy}**: 将路径上的关键门 {instances_str} 升级为更高驱动强度的单元"
            )
        elif strategy == "buffer_insertion":
            skill_suggestions.append(
                f"- **{strategy}**: 在 `{worst_path.startpoint}` 后添加缓冲器以减少扇出延迟"
            )

    prompt = f"""# R3E 时序违例修复请求

## 设计信息

- **设计名称**: {design_name}
- **时钟周期**: {clock_period_ns} ns
- **目标频率**: {1000/clock_period_ns:.1f} MHz

## 时序违例分析（物理反馈）

### 整体时序状况

| 指标 | 数值 | 状态 |
|------|------|------|
| WNS (Worst Negative Slack) | {timing_report.wns:+.4f} ns | ❌ 违例 |
| TNS (Total Negative Slack) | {timing_report.tns:+.4f} ns | ❌ 累积 |
| 违例路径数 | {timing_report.num_violations} | - |

### 最差关键路径详情

```
起点 (Startpoint): {worst_path.startpoint}
  └─ 上升沿触发寄存器（clocked by {worst_path.path_group}）

终点 (Endpoint):   {worst_path.endpoint}
  └─ 上升沿触发寄存器（clocked by {worst_path.path_group}）

路径组 (Path Group): {worst_path.path_group}
路径类型 (Path Type): {worst_path.path_type}
```

#### 时序计算

| 阶段 | 时间 (ns) |
|------|-----------|
| 数据到达时间 (Data Arrival Time) | {worst_path.data_arrival_time:.4f} |
| 数据要求时间 (Data Required Time) | {worst_path.data_required_time:.4f} |
| **时序裕量 (Slack)** | **{worst_path.slack:+.4f}** ⚠️ |

**结论**: 数据到达比要求晚 {abs(worst_path.slack):.4f}ns，需优化 {abs(worst_path.slack)/clock_period_ns*100:.1f}% 的路径延迟。

#### 路径延迟分解（Top 5 关键实例）

| 序号 | 实例名 | 单元类型 | 延迟贡献 (ns) | 占比 |
|------|--------|----------|---------------|------|
{chr(10).join([f"| {i+1} | `{inst}` | `{seg.cell_type if (seg := next((s for s in worst_path.segments if s.instance_name == inst), None)) else 'N/A'}` | {delay:.4f} | {delay/abs(worst_path.slack)*100:.1f}% |" for i, (inst, delay) in enumerate(key_instances)])}

**路径延迟总计**: {worst_path.total_delay():.4f}ns

## 可用修复策略

系统已识别的候选技能：

{chr(10).join(skill_suggestions)}

## 修复要求

请生成 **Verilog 代码修改**，以修复上述时序违例。

### 约束条件

1. **功能性**: 修改必须保持设计功能不变（仅优化时序）
2. **流水线**: 若选择插入流水线，需正确处理握手信号（req_val / resp_val）
3. **时钟域**: 所有新寄存器使用同一时钟 `core_clock`
4. **门控**: 允许添加使能信号，但必须保持原有控制逻辑

### 输出格式

请按以下 JSON 格式输出修复方案：

```json
{{
  "fix_strategy": "pipeline_stage|gate_sizing_up|buffer_insertion|compound",
  "rationale": "选择此策略的理由（引用上述物理路径分析）",
  "confidence": 0.85,
  "edits": [
    {{
      "filepath": "gcd.v",
      "module": "GcdUnitDpathRTL",
      "description": "修改描述",
      "original_code": "<原始代码片段（3-5行）>",
      "patched_code": "<修改后代码片段（完整替换）>",
      "target_instances": ["_637_", "_721_"],
      "expected_delay_reduction_ns": 0.15
    }}
  ],
  "timing_projection": {{
    "original_wns_ns": {timing_report.wns:.4f},
    "projected_wns_ns": "预测修复后 WNS",
    "improvement_percentage": "预计改善百分比"
  }}
}}
```

### 物理上下文引用

修复必须针对以下真实物理实例：
- 起点寄存器: `{worst_path.startpoint}`
- 终点寄存器: `{worst_path.endpoint}`
- 关键组合门: {", ".join([f"`{inst}`({delay:.3f}ns)" for inst, delay in key_instances[:3]])}

请基于上述物理反馈生成精准修复方案。
"""

    return prompt


# ═══════════════════════════════════════════════════════════════════════════════
#  Task 3: Local Verification (Dry Run)
# ═══════════════════════════════════════════════════════════════════════════════

def run_openroad_and_extract(
    design_name: str,
    clock_period_ns: float,
    work_dir: Path,
) -> tuple[Optional[TimingReport], str]:
    """
    运行 OpenROAD 并提取关键路径信息。
    注意：OpenROAD 的 report_checks 输出到 stdout 而非文件，需捕获 subprocess 输出。
    """
    FLOW_ROOT = Path(os.environ.get("ORFS_ROOT", "/path/to/OpenROAD-flow-scripts"))

    # OpenROAD report_checks 输出到 stdout，不能用 Tcl 文件重定向。
    # 改用 Python subprocess 捕获 stdout。
    tcl_lines = [
        f"read_lef {FLOW_ROOT / 'flow/platforms/nangate45/lef/NangateOpenCellLibrary.tech.lef'}",
        f"read_lef {FLOW_ROOT / 'flow/platforms/nangate45/lef/NangateOpenCellLibrary.macro.lef'}",
        f"read_lib {FLOW_ROOT / 'flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib'}",
        f"read_verilog {FLOW_ROOT / f'flow/results/nangate45/{design_name}/base/1_2_yosys.v'}",
        f"link_design {design_name}",
        f"create_clock -name core_clock -period {clock_period_ns} [get_ports clk]",
        f"set_propagated_clock [get_clocks core_clock]",
        f"report_checks -path_delay max",
        f"puts \"WORST_SLACK:[report_worst_slack -max]\"",
        f"puts \"TOTAL_TNS:[report_tns]\"",
    ]

    tcl_path = work_dir / "extract_timing.tcl"
    tcl_path.write_text("\n".join(tcl_lines))

    cmd = ["openroad", "-no_splash", "-exit", str(tcl_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

    # report_checks 输出到 stdout（不在文件中）
    report_text = result.stdout

    # 保存原始输出供调试
    (work_dir / "raw_openroad_output.txt").write_text(report_text)

    timing_report = extract_critical_paths(report_text)
    return timing_report, report_text


def main():
    parser = argparse.ArgumentParser(description="Phase 7: Critical Path Extraction")
    parser.add_argument("--design", default="gcd", help="Design name")
    parser.add_argument("--period", type=float, default=0.18, help="Clock period in ns")
    parser.add_argument("--verify", action="store_true", help="Run dry-run verification")
    parser.add_argument("--work-dir", type=Path, default=Path("/tmp/phase7_verify"))
    args = parser.parse_args()

    print(f"\n{'#'*70}")
    print(f"#  Phase 7: Critical Path Extraction & Prompt Generation")
    print(f"#  Design: {args.design}")
    print(f"#  Period: {args.period}ns")
    print(f"{'#'*70}\n")

    # ── Task 3: Local Verification ─────────────────────────────────────────
    print("=" * 60)
    print("Task 3: Local Verification (Dry Run)")
    print("=" * 60)

    args.work_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[1/4] Running OpenROAD for {args.design} @ {args.period}ns...")
    timing_report, raw_report = run_openroad_and_extract(
        args.design, args.period, args.work_dir
    )

    if timing_report is None:
        print(f"❌ Failed to extract timing report")
        print(f"Error: {raw_report[:500]}")
        sys.exit(1)

    print(f"✅ Timing report extracted successfully")
    print(f"   - WNS: {timing_report.wns:+.4f} ns")
    print(f"   - TNS: {timing_report.tns:+.4f} ns")
    print(f"   - Critical paths: {len(timing_report.critical_paths)}")

    # ── Task 1: Verify Parser ────────────────────────────────────────────
    print(f"\n[2/4] Task 1: Verifying extract_critical_paths()...")
    worst_path = timing_report.worst_path()
    if worst_path:
        print(f"✅ Parser extracted worst path:")
        print(f"   - Startpoint: {worst_path.startpoint}")
        print(f"   - Endpoint:   {worst_path.endpoint}")
        print(f"   - Slack:      {worst_path.slack:+.4f} ns")
        print(f"   - Segments:   {len(worst_path.segments)} gates")

        key_instances = worst_path.get_key_instances(top_n=5)
        print(f"   - Top 5 delay contributors:")
        for i, (inst, delay) in enumerate(key_instances, 1):
            cell_type = next((s.cell_type for s in worst_path.segments if s.instance_name == inst), "N/A")
            print(f"     {i}. {inst} ({cell_type}): {delay:.4f}ns")
    else:
        print("❌ No critical paths found")

    # ── Task 2: Generate Prompt ──────────────────────────────────────────
    print(f"\n[3/4] Task 2: Generating build_critical_path_prompt()...")

    available_skills = [
        {"skill_name": "pipeline_stage_gcd_dpath", "repair_strategy": "pipeline_stage"},
        {"skill_name": "gate_sizing_gcd_comparator", "repair_strategy": "gate_sizing_up"},
        {"skill_name": "buffer_insertion_gcd_ctrl", "repair_strategy": "buffer_insertion"},
    ]

    prompt = build_critical_path_prompt(
        timing_report=timing_report,
        design_name=args.design,
        clock_period_ns=args.period,
        available_skills=available_skills,
    )

    # Save prompt to file
    prompt_path = args.work_dir / "llm_prompt.txt"
    prompt_path.write_text(prompt)
    print(f"✅ Prompt generated and saved to: {prompt_path}")

    # ── Output Summary ───────────────────────────────────────────────────
    print(f"\n[4/4] Summary:")
    print(f"   - Raw report:     {args.work_dir / 'timing_report.rpt'}")
    print(f"   - Parsed data:    WNS={timing_report.wns:.4f}, TNS={timing_report.tns:.4f}")
    print(f"   - LLM Prompt:     {prompt_path}")
    print(f"   - Prompt length:  {len(prompt)} chars")

    # Print excerpt of the prompt
    print(f"\n{'='*60}")
    print("LLM Prompt Excerpt (first 80 lines):")
    print("=" * 60)
    prompt_lines = prompt.splitlines()
    for line in prompt_lines[:80]:
        print(line)
    if len(prompt_lines) > 80:
        print(f"... ({len(prompt_lines) - 80} more lines)")

    print(f"\n{'='*60}")
    print("✅ Phase 7 Verification Complete")
    print("=" * 60)
    print(f"\n下一步: 将此 Prompt 发送给 LLM，获取基于真实物理反馈的修复代码")


if __name__ == "__main__":
    main()
