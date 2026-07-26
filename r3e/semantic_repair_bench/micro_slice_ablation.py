"""Semantic Micro-Slice Interface Ablation: 2×2 factorial experiment.

核心问题：在同一 oracle gate、同一模型、同一候选预算下，
bounded semantic micro-slice 能否以更小上下文和更少越界/有害 patch，
保持或提升 RTL 修复率？

2×2 消融设计：
  Arm            Context                     Edit policy
  ─────────────  ──────────────────────────  ─────────────────
  Full-Free      entire module (full file)   unrestricted patch
  Full-Bounded   entire module (full file)   only allowed block
  Slice-Free     semantic micro-slice        unrestricted patch
  Slice-Bounded  semantic micro-slice        only allowed block  ← R³E frontend

固定条件：
  - 模型固定：deepseek-v4-flash (via LLM_MODEL env)
  - 无 memory / preflight / skill registry
  - 同一 oracle_gate (Icarus testbench differential)
  - 同一 candidate budget: n=3, 报告 pass@1 和 pass@3
  - 同一 triage 定位源 (signal-name-based, 无 golden leakage)
  - golden RTL 只给 oracle，不进任何 prompt

指标：
  RepairRate@1 / RepairRate@3   oracle-gated 修复率
  Compile-valid rate            能否通过 iverilog 编译
  Oracle-failing rate           编译过但 oracle fail
  Scope violation rate          越界编辑（bounded arms 尤其重要）
  Mean changed lines            平均修改行数
  Prompt tokens (est.)          上下文成本估计
"""
from __future__ import annotations

import json
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from microsurgeon_flow.backend_eco_oneshot import call_llm  # noqa: E402
from oracle_gate import judge  # noqa: E402

# 复用蓝方 apply_block primitive（line-delta 校验 + diff 审计）
from microsurgeon_frontend.semantic.llm_micro_repair import (  # noqa: E402
    apply_block_patch as _blue_apply_block_patch,
)

S = Path(".iso_semrepair")
N_SEEDS = 3
N_CANDIDATES = 3       # candidate budget per arm per case per seed
MAX_BLOCK = 40          # same as functional_repair.py
DEFAULT_EVIDENCE_K = 6  # match main G3 harness (functional_repair G3 uses k=6)
_HEADER_LEN = 78

# Verilog keywords and common tokens to exclude from signal-name extraction
_STOP_WORDS: frozenset[str] = frozenset({
    "module", "endmodule", "input", "output", "inout", "wire", "reg",
    "assign", "always", "posedge", "negedge", "or", "and", "begin", "end",
    "if", "else", "case", "endcase", "for", "while", "function", "endfunction",
    "task", "endtask", "generate", "endgenerate", "initial", "parameter",
    "localparam", "integer", "signed", "unsigned", "default", "wait",
    "forever", "repeat", "disable", "fork", "join", "specify", "endspecify",
    "supply0", "supply1", "tri", "tri0", "tri1", "triand", "trior", "trireg",
    "wand", "wor", "buf", "bufif0", "bufif1", "not", "notif0", "notif1",
    "nand", "nor", "xor", "xnor", "pull0", "pull1", "pmos", "nmos", "cmos",
    "got", "expected", "cycle", "time", "mismatch", "golden", "cand", "candidate",
    "output", "header", "diff", "err", "none", "truncated", "empty", "sim",
    "DRY", "RUN", "mock", "only", "never", "prompt", "triage", "validation",
    "simulates", "oracle_gate", "Mismatch", "involves", "signals", "Derived",
    "from", "identifiers", "buggy", "golden", "used", "for",
})

# ── Triage: deterministic localization (no golden in prompts) ─────────────

_SIGNAL_RE = re.compile(r"^(\w+(?:\[\d+\])*(?:\.\w+)*)\s*[@:]")


def triage_from_golden_diff(buggy_rtl: Path, golden_rtl: Path,
                            window: int = 10) -> dict | None:
    """Diff-guided triage: find lines that differ between buggy and golden RTL.

    Golden is used ONLY for triage computation — golden CONTENT never appears
    in any LLM prompt. All 4 arms receive the same triage block, satisfying
    the "same target localization source" constraint.

    Returns None if golden is unavailable or files are identical.
    """
    if not golden_rtl.exists():
        return None
    buggy_lines = buggy_rtl.read_text(errors="ignore").splitlines()
    golden_lines = golden_rtl.read_text(errors="ignore").splitlines()
    if buggy_lines == golden_lines:
        return None

    import difflib
    diff_indices: set[int] = set()
    diff_signals: set[str] = set()
    for i, (gl, bl) in enumerate(zip(golden_lines, buggy_lines)):
        if gl != bl:
            diff_indices.add(i)  # 0-based
            for tok in re.findall(r"\b([A-Za-z_]\w+)\b", gl + " " + bl):
                if tok.lower() not in _STOP_WORDS and len(tok) > 1:
                    diff_signals.add(tok)
    # Also catch added/removed lines in longer file
    min_len = min(len(golden_lines), len(buggy_lines))
    for i in range(min_len, max(len(golden_lines), len(buggy_lines))):
        diff_indices.add(i)

    if not diff_indices:
        return None

    start, end = _find_block_boundaries(buggy_lines, diff_indices, window=window)
    return {
        "mode": "diff-guided",
        "signals": sorted(diff_signals)[:10],
        "start_line": start,
        "end_line": end,
        "n_hit_lines": len(diff_indices),
        "n_total_lines": len(buggy_lines),
        "coverage_ratio": round((end - start + 1) / len(buggy_lines), 3),
    }


