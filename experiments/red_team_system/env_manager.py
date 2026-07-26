#!/usr/bin/env python3
"""
Environment Manager - EDA 环境执行与解析器
负责调用并控制后端工具链（Yosys, OpenSTA），并精准提取 PPA 指标
"""

import os
import subprocess
import re
import json
from pathlib import Path
from typing import Dict, Optional, Tuple, List
from dataclasses import dataclass, asdict


RELEASE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROJECT_ROOT = Path(os.environ.get("R3E_RED_PROJECT_ROOT", RELEASE_ROOT / "third_party" / "rtl"))
DEFAULT_ARTIFACT_ROOT = Path(os.environ.get("R3E_ARTIFACT_ROOT", RELEASE_ROOT / "artifacts"))


@dataclass
class FrontendCheckResult:
    """前端检查结果"""
    passed: bool
    tool: str  # 'verilator' | 'icarus' | 'yosys_check'
    errors: List[str]
    warnings: List[str]
    execution_time: float


@dataclass
class SynthesisResult:
    """Yosys 综合结果"""
    passed: bool
    area: Optional[float]  # 面积（门数量）
    gate_count: int
    cell_types: Dict[str, int]  # 各类型单元数量
    power_estimate: Optional[float]  # 功耗估计（如果有）
    log_file: str
    errors: List[str]


@dataclass
class TimingResult:
    """OpenSTA 时序分析结果"""
    passed: bool
    wns: Optional[float]  # Worst Negative Slack (最差负时序裕量)
    tns: Optional[float]  # Total Negative Slack (总负时序裕量)
    failing_endpoints: int  # 违例端点数量
    critical_path_delay: Optional[float]
    clock_period: Optional[float]
    log_file: str
    errors: List[str]


@dataclass
class BackendMetrics:
    """后端综合指标"""
    synthesis: Optional[SynthesisResult]
    timing: Optional[TimingResult]
    frontend_check: FrontendCheckResult


