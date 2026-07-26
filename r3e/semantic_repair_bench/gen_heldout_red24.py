#!/usr/bin/env python3
"""生成 Held-out Red-24: 24 个不重复的可修毒, 不与 Red-Fixed-12 重叠.

用法:
  source .env
  python3 gen_heldout_red24.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cirfix_adapter import parse_project
from red_mutator import generate_repairable_poison

CIRFIX = Path("/path/to/cirfix")
OUT = Path(".iso_semrepair/heldout_red24.jsonl")
RED12 = Path(".iso_semrepair/blue_abl/poison_pool.jsonl")

# 从 Red-Fixed-12 加载已有指纹
existing_fps: set[str] = set()
if RED12.exists():
    for line in RED12.read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            raw = f"{d.get('design_name','')}|{d.get('mutation_type','')}|{d.get('mutation_type','')}"
            existing_fps.add(hashlib.sha256(raw.encode()).hexdigest()[:16])

# 7 个 CirFix 设计（opencores 无 project.toml, reed_solomon_decoder 不在 CirFix 数据集）
ALL_DESIGNS = ["decoder_3_to_8", "first_counter_overflow", "flip_flop", "fsm_full",
               "lshift_reg", "mux_4_1", "sdram_controller"]

# 目标: 24 个不重复毒
poisons = []
seen_fp: set[str] = set(existing_fps)  # 不和 Red-Fixed-12 冲突
target = 24
max_attempts = 120  # 24/7 ≈ 3.4 per design, generous for failures+dup
attempt = 0

# 均匀分配: 24 / 8 = 3 per design
from collections import Counter
design_counts = Counter()

import os
import random

while len(poisons) < target and attempt < max_attempts:
    attempt += 1
    # 选当前最少的设计
    design = min(ALL_DESIGNS, key=lambda d: design_counts[d])
    case = parse_project(CIRFIX / design / "project.toml")[0]
    wd = OUT.parent / "gen_work" / f"try_{attempt}"

    old_temp = os.environ.get("LLM_TEMPERATURE")
    os.environ["LLM_TEMPERATURE"] = str(0.70 + (attempt % 10) * 0.03)
    try:
        poison = generate_repairable_poison(case, wd, max_tries=4, design=design)
    finally:
        if old_temp is not None:
            os.environ["LLM_TEMPERATURE"] = old_temp
        else:
            os.environ.pop("LLM_TEMPERATURE", None)

    if not poison.get("ok"):
        print(f"[{attempt}/{max_attempts}] {design}: FAIL {poison.get('reason','')[:40]}", flush=True)
        continue

    fp = hashlib.sha256(f"{design}|{poison.get('mutation_type','')}|{poison.get('rationale','')}".encode()).hexdigest()[:16]
    if fp in seen_fp:
        print(f"[{attempt}/{max_attempts}] {design}: DUPLICATE fp={fp}", flush=True)
        continue
    seen_fp.add(fp)
    design_counts[design] += 1

    entry = {
        "design_name": f"{design}__heldout_{len(poisons)}",
        "top_module": poison["top_module"],
        "buggy_rtl": poison["buggy_path"],
        "golden_rtl": poison["golden_rtl"],
        "tb_sources": poison["tb_sources"],
        "tb_output": poison["tb_output"],
        "deps": poison.get("deps", []),
        "sim_timeout": poison.get("sim_timeout", 10.0),
        "mutation_type": poison.get("mutation_type", ""),
        "rationale": poison.get("rationale", ""),
        "fingerprint": fp,
    }
    poisons.append(entry)
    print(f"[{attempt}/{max_attempts}] {design}: ADMITTED ({poison.get('mutation_type','?')}) "
          f"[{len(poisons)}/{target}] fp={fp}", flush=True)

OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w") as f:
    for p in poisons:
        f.write(json.dumps(p, ensure_ascii=False) + "\n")
print(f"\nGenerated {len(poisons)} held-out red poisons → {OUT}")
print(f"Per design: {dict(design_counts)}")
print(f"Skipped {len(existing_fps)} existing Red-Fixed-12 fingerprints")
