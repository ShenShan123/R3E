"""CirFix benchmark (project.toml) → 蓝方 manifest jsonl.

每个 (design, bug) 一行: buggy_rtl + golden_rtl + testbench + 仿真 oracle 信息.
功能 bug 域: golden=正确 RTL, buggy=注入 bug 变体, 判据走 oracle_gate(differential).
数据集来源: ekiwi/rtl-repair benchmarks/cirfix (CirFix ASPLOS'22, 引用非重分发).
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path


def parse_project(toml_path, tb_name: str | None = None) -> list[dict]:
    """解析一个 CirFix project.toml → 每个 bug 一条 manifest 行.

    tb_name 选 testbench(默认取首个 verilog-oracle 型: 有 sources+output).
    """
    toml_path = Path(toml_path)
    root = toml_path.parent
    d = tomllib.loads(toml_path.read_text())

    proj = d["project"]
    sources = [root / s for s in proj["sources"]]
    top = proj["toplevel"]

    # verilog-oracle 型 testbench(有 sources+output); CSV-table 型本期不接.
    vtbs = [t for t in d.get("testbenches", []) if t.get("sources") and t.get("output")]
    tb = None
    if tb_name:
        tb = next((t for t in vtbs if t.get("name") == tb_name), None)
    if tb is None and vtbs:
        tb = vtbs[0]

    rows = []
    for bug in d.get("bugs", []):
        original = bug["original"]
        rows.append({
            "design_name": f"{root.name}__{bug['name']}",
            "family": "cirfix",
            "case_dir": str(root),
            "top_module": top,
            "buggy_rtl": str(root / bug["buggy"]),
            "golden_rtl": str(root / original),
            # 其余 source(非被替换文件)作 deps; simple case 为空.
            "deps": [str(s) for s in sources if s.name != Path(original).name],
            "tb_sources": [str(root / s) for s in tb["sources"]] if tb else [],
            "tb_output": tb["output"] if tb else None,
            "tb_oracle": str(root / tb["oracle"]) if tb and tb.get("oracle") else None,
            "sim_timeout": float(tb.get("timeout", 10.0)) if tb else 10.0,
            "tb_name": tb.get("name") if tb else None,
        })
    return rows


def build_manifest(cirfix_root, out_jsonl, designs=None) -> list[dict]:
    """遍历 cirfix_root 下所有 project.toml → 汇总 manifest jsonl."""
    cirfix_root = Path(cirfix_root)
    tomls = sorted(cirfix_root.rglob("project.toml"))
    rows = []
    for t in tomls:
        if designs and t.parent.name not in designs:
            continue
        try:
            rows.extend(parse_project(t))
        except Exception as e:  # noqa: BLE001 — 单 case 解析失败不阻塞全集
            print(f"[adapter] skip {t.parent.name}: {e}")
    out_jsonl = Path(out_jsonl)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return rows


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--cirfix-root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--design", action="append", default=None,
                    help="只取指定 design 目录名(可重复); 缺省全集")
    args = ap.parse_args()
    rows = build_manifest(args.cirfix_root, args.out, designs=args.design)
    from collections import Counter
    by_design = Counter(r["design_name"].split("__")[0] for r in rows)
    print(f"manifest rows = {len(rows)} (designs={len(by_design)})")
    print(f"out = {args.out}")
