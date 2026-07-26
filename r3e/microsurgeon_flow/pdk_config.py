"""PDK 抽象 + PDK 参数化 eval/legal/action_class.

方案D ASAP7 阶段: trajectory agent 要在 nangate45 / asap7 上跑, 但 backend_eco_oneshot
(单 cell 对照基线) 的 LEF/lib/setRC 硬编码 nangate45 且为 frozen 基线不改。本模块把 PDK
路径与 PDK 相关逻辑(eval 口径 / legal 构造 / action_class)抽出来参数化, 复用 backend 的
PDK-agnostic 原语(_run_openroad / _WNS_RE / EcoAction / ALLOWED_ACTIONS / _FAMILY_RE /
_cell_strength)。nangate45 Pdk 精确复现 backend._preamble 口径, 行为零变化。

legal_mode:
  - "strength"  nangate45: 同族 drive-strength 双向 sizing(X1↔X2↔X4, 去自身)
  - "vt_swap"   asap7:     锁 func+strength, 换 VT 后缀 R↔L↔SL(同 footprint drop-in)
"""
from __future__ import annotations

import functools
import gzip
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from microsurgeon_flow import backend_eco_oneshot as _be

ORFS = _be.ORFS
# cell 名可带引号(sky130 `cell ("name")`)或不带(gf180/nangate45 `cell(name)`).
_CELL_LIB_RE = re.compile(r'^\s*cell\s*\(\s*"?([^)"]+?)"?\s*\)')
# asap7 cell 名: <func><xN>_ASAP7_<track>_<VT>, VT ∈ {R,L,SL}. VT 是末尾后缀。
_ASAP7_VT_RE = re.compile(r"^(?P<base>.+)_(?P<vt>R|L|SL)$")
_ASAP7_VT_RANK = {"R": 0, "L": 1, "SL": 2}   # 速度: SLVT 最快(低Vt高leakage)


@dataclass(frozen=True)
class Pdk:
    name: str
    tech_lef: Path
    cell_lefs: tuple[Path, ...]
    libs: tuple[Path, ...]
    setrc: Path
    legal_mode: str   # "strength" | "vt_swap"
    db_first: bool = False
    # db_first=True(asap7): read_db 先, 再补 cell LEF(额外 VT master), 再 read_liberty.
    #   asap7 odb 单 VT(RVT)建, L/SL master 不在 odb; 若先 read_lef 再 read_db,
    #   read_db 重建 db block 会冲掉这些 master → swapMaster 时 dbToSta=null → segfault.
    #   故 L/SL 必须在 read_db 之后补读 + read_liberty 链接(实测验证).
    # db_first=False(nangate45): 复现 backend._preamble 原序(odb 已嵌全库, 行为零变化).


# ── nangate45: 精确复现 backend_eco_oneshot._preamble 口径(行为零变化) ──────────
NANGATE45 = Pdk(
    name="nangate45",
    tech_lef=_be.TECH_LEF,
    cell_lefs=(_be.MACRO_LEF,),
    libs=(_be.LIB,),
    setrc=_be.SETRC,
    legal_mode="strength",
)

# ── asap7: tech + 三 VT cell LEF + DFF LEF; 全 TT-corner NLDM liberty(含 R/L/SL) ──
_A7 = ORFS / "platforms/asap7"


def _asap7_cell_lefs() -> tuple[Path, ...]:
    d = _A7 / "lef"
    lefs = sorted(d.glob("asap7sc7p5t_28_*_1x_*.lef")) + sorted(d.glob("asap7sc7p5t_DF*.lef"))
    return tuple(lefs)


def _asap7_tt_libs() -> tuple[Path, ...]:
    d = _A7 / "lib/NLDM"
    return tuple(sorted(d.glob("asap7sc7p5t_*_TT_nldm_*.lib*")))


ASAP7 = Pdk(
    name="asap7",
    tech_lef=_A7 / "lef/asap7_tech_1x_201209.lef",
    cell_lefs=_asap7_cell_lefs(),
    libs=_asap7_tt_libs(),
    setrc=_A7 / "setRC.tcl",
    legal_mode="vt_swap",
    db_first=True,
)

# ── 单 VT 成熟节点(strength sizing): sky130hd(130nm低成本) / gf180(180nm稳健) ──
_SKY = ORFS / "platforms/sky130hd"
SKY130HD = Pdk(
    name="sky130hd",
    tech_lef=_SKY / "lef/sky130_fd_sc_hd.tlef",
    cell_lefs=(_SKY / "lef/sky130_fd_sc_hd_merged.lef",),
    libs=(_SKY / "lib/sky130_fd_sc_hd__tt_025C_1v80.lib",),
    setrc=_SKY / "setRC.tcl",
    legal_mode="strength",
)

