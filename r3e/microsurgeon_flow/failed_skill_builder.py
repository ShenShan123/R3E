"""
§3.6.2 failed skill payload 装配 (落码版).

从蓝队 ECO 执行体的 revert 轨迹组装失败技能 payload, 给 SkillVetting.vet_distill
吃。当前只实例化 sizing_plateau / tool_no_effect (路径A 耗尽型) 与
structural_bottleneck / overconstraint_infeasible (路径B 根因型) 的兜底分类;
端点级信号 (§4.4 violating_endpoints) 由调用方传入, 缺则填空数组。

六关红线规避 (见 memory_integrity_guard.py):
  - precondition.is_failed_skill=true: 召回出口 filter 标志
  - action_template.allowed_edit_scope=["openroad_eco:<design>"]: 过 broad
  - validation.backend_metric="failed": 非 hollow 黑名单串
  - failure_signature 整块嵌 precondition 内: 不进 hollow 扫描面 (action+validation)
  - 全程不写 wns_delta / wns_improvement_target 键: 避 hollow 正则
"""

from __future__ import annotations

from typing import Any, Iterable


def derive_strategy_label(hist_entry: dict) -> str:
    """
    从 history 条目机械派生 strategy 标签, 不贴语义帽子。

    红线：机械派生、不贴语义帽子——critical_path_upsize 这类语义分桶是 D 的活，
    B 阶段伪造语义标签比 unknown 占位符更糟。
    """
    acts = hist_entry.get("actions") or []
    if not acts:
        return "no_action"
    types = sorted({a["action_type"] for a in acts})
    return f"{'+'.join(types)}_n{len(acts)}"


# §3.6.3 五类
_FAILURE_CLASSES = frozenset({
    "sizing_plateau",            # 路径A: 同策略多回合无改善 (B3 即此)
    "tool_no_effect",            # 路径A: 工具反复无 WNS 变化
    "structural_bottleneck",     # 路径B: 根因在域外 (前端结构需改)
    "overconstraint_infeasible", # 路径B: SDC 物理不可解
    "other",                     # 兜底
})


def classify_sizing_failure(
    *,
    wns_trajectory: list[float],
    rounds: int,
    distinct_strategies: int,
) -> str:
    """
    根据 ECO 执行体的回合统计判 failure_class.

    判据 (§4.3):
      - rounds>=3 且 WNS 无改善 → sizing_plateau
      - rounds>=1 且 WNS 几乎零波动 (max-min < 1e-4)      → tool_no_effect
      - 其它 → other (调用方可后续细分)

    distinct_strategies 参数保留仅做 API 兼容 + 证据透传, 不再参与路由。
    调用方写入 failure_signature["distinct_strategy_count"]。
    """
    if not wns_trajectory:
        return "other"
    span = max(wns_trajectory) - min(wns_trajectory)
    baseline = wns_trajectory[0]
    # best-so-far 严格改善: 任一轮 WNS 超过 baseline 即算"工具够得着空间"。
    # 不能只比首尾 (final > baseline) —— 非单调轨迹 (如中途改善后回归
    # [-0.06,-0.02,-0.08]) 首尾会漏判: 第2轮 -0.02 已证明有 headroom,
    # 误判 sizing_plateau (= sizing 耗尽) 与之矛盾。改 best-so-far 后此类
    # 落 other (回归/不稳定), 仅"任一轮都没超过 baseline"才算真 plateau。
    improved = max(wns_trajectory) > baseline + 1e-6
    if rounds >= 1 and span < 1e-4:
        return "tool_no_effect"
    if rounds >= 3 and not improved:
        return "sizing_plateau"
    return "other"


