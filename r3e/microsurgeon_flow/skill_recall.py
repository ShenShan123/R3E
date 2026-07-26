"""后端技能召回(B): 先检索相似, 再喂 LLM 参考决策(§3.6 防膨胀: 不一股脑喂).

对偶于三态蒸馏(A): A 写干净三态 skill, B 先按 precondition 相似度取候选池,
再按历史收益/闭合状态选 top-K 喂 propose.
§3.6 教训: failure 记忆发散会污染检索上下文、拖累修复率 → 必须"先检索→喂相似",
不可 dump-all. §3.4 泛化: 只喂可复用字段(action_class_sequence/repair_strategy/
context_pattern/failure_class), 不喂专属 provenance(裸 WNS 数值/具体 inst).

读 backend_lib skill_index.jsonl(纯读, 无 gate; 写库才走 three_lib_gate). 三态差异化呈现:
  closed         → "这条 VT-swap 路径闭合过, 可套用"
  routed_unclosed→ "改善但没闭合, 可作起点/叠加"
  failed         → "这些策略无效/结构不可解, 避开或考虑转域"
"""
from __future__ import annotations

import json
from pathlib import Path

_STATES = ("closed", "routed_unclosed", "failed")


def _load_skills(lib_path) -> list[dict]:
    p = Path(lib_path)
    if not p.exists():
        return []
    skills = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            skills.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return skills


def _metric_of(skill: dict) -> str | None:
    raw = (skill.get("validation", {}).get("backend_metric")
           or skill.get("precondition", {}).get("backend_outcome")
           or skill.get("action_template", {}).get("tristate"))
    if raw in _STATES:
        return raw
    text = str(raw or "")
    for state in _STATES:
        if state in text:
            return state
    return None


def _score(skill: dict, *, design: str, platform: str, period: float, endpoint: str) -> float:
    """precondition 相似度: 同 design 最强, 再 platform / period 邻近 / 端点重合."""
    pc = skill.get("precondition", {})
    fs = pc.get("failure_signature", {})
    s = 0.0
    if pc.get("design") == design:
        s += 3.0
    if pc.get("platform") == platform:
        s += 1.0
    sp = fs.get("period")
    if sp is None:
        poison = str(pc.get("poison_param", ""))
        if poison.startswith("clk_period="):
            try:
                sp = float(poison.split("=", 1)[1])
            except ValueError:
                sp = None
    if sp is not None and period:
        s += max(0.0, 1.0 - abs(float(sp) - float(period)) / max(float(period), 1e-9))
    if endpoint:
        eps = {e.get("endpoint") for e in fs.get("violating_endpoints", [])
               if isinstance(e, dict)}
        if endpoint in eps:
            s += 2.0
    return s


def _benefit_score(skill: dict, metric: str) -> float:
    """Historical repair benefit used only for ranking, not prompt text.

    Prefer real trajectory fields when present. Some deterministic baseline
    skills only carry setup_wns/tristate; for those, use a small state prior so
    they remain ordered but do not dominate real measured trajectories.
    """
    fs = skill.get("precondition", {}).get("failure_signature", {})
    initial = fs.get("initial_wns")
    final = fs.get("final_wns")
    if initial is not None and final is not None:
        try:
            delta = float(final) - float(initial)
        except (TypeError, ValueError):
            delta = 0.0
        closure_bonus = 1000.0 if metric == "closed" else 0.0
        return closure_bonus + max(0.0, delta)

    at = skill.get("action_template", {})
    tristate = at.get("tristate")
    if metric == "closed" or tristate == "closed":
        return 100.0
    if metric == "routed_unclosed" or tristate == "routed_unclosed":
        return 10.0
    return 0.0


