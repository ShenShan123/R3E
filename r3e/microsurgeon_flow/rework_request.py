"""后端→前端返修请求生成器(回环 B 的接线契约核心).

后端 trajectory abstain(域内尽力仍闭不了)时, 给前端 RTL 修复一份**言简意赅**的返修请求:
  problem(RTL级定位) + backend_attempt(现状) + recommended_fix(一条推荐).
信息粒度最优(剥门级噪声, 接 §4.4): 只喂 RTL 寄存器名/模块, 不喂中段门级 cell(无RTL语义=噪声);
recommended_fix 由 LLM 生成但 schema 强约束(一条 action + ≤1句 rationale + 一处 hint).

对抗自洽性(关键): recommended_fix 只许**功能等价**的时序修复 {retime, restructure}(过 formal
序列等价硬闸); 禁 pipeline(加延迟破等价)/禁放松约束——否则前端"放松时序"会作弊消解红方时序毒,
红毒就失去意义. 真实沙盒对抗中第三方裁判亦禁前端放松时序, 故此约束与裁判一致.
"""
from __future__ import annotations

import json
import re
from collections import Counter

# 等价保持的时序修复动作(过 formal 序列等价硬闸): retime 不改I/O延迟、restructure 布尔等价降深度.
# 禁 pipeline(加延迟=破等价)、禁放松约束——否则红方时序毒被作弊消解(§对抗自洽性).
_ACTIONS = ("retime", "restructure")


def _func_of(master: str) -> str:
    """从 master 名抽逻辑功能(剥驱动/VT/track). asap7 AND2x2_ASAP7_75t_R→AND2; nangate NAND2_X4→NAND2."""
    m = re.match(r"^([A-Za-z][A-Za-z0-9]*?)(?:x[p\d]+\w*_ASAP7|_X\d+)", master)
    return m.group(1) if m else master.split("_")[0]


def _path_stats(cell_chain: list[dict]) -> tuple[int, list[str]]:
    """组合深度(非DFF门数) + 主导逻辑类型(前3). 给 recommended LLM 作 grounding."""
    gates = [c for c in cell_chain if "DFF" not in c.get("master", "").upper()]
    funcs: Counter = Counter(_func_of(c["master"]) for c in gates)
    dominant = [f"{k}×{v}" for k, v in funcs.most_common(3)]
    return len(gates), dominant


def _module_of(startpoint: str) -> str:
    return startpoint.split(".", 1)[0] if "." in startpoint else ""


def _build_recommend_prompt(problem: dict, attempt: dict, dominant: list[str]) -> str:
    return (
        "你是 RTL 时序结构修复顾问. 后端 ECO(VT-swap/sizing)已尽力仍无法闭合某 reg-to-reg "
        "时序违例, 需给前端 RTL 修复**一条简洁可执行**的推荐(结构层, 非门级).\n\n"
        "## 违例段(RTL级)\n"
        f"startpoint={problem['startpoint']}\nendpoint={problem['endpoint']}\n"
        f"module={problem['module']}\n残差={problem['residual_gap_ps']}ps(后端尽力后仍差)\n"
        f"组合深度={problem['comb_depth']}级\n主导逻辑={dominant}\n\n"
        "## 后端现状\n"
        f"试过={attempt['tried']} 改善=+{attempt['improved_ps']}ps 耗尽原因={attempt['exhausted_reason']}\n\n"
        "## 任务: 输出严格 JSON, 仅一条推荐:\n"
        '{"action":"retime|restructure","rationale":"≤1句为何","hint":"一处具体位置/做法"}\n'
        "约束: action 二选一; rationale 最多 1 句; hint 指一处具体(重定时哪段/哪结构优化); "
        "**禁止罗列门级 cell, 不要大段描述**.\n"
        "硬规则(对抗自洽性, 不可违背): 修复必须**功能等价**(过 formal 序列等价硬闸)——\n"
        "**不许放松/改时序约束, 不许改 I/O 延迟或吞吐(禁插流水级 pipeline), 不许改功能**.\n"
        "选 action(两者都等价保持):\n"
        "- retime(重定时): 跨组合逻辑挪寄存器、**不改 I/O 延迟**; 适合多寄存器级间延迟不均、可重平衡.\n"
        "- restructure(布尔等价重构): 降组合深度(加法器树形/进位前瞻、比较器树形等), 功能不变; "
        "适合单条深 reg-to-reg path 或含宽算术(FA/HA/XNOR/XOR).\n"
        "启发: 单条深 path / 宽算术主导 → restructure; 多级可挪 / 延迟不均 → retime.\n"
    )


def build_rework_request(
    evidence,
    *,
    tried: list[str],
    improved_ps: float,
    residual_gap_ps: float,
    exhausted_reason: str,
    call_llm=None,
) -> dict:
    """装配返修请求. evidence=PathEvidence(cell_chain/startpoint/endpoint/slack).
    call_llm 缺省用 backend_eco_oneshot.call_llm(带 schema 约束生成 recommended_fix)."""
    comb_depth, dominant = _path_stats(evidence.cell_chain)
    problem = {
        "type": "setup_violation",
        "startpoint": evidence.startpoint,
        "endpoint": evidence.endpoint,
        "module": _module_of(evidence.startpoint),
        "residual_gap_ps": round(residual_gap_ps, 2),
        "comb_depth": comb_depth,
    }
    attempt = {
        "tried": list(tried),
        "improved_ps": round(improved_ps, 2),
        "exhausted_reason": exhausted_reason,
    }
    # recommended_fix: LLM 生成 + schema 强约束
    if call_llm is None:
        from microsurgeon_flow.backend_eco_oneshot import call_llm as _cl
        call_llm = _cl
    rec = _llm_recommend(call_llm, problem, attempt, dominant)
    return {"problem": problem, "backend_attempt": attempt, "recommended_fix": rec,
            "_grounding": {"dominant_logic": dominant}}


def _llm_recommend(call_llm, problem: dict, attempt: dict, dominant: list[str]) -> dict:
    llm = call_llm(_build_recommend_prompt(problem, attempt, dominant))
    if "llm_call_error" in llm:
        return {"action": "retime", "rationale": f"(llm_error: {llm['llm_call_error']})",
                "hint": "", "_llm_failed": True}
    action = llm.get("action")
    if action not in _ACTIONS:
        action = "retime"
    # 强约束: rationale 截 1 句, 防发散
    rationale = (llm.get("rationale") or "").strip()
    rationale = re.split(r"[。;\n]", rationale)[0][:120] if rationale else ""
    return {"action": action, "rationale": rationale, "hint": (llm.get("hint") or "").strip()[:120]}