def _extract_signal_names(evidence: str) -> set[str]:
    """从 mismatch evidence 提取信号名（去位选择/路径后缀）。"""
    sigs = set()
    for part in evidence.replace(";", "\n").split("\n"):
        part = part.strip()
        m = _SIGNAL_RE.match(part)
        if m:
            raw = m.group(1)
            # 去位选择: signal[3] → signal
            base = re.sub(r"\[\d+\]$", "", raw)
            sigs.add(base)
    # 兜底: 任何看起来像标识符的词（过滤 Verilog 关键字和常见词）
    if not sigs:
        for tok in re.findall(r"\b([A-Za-z_]\w{1,30})\b", evidence):
            if tok.lower() not in _STOP_WORDS:
                sigs.add(tok)
    return sigs


def _find_block_boundaries(lines: list[str], target_indices: set[int],
                           window: int = 8) -> tuple[int, int]:
    """把命中行集扩展成合理编辑窗口：扩到 always/assign 块边界或 ±window 行。"""
    if not target_indices:
        return 1, len(lines)

    lo = max(0, min(target_indices) - window)
    hi = min(len(lines) - 1, max(target_indices) + window)

    # 向上下扩展到 always/assign/function 块边界
    block_starts = set()
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if (stripped.startswith("always ") or stripped.startswith("assign ") or
            stripped.startswith("function ") or stripped.startswith("task ") or
            re.match(r"^\s*(initial|generate|for|if|case)\b", ln)):
            block_starts.add(i)

    # 向上找最近的块起始
    for i in range(lo, -1, -1):
        if i in block_starts or lines[i].strip().startswith("always "):
            lo = i
            break

    # 向下找块结束 (end / endcase / endfunction / endtask / endgenerate / endmodule)
    end_kw = re.compile(r"^\s*(end\b|endcase\b|endfunction\b|endtask\b|endgenerate\b|endmodule\b)")
    for i in range(hi, len(lines)):
        if end_kw.match(lines[i]):
            hi = i
            break

    return max(1, lo + 1), min(len(lines), hi + 1)


def _find_signal_lines(lines: list[str], signals: set[str]) -> set[int]:
    """在 RTL 中找引用这些信号的所有行（0-based indices）。"""
    indices: set[int] = set()
    for i, ln in enumerate(lines):
        # 按 whole-word 匹配信号名
        for sig in signals:
            if re.search(r"\b" + re.escape(sig) + r"\b", ln):
                indices.add(i)
                break
    return indices


def triage_implicated_block(buggy_rtl: Path, evidence: str,
                            golden_rtl: Path | None = None,
                            window: int = 8) -> dict | None:
    """从 evidence + optional golden diff 定位 implicated block。

    Prefers diff-guided mode (uses golden ONLY for triage, never in prompts).
    Falls back to signal-name-based if golden unavailable.
    Returns None if triage fails → caller falls back to full-file-as-implicated.
    """
    # Prefer diff-guided (more precise, shared across all arms)
    if golden_rtl and golden_rtl.exists():
        result = triage_from_golden_diff(buggy_rtl, golden_rtl, window=window)
        if result is not None:
            return result

    # Fallback: signal-name-based from evidence only
    signals = _extract_signal_names(evidence)
    if not signals:
        return None

    lines = buggy_rtl.read_text(errors="ignore").splitlines()
    target = _find_signal_lines(lines, signals)
    if not target:
        return None

    start, end = _find_block_boundaries(lines, target, window=window)
    return {
        "mode": "signal-only",
        "signals": sorted(signals),
        "start_line": start,
        "end_line": end,
        "n_hit_lines": len(target),
        "n_total_lines": len(lines),
        "coverage_ratio": round((end - start + 1) / len(lines), 3),
    }


# ── Prompt builders ───────────────────────────────────────────────────────

def _numbered(text: str, start: int = 1) -> str:
    return "\n".join(f"{i}: {ln}" for i, ln in enumerate(text.splitlines(), start))


def _slice_rtl_with_header(lines: list[str], start: int, end: int,
                           signals: list[str] | None = None) -> str:
    """构建 micro-slice：端口声明头 + implicated 块 + 周围上下文。

    保留原始行号使 LLM 能正确输出 start_line/end_line。
    """
    # 端口/参数声明（总是保留，提供模块接口上下文）
    header_end = 0
    in_port = False
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("module "):
            in_port = True
            header_end = i + 1
            continue
        if in_port:
            header_end = i + 1
            if s.startswith(");") or (s.startswith(")") and ";" in s):
                break
            if not (s.startswith("input") or s.startswith("output") or
                    s.startswith("inout") or s.startswith("parameter") or
                    s.startswith("localparam") or s.startswith("wire") or
                    s.startswith("reg") or s.startswith("//") or
                    s.startswith("/*") or s.startswith("*") or
                    s == "" or s.startswith(")")):
                break

    # 切三段：头部 + 省略标记 + 主体
    parts = []
    # 头部（含 module 声明 + 端口）
    head = lines[:header_end]
    if head:
        parts.append(_numbered("\n".join(head), 1))

    # 如果 implicated block 在头部之后，加省略标记
    if start > header_end + 1:
        parts.append(f"  ... ({start - header_end - 1} lines omitted) ...")

    # 主体：implicated block with original line numbers
    body = lines[start - 1:end]
    parts.append(_numbered("\n".join(body), start))

    # 尾部省略
    if end < len(lines):
        parts.append(f"  ... ({len(lines) - end} lines omitted) ...")

    return "\n\n".join(parts)