_GF = ORFS / "platforms/gf180"
GF180 = Pdk(
    name="gf180",
    tech_lef=_GF / "lef/gf180mcu_2LM_1TM_30K_7t_tech.lef",
    cell_lefs=(_GF / "lef/gf180mcu_2LM_1TM_30K_7t_sc.lef",),
    libs=(_GF / "lib/gf180mcu_fd_sc_mcu7t5v0__tt_025C_1v80.lib.gz",),
    setrc=_GF / "setRC.tcl",
    legal_mode="strength",
)

_PDKS = {"nangate45": NANGATE45, "asap7": ASAP7, "sky130hd": SKY130HD, "gf180": GF180}


def get_pdk(name: str) -> Pdk:
    if name not in _PDKS:
        raise ValueError(f"unknown pdk {name!r}; known={sorted(_PDKS)}")
    return _PDKS[name]


# ── PDK 参数化 eval 口径(复用 backend._run_openroad/_WNS_RE) ───────────────────
def preamble(pdk: Pdk, odb, sdc) -> str:
    if pdk.db_first:
        # asap7: read_db 先(带 odb 内嵌 tech+RVT master), 再补 cell LEF(加 L/SL master),
        # 再 read_liberty 链接全 VT → replace_cell 跨 VT 不崩(实测). 不另读 tech(odb 已含).
        lines = [f"read_db {odb}"]
        lines += [f"read_lef {lef}" for lef in pdk.cell_lefs]
        lines += [f"read_liberty {lib}" for lib in pdk.libs]
        lines += [f"read_sdc {sdc}", f"source {pdk.setrc}",
                  "estimate_parasitics -placement"]
    else:
        # nangate45: 复现 backend._preamble 原序(LEF→liberty→read_db), 行为零变化.
        lines = [f"read_lef {pdk.tech_lef}"]
        lines += [f"read_lef {lef}" for lef in pdk.cell_lefs]
        lines += [f"read_liberty {lib}" for lib in pdk.libs]
        lines += [f"read_db {odb}", f"read_sdc {sdc}", f"source {pdk.setrc}",
                  "estimate_parasitics -placement"]
    return "\n".join(lines) + "\n"


# trajectory eval 的 openroad 超时(覆盖 backend 默认 120s): asap7 读 29 库较重, 且共享机
# 负载波动时单 eval 偶尔变慢, 给 300s 余量防被误杀崩跑(单 eval 实测 ~16s, 余量充足).
_OR_TIMEOUT = 300


def eval_wns(pdk: Pdk, odb, sdc):
    tcl = preamble(pdk, odb, sdc) + 'puts "WNS_EVAL: [sta::worst_slack -max]"\nexit\n'
    out = _be._run_openroad(tcl, timeout=_OR_TIMEOUT)
    m = _be._WNS_RE.search(out)
    return float(m.group(1)) if m else None


def report_path(pdk: Pdk, odb, sdc) -> str:
    # -format full 不带 -fields(2列, 匹配刀1 parser; 同 trajectory nangate45 口径)
    tcl = preamble(pdk, odb, sdc) + "report_checks -path_delay max -format full\nexit\n"
    return _be._run_openroad(tcl, timeout=_OR_TIMEOUT)


def apply_and_eval(pdk: Pdk, base_odb, sdc, actions):
    """PDK 参数化 replace_cell 施加 + 重评估。镜像 backend.apply_and_eval 语义:
    接受返 (cand_odb, wns); 未施加/未生成返 (base_odb, wns|None)。"""
    work = Path(tempfile.mkdtemp(prefix="traj_apply_"))
    cand_odb = work / "cand.odb"
    legal = [a for a in actions if a.action_type in _be.ALLOWED_ACTIONS]
    cmds = []
    for a in legal:
        if a.action_type == "size_cell":
            nm = a.params.get("new_master")
            if a.target_inst and nm:
                cmds.append(f"replace_cell {{{a.target_inst}}} {nm}")
        elif a.action_type == "resize_chain":
            insts = a.target_insts or a.params.get("chain_insts", []) or []
            masters = a.params.get("target_masters", []) or []
            for inst, nm in zip(insts, masters):
                if inst and nm:
                    cmds.append(f"replace_cell {{{inst}}} {nm}")
    if not cmds:
        shutil.rmtree(work, ignore_errors=True)
        return base_odb, None
    tcl = (preamble(pdk, base_odb, sdc) + "\n".join(cmds) + "\n"
           + "estimate_parasitics -placement\n" + f"write_db {cand_odb}\n"
           + 'puts "WNS_EVAL: [sta::worst_slack -max]"\nexit\n')
    out = _be._run_openroad(tcl, timeout=_OR_TIMEOUT)
    m = _be._WNS_RE.search(out)
    wns = float(m.group(1)) if m else None
    if not cand_odb.exists():
        shutil.rmtree(work, ignore_errors=True)
        return base_odb, wns
    return cand_odb, wns


