#!/usr/bin/env python3
"""
synthesis_bridge.py — Phase 11: 轻量级综合桥梁
==============================================
目标：快速验证 LLM 生成的 RTL 修改是否可综合，
      而非生成与 ORFS 完全一致的网表。

策略：
1. 使用 Yosys 进行快速语法和可综合性检查
2. 验证修改后的 RTL 可以被解析和基本综合
3. 真正的时序评估通过阶段性批量 ORFS 运行完成
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple


# ═══════════════════════════════════════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════════════════════════════════════

FLOW_ROOT = Path(os.environ.get("ORFS_ROOT", "/path/to/OpenROAD-flow-scripts"))
PLATFORM_ROOT = FLOW_ROOT / "flow/platforms/nangate45"
LIB_FILE = PLATFORM_ROOT / "lib/NangateOpenCellLibrary_typical.lib"


# ═══════════════════════════════════════════════════════════════════════════════
#  Fast Yosys Check (Syntax + Synthesizable)
# ═══════════════════════════════════════════════════════════════════════════════

def check_rtl_synthesizable(rtl_path: Path, design_name: str, work_dir: Path) -> Tuple[bool, str, Optional[Path]]:
    """
    快速检查 RTL 是否可综合。

    流程：
    1. 读取 Verilog
    2. hierarchy -check (检查模块层次)
    3. basic synth (仅检查，不生成输出)

    Returns:
        (success, message, output_netlist_or_none)
    """
    output_netlist = work_dir / f"{design_name}_checked.v"

    # 仅检查可综合性，不追求输出质量
    yosys_script = f"""
# Fast RTL Synthesis Check
read_verilog "{rtl_path}"
hierarchy -check -top {design_name}
synth -run check
write_verilog "{output_netlist}"
"""

    script_path = work_dir / "check_synth.ys"
    script_path.write_text(yosys_script)

    yosys_cmd = _find_yosys()
    if not yosys_cmd:
        return False, "Yosys not found", None

    cmd = [str(yosys_cmd), "-s", str(script_path)]

    print(f"[SynthesisCheck] Checking synthesizability...")
    print(f"  RTL: {rtl_path}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(work_dir)
    )

    if result.returncode != 0:
        stderr_preview = result.stderr[-1000:] if len(result.stderr) > 1000 else result.stderr
        return False, f"Synthesis check failed:\n{stderr_preview}", None

    if not output_netlist.exists():
        return False, "Output not generated", None

    line_count = len(output_netlist.read_text().splitlines())
    print(f"  ✅ RTL is synthesizable ({line_count} lines)")

    return True, f"Synthesizable ({line_count} lines)", output_netlist


def run_yosys_quick_synth(design_name: str, rtl_path: Path, work_dir: Path) -> Optional[Path]:
    """
    快速综合 RTL 生成网表（用于分析结构变化，非精确时序）。

    注意：此网表结构与 ORFS 预综合网表不同，不能直接用于 WNS 对比。
    主要用于验证 LLM 修改引入了哪些类型的单元。
    """
    output_netlist = work_dir / f"{design_name}_quick.v"

    # 最简综合脚本
    yosys_script = f"""
