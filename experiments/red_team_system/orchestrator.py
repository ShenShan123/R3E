#!/usr/bin/env python3
"""
Red Team Orchestrator - 红队编排器
整合所有模块，实现完整的红队攻击流程
"""

import os
import time
import uuid
from pathlib import Path
from typing import List, Optional, Dict
import json

# Package-relative imports keep the released library importable from any cwd.
from .red_agent import RedAgent, PoisonPlan
from .env_manager import EnvManager, BackendMetrics
from .reward_calculator import RewardCalculator, RewardComponents
from .memory_distillation import MemoryDistillation


RELEASE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROJECT_ROOT = Path(os.environ.get("R3E_RED_PROJECT_ROOT", RELEASE_ROOT / "third_party" / "rtl"))
DEFAULT_ARTIFACT_ROOT = Path(os.environ.get("R3E_ARTIFACT_ROOT", RELEASE_ROOT / "artifacts"))
DEFAULT_MEMORY_ROOT = Path(os.environ.get("R3E_RED_MEMORY_ROOT", DEFAULT_ARTIFACT_ROOT / "red_team/memory"))


class RedTeamOrchestrator:
    """红队编排器 - 协调所有模块完成攻击流程"""

    def __init__(
        self,
        api_key: str,
        project_root: str = str(DEFAULT_PROJECT_ROOT),
        memory_dir: str = str(DEFAULT_MEMORY_ROOT),
        work_dir: Optional[str] = None,
        sandbox_root: Optional[str] = None,
        adversarial_mode: bool = True
    ):
        self.project_root = Path(project_root)
        exec_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
        self.sandbox_root = (Path(sandbox_root) if sandbox_root else
                             DEFAULT_ARTIFACT_ROOT / "red_team" / f"red_sandbox_{exec_id}")
        self.sandbox_root.mkdir(parents=True, exist_ok=True)
        self.work_dir = Path(work_dir) if work_dir else self.sandbox_root / "artifacts"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.adversarial_mode = adversarial_mode

        if self.project_root.resolve() == RELEASE_ROOT.resolve():
            print("[Orchestrator] 蓝方主仓库仅作为只读源使用，投毒写入将被限制在红队沙箱")

        # 初始化各模块
        self.red_agent = RedAgent(
            api_key=api_key,
            memory_dir=memory_dir,
            project_root=str(project_root),
            sandbox_work_root=str(self.sandbox_root)
        )

        self.env_manager = EnvManager(
            project_root=str(project_root),
            work_dir=str(self.sandbox_root / "tool_work"),
            adversarial_mode=adversarial_mode,
            frontend_tool="yosys_check"
        )

        self.reward_calculator = RewardCalculator(
            alpha=1.0,
            beta=1.5
        )

        self.memory_distiller = MemoryDistillation(memory_dir=memory_dir)

        print("[Orchestrator] 红队系统初始化完成")

    def set_baseline(
        self,
        verilog_files: List[str],
        top_module: str,
        sdc_file: Optional[str] = None,
        liberty_file: Optional[str] = None
    ):
        """
        建立基线指标（在投毒前运行）

        Args:
            verilog_files: 原始 Verilog 文件
            top_module: 顶层模块
            sdc_file: SDC 约束文件
            liberty_file: Liberty 工艺库
        """
        print("\n[Orchestrator] ========== 建立基线指标 ==========")

        metrics = self.env_manager.run_full_backend_flow(
            verilog_files=verilog_files,
            top_module=top_module,
            sdc_file=sdc_file,
            liberty_file=liberty_file
        )

        if not metrics.frontend_check.passed:
            print("[Orchestrator] 错误: 原始设计未通过前端检查")
            return False

        # 提取基线指标
        baseline_area = None
        baseline_wns = None
        baseline_tns = None

        if metrics.synthesis and metrics.synthesis.passed:
            baseline_area = metrics.synthesis.area or metrics.synthesis.gate_count

        if metrics.timing and metrics.timing.passed:
            baseline_wns = metrics.timing.wns
            baseline_tns = metrics.timing.tns

        # 设置到奖励计算器
        self.reward_calculator.set_baseline(
            baseline_area=baseline_area,
            baseline_wns=baseline_wns,
            baseline_tns=baseline_tns
        )

        print(f"[Orchestrator] 基线指标:")
        print(f"  Area/Gates: {baseline_area}")
        print(f"  WNS: {baseline_wns}ns")
        print(f"  TNS: {baseline_tns}ns")

        # 保存基线
        baseline_file = self.work_dir / "baseline_metrics.json"
        self.env_manager.save_metrics(metrics, str(baseline_file))

        return True

    def execute_attack(
        self,
        target_files: List[str],
        top_module: str,
        poison_level: int = 2,
        sdc_file: Optional[str] = None,
        liberty_file: Optional[str] = None,
        dry_run: bool = False
    ) -> Optional[str]:
        """
        执行完整的红队攻击流程

        Args:
            target_files: 目标文件列表
            top_module: 顶层模块
            poison_level: 投毒级别 (1-2)
            sdc_file: SDC 约束文件
            liberty_file: Liberty 工艺库
            dry_run: 是否为演练模式（不实际修改文件）

        Returns:
            记忆 ID（如果成功）
        """
        if poison_level not in RedAgent.ALLOWED_LEVELS:
            print(f"[Orchestrator] Scope 拒绝: 只允许 Level 1/2，收到 Level {poison_level}")
            return None

        print(f"\n[Orchestrator] ========== 开始红队攻击 (Level {poison_level}) ==========")

        # Step 1: 生成投毒计划
        print("\n[Step 1] 生成投毒计划...")
        plan = self.red_agent.generate_poison_plan(
            target_files=target_files,
            target_level=poison_level,
            temperature=0.8
        )

        if not plan:
            print("[Orchestrator] 投毒计划生成失败")
            return None

        print(f"[Orchestrator] 投毒计划:")
        print(f"  策略: {plan.strategy}")
        print(f"  动作数: {len(plan.actions)}")
        print(f"  预期影响: {plan.expected_backend_impact}")

        if dry_run:
            print("[Orchestrator] 演练模式，跳过实际执行")
            return None

        # Step 2: 执行投毒（修改文件）
        print("\n[Step 2] 执行投毒...")
        success, modified_files = self.red_agent.execute_poison_plan(plan)

        if not success:
            print("[Orchestrator] 投毒执行失败")
            return None

        print(f"[Orchestrator] 已修改 {len(modified_files)} 个文件")

        # Step 3: 运行后端流程
        print("\n[Step 3] 运行红队前端验证...")
        if self.red_agent.last_materialized_root:
            self.env_manager.project_root = self.red_agent.last_materialized_root
        metrics = self.env_manager.run_full_backend_flow(
            verilog_files=target_files,
            top_module=top_module,
            sdc_file=sdc_file,
            liberty_file=liberty_file
        )

        # 保存后端指标
        metrics_file = self.work_dir / f"attack_metrics_{poison_level}.json"
        self.env_manager.save_metrics(metrics, str(metrics_file))

        # Step 4: 计算奖励
        print("\n[Step 4] 计算奖励...")
        backend_evaluated = metrics.synthesis is not None or metrics.timing is not None
        reward = self.reward_calculator.calculate_total_reward(
            frontend_passed=metrics.frontend_check.passed,
            repair_iterations=0,  # 初始攻击，蓝方尚未修复
            repair_success=False,
            current_area=metrics.synthesis.area if metrics.synthesis else None,
            current_gate_count=metrics.synthesis.gate_count if metrics.synthesis else None,
            current_wns=metrics.timing.wns if metrics.timing else None,
            current_tns=metrics.timing.tns if metrics.timing else None,
            synthesis_failed=(not metrics.synthesis.passed) if metrics.synthesis else False,
            timing_failed=(not metrics.timing.passed) if metrics.timing else False,
            poison_level=poison_level,
            repair_observed=False,
        )

        print(f"[Orchestrator] 奖励评估:")
        print(f"  前端通过: {reward.frontend_pass}")
        print(f"  修复成本: {reward.repair_cost:.2f}")
        print(f"  后端破坏: {reward.backend_damage:.2f}")
        print(
            "  后端破坏来源: local_backend"
            if backend_evaluated else
            "  后端破坏来源: pending_blue_openroad"
        )
        print(f"  总奖励: {reward.total_reward:.2f}")
        print(f"  达成级别: {reward.breakdown.get('achieved_level', 'N/A')}")

        # Step 5: 记忆蒸馏
        print("\n[Step 5] 记忆蒸馏...")
        memory_id = self.memory_distiller.update_memory(
            actions=[
                {
                    'target_file': action.target_file,
                    'action_type': action.action_type,
                    'level': action.level,
                    'payload': action.payload,
                    'rationale': action.rationale
                }
                for action in plan.actions
            ],
            strategy=plan.strategy,
            poison_level=poison_level,
            target_files=target_files,
            is_frontend_pass=metrics.frontend_check.passed,
            reward=reward.total_reward,
            reward_breakdown=reward.breakdown,
            is_fixed_by_blue=False,
            frontend_errors=metrics.frontend_check.errors,
            synthesis_passed=metrics.synthesis.passed if metrics.synthesis else None,
            timing_passed=metrics.timing.passed if metrics.timing else None,
            area=metrics.synthesis.area if metrics.synthesis else None,
            gate_count=metrics.synthesis.gate_count if metrics.synthesis else None,
            wns=metrics.timing.wns if metrics.timing else None,
            tns=metrics.timing.tns if metrics.timing else None,
            tags=self._infer_tags(plan, metrics),
            backend_evaluated=backend_evaluated,
        )

        print(f"[Orchestrator] 记忆已保存: {memory_id}")

        # Step 6: 生成攻击报告
        self._generate_attack_report(plan, metrics, reward, memory_id)

        print("\n[Orchestrator] ========== 攻击流程完成 ==========")
        return memory_id

    def _infer_tags(self, plan: PoisonPlan, metrics: BackendMetrics) -> List[str]:
        """根据攻击结果推断标签"""
        tags = []

        # 根据级别添加标签
        if plan.target_level == 1:
            tags.append('area_bloat')
        elif plan.target_level == 2:
            tags.append('timing_attack')
        elif plan.target_level == 3:
            tags.append('hardware_trojan')
        elif plan.target_level == 4:
            tags.append('tool_crash')

        # 根据实际结果添加标签
        if metrics.synthesis and not metrics.synthesis.passed:
            tags.append('synthesis_failure')

        if metrics.timing and not metrics.timing.passed:
            tags.append('timing_violation')

        if metrics.timing and metrics.timing.wns and metrics.timing.wns < -5.0:
            tags.append('severe_timing')

        # 根据动作类型添加标签
        for action in plan.actions:
            if action.action_type == 'modify_sdc':
                tags.append('sdc_tampering')
            elif action.action_type == 'modify_makefile':
                tags.append('makefile_injection')

        return list(set(tags))  # 去重

    def _generate_attack_report(
        self,
        plan: PoisonPlan,
        metrics: BackendMetrics,
        reward: RewardComponents,
        memory_id: str
    ):
        """生成攻击报告"""
        report_file = self.work_dir / f"attack_report_{memory_id}.txt"

        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("红队攻击报告\n")
            f.write("=" * 80 + "\n\n")

            f.write(f"记忆 ID: {memory_id}\n")
            f.write(f"攻击级别: Level {plan.target_level}\n")
            f.write(f"攻击策略: {plan.strategy}\n\n")

            f.write("投毒动作:\n")
            for i, action in enumerate(plan.actions, 1):
                f.write(f"  {i}. {action.target_file}\n")
                f.write(f"     类型: {action.action_type}\n")
                f.write(f"     理由: {action.rationale}\n\n")

            f.write("前端检查结果:\n")
            f.write(f"  通过: {metrics.frontend_check.passed}\n")
            if metrics.frontend_check.errors:
                f.write(f"  错误: {metrics.frontend_check.errors}\n")
            f.write("\n")

            if metrics.synthesis:
                f.write("综合结果:\n")
                f.write(f"  通过: {metrics.synthesis.passed}\n")
                f.write(f"  Gate Count: {metrics.synthesis.gate_count}\n")
                f.write(f"  Area: {metrics.synthesis.area}\n")
                f.write("\n")

            if metrics.timing:
                f.write("时序分析结果:\n")
                f.write(f"  通过: {metrics.timing.passed}\n")
                f.write(f"  WNS: {metrics.timing.wns}ns\n")
                f.write(f"  TNS: {metrics.timing.tns}ns\n")
                f.write(f"  违例端点: {metrics.timing.failing_endpoints}\n")
                f.write("\n")

            f.write("奖励评估:\n")
            f.write(f"  总奖励: {reward.total_reward:.2f}\n")
            f.write(f"  修复成本: {reward.repair_cost:.2f}\n")
            f.write(f"  后端破坏: {reward.backend_damage:.2f}\n")
            f.write(
                "  后端破坏来源: local_backend\n"
                if metrics.synthesis is not None or metrics.timing is not None
                else "  后端破坏来源: pending_blue_openroad\n"
            )
            f.write(f"  达成级别: {reward.breakdown.get('achieved_level', 'N/A')}\n")
            f.write("\n")

            f.write("详细分解:\n")
            for key, value in reward.breakdown.items():
                f.write(f"  {key}: {value}\n")

        print(f"[Orchestrator] 攻击报告已保存: {report_file}")

    def show_statistics(self):
        """显示记忆库统计信息"""
        print("\n[Orchestrator] ========== 记忆库统计 ==========")
        stats = self.memory_distiller.get_statistics()

        print(f"总记忆数: {stats['total']}")
        print(f"\n分类统计:")
        for category, count in stats['by_category'].items():
            print(f"  {category}: {count}")

        print(f"\n级别统计:")
        for level, count in stats['by_level'].items():
            print(f"  Level {level}: {count}")

        print(f"\n平均奖励:")
        for category, avg in stats['avg_reward'].items():
            print(f"  {category}: {avg:.2f}")

        print(f"\n最高奖励:")
        for category, top in stats['top_reward'].items():
            print(f"  {category}: {top:.2f}")