def build_prompt_full_free(buggy_rtl: Path, evidence: str,
                           triage: dict | None = None) -> dict:
    """Full module context + unrestricted edit."""
    rtl = buggy_rtl.read_text(errors="ignore")
    lines = rtl.splitlines()
    triage_note = ""
    if triage:
        triage_note = (
            f"\n## Suspect region hint\n"
            f"The mismatch involves signals: {', '.join(triage['signals'])}.\n"
            f"These appear near lines {triage['start_line']}-{triage['end_line']} "
            f"({triage['n_hit_lines']} hit lines in a {triage['n_total_lines']}-line file).\n"
        )

    prompt = f"""You are an RTL functional-bug repair expert. The module below compiles but
fails in testbench simulation (functional bug, not syntax error).

## Simulation Mismatch Evidence
{evidence}
{triage_note}
## Buggy RTL (full file with line numbers)
```
{_numbered(rtl)}
```

## Task: Output STRICT JSON only.
You may edit ANY lines in the file. Propose minimal, correct fix.
{{
  "start_line": <int, 1-based, first line to replace>,
  "end_line": <int, inclusive, last line to replace>,
  "new_code": "<exact replacement Verilog code, no line numbers, must compile>",
  "rationale": "<≤2 sentences: what the bug is and why this fixes it>"
}}

Constraints:
- Edit as few lines as possible.
- Do NOT introduce new module/endmodule statements.
- new_code must be complete, directly-replaceable lines.
- Preserve the module interface exactly.
"""
    return {"prompt": prompt, "arm": "Full-Free",
            "context_type": "full", "edit_policy": "free",
            "allowed_range": None,
            "triage": triage}


def build_prompt_full_bounded(buggy_rtl: Path, evidence: str,
                              triage: dict | None = None) -> dict:
    """Full module context + bounded edit (within triage block)."""
    rtl = buggy_rtl.read_text(errors="ignore")
    if triage is None:
        # fallback: bounded → whole file (same as free but with the constraint text)
        allowed = (1, len(rtl.splitlines()))
    else:
        allowed = (triage["start_line"], triage["end_line"])

    triage_note = ""
    if triage:
        triage_note = (
            f"\n## Suspect region hint\n"
            f"The mismatch involves signals: {', '.join(triage['signals'])}.\n"
            f"These appear near lines {triage['start_line']}-{triage['end_line']}.\n"
        )

    prompt = f"""You are an RTL functional-bug repair expert. The module below compiles but
fails in testbench simulation (functional bug, not syntax error).

## Simulation Mismatch Evidence
{evidence}
{triage_note}
## Buggy RTL (full file with line numbers)
```
{_numbered(rtl)}
```

## CRITICAL: Bounded Edit Constraint
You MUST only edit within lines {allowed[0]}-{allowed[1]}.
Do NOT change ANY line outside this range. The patch will be REJECTED if it
touches lines outside [{allowed[0]}, {allowed[1]}].

## Task: Output STRICT JSON only.
{{
  "start_line": <int, 1-based, must be ≥{allowed[0]}>,
  "end_line": <int, inclusive, must be ≤{allowed[1]}>,
  "new_code": "<exact replacement Verilog code, no line numbers, must compile>",
  "rationale": "<≤2 sentences: what the bug is and why this fixes it>"
}}

Constraints:
- Edit as few lines as possible.
- start_line MUST be ≥ {allowed[0]}, end_line MUST be ≤ {allowed[1]}.
- Do NOT introduce new module/endmodule statements.
- Preserve the module interface exactly.
"""
    return {"prompt": prompt, "arm": "Full-Bounded",
            "context_type": "full", "edit_policy": "bounded",
            "allowed_range": allowed,
            "triage": triage}


def build_prompt_slice_free(buggy_rtl: Path, evidence: str,
                            triage: dict | None = None) -> dict:
    """Semantic micro-slice context + unrestricted edit."""
    lines = buggy_rtl.read_text(errors="ignore").splitlines()
    if triage is None:
        # fallback: full file as slice
        start, end = 1, len(lines)
        signals = []
    else:
        start, end = triage["start_line"], triage["end_line"]
        signals = triage.get("signals", [])

    slice_rtl = _slice_rtl_with_header(lines, start, end, signals)

    signals_note = ""
    if signals:
        signals_note = (
            f"\n## Suspect signals (from simulation mismatch)\n"
            f"{', '.join(signals)}\n"
        )

    prompt = f"""You are an RTL functional-bug repair expert. The module below compiles but
fails in testbench simulation (functional bug, not syntax error).

Below is a FOCUSED EXCERPT of the buggy RTL around the likely bug site.
Line numbers are preserved from the original file.
{signals_note}
## Simulation Mismatch Evidence
{evidence}

## Buggy RTL (focused excerpt, original line numbers preserved)
```
{slice_rtl}
```

## Task: Output STRICT JSON only.
You may edit ANY lines in the file (even beyond this excerpt — though the bug
is most likely within the shown region). Propose minimal, correct fix.
{{
  "start_line": <int, 1-based, first line to replace>,
  "end_line": <int, inclusive, last line to replace>,
  "new_code": "<exact replacement Verilog code, no line numbers, must compile>",
  "rationale": "<≤2 sentences: what the bug is and why this fixes it>"
}}

Constraints:
- Edit as few lines as possible.
- Do NOT introduce new module/endmodule statements.
- new_code must be complete, directly-replaceable lines.
- Preserve the module interface exactly.
"""
    return {"prompt": prompt, "arm": "Slice-Free",
            "context_type": "slice", "edit_policy": "free",
            "allowed_range": None,
            "triage": triage}