def build_failed_skill_payload(
    *,
    case_id: str,
    design: str,
    platform: str,
    variant: str,
    poison_param: str,
    period: float,
    failure_class: str,
    wns_trajectory: list[float],
    strategy_tried: list[str],
    rounds: int,
    violating_endpoints: list[dict[str, Any]] | None = None,
    verdict: str = "domain_exhausted_no_increment",
    triggered_by_cases: Iterable[str] | None = None,
    distinct_strategy_count: int | None = None,  # B: A-2 标签真去重计数, 入 signature 仅证据透传
) -> dict[str, Any]:
    """
    装配 failed skill payload, 返回 vet_distill(**payload) 直接可吃的 dict。

    Parameters
    ----------
    case_id : 失败这次 run 的 case_id (与原 case 关联)
    failure_class : §3.6.3 五类之一; 调用方自行判定 (sizing 类可用 classify_sizing_failure)
    wns_trajectory : 各回合末 WNS, 键名故意不叫 wns_delta (避 hollow 正则)
    strategy_tried : 各回合 repair 策略短串 (e.g. "sizing_up_NAND2_X4")
    rounds : §4.3 agent 级回合数, 非工具内迭代轮
    violating_endpoints : §4.4 端点对; B3 当时未采可传 None/[], 不影响 schema 立住
    triggered_by_cases : 跨 case 合并时的去重列表; 当前默认 [case_id]
    distinct_strategy_count : A-2 标签去重计数，证据透传，不参与路由
    """
    if failure_class not in _FAILURE_CLASSES:
        raise ValueError(
            f"failure_class={failure_class!r} 非 §3.6.3 五类之一: {sorted(_FAILURE_CLASSES)}"
        )
    if violating_endpoints is None:
        violating_endpoints = []
    if triggered_by_cases is None:
        triggered_by_cases = [case_id]

    # skill_name: §3.6.2 不带 design 便于跨设计合并 (skill_hash 自动去重靠 distill)
    skill_name = f"failed_{failure_class}"

    precondition = {
        # ── 召回门 ────────────────────────────────────────────────────
        "is_failed_skill": True,                       # ★出口 filter 标志
        "error_signature": f"FAILED_{failure_class}",  # 反查询值: 即使 filter 漏也难撞
        "context_pattern": "blue_llm_eco_no_increment",
        # ── case 溯源 ─────────────────────────────────────────────────
        "source": "blue_llm_active_eco",
        "design": design,
        "platform": platform,
        "variant": variant,
        "is_poisoned": True,
        "poison_param": poison_param,
        "semantic": "poison",
        # ── §3.6.2 强制结构化签名 (嵌 precondition 避 hollow 扫描面) ──
        "failure_signature": {
            "failure_class": failure_class,
            "violating_endpoints": list(violating_endpoints),
            "period": period,
            "strategy_tried": list(strategy_tried),
            "wns_trajectory": list(wns_trajectory),   # 键名不叫 wns_delta
            "rounds": rounds,
            "verdict": verdict,
            "triggered_by_cases": list(triggered_by_cases),
            "prompt_harness": "isomorphic_v1",  # A-2: Phase 2 合并隔离; 入 signature 而非 _metadata
            "distinct_strategy_count": distinct_strategy_count,  # B: 证据透传, 不参与路由
        },
    }

    action_template = {
        "repair_strategy": "blue_llm_eco_sizing",     # 非 relaxation 类 → 过 cheat 检
        "allowed_edit_scope": [f"openroad_eco:{design}"],  # ★有界 scope → 过 broad
        "key_actions": list(strategy_tried),
        # 绝不写 wns_delta / wns_improvement_target / full_pass 键
    }

    validation = {
        "backend_metric": "failed",                   # 非 hollow 黑名单串; backend 域必需
    }

    rollback_condition = {
        "wns_degradation": True,
    }

    return {
        "skill_name": skill_name,
        "case_id": case_id,
        "precondition": precondition,
        "action_template": action_template,
        "validation": validation,
        "rollback_condition": rollback_condition,
    }


# ── §3.1 后端三态技能 builder(closed/routed_unclosed/failed; 路径蒸馏版) ──────────
# 动机: §3.1 后端库 schema 本就定义三态(backend_metric=closed/routed_unclosed/failed),
# 但路 C 负结果时代只实现了 failed 支(单 VT 后端永不闭合). ASAP7 multi-VT VT-swap 首次
# 产正增量+闭合 → 需 closed/routed_unclosed 支. 本 builder 实现三态, 供方案D trajectory
# (路径执行体)用. 旧 build_failed_skill_payload 保留给单 cell 基线(backend_eco_oneshot).
#
# 边界纪律(严禁模糊):
#   - 后端域内技能: 始终 domain="backend", 绝不写 loopback(跨域往返由 orchestrator 完成后另蒸).
#   - 三态明显区分(LLM 召回无歧义): skill_name 前缀 + precondition.backend_outcome +
#     validation.backend_metric 三重冗余; failed→is_failed_skill, closed→is_success_skill.
#   - 泛化 vs 专有: 可复用配方(repair_strategy/action_class_sequence/context_pattern/lesson)
#     与专属 provenance(period/final_wns/violating_endpoints/select_reasons) 分字段位标注.
#   - 路径蒸馏: key_actions = action_class_sequence(粗粒度 up/down/vt 序列, 无 cell 类型, T3).
#   - hollow-safe: 时序数值入 precondition.failure_signature(不进 validation/action 扫描面);
#     validation 仅三态枚举 + route 标志; 全程不写 wns_delta 键.
_BACKEND_METRICS = frozenset({"closed", "routed_unclosed", "failed"})
_SIG_PREFIX = {"closed": "CLOSED", "routed_unclosed": "PARTIAL", "failed": "FAILED"}


