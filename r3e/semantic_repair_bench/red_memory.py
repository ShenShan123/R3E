"""红队记忆: 投毒经验自进化(对称蓝方 memory).

- 失败投毒(too_weak/too_obvious, 蓝方轻易修好/太明显) → 记教训 + upgrade_hint, 下次 recall 升级深化.
- 有效毒(effective, 蓝方费力/未修且隐蔽) → 作种子.
红队据此"不断深化毒样 + 增加新毒", 喂蓝方更多样实践. recall 注入红 mutator prompt.
"""
from __future__ import annotations

import json
from pathlib import Path


def family_of(d: str) -> str:
    return d.split("__")[0] if "__" in d else d


def update(memory_path, record: dict):
    """append 一条投毒评估记录.

    verdict 包含:
      - effective / too_weak / too_obvious: 已生成可修毒后的 critic 评价;
      - generation_failed: red mutator 未能生成通过 gate 的可修毒, 也应沉淀为失败经验.
    """
    memory_path = Path(memory_path)
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    with open(memory_path, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def stats(memory_path) -> dict:
    if not Path(memory_path).exists():
        return {}
    from collections import Counter
    recs = [json.loads(l) for l in open(memory_path) if l.strip()]
    return dict(Counter(r.get("verdict") for r in recs))


def update_pack(pack_path, entry: dict):
    """泛化毒策略经验包入库(去重: 同 strategy 前缀只存一次)."""
    pack_path = Path(pack_path)
    pack_path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if pack_path.exists():
        existing = [json.loads(l) for l in open(pack_path) if l.strip()]
    key = (entry.get("strategy", "") or "")[:40]
    if any((e.get("strategy", "") or "")[:40] == key for e in existing):
        return False
    with open(pack_path, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return True


def make_pack_recall(pack_path, k: int = 3):
    """经验包 recall: 注入高级毒策略库, 指导红队投更隐蔽多样的毒."""
    if not Path(pack_path).exists():
        return lambda design=None: ""
    packs = [json.loads(l) for l in open(pack_path) if l.strip()]

    def recall(design=None) -> str:
        if not packs:
            return ""
        sel = packs[-k:]
        lines = ["", "## 高级毒策略经验包（泛化自历史有效毒，据此投更隐蔽多样的毒）"]
        for i, p in enumerate(sel, 1):
            lines.append(f"策略{i}[{p.get('mutation_family')}|适用{p.get('applicable_to', '')[:20]}]: "
                         f"{p.get('strategy', '')[:90]}")
        return "\n".join(lines)

    return recall


def make_red_recall(memory_path, k_fail: int = 4, k_succ: int = 2):
    """红 mutator 生成时的记忆注入: 避开历史弱毒 + 按 upgrade 升级 + 参考有效毒种子."""
    if not Path(memory_path).exists():
        return lambda design=None: ""
    recs = [json.loads(l) for l in open(memory_path) if l.strip()]

    def recall(design=None) -> str:
        fam = family_of(design) if design else None
        # 同 family 优先, 不足全局补
        fails = [
            r for r in recs
            if r.get("verdict") in ("too_weak", "too_obvious", "generation_failed")
        ]
        succ = [r for r in recs if r.get("verdict") == "effective"]
        if fam:
            fails = ([r for r in fails if family_of(r.get("design", "")) == fam]
                     + [r for r in fails if family_of(r.get("design", "")) != fam])
            succ = ([r for r in succ if family_of(r.get("design", "")) == fam]
                    + [r for r in succ if family_of(r.get("design", "")) != fam])
        fails, succ = fails[:k_fail], succ[:k_succ]
        if not fails and not succ:
            return ""
        lines = ["", "## 红队投毒经验（自进化记忆，据此深化毒样、避免重复弱毒）"]
        for i, r in enumerate(fails, 1):
            lines.append(f"弱毒教训{i}[{r.get('mutation_type')}]: "
                         f"{(r.get('reason', '') or '')[:55]} → **升级**: "
                         f"{(r.get('upgrade_hint', '') or '')[:75]}")
        for i, r in enumerate(succ, 1):
            lines.append(f"有效毒种子{i}[{r.get('mutation_type')}]: "
                         f"{(r.get('reason', '') or '')[:60]}")
        lines.append("→ 本次请生成**更隐蔽、更有挑战**的新毒（避开上述弱毒模式，按升级方向深化）。")
        return "\n".join(lines)

    return recall
