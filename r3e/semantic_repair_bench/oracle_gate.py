"""功能判据闸: differential testing (correctness gate).

candidate RTL 功能正确 ⟺ 在同一 testbench + 同一仿真器(iverilog) 下, candidate 的输出
逐周期逐位与 golden RTL 一致. 用 iverilog 自生成 golden 输出作 oracle, 消除 VCS/iverilog
仿真器边界 race 差异(直接用仓库 VCS-oracle.txt 会把 golden 误判 FAIL).

列对比逻辑复用 RTL-Repair benchmarks/run.py 的 check_against_oracle: 逐列比, oracle 为
'x'(don't-care) 的位忽略, 首个 mismatch 即 FAIL.

这是蓝方在功能 bug 域的新 gate(替代前端编译门): "编译能过" → "testbench 输出对".
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


def _parse_item(s: str) -> str:
    s = s.strip()
    if len(s) > 1 and s[0] == '"' and s[-1] == '"':
        return s[1:-1].strip()
    return s


def _parse_line(line: str) -> list[str]:
    return [_parse_item(n) for n in line.split(",")]


@dataclass
class SimOutcome:
    ok: bool
    stage: str            # golden_sim | cand_sim | compare
    mismatch: str = ""    # 首个不一致(信号@周期: actual!=expected) — 给蓝方修复作证据
    structured: str = ""  # 结构化故障诊断(bus 重组+临界对照+偏移模式) — 给 generation 的高质量证据
    golden_lines: int = 0
    cand_lines: int = 0
    err: str = ""
    detail: dict = field(default_factory=dict)


_BUS_RE = re.compile(r"^(.*?)\[(\d+)\]$")


def _bus_layout(header: list[str]) -> list[tuple[str, list[int]]]:
    """把 bit-split 列(name[3],name[2],..)重组成 bus, MSB→LSB 排列. 返回 [(base, [col...])]."""
    groups: dict[str, list[tuple[int, int]]] = {}
    order: list[str] = []
    for col, name in enumerate(header):
        m = _BUS_RE.match(name)
        base = m.group(1) if m else name
        bit = int(m.group(2)) if m else 0
        if base not in groups:
            groups[base] = []
            order.append(base)
        groups[base].append((bit, col))
    for b in groups:
        groups[b].sort(key=lambda t: -t[0])
    return [(b, [c for _, c in groups[b]]) for b in order]


def _bus_val(row: list[str], cols: list[int]) -> tuple[str, int | None]:
    """bus 二进制串(MSB→LSB) + 十进制(含 x 则 None)."""
    bits = [row[c].lower() if c < len(row) else "?" for c in cols]
    bstr = "".join(bits)
    dec = int(bstr, 2) if bits and all(x in "01" for x in bits) else None
    return bstr, dec


def _bus_diverges(grow: list[str], crow: list[str], cols: list[int]) -> bool:
    """golden 为 'x' 的位忽略(don't-care), 任一非 x 位不同即 bus 发散 — 与 _compare 同语义."""
    for c in cols:
        ge = grow[c].lower() if c < len(grow) else "?"
        ce = crow[c].lower() if c < len(crow) else "?"
        if ge != "x" and ge != ce:
            return True
    return False


def _classify_pattern(deltas: list[int], gseq: list[int], cseq: list[int]) -> str:
    """发散窗口内 实得−应为 序列 → 偏移模式诊断(off-by-one / 翻倍 / 周期错位 / 累积)."""
    if not deltas:
        return "无数值偏移(疑 x/don't-care 或非数值信号)"
    if len(set(deltas)) == 1:
        d = deltas[0]
        return f"恒定 {d:+d} → 疑 off-by-one/边界(< vs <=, 初值或常量 ±1)"
    # 每周期步进对比(counter 类): golden 步进 vs buggy 步进
    if len(gseq) >= 2 and len(cseq) >= 2:
        gst = [gseq[i + 1] - gseq[i] for i in range(len(gseq) - 1)]
        cst = [cseq[i + 1] - cseq[i] for i in range(len(cseq) - 1)]
        if len(set(gst)) == 1 and len(set(cst)) == 1 and gst[0] != cst[0]:
            return (f"每周期步进: 实得 {cst[0]:+d}/cycle vs 应为 {gst[0]:+d}/cycle "
                    f"→ 疑 增量步长错(如 +2 应 +1 / << 多移一位)")
    # 周期错位: cand[i] 命中 golden[i±1]
    if len(gseq) >= 2 and all(cseq[i] == gseq[i + 1] for i in range(len(cseq) - 1)):
        return "实得超前 golden 一个周期 → 疑 状态/时序互换(当前态↔下一态, 阻塞↔非阻塞)"
    if len(gseq) >= 2 and all(cseq[i + 1] == gseq[i] for i in range(len(cseq) - 1)):
        return "实得滞后 golden 一个周期 → 疑 多打一拍/寄存器多级"
    return f"变化偏移 {deltas[:6]} → 多点或累积偏移(非单点常量错)"


def extract_structured_evidence(golden_txt: str, cand_txt: str, max_signals: int = 2,
                                window: int = 8) -> str:
    """把逐位散乱 mismatch 重组成结构化诊断: bus 重组 + 首发散 + 临界对照 + 偏移模式.

    纯证据提取, 不参与 ok 判定(judge 的 ok 仍由 _compare 决定). 供 generation 用高质量证据.
    后续 prompt 侧会与 raw recurrence 组合成 hybrid evidence, 避免结构化摘要吞掉连续失败轨迹.
    """
    g = [ln for ln in golden_txt.splitlines() if ln.strip()]
    c = [ln for ln in cand_txt.splitlines() if ln.strip()]
    if len(g) < 2 or len(c) < 2:
        return ""
    gh = _parse_line(g[0])
    has_time = gh and gh[0].lower() == "time"
    grows = [_parse_line(ln) for ln in g[1:]]
    crows = [_parse_line(ln) for ln in c[1:]]
    # 去掉 time 列后做 bus 重组(列下标随之 -1)
    header = gh[1:] if has_time else gh
    if has_time:
        gtimes = [r[0] for r in grows]
        grows = [r[1:] for r in grows]
        crows = [r[1:] for r in crows]
    else:
        gtimes = [str(i) for i in range(len(grows))]
    buses = _bus_layout(header)

    # 每个 bus 找首发散周期
    found: list[tuple[int, str, list[int]]] = []  # (first_cycle, base, cols)
    for base, cols in buses:
        for i in range(min(len(grows), len(crows))):
            if _bus_diverges(grows[i], crows[i], cols):
                found.append((i, base, cols))
                break
    if not found:
        return ""
    found.sort(key=lambda t: t[0])
    primary = found[0]

    lines = ["[结构化故障诊断] golden=正确电路 oracle, 以下为 candidate 的功能偏离:"]
    suspects = [b for _, b, _ in found[:max_signals]]
    i0, base, cols = primary
    gbin, gdec = _bus_val(grows[i0], cols)
    cbin, cdec = _bus_val(crows[i0], cols)
    dtxt = f", 差 {cdec - gdec:+d}" if (gdec is not None and cdec is not None) else ""
    gv = f"{gbin}({gdec})" if gdec is not None else gbin
    cv = f"{cbin}({cdec})" if cdec is not None else cbin
    lines.append(f"• 首个发散 @cycle{i0}(time {gtimes[i0]}): 信号 `{base}` "
                 f"实得 {cv} ≠ 应为 {gv}{dtxt}")
    # 临界对照: 上一周期(最后正确)
    if i0 >= 1:
        pgb, pgd = _bus_val(grows[i0 - 1], cols)
        pcb, pcd = _bus_val(crows[i0 - 1], cols)
        pv = f"{pcb}({pcd})" if pcd is not None else pcb
        pgv = f"{pgb}({pgd})" if pgd is not None else pgb
        mark = "✓ 一致" if not _bus_diverges(grows[i0 - 1], crows[i0 - 1], cols) else "✗"
        lines.append(f"• 临界对照 `{base}`: cycle{i0 - 1}(最后正确) 实得 {pv} / 应为 {pgv} {mark}"
                     f" → cycle{i0}(首错) 实得 {cv} / 应为 {gv} ✗")
    # 偏移模式: 发散窗口内 实得−应为 序列
    deltas, gseq, cseq = [], [], []
    for i in range(i0, min(i0 + window, len(grows), len(crows))):
        _, gd = _bus_val(grows[i], cols)
        _, cd = _bus_val(crows[i], cols)
        if gd is not None and cd is not None:
            deltas.append(cd - gd)
            gseq.append(gd)
            cseq.append(cd)
    lines.append(f"• 偏移模式 `{base}`(发散窗口内): {_classify_pattern(deltas, gseq, cseq)}")
    if len(suspects) > 1:
        lines.append(f"• 受累信号: {suspects[0]}(主), " + ", ".join(suspects[1:]))
    return "\n".join(lines)


def _set_pdeathsig():
    """Linux: 子进程在父进程(线程)死时自动收 SIGKILL, 防孤儿 vvp 死循环遗留(会话中断时).

    正常 timeout 由 subprocess.run(timeout=) 处理; 此 helper 专防"父被外部 SIGKILL→vvp 变孤儿".
    """
    try:
        import ctypes
        import signal as _sig
        ctypes.CDLL("libc.so.6").prctl(1, _sig.SIGKILL)  # PR_SET_PDEATHSIG=1
    except Exception:  # noqa: BLE001
        pass


def _simulate(dut_files, tb_files, output_name, work_dir, timeout) -> tuple[str | None, str]:
    """iverilog 编译 dut+tb → vvp 跑 → 读相对路径 output_name. 返回 (内容, err)."""
    work_dir = Path(work_dir)
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    names = []
    for f in list(dut_files) + list(tb_files):
        f = Path(f)
        dst = work_dir / f.name
        shutil.copy2(f, dst)
        names.append(f.name)

    try:
        cp = subprocess.run(["iverilog", "-g2012", "-o", "a.out"] + names,
                            cwd=work_dir, capture_output=True, text=True, timeout=timeout,
                            preexec_fn=_set_pdeathsig)
    except subprocess.TimeoutExpired:
        return None, "compile_timeout"
    if cp.returncode != 0:
        return None, f"compile_err: {(cp.stderr or cp.stdout).strip()[:300]}"

    try:
        subprocess.run(["vvp", "a.out"], cwd=work_dir, capture_output=True,
                       text=True, timeout=timeout, preexec_fn=_set_pdeathsig)
    except subprocess.TimeoutExpired:
        return None, "sim_timeout"

    outp = work_dir / output_name
    if not outp.exists():
        return None, f"no_output({output_name})"
    return outp.read_text(errors="ignore"), ""


def _compare(golden_txt: str, cand_txt: str, max_evidence: int = 1) -> tuple[bool, str]:
    """逐列对比, golden 为 'x' 的位忽略. 返回 (一致, mismatch 证据).

    max_evidence=1: 仅首个 mismatch(baseline). >1: 收集前 N 个 mismatch(覆盖不同信号@周期),
    给下游 LLM 更全的故障画像(针对 off-by-one/索引偏移等多处偏移毒).
    """
    g = [ln for ln in golden_txt.splitlines() if ln.strip()]
    c = [ln for ln in cand_txt.splitlines() if ln.strip()]
    if not g:
        return False, "golden_empty"
    gh, ch = _parse_line(g[0]), _parse_line(c[0]) if c else []
    if gh != ch:
        return False, f"header_diff: {gh} != {ch}"
    has_time = gh[0].lower() == "time"
    hdr = gh[1:] if has_time else gh

    evid = []
    sigs = set()
    for ii, (ge, ce) in enumerate(zip(g[1:], c[1:])):
        ge, ce = _parse_line(ge), _parse_line(ce)
        if has_time:
            ge, ce = ge[1:], ce[1:]
        for ee, aa, nn in zip(ge, ce, hdr):
            ee, aa = ee.lower(), aa.lower()
            if ee != "x" and ee != aa:
                # 优先覆盖不同信号(off-by-one 常累及多位/多信号)
                if nn not in sigs or len(evid) < max_evidence:
                    evid.append(f"{nn}@cycle{ii}: got {aa}, expected {ee}")
                    sigs.add(nn)
                if len(evid) >= max_evidence:
                    break
        if len(evid) >= max_evidence:
            break

    if evid:
        return False, "; ".join(evid)
    if len(c) - 1 < len(g) - 1:
        return False, f"output_truncated: {len(c) - 1} < {len(g) - 1} cycles"
    return True, ""


def judge(case: dict, candidate_rtl, work_dir, timeout: float | None = None,
          evidence_k: int = 1) -> SimOutcome:
    """判 candidate_rtl 在 case 的 testbench 下是否功能等价于 golden.

    case: 含 golden_rtl, deps, tb_sources, tb_output, top_module, sim_timeout.
    candidate_rtl: 待判 RTL(buggy 或 patched). golden 先跑作自洽 oracle.
    evidence_k: 收集多少个 mismatch 作证据(1=baseline, >1=丰富证据攻 off-by-one).
    """
    work_dir = Path(work_dir)
    deps = [Path(p) for p in case.get("deps", [])]
    tb = [Path(p) for p in case["tb_sources"]]
    out_name = case["tb_output"]
    to = timeout if timeout is not None else float(case.get("sim_timeout", 10.0))
    # tb 内 #delay 单位与 timeout 不同源, 给足余量防误杀.
    sim_to = max(20.0, to * 3)

    g_txt, g_err = _simulate([Path(case["golden_rtl"])] + deps, tb, out_name,
                             work_dir / "golden", sim_to)
    if g_txt is None:
        return SimOutcome(ok=False, stage="golden_sim", err=g_err)  # case 无效(golden 跑不了)

    c_txt, c_err = _simulate([Path(candidate_rtl)] + deps, tb, out_name,
                             work_dir / "cand", sim_to)
    if c_txt is None:
        # 编译错/无输出 = 功能 FAIL; err 作证据(语法型 bug 也落这).
        return SimOutcome(ok=False, stage="cand_sim", err=c_err,
                          golden_lines=len(g_txt.splitlines()))

    ok, msg = _compare(g_txt, c_txt, max_evidence=evidence_k)  # ← ok 判定唯一来源, 不动
    structured = "" if ok else extract_structured_evidence(g_txt, c_txt)
    # Generation side needs the repeated raw failure trajectory as well as the
    # structured diagnosis. This is diagnostic evidence only; ok still comes
    # exclusively from the _compare call above.
    raw_recurrence = "" if ok else _compare(g_txt, c_txt, max_evidence=max(1, evidence_k))[1]
    return SimOutcome(ok=ok, stage="compare", mismatch=msg, structured=structured,
                      golden_lines=len(g_txt.splitlines()),
                      cand_lines=len(c_txt.splitlines()),
                      detail={"raw_recurrence": raw_recurrence})
