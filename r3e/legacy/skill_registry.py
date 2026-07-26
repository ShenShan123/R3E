"""Deprecated legacy skill registry for historical R³E experiments.

采用策略级进化方式: 不让历史经验进 prompt(已被 oracle≈random 判死), 而把"被验证有效
的修复策略"压缩成可触发/可验证/可回归的 skill. skill = trigger(bug_family) → action_policy
(evidence_k/n_candidates/patch_scope) + promotion_evidence + regression_guard.
只有过 promotion gate 的 skill 能进蓝方执行链. 对应方案 G4 skill-routed.
"""
from __future__ import annotations

import json
from pathlib import Path

# 红队 mutation_type → bug_family(skill trigger 用)
_MUT_TO_FAMILY = {
    "索引/位宽偏移(off-by-one)": "off_by_one",
    "索引/位宽偏移": "off_by_one",
    "常量错误": "constant_error",
    "算子翻转（比较）": "operator_error",
    "算子翻转": "operator_error",
    "条件分支交换": "condition_error",
    # P5 co-evolution 暴露的结构毒族(蓝方原 2 族未覆盖, 补 strategy)
    "数据流反向": "data_flow_error",
    "data flow reverse": "data_flow_error",
    "移位方向反转": "data_flow_error",
    "状态信号互换": "state_swap_error",
}

_BASELINE_POLICY = {"evidence_k": 1, "n_candidates": 1, "patch_scope": "local_block"}


def infer_bug_family(case: dict) -> str:
    """判 case 的 bug_family: 优先 mutation_type(红队毒有标签), 否则 '*'(universal, 走通用 skill).

    CirFix-39 无 mutation_type → '*'; Red-Fixed-12 有 → 映射到具体 family 走 conditional skill.
    (后续可加: 从 pre-judge mismatch 模式启发式推断 family)
    """
    mt = (case.get("mutation_type") or case.get("mut") or "").lower()
    if not mt:
        return "*"
    # 关键词 contains 匹配(LLM mutation_type 字符串多变, 如 "data flow reversal"≠"reverse");
    # 结构毒族优先(更具体), 避免 off_by_one 的"偏移"误抢 data_flow 的"移位方向"
    for fam, kws in _FAMILY_KEYWORDS.items():
        if any(kw.lower() in mt for kw in kws):
            return fam
    return "*"


_FAMILY_KEYWORDS = {
    "data_flow_error": ["数据流", "data flow", "dataflow", "移位方向"],
    "state_swap_error": ["状态信号互换", "状态互换", "state swap", "当前态"],
    "off_by_one": ["索引", "位宽偏移", "off-by-one", "index offset", "偏移"],
    "constant_error": ["常量", "字面量", "constant"],
    "operator_error": ["算子翻转", "operator"],
    "condition_error": ["条件分支", "condition", "分支交换"],
}


def load_registry(path, *, formal_mode: bool = False) -> list[dict]:
    if formal_mode:
        raise RuntimeError(
            "legacy/manual skill registry is forbidden in formal_mode=True"
        )
    if not Path(path).exists():
        return []
    return [json.loads(l) for l in open(path) if l.strip()]


def route(
    case: dict,
    registry: list[dict],
    *,
    formal_mode: bool = False,
) -> tuple[dict, str]:
    """给 case 路由 action_policy: 匹配 promoted skill(具体 bug_family 优先于 universal '*').

    返回 (action_policy, skill_id). 无匹配 promoted skill → baseline policy.
    """
    if formal_mode:
        raise RuntimeError("legacy/manual skill routing is forbidden in formal mode")
    bf = infer_bug_family(case)
    promoted = [
        s for s in registry
        if s.get("status") in {"promoted", "manual"}
        and s.get("authority", "legacy_only") == "legacy_only"
    ]
    # 具体 family 匹配优先
    for sk in promoted:
        if sk["trigger"]["bug_family"] == bf and bf != "*":
            return sk["action_policy"], sk["skill_id"]
    # 退到 universal skill
    for sk in promoted:
        if sk["trigger"]["bug_family"] == "*":
            return sk["action_policy"], sk["skill_id"]
    return _BASELINE_POLICY, "baseline"


def make_skill_recall_policy(registry_path):
    """driver 用: 返回 policy_fn(case) → (action_policy, skill_id). 替代 prompt-injection recall_fn."""
    registry = load_registry(registry_path)

    def policy_fn(case):
        return route(case, registry)
    return policy_fn


# ── skill strategy 层: family-specific 修复方法论(检查清单, 非历史 case 文本) ──────────
# 关键区别于被判死的 naive memory: 注入的是"修这类 bug 的通用方法论"(可迁移), 不是"某历史
# case 怎么修的"(不可迁移+干扰, oracle≈random 已判死). G4 揭示 k/n-only 无 budget 优势→需此层.
_REPAIR_HINTS = {
    "constant_error": (
        "该 bug 疑似**常量值错误**。系统检查：① 对比仿真证据中期望输出，反推正确常量值；"
        "② 检查所有字面量常量（如 4'bxxxx、参数初值、case 标签）是否覆盖所有取值；"
        "③ 特别注意 case/条件分支的常量是否完整、是否有重复或遗漏的取值。"
    ),
    "off_by_one": (
        "该 bug 疑似**索引/位宽偏移（off-by-one）**。系统检查：① 所有数组索引、位选 [x:y] "
        "是否差 1；② 移位/计数的边界条件（<= vs <、+1/-1）；③ 特别注意最高位/最低位、"
        "计数回绕边界、反馈源的位置偏移。"
    ),
    # P5 暴露的结构毒族 strategy(覆盖蓝方原 2 族未及的结构性深毒)
    "data_flow_error": (
        "该 bug 疑似**数据流方向错误**。系统检查：① 移位方向（`<<` vs `>>`）是否反；"
        "② 赋值的目标与源寄存器是否被互换（如 `a<=b` 应为 `b<=a`）；③ 迭代/流水线/反馈链"
        "的数据流向是否相反；④ 连续赋值中数据沿相反方向流动。对照仿真证据反推正确流向。"
    ),
    "state_swap_error": (
        "该 bug 疑似**状态/时序信号互换**。系统检查：① 当前状态与下一状态信号是否被互换"
        "（利用非阻塞赋值延迟，语法对但时序错）；② 状态转移目标是否错配；③ 多个时序信号"
        "的赋值是否串位。特别注意 always 块内非阻塞赋值的更新时序。"
    ),
}


def make_strategy_recall(registry_path=None):
    """skill strategy 层 recall: 按 bug_family 注入修复方法论(非历史 case). 替 naive memory recall.

    返回 recall_fn(case) → strategy hint 段(注入 propose 的 {memory} 位置). 无对应 family → ''.
    """
    def recall(case):
        bf = infer_bug_family(case)
        hint = _REPAIR_HINTS.get(bf)
        if not hint:
            return ""
        return f"\n## 修复方法论提示（此类 bug 的通用检查清单，非历史案例，需独立判断）\n{hint}\n"
    return recall