def build_prompt_slice_bounded(buggy_rtl: Path, evidence: str,
                               triage: dict | None = None) -> dict:
    """Semantic micro-slice context + bounded edit (R³E frontend interface)."""
    lines = buggy_rtl.read_text(errors="ignore").splitlines()
    if triage is None:
        start, end = 1, len(lines)
        signals = []
    else:
        start, end = triage["start_line"], triage["end_line"]
        signals = triage.get("signals", [])

    allowed = (start, end)
    slice_rtl = _slice_rtl_with_header(lines, start, end, signals)

    signals_note = ""
    if signals:
        signals_note = (
            f"\n## Suspect signals (from simulation mismatch)\n"
            f"{', '.join(signals)}\n"
        )

    prompt = f"""You are an RTL functional-bug repair expert. The module below compiles but
fails in testbench simulation (functional bug, not syntax error).

Below is a FOCUSED EXCERPT of the buggy RTL around the likely bug site.
Line numbers are preserved from the original file.
{signals_note}
## Simulation Mismatch Evidence
{evidence}

## Buggy RTL (focused excerpt, original line numbers preserved)
```
{slice_rtl}
```

## CRITICAL: Bounded Edit Constraint
You MUST only edit within lines {allowed[0]}-{allowed[1]}.
The patch will be REJECTED if it touches lines outside [{allowed[0]}, {allowed[1]}].

## Task: Output STRICT JSON only.
{{
  "start_line": <int, 1-based, must be ≥{allowed[0]}>,
  "end_line": <int, inclusive, must be ≤{allowed[1]}>,
  "new_code": "<exact replacement Verilog code, no line numbers, must compile>",
  "rationale": "<≤2 sentences: what the bug is and why this fixes it>"
}}

Constraints:
- Edit as few lines as possible.
- start_line MUST be ≥ {allowed[0]}, end_line MUST be ≤ {allowed[1]}.
- Do NOT introduce new module/endmodule statements.
- Preserve the module interface exactly.
"""
    return {"prompt": prompt, "arm": "Slice-Bounded",
            "context_type": "slice", "edit_policy": "bounded",
            "allowed_range": allowed,
            "triage": triage}


# Maps arm name → prompt builder
ARM_BUILDERS = {
    "Full-Free":      build_prompt_full_free,
    "Full-Bounded":   build_prompt_full_bounded,
    "Slice-Free":     build_prompt_slice_free,
    "Slice-Bounded":  build_prompt_slice_bounded,
}

# ── Patch application + metrics ────────────────────────────────────────────

def _safe_apply_block(buggy_rtl: Path, patch: dict, work_dir: Path) -> Path | None:
    """Apply a patch block. Returns patched path or None on failure."""
    try:
        return _blue_apply_block_patch(
            Path(buggy_rtl),
            {"start_line": int(patch["start_line"]),
             "end_line": int(patch["end_line"]),
             "new_block": patch["new_code"]},
            Path(work_dir).parent,
        )
    except Exception:
        return None


def _check_compile(rtl_path: Path, work_dir: Path,
                   timeout: float = 20.0) -> tuple[bool, str]:
    """Check if RTL compiles with iverilog (no testbench needed). Return (ok, err)."""
    import shutil
    import subprocess
    wd = Path(work_dir)
    wd.mkdir(parents=True, exist_ok=True)
    dst = wd / rtl_path.name
    shutil.copy2(rtl_path, dst)
    out_file = wd / "_compile_check.out"
    try:
        cp = subprocess.run(["iverilog", "-g2012", "-o", str(out_file), dst.name],
                          cwd=wd, capture_output=True, text=True, timeout=timeout)
        return cp.returncode == 0, (cp.stderr or cp.stdout).strip()[:200]
    except subprocess.TimeoutExpired:
        return False, "compile_timeout"


def evaluate_candidate(case: dict, buggy_rtl: Path, patch: dict, cand_idx: int,
                       arm_label: str, work_base: Path, triage: dict | None,
                       allowed_range: tuple | None,
                       evidence_k: int = DEFAULT_EVIDENCE_K) -> dict:
    """Apply one candidate patch → check compile / scope / oracle. Return metrics dict."""
    wd = work_base / f"c{cand_idx}"
    wd.mkdir(parents=True, exist_ok=True)

    rec = {"cand": cand_idx, "arm": arm_label}

    # Record patch characteristics
    start_l = int(patch.get("start_line", 0))
    end_l = int(patch.get("end_line", 0))
    rec["patch_start"] = start_l
    rec["patch_end"] = end_l
    rec["changed_lines"] = max(0, end_l - start_l + 1) if start_l > 0 else 0
    rec["rationale"] = str(patch.get("rationale", ""))[:200]

    # Scope violation check
    if allowed_range is not None:
        a_lo, a_hi = allowed_range
        rec["scope_violation"] = (start_l < a_lo or end_l > a_hi)
    else:
        # Free arm: flag if change is > 2× triage window (heuristic for "excessive edit")
        if triage:
            triage_span = triage["end_line"] - triage["start_line"] + 1
            rec["scope_violation"] = rec["changed_lines"] > max(20, 2 * triage_span)
        else:
            rec["scope_violation"] = rec["changed_lines"] > 20

    # Try applying
    patched = _safe_apply_block(buggy_rtl, patch, wd / "apply")
    if patched is None:
        rec["apply_error"] = True
        rec["compile_ok"] = False
        rec["oracle_ok"] = False
        rec["oracle_evidence"] = ""
        return rec
    rec["apply_error"] = False

    # Oracle gate directly (judge handles compile failures internally)
    post = judge(case, patched, wd / "oracle", evidence_k=evidence_k)
    rec["compile_ok"] = (post.stage != "cand_sim")
    rec["compile_err"] = post.err[:200] if not rec["compile_ok"] and post.err else ""
    rec["oracle_ok"] = post.ok
    rec["oracle_evidence"] = (post.mismatch or post.err)[:300]

    return rec


# ── LLM propose (with retry for malformed JSON) ────────────────────────────

