"""红队评估器: LLM 模拟经验丰富的硬件验证工程师, 据蓝方修复结果评估投毒质量.

输出 verdict + upgrade_hint → 喂红队 memory 自进化:
  - too_weak/too_obvious(蓝方一次就修好 / bug 太明显) = 失败投毒 → 记教训 + 升级方向, 下次深化.
  - effective(蓝方费力或修不好, 且 bug 隐蔽合理) = 有效毒 → 作种子.
红队据此"不断深化毒样", 喂蓝方更多样实践让蓝方更强.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from microsurgeon_flow.backend_eco_oneshot import call_llm  # noqa: E402

_CRITIC_PROMPT = """你是一位经验丰富的硬件验证工程师。评估下面这个被注入 RTL 的功能 bug 的"投毒质量"
——目标是制造**隐蔽且有挑战性、但仍可修复**的 bug 来锻炼下游修复器。

## 改动区域 — golden（正确）
{golden_snip}

## 改动区域 — buggy（注入后）
{buggy_snip}

## bug 类型: {mutation_type}
## 下游修复器(蓝方)结果: {blue_outcome}

## 评估任务（资深工程师视角），输出严格 JSON：
{{"verdict": "effective|too_weak|too_obvious", "stealth": <1-5 隐蔽性>, "challenge": <1-5 挑战性>, "reason": "<≤2句:为何这样评>", "upgrade_hint": "<如何把这类毒深化/升级得更隐蔽更有挑战(如改用更间接的位置、组合多处、利用边界条件); ≤2句>"}}

评判原则:
- 蓝方一次就修好 且 bug 一眼可见 → too_obvious
- 蓝方轻易修好 且改动浅显（如直接翻转明显常量）→ too_weak
- 蓝方费力(多次)或未修好, 且 bug 隐蔽合理(像真实工程笔误)→ effective
- upgrade_hint 永远要给(即使 effective 也给"如何更进一步"), 引导红队不断深化毒样.
"""


def _snippet(rtl_path, rng, pad=2):
    lines = Path(rtl_path).read_text(errors="ignore").splitlines()
    if not rng or rng[0] is None:
        return "\n".join(f"{i}: {l}" for i, l in enumerate(lines[:12], 1))
    s = max(1, int(rng[0]) - pad)
    e = min(len(lines), int(rng[1]) + pad)
    return "\n".join(f"{i}: {lines[i - 1]}" for i in range(s, e + 1))


def critique(golden_rtl, buggy_rtl, mutation_type, rng, blue_repaired, blue_tries=None) -> dict:
    """据蓝方修复结果评估一条毒. 返回 {verdict, stealth, challenge, reason, upgrade_hint}."""
    if blue_repaired:
        bo = f"蓝方修复**成功**(尝试 {blue_tries or '?'} 次)"
    else:
        bo = "蓝方**未能修复**"
    prompt = _CRITIC_PROMPT.format(
        golden_snip=_snippet(golden_rtl, rng), buggy_snip=_snippet(buggy_rtl, rng),
        mutation_type=mutation_type, blue_outcome=bo)
    out = call_llm(prompt)
    if isinstance(out, list):
        out = next((x for x in out if isinstance(x, dict)), None) or {}
    if not isinstance(out, dict) or "llm_call_error" in out:
        # 评估失败兜底: 据蓝结果给保守 verdict.
        return {"verdict": "too_weak" if blue_repaired else "effective",
                "stealth": 2, "challenge": 2 if blue_repaired else 4,
                "reason": "critic_llm_unavailable, fallback by blue outcome",
                "upgrade_hint": "改用更间接的位置或组合多处变异以提升隐蔽性与挑战性"}
    v = out.get("verdict", "")
    if v not in ("effective", "too_weak", "too_obvious"):
        out["verdict"] = "too_weak" if blue_repaired else "effective"
    out.setdefault("upgrade_hint", "组合多处变异或利用边界条件以深化毒样")
    out.setdefault("reason", "")
    return out


_PACK_PROMPT = """你是硬件验证专家。下面是一个**有效**（隐蔽且有挑战、下游难修）的 RTL 功能 bug
投毒案例。请把它**泛化**成一条**可迁移的"毒策略"经验**，供后续在其他设计上投出更隐蔽多样的毒。

## 设计: {design}   bug 类型: {mutation_type}
## golden 片段
{golden_snip}
## buggy 片段
{buggy_snip}
## 为何有效: {reason}

## 泛化任务，输出严格 JSON（strategy 必须抽象、**不绑定具体行号/信号名**、可跨设计迁移）：
{{"strategy": "<泛化的毒策略，可迁移>", "applicable_to": "<适用什么类型电路/结构(如状态机/移位寄存器/算术)>", "why_stealthy": "<为何隐蔽难修>", "mutation_family": "<毒类型族>"}}

示例 strategy: "在状态机的状态编码常量上偏移一位，而非直接改输出赋值——错误传播延迟且不直接对应输出，定位更难"。
"""


def generalize(design, mutation_type, golden_rtl, buggy_rtl, rng, reason) -> dict | None:
    """把一条 effective 毒泛化成可迁移的毒策略经验包条目. 返回 pack dict 或 None."""
    prompt = _PACK_PROMPT.format(
        design=design, mutation_type=mutation_type,
        golden_snip=_snippet(golden_rtl, rng), buggy_snip=_snippet(buggy_rtl, rng),
        reason=reason or "")
    out = call_llm(prompt)
    if isinstance(out, list):
        out = next((x for x in out if isinstance(x, dict)), None)
    if not isinstance(out, dict) or "llm_call_error" in out or not out.get("strategy"):
        return None
    return {
        "strategy": out["strategy"], "applicable_to": out.get("applicable_to", ""),
        "why_stealthy": out.get("why_stealthy", ""),
        "mutation_family": out.get("mutation_family", mutation_type),
        "from_design": design,
    }