def recall_skills(lib_path, *, design: str, platform: str, period: float,
                  endpoint: str = "", k: int = 3,
                  candidate_k: int = 12) -> dict[str, list[dict]]:
    """检索相似候选池, 再按历史收益选 top-K.

    For each state:
      1. keep the top ``candidate_k`` by precondition similarity;
      2. within that similar pool, rank by historical benefit, then similarity;
      3. return up to ``k`` prompt entries.

    score≤0 不召回. This keeps high-benefit but unrelated skills out.
    """
    scored = []
    for sk in _load_skills(lib_path):
        m = _metric_of(sk)
        if m not in _STATES:
            continue
        sc = _score(sk, design=design, platform=platform, period=period, endpoint=endpoint)
        if sc > 0:
            scored.append((sc, _benefit_score(sk, m), m, sk))
    out: dict[str, list[dict]] = {st: [] for st in _STATES}
    for st in _STATES:
        pool = [row for row in scored if row[2] == st]
        pool.sort(key=lambda t: t[0], reverse=True)
        pool = pool[:max(k, candidate_k)]
        pool.sort(key=lambda t: (t[1], t[0]), reverse=True)
        out[st] = [sk for _, _, _, sk in pool[:k]]
    return out


def _summarize(skill: dict) -> str:
    """只取泛化字段(无裸 WNS / 无具体 inst)."""
    pc = skill.get("precondition", {})
    fs = pc.get("failure_signature", {})
    at = skill.get("action_template", {})
    seq = fs.get("action_class_sequence") or at.get("key_actions") or []
    fc = fs.get("failure_class")
    ctx = pc.get("context_pattern", "")
    strat = at.get("repair_strategy", "")
    parts = [f"ctx[{ctx}]", f"strategy={strat}", f"path={seq}"]
    if fc:
        parts.append(f"class={fc}")
    return " ".join(parts)


_STATE_HDR = {
    "closed": "### 成功配方(closed, 这类违例上闭合过 → 可套用此路径)",
    "routed_unclosed": "### 部分配方(routed_unclosed, 改善但没闭合 → 可作起点/叠加)",
    "failed": "### 负向标记(failed, 试过无效/结构不可解 → 避开或考虑回前端转域)",
}


def format_recall(recalled: dict[str, list[dict]]) -> str:
    """三态差异化呈现; 全空返 ''(propose 不加召回段)."""
    blocks = []
    for st in _STATES:
        items = recalled.get(st, [])
        if not items:
            continue
        lines = "\n".join(f"  - {_summarize(sk)}" for sk in items)
        blocks.append(f"{_STATE_HDR[st]}\n{lines}")
    if not blocks:
        return ""
    return "## 历史经验(召回 top-K 相似, 仅供参考决策, 非强制)\n" + "\n".join(blocks) + "\n"


def make_recall_fn(lib_path, *, design: str, platform: str, period: float,
                   k: int = 3, candidate_k: int = 12):
    """driver 用: 绑定 lib + case 元信息, 返回 recall_fn(evidence)->str(已格式化)."""
    def recall_fn(evidence) -> str:
        endpoint = getattr(evidence, "endpoint", "") or ""
        recalled = recall_skills(lib_path, design=design, platform=platform,
                                 period=period, endpoint=endpoint, k=k,
                                 candidate_k=candidate_k)
        hits = {st: len(v) for st, v in recalled.items() if v}
        if hits:                              # 可观测: 召回命中(真跑 log / ablation 分析用)
            print(f"[RECALL] endpoint={endpoint} hits={hits}")
        return format_recall(recalled)
    return recall_fn


# ── loopback_lib 召回(P4 批跑前置): 后端 abstain → 取相似跨域回环修法 ──────────────
# 注: loopback skill 结构异于 backend(validation.loopback_metric=reclosed,
# precondition.backend_failure_signature.{failure_class, violating_endpoints: list[str]}),
# 故另起召回, 不复用 backend 的 _metric_of/_score(读 backend_metric/failure_signature).

def recall_loopback_skills(lib_path, *, failure_class: str, endpoint: str = "",
                           k: int = 3) -> list[dict]:
    """按后端失败签名(failure_class + 违例端点)相似度取 top-K 跨域回环 skill.
    给定新后端 abstain → 取历史"哪类违例→前端怎么修(frontend_skill_ref)"供决策."""
    scored = []
    for sk in _load_skills(lib_path):
        pc = sk.get("precondition", {})
        if not pc.get("is_loopback_skill"):
            continue
        fs = pc.get("backend_failure_signature", {})
        s = 0.0
        if fs.get("failure_class") == failure_class:
            s += 3.0
        if endpoint and endpoint in set(fs.get("violating_endpoints", [])):
            s += 2.0
        if s > 0:
            scored.append((s, sk))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [sk for _, sk in scored[:k]]