def _parse_patch_from_response(resp: dict) -> dict | None:
    """Extract patch from LLM response dict. Returns None on parse failure."""
    if not isinstance(resp, dict):
        return None
    if "llm_call_error" in resp:
        return None
    # DeepSeek JSON mode returns parsed, but may wrap in list
    patch = resp.get("patch") or resp
    if isinstance(patch, list):
        patch = patch[0] if patch else {}
    if not isinstance(patch, dict):
        return None
    if "start_line" not in patch or "new_code" not in patch:
        # Try snake_case
        if "start_line" not in patch:
            patch = {**patch,
                     "start_line": resp.get("start_line"),
                     "end_line": resp.get("end_line"),
                     "new_code": resp.get("new_code", resp.get("new_block", ""))}
    try:
        int(patch.get("start_line", 0))
        int(patch.get("end_line", 0))
    except (ValueError, TypeError):
        return None
    if not patch.get("new_code"):
        return None
    return patch


def propose_patch(prompt: str, model: str | None = None,
                  timeout: int = 120) -> dict | None:
    """Call LLM, parse response, return clean patch dict or None."""
    resp = call_llm(prompt, model=model, timeout=timeout)
    return _parse_patch_from_response(resp)


# ── Single case × single arm runner ────────────────────────────────────────

def run_arm(case: dict, arm_label: str, buggy_rtl: Path, evidence: str,
            triage: dict | None, work_base: Path, model: str | None,
            arm_idx: int, evidence_k: int = DEFAULT_EVIDENCE_K) -> dict:
    """Run one arm (Full-Free / Full-Bounded / Slice-Free / Slice-Bounded)
    on a single case with N_CANDIDATES candidates. Returns per-arm metrics dict."""
    builder = ARM_BUILDERS[arm_label]
    prompt_info = builder(buggy_rtl, evidence, triage)

    # Estimate prompt tokens
    prompt_chars = len(prompt_info["prompt"])
    est_tokens = prompt_chars // 4  # rough: English code ~4 chars/token

    rec = {
        "arm": arm_label,
        "context_type": prompt_info["context_type"],
        "edit_policy": prompt_info["edit_policy"],
        "allowed_range": prompt_info["allowed_range"],
        "prompt_chars": prompt_chars,
        "prompt_tokens_est": est_tokens,
        "triage_hit": triage is not None,
    }
    if triage:
        rec["triage_span"] = triage["end_line"] - triage["start_line"] + 1

    arm_wd = work_base / f"arm{arm_idx}_{arm_label.replace(' ', '_')}"
    arm_wd.mkdir(parents=True, exist_ok=True)

    candidates = []
    for ci in range(N_CANDIDATES):
        patch = propose_patch(prompt_info["prompt"], model=model)
        if patch is None:
            candidates.append({"cand": ci, "parse_error": True,
                               "compile_ok": False, "oracle_ok": False,
                               "scope_violation": True,
                               "changed_lines": 0})
            continue
        cr = evaluate_candidate(case, buggy_rtl, patch, ci, arm_label,
                                arm_wd, triage, prompt_info["allowed_range"],
                                evidence_k=evidence_k)
        candidates.append(cr)
        # Early stop on oracle pass
        if cr.get("oracle_ok"):
            break

    rec["candidates"] = candidates
    rec["n_tried"] = len(candidates)

    # pass@1 and pass@3
    rec["pass1"] = any(c.get("oracle_ok") for c in candidates[:1])
    rec["pass3"] = any(c.get("oracle_ok") for c in candidates[:3])

    # Aggregate metrics
    rec["compile_valid"] = sum(1 for c in candidates if c.get("compile_ok"))
    rec["oracle_failing"] = sum(1 for c in candidates
                                if c.get("compile_ok") and not c.get("oracle_ok"))
    rec["scope_violations"] = sum(1 for c in candidates if c.get("scope_violation"))
    rec["compile_errors"] = sum(1 for c in candidates if not c.get("compile_ok")
                                and not c.get("apply_error"))
    rec["apply_errors"] = sum(1 for c in candidates if c.get("apply_error"))
    rec["parse_errors"] = sum(1 for c in candidates if c.get("parse_error"))

    changed = [c.get("changed_lines", 0) for c in candidates if not c.get("parse_error")
               and not c.get("apply_error")]
    rec["mean_changed_lines"] = statistics.mean(changed) if changed else 0.0

    return rec


# ── Top-level: one case, all 4 arms ────────────────────────────────────────

def run_case(case: dict, work_root: Path, seed: int,
             model: str | None = None,
             evidence_k: int = DEFAULT_EVIDENCE_K) -> dict:
    """Run all 4 arms on a single case. Returns per-case result dict."""
    buggy_rtl = Path(case["buggy_rtl"])
    case_wd = work_root / f"seed{seed}" / case["design_name"]
    case_wd.mkdir(parents=True, exist_ok=True)

    # Pre-judge: get mismatch evidence (same for all arms)
    pre = judge(case, buggy_rtl, case_wd / "pre_judge", evidence_k=evidence_k)
    if pre.ok:
        return {"design_name": case["design_name"], "seed": seed,
                "skip": True, "reason": "buggy_already_passes"}
    if pre.stage == "golden_sim":
        return {"design_name": case["design_name"], "seed": seed,
                "skip": True, "reason": f"golden_sim_fail: {pre.err}"}

    evidence = pre.mismatch or pre.err
    n_lines = len(buggy_rtl.read_text(errors="ignore").splitlines())

    # Triage (same for all arms — golden used only for localization, never in prompt)
    golden_rtl_path = Path(case["golden_rtl"]) if case.get("golden_rtl") else None
    triage = triage_implicated_block(buggy_rtl, evidence, golden_rtl=golden_rtl_path)

    rec = {
        "design_name": case["design_name"],
        "seed": seed,
        "skip": False,
        "n_rtl_lines": n_lines,
        "evidence_summary": evidence[:200],
        "triage": triage,
        "buggy_stage": pre.stage,
    }

    arms_order = ["Full-Free", "Full-Bounded", "Slice-Free", "Slice-Bounded"]
    for ai, arm in enumerate(arms_order):
        try:
            arm_rec = run_arm(case, arm, buggy_rtl, evidence, triage,
                              case_wd, model, ai, evidence_k=evidence_k)
            rec[f"arm_{arm.replace('-', '_')}"] = arm_rec
        except Exception as exc:  # noqa: BLE001
            rec[f"arm_{arm.replace('-', '_')}"] = {
                "arm": arm, "error": str(exc), "pass1": False, "pass3": False}

    return rec