read_liberty -lib "{LIB_FILE}"
read_verilog "{rtl_path}"
hierarchy -check -top {design_name}
synth -flatten -run coarse:fine
dfflibmap -liberty "{LIB_FILE}"
abc -liberty "{LIB_FILE}"
opt_clean -purge
write_verilog -nohex -nodec "{output_netlist}"
stat
"""

    script_path = work_dir / "quick_synth.ys"
    script_path.write_text(yosys_script)

    yosys_cmd = _find_yosys()
    if not yosys_cmd:
        return None

    cmd = [str(yosys_cmd), "-s", str(script_path)]

    print(f"[QuickSynth] Running quick synthesis...")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(work_dir)
    )

    if result.returncode != 0:
        stderr_preview = result.stderr[-500:] if len(result.stderr) > 500 else result.stderr
        print(f"  ⚠️ Quick synthesis had issues: {stderr_preview[:200]}...")
        # 即使有问题，也可能生成了部分网表

    if not output_netlist.exists():
        return None

    file_size = output_netlist.stat().st_size
    line_count = len(output_netlist.read_text().splitlines())
    print(f"  ✅ Quick synthesis complete: {file_size/1024:.1f} KB, {line_count} lines")

    return output_netlist


def count_register_instances(netlist_path: Path) -> dict:
    """
    统计网表中寄存器实例数量（检测 LLM 是否添加了新的寄存器）。

    用于验证：流水线插入应该增加寄存器数量。
    """
    content = netlist_path.read_text()
    lines = content.splitlines()

    stats = {
        "dff_count": 0,
        "total_instances": 0,
        "module_count": 0,
    }

    for line in lines:
        # 简单统计 DFF/DLatch 实例
        if any(x in line.lower() for x in ['_dff', '_dlatch', 'reg ', 'dff_']):
            stats["dff_count"] += 1
        if ' (' in line and ');' in line:
            stats["total_instances"] += 1
        if line.strip().startswith('module '):
            stats["module_count"] += 1

    return stats


def compare_netlist_structure(before_path: Path, after_path: Path) -> dict:
    """
    比较两个网表的结构变化。

    Returns:
        结构变化统计
    """
    before_stats = count_register_instances(before_path)
    after_stats = count_register_instances(after_path)

    return {
        "before": before_stats,
        "after": after_stats,
        "dff_delta": after_stats["dff_count"] - before_stats["dff_count"],
        "instance_delta": after_stats["total_instances"] - before_stats["total_instances"],
        "structure_changed": before_stats != after_stats,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  OpenROAD with ORFS Netlist (for real timing)
# ═══════════════════════════════════════════════════════════════════════════════

def generate_sdc(design_name: str, clock_period_ns: float, work_dir: Path) -> Path:
    """生成 SDC 约束文件"""
    sdc_content = f"""# Auto-generated SDC for R3E
set current_design {design_name}
create_clock -name core_clock -period {clock_period_ns} [get_ports clk]
set_propagated_clock [get_clocks core_clock]
set_input_delay 0.0 -clock [get_clocks core_clock] [all_inputs -no_clocks]
set_output_delay 0.0 -clock [get_clocks core_clock] [all_outputs]
set_clock_transition 0.010 [get_clocks core_clock]
"""
    sdc_path = work_dir / f"{design_name}_{int(clock_period_ns*1000)}ps.sdc"
    sdc_path.write_text(sdc_content)
    return sdc_path


def run_openroad_with_orfs_netlist(
    design_name: str,
    clock_period_ns: float,
    work_dir: Path,
    use_original: bool = True
) -> Tuple[Optional[dict], str]:
    """
    使用 ORFS 预综合网表运行 OpenROAD 时序分析。

    Args:
        use_original: True 使用原始 ORFS 网表，False 使用修改后的网表（需要手动复制）

    Returns:
        (metrics_dict, stdout)
    """
    TECH_LEF = PLATFORM_ROOT / "lef/NangateOpenCellLibrary.tech.lef"
    MACRO_LEF = PLATFORM_ROOT / "lef/NangateOpenCellLibrary.macro.lef"

    if use_original:
        netlist_path = FLOW_ROOT / f"flow/results/nangate45/{design_name}/base/1_2_yosys.v"
    else:
        # 修改后的网表应该放在 work_dir 中
        netlist_path = work_dir / f"{design_name}_synth.v"

    if not netlist_path.exists():
        return None, f"Netlist not found: {netlist_path}"

    sdc_path = generate_sdc(design_name, clock_period_ns, work_dir)

    tcl_script = f"""