# ── lib cell 全集(legal 构造用; asap7 多 lib 读一次缓存) ───────────────────────
@functools.lru_cache(maxsize=None)
def lib_cells(pdk: Pdk) -> frozenset[str]:
    cells: set[str] = set()
    for lib in pdk.libs:
        opener = gzip.open if str(lib).endswith(".gz") else open
        with opener(lib, "rt") as f:
            for line in f:
                m = _CELL_LIB_RE.match(line)
                if m:
                    cells.add(m.group(1).strip())
    return frozenset(cells)


# 泛化 strength 解析(精确 family=含分隔符 _X? + 尾号 strength): 三 PDK 命名通用.
#   nangate45 AND2_X1→(AND2_X, 1); sky130 ..._hd__and2_1→(..._hd__and2_, 1); gf180 同理.
_GEN_FAMILY_RE = re.compile(r"^(.+?_X?)(\d+)$")


def _gen_family(cell: str):
    m = _GEN_FAMILY_RE.match(cell)
    return m.group(1) if m else None


def _gen_strength(cell: str):
    m = _GEN_FAMILY_RE.match(cell)
    return int(m.group(2)) if m else None


# ── legal 构造(双向) ─────────────────────────────────────────────────────────
def _strength_bidirectional_legal(inst_master_map, cells, *, family_fn=None, strength_fn=None):
    """同族全 strength 去自身(up+down). nangate45 用 _be 解析, sky130/gf180 用泛化解析."""
    family_fn = family_fn or (lambda c: (lambda m: m.group(1) if m else None)(_be._FAMILY_RE.match(c)))
    strength_fn = strength_fn or _be._cell_strength
    scoped: dict[str, list[str]] = {}
    for inst, cur in inst_master_map.items():
        prefix = family_fn(cur)
        s = strength_fn(cur)
        if prefix is None or s is None:
            scoped[inst] = []
            continue
        scoped[inst] = sorted(
            [c for c in cells if c.startswith(prefix)
             and strength_fn(c) is not None and strength_fn(c) != s],
            key=strength_fn)
    return scoped


def _vt_swap_legal(inst_master_map, cells):
    """asap7: 锁 base(func+strength+track), 换 VT 后缀 R/L/SL 去自身。"""
    scoped: dict[str, list[str]] = {}
    for inst, cur in inst_master_map.items():
        m = _ASAP7_VT_RE.match(cur)
        if not m:
            scoped[inst] = []
            continue
        base = m.group("base")
        scoped[inst] = sorted(
            cand for vt in ("R", "L", "SL")
            for cand in (f"{base}_{vt}",)
            if cand != cur and cand in cells)
    return scoped


def legal_candidates(pdk: Pdk, inst_master_map, cells):
    if pdk.legal_mode == "vt_swap":
        return _vt_swap_legal(inst_master_map, cells)
    # nangate45 用 _be 解析(_X 专属); sky130/gf180 用泛化解析(__func_<N>).
    if pdk.name == "nangate45":
        return _strength_bidirectional_legal(inst_master_map, cells)
    return _strength_bidirectional_legal(inst_master_map, cells,
                                         family_fn=_gen_family, strength_fn=_gen_strength)


def action_class(pdk: Pdk, cur_master: str, new_master: str) -> str:
    """粗粒度动作类(蒸馏 action_class_sequence 用, 不带 cell 类型)。"""
    if pdk.legal_mode == "vt_swap":
        cm = _ASAP7_VT_RE.match(cur_master)
        nm = _ASAP7_VT_RE.match(new_master)
        if cm and nm:
            cr = _ASAP7_VT_RANK.get(cm.group("vt"), -1)
            nr = _ASAP7_VT_RANK.get(nm.group("vt"), -1)
            return "vt_faster" if nr > cr else "vt_slower"
        return "vt_swap"
    cs, ns = _be._cell_strength(cur_master), _be._cell_strength(new_master)
    return "upsize" if (cs is not None and ns is not None and ns > cs) else "downsize"