def build_backend_skill_payload(
    *,
    backend_metric: str,                 # closed | routed_unclosed | failed
    case_id: str,
    design: str,
    platform: str,
    variant: str,
    poison_param: str,
    period: float,
    initial_wns: float,
    final_wns: float,
    wns_trajectory: list[float],
    action_class_sequence: list[str],
    rounds: int,
    select_reasons: list[str] | None = None,
    violating_endpoints: list[dict[str, Any]] | None = None,
    failure_class: str | None = None,    # closed 为 None; routed_unclosed/failed 用 §3.6.3
    cross_domain_trigger: bool = False,  # 后端 abstain → 阀门判回环的信号(非写 loopback)
    abstain_reason: str | None = None,   # 调用方须已脱敏
    prompt_harness: str = "trajectory_v1",
    repair_strategy: str = "blue_llm_eco_vtswap",
    lesson: str = "",
    triggered_by_cases: "Iterable[str] | None" = None,
) -> dict[str, Any]:
    """装配后端三态技能 payload(vet_distill 直接可吃)。见上方边界纪律。"""
    if backend_metric not in _BACKEND_METRICS:
        raise ValueError(
            f"backend_metric={backend_metric!r} 非三态之一: {sorted(_BACKEND_METRICS)}"
        )
    if backend_metric != "closed" and failure_class is not None \
            and failure_class not in _FAILURE_CLASSES:
        raise ValueError(
            f"failure_class={failure_class!r} 非 §3.6.3 五类之一: {sorted(_FAILURE_CLASSES)}"
        )
    violating_endpoints = list(violating_endpoints or [])
    select_reasons = list(select_reasons or [])
    triggered_by_cases = list(triggered_by_cases or [case_id])
    is_failed = backend_metric == "failed"
    is_closed = backend_metric == "closed"

    precondition = {
        # ── 三态标注(三重冗余, LLM 召回无歧义) ───────────────────────────
        "backend_outcome": backend_metric,                 # ★显式三态 marker
        "is_failed_skill": is_failed,                      # 仅 failed=True(兼容旧召回 filter)
        "is_success_skill": is_closed,                     # 仅 closed=True
        "error_signature": f"{_SIG_PREFIX[backend_metric]}_{failure_class or design}",
        "context_pattern":
            f"design={design},wns_stage=post_route,clock={period},platform={platform}",
        # ── 溯源 ──────────────────────────────────────────────────────
        "source": "blue_llm_trajectory_eco",
        "design": design, "platform": platform, "variant": variant,
        "is_poisoned": True, "poison_param": poison_param, "semantic": "poison",
        # ── 结构化签名(嵌 precondition, 避 hollow 扫描面) ──────────────────
        "failure_signature": {
            "backend_metric": backend_metric,
            "failure_class": failure_class,
            "violating_endpoints": violating_endpoints,
            "period": period,
            "action_class_sequence": list(action_class_sequence),
            "select_reasons": select_reasons,              # 调用方已脱敏
            "wns_trajectory": list(wns_trajectory),        # 键名非 wns_delta
            "initial_wns": initial_wns,
            "final_wns": final_wns,
            "rounds": rounds,
            "cross_domain_trigger": cross_domain_trigger,
            "abstain_reason": abstain_reason,
            "prompt_harness": prompt_harness,
            "triggered_by_cases": triggered_by_cases,
        },
    }
    action_template = {
        # 泛化(可复用配方): closed/routed_unclosed 是有效/部分有效配方, failed 是负向标记
        "repair_strategy": repair_strategy,
        "allowed_edit_scope": [f"openroad_eco:{design}"],  # 有界 scope(过 broad)
        "key_actions": list(action_class_sequence),        # 路径蒸馏: 粗粒度序列, 无 cell 类型
        "trigger": "llm_trajectory_vtswap",
        "lesson": lesson,
        # 绝不写 wns_delta / wns_improvement_target 键(避 hollow 正则)
    }
    validation = {
        "backend_metric": backend_metric,                  # ★三态权威(非 hollow 黑名单串)
        "route_completed": True,
        "gds_generated": False,                            # trajectory 在 route odb 上, 不产 GDS
    }
    rollback_condition = {"wns_degradation": True}

    return {
        "skill_name": f"{backend_metric}_trajectory",      # ★三态前缀
        "case_id": case_id,
        "precondition": precondition,
        "action_template": action_template,
        "validation": validation,
        "rollback_condition": rollback_condition,
    }