read_lef {TECH_LEF}
read_lef {MACRO_LEF}
read_lib {LIB_FILE}
read_verilog {netlist_path}
link_design {design_name}
read_sdc {sdc_path}
set_propagated_clock [get_clocks core_clock]
report_checks -path_delay max -fields {{delay slew cap slack}} -format full
puts "WORST_SLACK:[report_worst_slack -max]"
puts "TOTAL_TNS:[report_tns]"
exit
"""

    tcl_path = work_dir / "openroad_sta.tcl"
    tcl_path.write_text(tcl_script)

    cmd = ["openroad", "-no_splash", "-exit", str(tcl_path)]

    print(f"[OpenROAD] STA analysis...")
    print(f"  Netlist: {netlist_path}")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=str(work_dir))

    if result.returncode != 0:
        return None, f"OpenROAD failed: {result.stderr[:500]}"

    stdout = result.stdout

    # 解析指标
    metrics = {}
    for line in stdout.splitlines():
        if "worst slack" in line.lower():
            import re
            match = re.search(r'worst\s+slack\s+([-\d.]+)', line, re.IGNORECASE)
            if match:
                metrics["wns"] = float(match.group(1))
        if "tns" in line.lower() and not line.lower().startswith("total negative slack"):
            import re
            match = re.search(r'tns\s+([-\d.]+)', line, re.IGNORECASE)
            if match:
                metrics["tns"] = float(match.group(1))

    if "wns" in metrics:
        print(f"  ✅ WNS: {metrics['wns']:+.4f}ns, TNS: {metrics.get('tns', 0):+.4f}ns")
    else:
        print(f"  ⚠️ Could not parse WNS from output")

    return metrics, stdout


# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _find_yosys() -> Optional[Path]:
    """查找 yosys 可执行文件"""
    import os
    yosys_env = os.environ.get("YOSYS_BIN")
    if yosys_env:
        return Path(yosys_env)

    common_paths = [
        "/usr/bin/yosys",
        "/usr/local/bin/yosys",
    ]
    for p in common_paths:
        if Path(p).exists():
            return Path(p)

    result = subprocess.run(["which", "yosys"], capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip())

    return None


def check_prerequisites() -> Tuple[bool, str]:
    """检查工具链前提"""
    errors = []

    yosys_path = _find_yosys()
    if not yosys_path:
        errors.append("Yosys not found")

    result = subprocess.run(["which", "openroad"], capture_output=True)
    if result.returncode != 0:
        errors.append("OpenROAD not found")

    if not LIB_FILE.exists():
        errors.append(f"Liberty file not found: {LIB_FILE}")

    if errors:
        return False, "; ".join(errors)

    return True, f"Yosys: {yosys_path}, OpenROAD: OK"


# ═══════════════════════════════════════════════════════════════════════════════
#  Main Test
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("="*60)
    print("Synthesis Bridge Test")
    print("="*60)

    ok, msg = check_prerequisites()
    print(f"Prerequisites: {'✅' if ok else '❌'} {msg}")

    if ok:
        work_dir = Path("/tmp/synth_bridge_v2")
        work_dir.mkdir(exist_ok=True)

        # 测试原始 RTL
        rtl_path = FLOW_ROOT / "flow/designs/src/gcd/gcd.v"

        print("\n--- Test 1: Synthesis Check ---")
        success, msg, checked_netlist = check_rtl_synthesizable(rtl_path, "gcd", work_dir)
        print(f"Result: {msg}")

        if checked_netlist:
            print("\n--- Test 2: Quick Synthesis ---")
            quick_netlist = run_yosys_quick_synth("gcd", rtl_path, work_dir)

            if quick_netlist:
                stats = count_register_instances(quick_netlist)
                print(f"Register stats: {stats}")

        print("\n--- Test 3: OpenROAD with ORFS Netlist ---")
        metrics, stdout = run_openroad_with_orfs_netlist("gcd", 0.18, work_dir, use_original=True)
        if metrics:
            print(f"WNS: {metrics.get('wns')}, TNS: {metrics.get('tns')}")
