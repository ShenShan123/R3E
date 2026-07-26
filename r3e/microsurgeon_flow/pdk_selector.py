"""PDK 选择器(LLM 推理): 给定电路特征 + 各 PDK 特性 → 推理选最合适工艺库 + 理由.

真实场景按设计不同需求用最合适 PDK: 紧时序/高频→先进节点(asap7 7nm); 低成本/中低频→成熟节点
(sky130 130nm); 高压/鲁棒→极成熟(gf180 180nm). 本模块抽电路特征 + 喂 PDK 知识库 → LLM 推理选.
schema 强约束输出 {pdk, rationale}(单选 + ≤2句理由).
"""
from __future__ import annotations

import re
from pathlib import Path

# PDK 知识库(节点/相对性能/功耗/成本/multi-VT/适用场景). 给 LLM 作 grounding.
PDK_PROFILES = {
    "asap7": {
        "node_nm": 7, "rel_perf": "highest", "rel_power": "lowest", "rel_cost": "highest",
        "multi_vt": True,
        "fit": "7nm FinFET 先进节点; 高性能低功耗; 紧时序/高频/低功耗预算首选; "
               "multi-VT(VT-swap)给后端正增量 headroom; 算术密集(乘法器)受益于先进节点.",
    },
    "nangate45": {
        "node_nm": 45, "rel_perf": "high", "rel_power": "moderate", "rel_cost": "moderate",
        "multi_vt": False,
        "fit": "45nm 学术通用; 中高性能; 单 VT(仅 sizing, 后端 headroom 有限).",
    },
    "sky130hd": {
        "node_nm": 130, "rel_perf": "moderate", "rel_power": "moderate", "rel_cost": "low",
        "multi_vt": False,
        "fit": "130nm 成熟开源; 低成本; IoT/中低频/成本敏感设计; 单 VT.",
    },
    "gf180": {
        "node_nm": 180, "rel_perf": "lowest", "rel_power": "higher", "rel_cost": "lowest",
        "multi_vt": False,
        "fit": "180nm 极成熟稳健; 高压容差; 模拟友好/鲁棒/低频/极低成本; 单 VT.",
    },
}


def extract_circuit_features(rtl_paths) -> dict:
    """从 RTL 抽电路特征(给选择器作输入). 轻量: 算术算子计数 + 时序块 + 行数规模."""
    if isinstance(rtl_paths, (str, Path)):
        rtl_paths = [rtl_paths]
    text = ""
    for p in rtl_paths:
        try:
            text += Path(p).read_text(errors="ignore") + "\n"
        except OSError:
            pass
    return {
        "n_mul": len(re.findall(r"[^*]\*[^*]", text)),       # 乘法器(粗估)
        "n_add": len(re.findall(r"[^+]\+[^+]", text)),       # 加法器
        "n_seq_blocks": len(re.findall(r"always\s*@\s*\(\s*posedge", text)),
        "lines": text.count("\n"),
        "arith_dense": len(re.findall(r"[^*]\*[^*]", text)) >= 3,  # 乘法密集
    }


def _build_prompt(features: dict, requirement: dict, options: list[str]) -> str:
    profiles = "\n".join(
        f"- {k}: 节点{PDK_PROFILES[k]['node_nm']}nm 性能{PDK_PROFILES[k]['rel_perf']} "
        f"功耗{PDK_PROFILES[k]['rel_power']} 成本{PDK_PROFILES[k]['rel_cost']} "
        f"multiVT={PDK_PROFILES[k]['multi_vt']}; {PDK_PROFILES[k]['fit']}"
        for k in options
    )
    return (
        "你是 EDA 工艺库选型顾问. 据电路特征 + 设计需求, 从候选 PDK 选**最合适的一个**.\n\n"
        "## 电路特征\n"
        f"- 乘法器数≈{features.get('n_mul')} 加法器≈{features.get('n_add')} "
        f"时序块{features.get('n_seq_blocks')} 规模{features.get('lines')}行 "
        f"算术密集={features.get('arith_dense')}\n\n"
        "## 设计需求\n"
        f"- 优先级: {requirement.get('priority', 'balanced')}"
        " (performance=高频/紧时序 | power=低功耗 | cost=低成本 | balanced=均衡)\n"
        f"- 目标频率档: {requirement.get('freq_tier', 'medium')} (high/medium/low)\n\n"
        "## 候选 PDK\n" + profiles + "\n\n"
        "## 任务: 输出严格 JSON, 选一个:\n"
        '{"pdk":"<候选之一>","rationale":"≤2句: 为何这个最合适(扣电路特征+需求)"}\n'
        "原则: 紧时序/高频/算术密集 → 先进节点(asap7, 还有 VT-swap headroom); "
        "成本敏感/中低频 → 成熟节点(sky130/gf180); 均衡看节点与需求匹配. "
        "rationale 必须扣具体特征(如乘法器数/优先级/频率档), 不空泛.\n"
    )


def select_pdk(features: dict, *, requirement: dict | None = None,
               options: list[str] | None = None, call_llm=None) -> dict:
    """LLM 推理选 PDK. 返回 {pdk, rationale}. options 缺省全候选."""
    requirement = requirement or {"priority": "balanced", "freq_tier": "medium"}
    options = options or list(PDK_PROFILES)
    if call_llm is None:
        from microsurgeon_flow.backend_eco_oneshot import call_llm as _cl
        call_llm = _cl
    llm = call_llm(_build_prompt(features, requirement, options))
    if "llm_call_error" in llm:
        return {"pdk": "nangate45", "rationale": f"(llm_error: {llm['llm_call_error']})",
                "_llm_failed": True}
    pdk = llm.get("pdk")
    if pdk not in options:
        pdk = options[0]  # 兜底
    rationale = (llm.get("rationale") or "").strip()
    # 截前 2 句(中文句号常无空格, 按句末标点切)
    sentences = [s for s in re.findall(r"[^。.!?！？]*[。.!?！？]?", rationale) if s.strip()]
    rationale = "".join(sentences[:2])[:200]
    return {"pdk": pdk, "rationale": rationale}
