#!/usr/bin/env python3
"""
Reward Calculator - 红队收益计算器
实现联合奖励函数，评估投毒攻击的有效性
"""

from typing import Dict, Optional
from dataclasses import dataclass


@dataclass
class RewardComponents:
    """奖励函数的各个组成部分"""
    frontend_pass: bool  # 是否通过前端检查
    repair_cost: float  # 蓝方修复成本 (C_repair)
    backend_damage: float  # 后端破坏程度 (D_backend)
    total_reward: float  # 总奖励
    breakdown: Dict[str, float]  # 详细分解


class RewardCalculator:
    """
    红队联合奖励函数计算器

    奖励公式:
    R_red = 0,                                    if Frontend Check = Fail
    R_red = α * C_repair + β * D_backend,         if Frontend Check = Pass

    其中:
    - C_repair: 蓝方修复成本（基于修复迭代轮数）
    - D_backend: 后端破坏程度（基于 WNS/TNS 恶化 + Area 暴增）
    - α, β: 权重系数
    """

    def __init__(
        self,
        alpha: float = 1.0,  # 修复成本权重
        beta: float = 1.5,   # 后端破坏权重
        baseline_area: Optional[float] = None,  # 基线面积（用于计算增长率）
        baseline_wns: Optional[float] = None,   # 基线 WNS（用于计算恶化程度）
        baseline_tns: Optional[float] = None    # 基线 TNS
    ):
        self.alpha = alpha
        self.beta = beta
        self.baseline_area = baseline_area
        self.baseline_wns = baseline_wns
        self.baseline_tns = baseline_tns

    def calculate_repair_cost(
        self,
        repair_iterations: int,
        repair_success: bool,
        time_to_repair: Optional[float] = None,
        repair_observed: bool = True,
    ) -> float:
        """
        计算蓝方修复成本 C_repair

        Args:
            repair_iterations: 蓝方修复的迭代轮数
            repair_success: 蓝方是否成功修复
            time_to_repair: 修复耗时（秒，可选）

        Returns:
            修复成本分数（越高表示红队越成功）

        计算逻辑:
        - 基础分数 = repair_iterations * 10
        - 如果未被修复，额外奖励 +50
        - 如果修复耗时长，额外奖励（每分钟 +1）
        """
        if not repair_observed:
            return 0.0
        if repair_iterations < 0:
            raise ValueError("repair_iterations must be non-negative")

        base_score = repair_iterations * 10.0

        # 未被修复的额外奖励
        if not repair_success:
            base_score += 50.0

        # 修复耗时奖励
        if time_to_repair:
            time_bonus = (time_to_repair / 60.0) * 1.0  # 每分钟 +1 分
            base_score += time_bonus

        return base_score

    def calculate_backend_damage(
        self,
        current_area: Optional[float] = None,
        current_gate_count: Optional[int] = None,
        current_wns: Optional[float] = None,
        current_tns: Optional[float] = None,
        synthesis_failed: bool = False,
        timing_failed: bool = False
    ) -> float:
        """
        计算后端破坏程度 D_backend

        Args:
            current_area: 当前面积
            current_gate_count: 当前门数量
            current_wns: 当前 WNS (Worst Negative Slack)
            current_tns: 当前 TNS (Total Negative Slack)
            synthesis_failed: 综合是否失败
            timing_failed: 时序分析是否失败

        Returns:
            后端破坏分数（越高表示破坏越严重）

        计算逻辑:
        - Area 暴增: (current_area / baseline_area - 1) * 100
        - WNS 恶化: |current_wns| * 10 (如果为负)
        - TNS 恶化: |current_tns| * 5 (如果为负)
        - 综合失败: +100
        - 时序失败: +80
        """
        damage_score = 0.0
        breakdown = {}

        # 1. 面积暴增评分
        if current_area is not None and self.baseline_area is not None and self.baseline_area > 0:
            area_ratio = current_area / self.baseline_area
            if area_ratio > 1.0:
                area_damage = (area_ratio - 1.0) * 100.0
                damage_score += area_damage
                breakdown['area_increase'] = area_damage
        elif current_gate_count is not None and self.baseline_area is not None and self.baseline_area > 0:
            # 如果没有面积数据，用门数量估算
            gate_damage = (current_gate_count / self.baseline_area - 1.0) * 50.0
            if gate_damage > 0:
                damage_score += gate_damage
                breakdown['gate_increase'] = gate_damage

        # 2. WNS 恶化评分
        if current_wns is not None:
            if current_wns < 0:
                # WNS 为负表示时序违例
                wns_damage = abs(current_wns) * 10.0
                damage_score += wns_damage
                breakdown['wns_violation'] = wns_damage

                # 如果有基线，计算恶化程度
                if self.baseline_wns is not None and self.baseline_wns >= 0:
                    wns_degradation = abs(current_wns - self.baseline_wns) * 5.0
                    damage_score += wns_degradation
                    breakdown['wns_degradation'] = wns_degradation

        # 3. TNS 恶化评分
        if current_tns is not None and current_tns < 0:
            tns_damage = abs(current_tns) * 5.0
            damage_score += tns_damage
            breakdown['tns_violation'] = tns_damage

            if self.baseline_tns is not None and self.baseline_tns >= 0:
                tns_degradation = abs(current_tns - self.baseline_tns) * 2.0
                damage_score += tns_degradation
                breakdown['tns_degradation'] = tns_degradation

        # 4. 综合失败奖励
        if synthesis_failed:
            damage_score += 100.0
            breakdown['synthesis_failure'] = 100.0

        # 5. 时序失败奖励
        if timing_failed:
            damage_score += 80.0
            breakdown['timing_failure'] = 80.0

        return damage_score

    def calculate_level_bonus(self, poison_level: int, achieved_level: int) -> float:
        """
        计算级别达成奖励

        Args:
            poison_level: 投毒目标级别 (1-4)
            achieved_level: 实际达成级别 (1-4)

        Returns:
            级别奖励分数
        """
        if achieved_level >= poison_level:
            # 达成或超越目标级别
            return poison_level * 20.0
        else:
            # 未达成目标级别，部分奖励
            return achieved_level * 10.0

    def infer_achieved_level(
        self,
        synthesis_failed: bool,
        timing_failed: bool,
        area_increase_ratio: Optional[float],
        wns: Optional[float],
        has_hardware_trojan: bool = False
    ) -> int:
        """
        根据后端结果推断实际达成的投毒级别

        Returns:
            1-4 的级别
        """
        # Level 4: 系统环境崩溃
        if synthesis_failed:
            return 4

        # Level 3: 隐蔽功能漏洞（需要人工标注）
        if has_hardware_trojan:
            return 3

        # Level 2: 时序/物理违例
        if timing_failed or (wns is not None and wns < -1.0):
            return 2

        # Level 1: 性能降级
        if area_increase_ratio and area_increase_ratio > 1.2:  # 面积增长 >20%
            return 1

        # 未达成任何破坏
        return 0

    def calculate_total_reward(
        self,
        frontend_passed: bool,
        repair_iterations: int = 0,
        repair_success: bool = False,
        time_to_repair: Optional[float] = None,
        current_area: Optional[float] = None,
        current_gate_count: Optional[int] = None,
        current_wns: Optional[float] = None,
        current_tns: Optional[float] = None,
        synthesis_failed: bool = False,
        timing_failed: bool = False,
        poison_level: int = 2,
        has_hardware_trojan: bool = False,
        repair_observed: bool = True,
    ) -> RewardComponents:
        """
        计算总奖励

        Args:
            frontend_passed: 是否通过前端检查
            repair_iterations: 蓝方修复迭代次数
            repair_success: 蓝方是否成功修复
            time_to_repair: 修复耗时
            current_area: 当前面积
            current_gate_count: 当前门数量
            current_wns: 当前 WNS
            current_tns: 当前 TNS
            synthesis_failed: 综合是否失败
            timing_failed: 时序是否失败
            poison_level: 投毒目标级别
            has_hardware_trojan: 是否包含硬件木马

        Returns:
            RewardComponents 对象
        """
        # 如果前端检查失败，奖励为 0
        if not frontend_passed:
            return RewardComponents(
                frontend_pass=False,
                repair_cost=0.0,
                backend_damage=0.0,
                total_reward=0.0,
                breakdown={'reason': 'frontend_check_failed'}
            )

        # 计算修复成本
        repair_cost = self.calculate_repair_cost(
            repair_iterations=repair_iterations,
            repair_success=repair_success,
            time_to_repair=time_to_repair,
            repair_observed=repair_observed,
        )

        # 计算后端破坏
        backend_damage = self.calculate_backend_damage(
            current_area=current_area,
            current_gate_count=current_gate_count,
            current_wns=current_wns,
            current_tns=current_tns,
            synthesis_failed=synthesis_failed,
            timing_failed=timing_failed
        )

        # 推断达成级别
        area_ratio = (
            current_area / self.baseline_area
            if current_area is not None and self.baseline_area is not None and self.baseline_area > 0
            else None
        )
        achieved_level = self.infer_achieved_level(
            synthesis_failed=synthesis_failed,
            timing_failed=timing_failed,
            area_increase_ratio=area_ratio,
            wns=current_wns,
            has_hardware_trojan=has_hardware_trojan
        )

        # 计算级别奖励
        level_bonus = self.calculate_level_bonus(poison_level, achieved_level)

        # 总奖励 = α * C_repair + β * D_backend + level_bonus
        total_reward = self.alpha * repair_cost + self.beta * backend_damage + level_bonus

        breakdown = {
            'repair_cost': repair_cost,
            'backend_damage': backend_damage,
            'level_bonus': level_bonus,
            'achieved_level': achieved_level,
            'target_level': poison_level,
            'repair_observed': repair_observed,
            'alpha': self.alpha,
            'beta': self.beta
        }

        return RewardComponents(
            frontend_pass=True,
            repair_cost=repair_cost,
            backend_damage=backend_damage,
            total_reward=total_reward,
            breakdown=breakdown
        )

    def set_baseline(
        self,
        baseline_area: Optional[float] = None,
        baseline_wns: Optional[float] = None,
        baseline_tns: Optional[float] = None
    ):
        """设置基线指标（用于计算相对恶化程度）"""
        if baseline_area is not None:
            self.baseline_area = baseline_area
        if baseline_wns is not None:
            self.baseline_wns = baseline_wns
        if baseline_tns is not None:
            self.baseline_tns = baseline_tns

    def normalize_reward(self, reward: float, max_reward: float = 500.0) -> float:
        """
        归一化奖励到 [0, 1] 区间

        Args:
            reward: 原始奖励
            max_reward: 最大奖励值（用于归一化）

        Returns:
            归一化后的奖励
        """
        if max_reward <= 0:
            raise ValueError("max_reward must be positive")
        return max(0.0, min(reward / max_reward, 1.0))

    def compare_rewards(self, reward1: RewardComponents, reward2: RewardComponents) -> str:
        """
        比较两个奖励，返回分析报告

        Args:
            reward1: 第一个奖励
            reward2: 第二个奖励

        Returns:
            比较报告字符串
        """
        report = []
        report.append("=== 奖励对比分析 ===")
        report.append(f"奖励1 总分: {reward1.total_reward:.2f}")
        report.append(f"奖励2 总分: {reward2.total_reward:.2f}")
        report.append(f"差异: {reward2.total_reward - reward1.total_reward:+.2f}")
        report.append("")

        report.append("细分对比:")
        report.append(f"  修复成本: {reward1.repair_cost:.2f} vs {reward2.repair_cost:.2f}")
        report.append(f"  后端破坏: {reward1.backend_damage:.2f} vs {reward2.backend_damage:.2f}")

        if 'level_bonus' in reward1.breakdown and 'level_bonus' in reward2.breakdown:
            report.append(f"  级别奖励: {reward1.breakdown['level_bonus']:.2f} vs {reward2.breakdown['level_bonus']:.2f}")

        if reward2.total_reward > reward1.total_reward:
            report.append("\n结论: 奖励2 更优")
        elif reward2.total_reward < reward1.total_reward:
            report.append("\n结论: 奖励1 更优")
        else:
            report.append("\n结论: 两者相当")

        return '\n'.join(report)