class EnvManager:
    """EDA 工具链环境管理器"""

    def __init__(
        self,
        project_root: str = str(DEFAULT_PROJECT_ROOT),
        yosys_bin: str = "yosys",
        sta_bin: str = "sta",
        verilator_bin: str = "verilator",
        work_dir: Optional[str] = None,
        adversarial_mode: bool = True,
        frontend_tool: str = "yosys_check"
    ):
        self.project_root = Path(project_root)
        self.yosys_bin = yosys_bin
        self.sta_bin = sta_bin
        self.verilator_bin = verilator_bin
        self.work_dir = (
            Path(work_dir)
            if work_dir
            else DEFAULT_ARTIFACT_ROOT / "red_team" / "tool_work"
        )
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.adversarial_mode = adversarial_mode
        self.frontend_tool = frontend_tool

    def _project_file(self, relative_path: str) -> Path:
        path = Path(relative_path)
        if path.is_absolute():
            raise ValueError(f"RTL path must be relative to project_root: {relative_path}")
        root = self.project_root.resolve()
        resolved = (root / path).resolve()
        if resolved == root or root not in resolved.parents:
            raise ValueError(f"RTL path escapes project_root: {relative_path}")
        return resolved

    @staticmethod
    def _validate_top_module(top_module: str) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top_module):
            raise ValueError(f"invalid Verilog top-module identifier: {top_module!r}")

    @staticmethod
    def _tcl_path(path: str | Path) -> str:
        value = str(path)
        if "}" in value:
            raise ValueError(f"unsupported closing brace in Tcl path: {value!r}")
        return "{" + value + "}"

    def run_frontend_check(
        self,
        verilog_files: List[str],
        top_module: str,
        tool: str = "verilator"
    ) -> FrontendCheckResult:
        """
        运行前端语法检查

        Args:
            verilog_files: Verilog 文件列表
            top_module: 顶层模块名
            tool: 使用的工具 ('yosys_check' | 'verilator' | 'icarus')

        Returns:
            FrontendCheckResult 对象
        """
        import time
        start_time = time.time()
        self._validate_top_module(top_module)
        resolved_files = [self._project_file(path) for path in verilog_files]

        errors = []
        warnings = []
        passed = False

        try:
            if tool == "verilator":
                # Verilator 语法检查（不生成可执行文件）
                cmd = [
                    self.verilator_bin,
                    "--lint-only",  # 只做语法检查
                    "-Wall",  # 所有警告
                    "--top-module", top_module
                ] + [str(path) for path in resolved_files]

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=60
                )

                # 解析输出
                output = result.stderr + result.stdout
                for line in output.split('\n'):
                    if '%Error' in line:
                        errors.append(line.strip())
                    elif '%Warning' in line:
                        warnings.append(line.strip())

                passed = (result.returncode == 0 and len(errors) == 0)

            elif tool == "icarus":
                # Icarus Verilog 语法检查
                output_file = self.work_dir / "frontend_check.vvp"
                cmd = [
                    "iverilog",
                    "-o", str(output_file),
                    "-s", top_module
                ] + [str(path) for path in resolved_files]

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=60
                )

                output = result.stderr + result.stdout
                for line in output.split('\n'):
                    if 'error:' in line.lower():
                        errors.append(line.strip())
                    elif 'warning:' in line.lower():
                        warnings.append(line.strip())

                passed = (result.returncode == 0 and len(errors) == 0)

                # 清理临时文件
                if output_file.exists():
                    output_file.unlink()

            elif tool == "yosys_check":
                # Yosys 前端语法/层次检查；与蓝方 sandbox allowlist 对齐
                script_file = self.work_dir / f"frontend_check_{top_module}.ys"
                yosys_script = []
                for vfile in resolved_files:
                    yosys_script.append(f"read_verilog {{{vfile}}}")
                yosys_script.append(f"hierarchy -check -top {top_module}")
                yosys_script.append("proc")
                yosys_script.append("check")
                with open(script_file, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(yosys_script))

                result = subprocess.run(
                    [self.yosys_bin, "-s", str(script_file)],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=str(self.project_root)
                )

                output = result.stderr + result.stdout
                for line in output.split('\n'):
                    if 'ERROR' in line or 'Error' in line:
                        errors.append(line.strip())
                    elif 'Warning' in line or 'WARNING' in line:
                        warnings.append(line.strip())

                passed = (result.returncode == 0 and len(errors) == 0)

            else:
                errors.append(f"不支持的前端检查工具: {tool}")

        except subprocess.TimeoutExpired:
            errors.append(f"{tool} 执行超时")
        except Exception as e:
            errors.append(f"{tool} 执行异常: {str(e)}")

        execution_time = time.time() - start_time

        return FrontendCheckResult(
            passed=passed,
            tool=tool,
            errors=errors,
            warnings=warnings,
            execution_time=execution_time
        )

    def run_yosys_synthesis(
        self,
        verilog_files: List[str],
        top_module: str,
        liberty_file: Optional[str] = None,
        output_netlist: Optional[str] = None
    ) -> SynthesisResult:
        """
        运行 Yosys 逻辑综合并提取 Area/Gate Count

        Args:
            verilog_files: Verilog 源文件列表
            top_module: 顶层模块名
            liberty_file: Liberty 工艺库文件（可选）
            output_netlist: 输出网表路径（可选）

        Returns:
            SynthesisResult 对象
        """
        log_file = self.work_dir / f"yosys_synth_{top_module}.log"
        netlist_file = output_netlist or str(self.work_dir / f"{top_module}_synth.v")
        self._validate_top_module(top_module)
        resolved_files = [self._project_file(path) for path in verilog_files]

        # 构建 Yosys 脚本
        yosys_script = []

        # 读取 Verilog 文件
        for vfile in resolved_files:
            yosys_script.append(f"read_verilog {{{vfile}}}")

        # 层次化设计
        yosys_script.append(f"hierarchy -check -top {top_module}")

        # 综合流程
        if liberty_file:
            # 使用工艺库综合
            yosys_script.append(f"read_liberty -lib {{{liberty_file}}}")
            yosys_script.append(f"synth -top {top_module}")
            yosys_script.append(f"dfflibmap -liberty {{{liberty_file}}}")
            yosys_script.append(f"abc -liberty {{{liberty_file}}}")
        else:
            # 通用综合（映射到内部库）
            yosys_script.append(f"synth -top {top_module}")

        # 输出统计信息
        yosys_script.append("stat")
        yosys_script.append(f"tee -o {{{log_file}}} stat -liberty {{{liberty_file}}}" if liberty_file else f"tee -o {{{log_file}}} stat")

        # 写出网表
        yosys_script.append(f"write_verilog {{{netlist_file}}}")

        # 写入脚本文件
        script_file = self.work_dir / f"synth_{top_module}.ys"
        with open(script_file, 'w') as f:
            f.write('\n'.join(yosys_script))

        # 执行 Yosys
        errors = []
        try:
            result = subprocess.run(
                [self.yosys_bin, "-s", str(script_file)],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(self.work_dir)
            )

            # 保存完整日志
            with open(log_file, 'w') as f:
                f.write(result.stdout)
                f.write(result.stderr)

            # 解析输出
            output = result.stdout + result.stderr

            # 提取错误
            for line in output.split('\n'):
                if 'ERROR' in line or 'Error' in line:
                    errors.append(line.strip())

            # 解析统计信息
            area, gate_count, cell_types = self._parse_yosys_stat(output)

            passed = (result.returncode == 0 and len(errors) == 0)

            return SynthesisResult(
                passed=passed,
                area=area,
                gate_count=gate_count,
                cell_types=cell_types,
                power_estimate=None,  # Yosys 默认不提供功耗估计
                log_file=str(log_file),
                errors=errors
            )

        except subprocess.TimeoutExpired:
            errors.append("Yosys 综合超时")
        except Exception as e:
            errors.append(f"Yosys 执行异常: {str(e)}")

        return SynthesisResult(
            passed=False,
            area=None,
            gate_count=0,
            cell_types={},
            power_estimate=None,
            log_file=str(log_file),
            errors=errors
        )

    def _parse_yosys_stat(self, output: str) -> Tuple[Optional[float], int, Dict[str, int]]:
        """
        解析 Yosys stat 命令输出

        Returns:
            (area, gate_count, cell_types)
        """
        area = None
        gate_count = 0
        cell_types = {}

        # 查找统计表格
        # 典型输出格式:
        #   Number of cells:               1234
        #   $_AND_    123
        #   $_OR_     456
        #   ...
        #   Chip area for module '\top': 12345.678

        lines = output.split('\n')
        in_stat_section = False

        for line in lines:
            # 提取总门数
            match = re.search(r'Number of cells:\s+(\d+)', line)
            if match:
                gate_count = int(match.group(1))
                in_stat_section = True
                continue

            # 提取面积
            match = re.search(r'Chip area.*?:\s+([\d.]+)', line)
            if match:
                area = float(match.group(1))
                continue

            # 提取各类型单元数量
            if in_stat_section:
                match = re.search(r'(\$_\w+_|\w+)\s+(\d+)', line)
                if match:
                    cell_type = match.group(1).strip()
                    count = int(match.group(2))
                    cell_types[cell_type] = count

        return area, gate_count, cell_types

    def run_opensta_timing(
        self,
        netlist_file: str,
        sdc_file: str,
        liberty_file: str,
        top_module: str
    ) -> TimingResult:
        """
        运行 OpenSTA 时序分析并精准提取 WNS/TNS

        Args:
            netlist_file: 综合后的网表文件
            sdc_file: SDC 约束文件
            liberty_file: Liberty 工艺库文件
            top_module: 顶层模块名

        Returns:
            TimingResult 对象
        """
        log_file = self.work_dir / f"sta_{top_module}.log"

        # 构建 OpenSTA TCL 脚本
        self._validate_top_module(top_module)
        sta_script = f"""
# OpenSTA 时序分析脚本
read_liberty {self._tcl_path(liberty_file)}
read_verilog {self._tcl_path(netlist_file)}
link_design {top_module}
read_sdc {self._tcl_path(sdc_file)}

# 运行时序分析
report_checks -path_delay min_max -format full_clock_expanded
report_worst_slack -min -max
report_tns
report_checks -path_delay max -format summary

# 输出详细报告
report_checks -path_delay max -format full_clock_expanded > {self._tcl_path(log_file)}
"""

        script_file = self.work_dir / f"sta_{top_module}.tcl"
        with open(script_file, 'w') as f:
            f.write(sta_script)

        errors = []
        try:
            # 执行 OpenSTA
            result = subprocess.run(
                [self.sta_bin, "-f", str(script_file)],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(self.work_dir)
            )

            # 保存完整日志
            with open(log_file, 'w') as f:
                f.write(result.stdout)
                f.write(result.stderr)

            output = result.stdout + result.stderr

            # 提取错误
            for line in output.split('\n'):
                if 'Error' in line or 'ERROR' in line:
                    errors.append(line.strip())

            # 解析时序指标
            wns, tns, failing_endpoints, critical_path, clock_period = self._parse_sta_report(output)

            passed = (
                result.returncode == 0
                and len(errors) == 0
                and wns is not None
                and wns >= 0
            )

            return TimingResult(
                passed=passed,
                wns=wns,
                tns=tns,
                failing_endpoints=failing_endpoints,
                critical_path_delay=critical_path,
                clock_period=clock_period,
                log_file=str(log_file),
                errors=errors
            )

        except subprocess.TimeoutExpired:
            errors.append("OpenSTA 时序分析超时")
        except Exception as e:
            errors.append(f"OpenSTA 执行异常: {str(e)}")

        return TimingResult(
            passed=False,
            wns=None,
            tns=None,
            failing_endpoints=0,
            critical_path_delay=None,
            clock_period=None,
            log_file=str(log_file),
            errors=errors
        )

    def _parse_sta_report(self, output: str) -> Tuple[Optional[float], Optional[float], int, Optional[float], Optional[float]]:
        """
        解析 OpenSTA 报告，提取关键时序指标

        Returns:
            (wns, tns, failing_endpoints, critical_path_delay, clock_period)
        """
        wns = None
        tns = None
        failing_endpoints = 0
        critical_path = None
        clock_period = None

        lines = output.split('\n')

        for line in lines:
            # 提取 WNS (Worst Negative Slack)
            # 典型格式: "wns -2.345" 或 "worst slack: -2.345"
            match = re.search(r'wns\s+([-\d.]+)', line, re.IGNORECASE)
            if match:
                wns = float(match.group(1))
                continue

            match = re.search(r'worst\s+slack[:\s]+([-\d.]+)', line, re.IGNORECASE)
            if match:
                wns = float(match.group(1))
                continue

            # 提取 TNS (Total Negative Slack)
            # 典型格式: "tns -15.678" 或 "total negative slack: -15.678"
            match = re.search(r'tns\s+([-\d.]+)', line, re.IGNORECASE)
            if match:
                tns = float(match.group(1))
                continue

            match = re.search(r'total\s+negative\s+slack[:\s]+([-\d.]+)', line, re.IGNORECASE)
            if match:
                tns = float(match.group(1))
                continue

            # 提取违例端点数量
            match = re.search(r'(\d+)\s+endpoint.*violated', line, re.IGNORECASE)
            if match:
                failing_endpoints = int(match.group(1))
                continue

            # 提取关键路径延迟
            match = re.search(r'data\s+arrival\s+time\s+([\d.]+)', line, re.IGNORECASE)
            if match:
                critical_path = float(match.group(1))
                continue

            # 提取时钟周期
            match = re.search(r'clock\s+period\s+([\d.]+)', line, re.IGNORECASE)
            if match:
                clock_period = float(match.group(1))
                continue

        return wns, tns, failing_endpoints, critical_path, clock_period

    def run_full_backend_flow(
        self,
        verilog_files: List[str],
        top_module: str,
        sdc_file: Optional[str] = None,
        liberty_file: Optional[str] = None
    ) -> BackendMetrics:
        """
        运行完整的后端流程：前端检查 -> 综合 -> 时序分析

        Args:
            verilog_files: Verilog 源文件列表
            top_module: 顶层模块名
            sdc_file: SDC 约束文件（可选）
            liberty_file: Liberty 工艺库文件（可选）

        Returns:
            BackendMetrics 对象
        """
        print(f"[EnvManager] 开始后端流程: {top_module}")

        # Step 1: 前端检查
        print("[EnvManager] Step 1: 前端语法检查...")
        frontend_result = self.run_frontend_check(verilog_files, top_module, tool=self.frontend_tool)

        if not frontend_result.passed:
            print(f"[EnvManager] 前端检查失败: {len(frontend_result.errors)} 个错误")
            return BackendMetrics(
                synthesis=None,
                timing=None,
                frontend_check=frontend_result
            )

        print(f"[EnvManager] 前端检查通过 ({frontend_result.execution_time:.2f}s)")

        if self.adversarial_mode:
            print("[EnvManager] 对抗模式: 跳过红队自跑 Yosys 综合/OpenSTA，WNS/TNS 留给蓝方 OpenROAD 回传")
            return BackendMetrics(
                synthesis=None,
                timing=None,
                frontend_check=frontend_result
            )

        # Step 2: Yosys 综合
        print("[EnvManager] Step 2: Yosys 逻辑综合...")
        synth_result = self.run_yosys_synthesis(
            verilog_files,
            top_module,
            liberty_file=liberty_file
        )

        if not synth_result.passed:
            print(f"[EnvManager] 综合失败: {len(synth_result.errors)} 个错误")
        else:
            print(f"[EnvManager] 综合成功: Gate Count = {synth_result.gate_count}, Area = {synth_result.area}")

        # Step 3: OpenSTA 时序分析（如果提供了约束和工艺库）
        timing_result = None
        if sdc_file and liberty_file and synth_result.passed:
            print("[EnvManager] Step 3: OpenSTA 时序分析...")
            netlist_file = self.work_dir / f"{top_module}_synth.v"

            timing_result = self.run_opensta_timing(
                netlist_file=str(netlist_file),
                sdc_file=sdc_file,
                liberty_file=liberty_file,
                top_module=top_module
            )

            if timing_result.passed:
                print(f"[EnvManager] 时序分析通过: WNS = {timing_result.wns}ns")
            else:
                print(f"[EnvManager] 时序违例: WNS = {timing_result.wns}ns, TNS = {timing_result.tns}ns")

        return BackendMetrics(
            synthesis=synth_result,
            timing=timing_result,
            frontend_check=frontend_result
        )

    def save_metrics(self, metrics: BackendMetrics, output_file: str):
        """保存后端指标到 JSON 文件"""
        data = {
            'frontend_check': asdict(metrics.frontend_check),
            'synthesis': asdict(metrics.synthesis) if metrics.synthesis else None,
            'timing': asdict(metrics.timing) if metrics.timing else None
        }

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"[EnvManager] 指标已保存到: {output_file}")