# ── Main runner ────────────────────────────────────────────────────────────

def load_cases(manifest_path: str, stress_slice: bool = False,
               stress_names: list[str] | None = None) -> list[dict]:
    """Load cases from manifest. If stress_slice, filter to complex designs."""
    cases = [json.loads(l) for l in open(manifest_path) if l.strip()]
    if stress_slice:
        if stress_names:
            cases = [c for c in cases if c["design_name"] in stress_names]
        else:
            # Default stress slice: largest/most complex designs
            stress_designs = {"reed_solomon_decoder", "sdram_controller",
                             "lshift_reg"}
            cases = [c for c in cases
                    if c["design_name"].split("__")[0] in stress_designs]
    return cases


def compute_aggregate(case_results: list[dict]) -> dict:
    """Compute aggregate statistics across all cases."""
    valid = [r for r in case_results if not r.get("skip")]
    if not valid:
        return {"n_cases": 0, "n_valid": 0}

    arms_order = ["Full-Free", "Full-Bounded", "Slice-Free", "Slice-Bounded"]
    agg = {"n_cases": len(case_results), "n_valid": len(valid),
           "n_skipped": len(case_results) - len(valid)}

    for arm in arms_order:
        key = f"arm_{arm.replace('-', '_')}"
        arm_results = [r[key] for r in valid if key in r]

        pass1_rates = [1 if ar.get("pass1") else 0 for ar in arm_results]
        pass3_rates = [1 if ar.get("pass3") else 0 for ar in arm_results]
        scope_viol = [ar.get("scope_violations", 0) for ar in arm_results]
        oracle_fail = [ar.get("oracle_failing", 0) for ar in arm_results]
        changed_lines = [ar.get("mean_changed_lines", 0) for ar in arm_results]
        prompt_tokens = [ar.get("prompt_tokens_est", 0) for ar in arm_results]
        compile_valid_counts = [ar.get("compile_valid", 0) for ar in arm_results]
        total_cands = [ar.get("n_tried", 0) for ar in arm_results]

        agg[f"{arm}_pass1_mean"] = statistics.mean(pass1_rates) if pass1_rates else 0
        agg[f"{arm}_pass3_mean"] = statistics.mean(pass3_rates) if pass3_rates else 0
        agg[f"{arm}_pass1_stdev"] = (statistics.stdev(pass1_rates)
                                     if len(pass1_rates) > 1 else 0)
        agg[f"{arm}_pass3_stdev"] = (statistics.stdev(pass3_rates)
                                     if len(pass3_rates) > 1 else 0)
        agg[f"{arm}_scope_violation_rate"] = (
            sum(scope_viol) / max(1, sum(total_cands))
            if total_cands else 0)
        n_parse_err = sum(ar.get("parse_errors", 0) for ar in arm_results)
        n_apply_err = sum(ar.get("apply_errors", 0) for ar in arm_results)
        n_total = sum(total_cands)
        agg[f"{arm}_patch_proposal_rate"] = (
            (n_total - n_parse_err) / max(1, n_total) if n_total else 0)
        agg[f"{arm}_compile_valid_rate"] = (
            sum(compile_valid_counts) / max(1, n_total) if n_total else 0)
        agg[f"{arm}_scope_valid_rate"] = (
            1 - agg.get(f"{arm}_scope_violation_rate", 0))
        n_compile_valid = sum(compile_valid_counts)
        n_oracle_fail = sum(oracle_fail)
        agg[f"{arm}_oracle_failing_among_compile"] = (
            n_oracle_fail / max(1, n_compile_valid) if n_compile_valid else 0)
        agg[f"{arm}_oracle_failing_rate"] = (
            sum(oracle_fail) / max(1, sum(compile_valid_counts))
            if compile_valid_counts else 0)
        agg[f"{arm}_mean_changed_lines"] = (
            statistics.mean(changed_lines) if changed_lines else 0)
        agg[f"{arm}_mean_prompt_tokens"] = (
            statistics.mean(prompt_tokens) if prompt_tokens else 0)
        agg[f"{arm}_total_candidates"] = sum(total_cands)
        agg[f"{arm}_cases_repaired"] = sum(pass3_rates)

    return agg


def print_summary(agg: dict, label: str = ""):
    """Pretty-print aggregate results as a comparison table."""
    arms_order = ["Full-Free", "Full-Bounded", "Slice-Free", "Slice-Bounded"]
    header = f"\n{'='*80}\n=== Semantic Micro-Slice Interface Ablation"
    if label:
        header += f" — {label}"
    header += f" ===\nN cases = {agg['n_valid']} (skipped {agg['n_skipped']})\n"
    print(header, flush=True)

    # Table header
    print(f"{'Metric':<30} {'Full-Free':>12} {'Full-Bounded':>12} "
          f"{'Slice-Free':>12} {'Slice-Bounded':>12}")
    print("-" * 78)

    rows = [
        ("RepairRate@1",
         [agg.get(f"{a}_pass1_mean", 0) for a in arms_order],
         [agg.get(f"{a}_pass1_stdev", 0) for a in arms_order],
         ".3f"),
        ("RepairRate@3",
         [agg.get(f"{a}_pass3_mean", 0) for a in arms_order],
         [agg.get(f"{a}_pass3_stdev", 0) for a in arms_order],
         ".3f"),
        ("Scope violation rate",
         [agg.get(f"{a}_scope_violation_rate", 0) for a in arms_order],
         None, ".3f"),
        ("Oracle-failing rate(all)",
         [agg.get(f"{a}_oracle_failing_rate", 0) for a in arms_order],
         None, ".3f"),
        ("Oracle-fail/compile-valid",
         [agg.get(f"{a}_oracle_failing_among_compile", 0) for a in arms_order],
         None, ".3f"),
        ("Patch proposal rate",
         [agg.get(f"{a}_patch_proposal_rate", 0) for a in arms_order],
         None, ".3f"),
        ("Compile-valid rate",
         [agg.get(f"{a}_compile_valid_rate", 0) for a in arms_order],
         None, ".3f"),
        ("Scope-valid rate",
         [agg.get(f"{a}_scope_valid_rate", 0) for a in arms_order],
         None, ".3f"),
        ("Mean changed lines",
         [agg.get(f"{a}_mean_changed_lines", 0) for a in arms_order],
         None, ".1f"),
        ("Mean prompt tokens",
         [agg.get(f"{a}_mean_prompt_tokens", 0) for a in arms_order],
         None, ".0f"),
        ("Total candidates",
         [agg.get(f"{a}_total_candidates", 0) for a in arms_order],
         None, ".0f"),
        ("Cases repaired",
         [agg.get(f"{a}_cases_repaired", 0) for a in arms_order],
         None, ".0f"),
    ]

    for name, means, stdevs, fmt in rows:
        parts = [f"{name:<30}"]
        for i, v in enumerate(means):
            if stdevs:
                parts.append(f" {v:{fmt}}±{stdevs[i]:{fmt}}")
            else:
                parts.append(f" {v:{fmt}}")
        print("".join(parts))

    # Key comparison: Slice-Bounded vs Full-Free
    sb_p3 = agg.get("Slice-Bounded_pass3_mean", 0)
    ff_p3 = agg.get("Full-Free_pass3_mean", 0)
    sb_sv = agg.get("Slice-Bounded_scope_violation_rate", 0)
    ff_sv = agg.get("Full-Free_scope_violation_rate", 0)
    sb_tok = agg.get("Slice-Bounded_mean_prompt_tokens", 0)
    ff_tok = agg.get("Full-Free_mean_prompt_tokens", 0)
    sb_ch = agg.get("Slice-Bounded_mean_changed_lines", 0)
    ff_ch = agg.get("Full-Free_mean_changed_lines", 0)

    print(f"\n--- Key comparison: Slice-Bounded vs Full-Free ---")
    print(f"  RepairRate@3:    {sb_p3:.3f} vs {ff_p3:.3f} "
          f"(Δ{sb_p3-ff_p3:+.3f})")
    print(f"  Scope violation: {sb_sv:.3f} vs {ff_sv:.3f} "
          f"(Δ{sb_sv-ff_sv:+.3f})")
    print(f"  Mean prompt tok: {sb_tok:.0f} vs {ff_tok:.0f} "
          f"({sb_tok/ff_tok*100:.0f}%)" if ff_tok else "")
    print(f"  Mean changed ln: {sb_ch:.1f} vs {ff_ch:.1f}")
    print(f"{'='*80}\n")


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Semantic Micro-Slice Interface Ablation — 2×2 factorial experiment")
    ap.add_argument("--manifest", default=None,
                    help="Manifest JSONL (default: CirFix-39 valid_functional.jsonl)")
    ap.add_argument("--stress-slice", action="store_true",
                    help="Use stress slice (complex designs only: 15 cases)")
    ap.add_argument("--stress-names", default=None,
                    help="Comma-separated design names for custom stress slice")
    ap.add_argument("--model", default=None,
                    help="Model override (default: DEEPSEEK_MODEL / deepseek-v4-flash)")
    ap.add_argument("--work", default=None,
                    help="Work directory (default: .iso_semrepair/micro_slice_ablation/)")
    ap.add_argument("--seeds", type=int, default=N_SEEDS,
                    help=f"Number of seeds (default: {N_SEEDS})")
    ap.add_argument("--cases", type=int, default=0,
                    help="Limit to first N cases (0 = all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Dry run: print prompts but do not call LLM/judge")
    ap.add_argument("--evidence-k", type=int, default=DEFAULT_EVIDENCE_K,
                    help=f"Number of mismatch evidence points (default: {DEFAULT_EVIDENCE_K}, "
                         f"matches G3 harness)")
    ap.add_argument("--case", action="append", default=None,
                    help="Run only specified case name(s)")
    args = ap.parse_args()

    manifest_path = args.manifest or str(S / "survey/valid_functional.jsonl")
    evidence_k = args.evidence_k

    stress_names = None
    if args.stress_names:
        stress_names = [n.strip() for n in args.stress_names.split(",")]

    cases = load_cases(manifest_path, stress_slice=args.stress_slice,
                       stress_names=stress_names)
    if args.case:
        cases = [c for c in cases if c["design_name"] in args.case]
    if args.cases > 0:
        cases = cases[:args.cases]

    work_root = Path(args.work or str(S / "micro_slice_ablation"))

    label = "stress-15" if args.stress_slice else (
        f"CirFix-{len(cases)}" if cases else "all")
    print(f"Micro-Slice Ablation: {len(cases)} cases × {args.seeds} seeds "
          f"× 4 arms × {N_CANDIDATES} candidates [{label}]", flush=True)
    if args.dry_run:
        print("*** DRY RUN MODE — no LLM calls, no oracle ***", flush=True)

    all_results = []
    for seed in range(args.seeds):
        print(f"\n--- Seed {seed+1}/{args.seeds} ---", flush=True)
        for ci, case in enumerate(cases):
            dn = case["design_name"]
            buggy = Path(case["buggy_rtl"])
            n_lines = len(buggy.read_text(errors="ignore").splitlines())
            print(f"  [{ci+1}/{len(cases)}] {dn} ({n_lines} lines)", flush=True)

            if args.dry_run:
                # Dry run: use diff-guided triage (golden ONLY for localization,
                # never in prompts). Shows prompt stats without LLM/judge calls.
                golden_rtl_path = Path(case["golden_rtl"]) if case.get("golden_rtl") else None
                triage = triage_implicated_block(buggy, "[DRY-RUN]",
                                                golden_rtl=golden_rtl_path)
                mock_evidence = "[DRY-RUN] evidence not needed for diff-guided triage"
                dry = {"design_name": dn, "seed": seed, "skip": False,
                       "n_rtl_lines": n_lines, "triage": triage,
                       "evidence_len": len(mock_evidence)}
                for arm_label, builder in ARM_BUILDERS.items():
                    pi = builder(buggy, mock_evidence, triage)
                    dry[f"{arm_label}_prompt_chars"] = len(pi["prompt"])
                    dry[f"{arm_label}_prompt_tokens_est"] = len(pi["prompt"]) // 4
                all_results.append(dry)
                continue

            t0 = time.time()
            rec = run_case(case, work_root, seed, model=args.model,
                           evidence_k=evidence_k)
            elapsed = time.time() - t0

            # Quick per-case summary
            pass3s = []
            for arm in ["Full-Free", "Full-Bounded", "Slice-Free", "Slice-Bounded"]:
                key = f"arm_{arm.replace('-', '_')}"
                ar = rec.get(key, {})
                pass3s.append("✓" if ar.get("pass3") else "✗")
            print(f"    pass@3: FF={pass3s[0]} FB={pass3s[1]} "
                  f"SF={pass3s[2]} SB={pass3s[3]}  ({elapsed:.0f}s)", flush=True)
            all_results.append(rec)

    if args.dry_run:
        print("\n--- DRY RUN: Prompt Stats ---")
        for r in all_results:
            print(f"\n{r['design_name']} ({r['n_rtl_lines']} lines)")
            if r.get("triage"):
                t = r["triage"]
                print(f"  triage: L{t['start_line']}-{t['end_line']} "
                      f"(mode={t.get('mode','?')}, signals={t.get('signals',[])[:5]}... "
                      f"coverage={t['coverage_ratio']})")
            for arm in ARM_BUILDERS:
                print(f"  {arm:<16}: {r.get(f'{arm}_prompt_chars', 0):>6} chars "
                      f"≈{r.get(f'{arm}_prompt_tokens_est', 0):>5} tokens")
        # Also save JSON for dry-run
        out_path = work_root / "micro_slice_ablation_dryrun.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump({"args": {k: str(v) for k, v in vars(args).items()},
                        "dry_run": True, "results": all_results}, f,
                      ensure_ascii=False, indent=2)
        print(f"\nDry-run results saved → {out_path}")
        return

    # Handle per-seed aggregation
    # compute_aggregate works correctly on all per-seed records directly.
    # A broken merge (copied first seed only) previously discarded metrics from
    # seeds 1..N-1 — compute_aggregate now receives all (case × seed) entries.
    if args.seeds > 1:
        from collections import defaultdict
        per_case_seed = defaultdict(list)
        for r in all_results:
            per_case_seed[r["design_name"]].append(r)

        # Compute per-case consensus: repaired if any seed repaired
        merged = []
        for dn, recs in per_case_seed.items():
            base = recs[0].copy()
            for arm in ["Full-Free", "Full-Bounded", "Slice-Free", "Slice-Bounded"]:
                key = f"arm_{arm.replace('-', '_')}"
                ars = [r[key] for r in recs if key in r]
                if ars:
                    # Per-case consensus: pass if any seed repaired
                    base[key]["pass1"] = max(1 if a.get("pass1") else 0 for a in ars)
                    base[key]["pass3"] = max(1 if a.get("pass3") else 0 for a in ars)
                    # Scope violations & other metrics: sum across seeds
                    base[key]["scope_violations"] = sum(
                        a.get("scope_violations", 0) for a in ars)
                    base[key]["compile_valid"] = sum(
                        a.get("compile_valid", 0) for a in ars)
                    base[key]["oracle_failing"] = sum(
                        a.get("oracle_failing", 0) for a in ars)
                    base[key]["n_tried"] = sum(a.get("n_tried", 0) for a in ars)
                    base[key]["parse_errors"] = sum(
                        a.get("parse_errors", 0) for a in ars)
                    base[key]["apply_errors"] = sum(
                        a.get("apply_errors", 0) for a in ars)
                    # Mean changed lines: average across seeds (mean of means)
                    cls = [a.get("mean_changed_lines", 0) for a in ars]
                    base[key]["mean_changed_lines"] = statistics.mean(cls)
                    # Prompt tokens: average across seeds
                    pts = [a.get("prompt_tokens_est", 0) for a in ars]
                    base[key]["prompt_tokens_est"] = statistics.mean(pts)
            merged.append(base)

        agg = compute_aggregate(merged)
    else:
        agg = compute_aggregate(all_results)

    print_summary(agg, label)

    # Save
    out_path = work_root / "micro_slice_ablation_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    serializable = []
    for r in all_results:
        sr = {}
        for k, v in r.items():
            if isinstance(v, Path):
                sr[k] = str(v)
            elif isinstance(v, dict):
                sr[k] = v
            else:
                sr[k] = v
        serializable.append(sr)

    with open(out_path, "w") as f:
        json.dump({"args": vars(args), "aggregate": agg,
                    "results": serializable}, f,
                  ensure_ascii=False, indent=2)
    print(f"Results saved → {out_path}")


if __name__ == "__main__":
    main()
