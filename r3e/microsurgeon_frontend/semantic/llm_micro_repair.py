#!/usr/bin/env python3
"""
Frontend LLM micro-repair helpers — pure functions only.

build_repair_prompt  : construct the constrained single-site repair prompt
apply_patch          : apply a {line, old_identifier, new_identifier} patch in isolation
"""
from __future__ import annotations

import difflib
import hashlib
import re
from pathlib import Path
from typing import List, Tuple

from .propose_patch_spec import read_lines, whole_word


def build_repair_prompt(
    rtl_path: Path,
    error_log_path: Path,
    target_line: int,
    old_identifier: str,
    context_ranges: List[Tuple[int, int]],
    repair_mode: str = "identifier",
    block_bounds: Tuple[int, int] | None = None,
    rework_request: dict | None = None,
) -> str:
    """Return a constrained single-site repair prompt string; no files written."""
    rtl_path = Path(rtl_path)
    lines = read_lines(rtl_path)

    blocks = []
    for start, end in context_ranges:
        body = []
        for line_no in range(start, end + 1):
            if 1 <= line_no <= len(lines):
                body.append(f"{line_no:4d}: {lines[line_no - 1]}")
        if body:
            blocks.append("\n".join(body))
    rtl_context = "\n\n".join(blocks)

    error_excerpt = "\n".join(
        line
        for line in Path(error_log_path).read_text(errors="ignore").splitlines()
        if old_identifier in line or "Unable to elaborate condition expression" in line
    )

    rtl_filename = rtl_path.name
    if repair_mode == "block":
        if block_bounds is None:
            raise ValueError("block repair requires block_bounds")
        start_line, end_line = block_bounds
        if not (1 <= start_line <= end_line <= len(lines)):
            raise ValueError(
                f"invalid block bounds {block_bounds!r} for {rtl_path} ({len(lines)} lines)"
            )
        block_body = "\n".join(
            f"{line_no:4d}: {lines[line_no - 1]}"
            for line_no in range(start_line, end_line + 1)
        )
        return (
            "You are repairing a SystemVerilog timing problem in an isolated buggy RTL copy.\n"
            "\n"
            "Important constraints:\n"
            "- Use only the buggy RTL block and violation context included below.\n"
            "- Do not assume access to clean or golden RTL.\n"
            "- Rewrite only the framed block. Do not edit lines outside the block.\n"
            "- Preserve the module interface and functional equivalence.\n"
            "- Return JSON only, with this schema:\n"
            "  {\n"
            '    "patch": {\n'
            f'      "file": "{rtl_filename}",\n'
            f'      "start_line": {start_line},\n'
            f'      "end_line": {end_line},\n'
            '      "new_block": "<complete replacement text for exactly this block>"\n'
            "    },\n"
            '    "rationale": "<brief rationale from buggy block context>"\n'
            "  }\n"
            "\n"
            "Backend violation context:\n"
            "```text\n"
            f"{error_excerpt}\n"
            "```\n"
            "\n"
            f"Framed RTL block ({start_line}-{end_line}) from the buggy file:\n"
            "```verilog\n"
            f"{block_body}\n"
            "```\n"
        )
    if repair_mode == "structural_timing":
        # 回环B: 后端ECO物理耗尽 → 前端做**等价保持**的结构性时序修复(retime/restructure),
        # 注入 rework_request(problem+attempt+recommended); verify_equiv(formal序列等价)是硬闸.
        if block_bounds is None:
            raise ValueError("structural_timing repair requires block_bounds")
        if rework_request is None:
            raise ValueError("structural_timing repair requires rework_request")
        start_line, end_line = block_bounds
        if not (1 <= start_line <= end_line <= len(lines)):
            raise ValueError(
                f"invalid block bounds {block_bounds!r} for {rtl_path} ({len(lines)} lines)"
            )
        # 不带行号(防 LLM 把行号前缀 echo 进 new_block 污染 RTL); start/end_line 在 schema 给.
        block_body = "\n".join(
            lines[line_no - 1] for line_no in range(start_line, end_line + 1)
        )
        prob = rework_request.get("problem", {})
        att = rework_request.get("backend_attempt", {})
        rec = rework_request.get("recommended_fix", {})
        return (
            "You are fixing a SETUP TIMING violation in an isolated buggy RTL copy by an "
            "EQUIVALENCE-PRESERVING structural transform.\n"
            "Backend gate-level ECO (VT-swap/sizing) exhausted physical means and cannot close "
            "this reg-to-reg path; an RTL micro-architecture change is required.\n"
            "\n"
            "Timing violation (RTL level):\n"
            f"- startpoint: {prob.get('startpoint')}\n"
            f"- endpoint:   {prob.get('endpoint')}\n"
            f"- module:     {prob.get('module')}\n"
            f"- residual gap: {prob.get('residual_gap_ps')} ps (still short after backend)\n"
            f"- combinational depth: {prob.get('comb_depth')} levels\n"
            f"- backend tried {att.get('tried')}, improved +{att.get('improved_ps')} ps, "
            f"then {att.get('exhausted_reason')}\n"
            "\n"
            "Recommended fix (backend advisory; refine with your RTL judgment):\n"
            f"- action: {rec.get('action')}\n"
            f"- rationale: {rec.get('rationale')}\n"
            f"- hint: {rec.get('hint')}\n"
            "\n"
            "HARD CONSTRAINTS (a formal sequential-equivalence check WILL reject any violation):\n"
            "- Preserve functional equivalence and the module interface EXACTLY.\n"
            "- Do NOT relax or change any timing constraint.\n"
            "- Do NOT change I/O latency or throughput; do NOT insert pipeline registers "
            "(adding latency breaks equivalence).\n"
            "- Allowed transforms (equivalence-preserving ONLY):\n"
            "  * retime: move registers across combinational logic WITHOUT changing I/O latency.\n"
            "  * restructure: boolean-equivalent logic restructuring that reduces combinational "
            "depth (balanced operator tree, carry-lookahead adder, tree comparator, etc.).\n"
            "- Rewrite ONLY the framed block; do not edit outside it.\n"
            "- Return JSON only, with this schema:\n"
            "  {\n"
            '    "patch": {\n'
            f'      "file": "{rtl_filename}",\n'
            f'      "start_line": {start_line},\n'
            f'      "end_line": {end_line},\n'
            '      "new_block": "<complete equivalence-preserving replacement for exactly this block>"\n'
            "    },\n"
            '    "rationale": "<brief: which equivalence-preserving transform and why>"\n'
            "  }\n"
            "\n"
            f"Framed RTL block ({start_line}-{end_line}) from the buggy file:\n"
            "```verilog\n"
            f"{block_body}\n"
            "```\n"
        )
    if repair_mode != "identifier":
        raise ValueError(f"unknown repair_mode {repair_mode!r}")
    return (
        "You are repairing a SystemVerilog frontend failure in an isolated buggy RTL copy.\n"
        "\n"
        "Important constraints:\n"
        "- Use only the buggy RTL context included below.\n"
        "- Do not assume access to clean or golden RTL.\n"
        "- Propose exactly one single-site identifier replacement.\n"
        "- Do not propose structural edits, module boundary changes, declarations, new wires, or multi-site changes.\n"
        "- Return JSON only, with this schema:\n"
        "  {\n"
        '    "patch": {\n'
        f'      "file": "{rtl_filename}",\n'
        f'      "line": {target_line},\n'
        f'      "old_identifier": "{old_identifier}",\n'
        '      "new_identifier": "<replacement identifier>"\n'
        "    },\n"
        '    "rationale": "<brief rationale from buggy context>"\n'
        "  }\n"
        "\n"
        "Frontend error:\n"
        "```text\n"
        f"{error_excerpt}\n"
        "```\n"
        "\n"
        "Buggy RTL context:\n"
        "```verilog\n"
        f"{rtl_context}\n"
        "```\n"
    )


def apply_patch(rtl_path: Path, patch: dict, work_dir: Path) -> Path:
    """
    Copy rtl_path into work_dir and apply a single whole-word identifier substitution.

    patch keys:  line (1-based), old_identifier, new_identifier.

    Raises ValueError if old_identifier is not found as a whole word on the target line.
    Returns the path to the isolated copy.
    """
    rtl_path = Path(rtl_path)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    dest = work_dir / rtl_path.name

    lines = rtl_path.read_text(errors="ignore").splitlines(keepends=True)

    line_no = patch["line"]
    old_id = patch["old_identifier"]
    new_id = patch["new_identifier"]

    if not (1 <= line_no <= len(lines)):
        raise ValueError(f"line {line_no} out of range (file has {len(lines)} lines)")

    idx = line_no - 1
    pattern = whole_word(old_id)
    if not pattern.search(lines[idx]):
        raise ValueError(
            f"old_identifier {old_id!r} not found as whole word on line {line_no}: {lines[idx]!r}"
        )
    lines[idx] = pattern.sub(new_id, lines[idx])
    dest.write_text("".join(lines))
    return dest


_LINENO_PREFIX_RE = re.compile(r"^\s*\d+:\s")


def _strip_echoed_line_numbers(new_block: str) -> str:
    """防御: LLM 偶把框选块的行号前缀(如 '  6: ')echo 进 new_block 污染 RTL → syntax error.
    仅当**每个非空行**都带 `数字:` 前缀(=统一行号 echo)才剥; case 标签是稀疏的不会全中, 故安全."""
    lines = new_block.splitlines()
    nonblank = [ln for ln in lines if ln.strip()]
    if nonblank and all(_LINENO_PREFIX_RE.match(ln) for ln in nonblank):
        return "\n".join(_LINENO_PREFIX_RE.sub("", ln) if ln.strip() else ln for ln in lines)
    return new_block


def apply_block_patch(rtl_path: Path, patch: dict, work_dir: Path) -> Path:
    """
    Copy rtl_path into work_dir and replace exactly [start_line, end_line].

    patch keys: start_line, end_line, new_block.
    Writes block_patch.diff in work_dir for auditability.
    """
    rtl_path = Path(rtl_path)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    dest = work_dir / rtl_path.name

    lines = rtl_path.read_text(errors="ignore").splitlines(keepends=True)
    original_line_count = len(lines)

    start_line = int(patch["start_line"])
    end_line = int(patch["end_line"])
    new_block = patch["new_block"]
    if not isinstance(new_block, str) or not new_block.strip():
        raise ValueError("new_block must be a non-empty string")
    new_block = _strip_echoed_line_numbers(new_block)
    if not (1 <= start_line <= end_line <= len(lines)):
        raise ValueError(
            f"block {start_line}-{end_line} out of range (file has {len(lines)} lines)"
        )

    replacement = [line + "\n" for line in new_block.splitlines()]
    expected_delta = len(replacement) - (end_line - start_line + 1)
    before = list(lines)
    lines[start_line - 1:end_line] = replacement
    actual_delta = len(lines) - original_line_count
    if actual_delta != expected_delta:
        raise AssertionError(
            f"block replacement line delta mismatch: expected {expected_delta}, got {actual_delta}"
        )

    dest.write_text("".join(lines))
    diff = difflib.unified_diff(
        before,
        lines,
        fromfile=str(rtl_path),
        tofile=str(dest),
    )
    (work_dir / "block_patch.diff").write_text("".join(diff))
    return dest


# ── Stage B: LLM propose + formal equivalence ─────────────────────────────────
# (appended; do not edit the functions above this line)

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

_ALLOWED_HOSTS: frozenset = frozenset({"api.deepseek.com", "api.deepseek.com:443"})
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_PROVEN_RE = re.compile(r"Of those cells (\d+) are proven and (\d+) are unproven\.")
_EQUIV_OK_MSG = "Equivalence successfully proven!"
# seq_miter(retiming/时序变换用): sat 时序 miter 按 I/O 对齐, bounded N 拍. equiv_induct 的
# k-induction 对齐寄存器, 证不了 retiming(寄存器内容变); sat-miter 不要求寄存器对应 → 能证.
_SAT_PROOF_OK_MSG = "SAT proof finished - no model found: SUCCESS!"
_SAT_COUNTEREXAMPLE_MSG = "SAT proof finished - model found:"


def _egress_guard(base_url: str) -> str:
    """Enforce https + ALLOWED_HOSTS; return /chat/completions endpoint."""
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme != "https":
        raise RuntimeError(
            f"egress guard: scheme must be https, got {parsed.scheme!r}"
        )
    if parsed.netloc not in _ALLOWED_HOSTS:
        raise RuntimeError(
            f"egress guard: host {parsed.netloc!r} not in ALLOWED_HOSTS={_ALLOWED_HOSTS}"
        )
    return base_url.rstrip("/") + "/chat/completions"


def _extract_json(text: str) -> dict:
    """Strip optional ```json fence and parse JSON; fall back to greedy search."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def _validate_patch(
    obj: dict,
    *,
    target_line: int,
    old_identifier: str,
    expected_file: str,
) -> "tuple[dict, list[str]]":
    """Return (patch_dict, errors); errors is empty on success."""
    errors: List[str] = []
    patch = obj.get("patch") if isinstance(obj, dict) else None
    if not isinstance(patch, dict):
        errors.append("missing patch object")
        return {}, errors

    # Compare basenames to tolerate 'rtl/foo.v' vs 'foo.v' variants.
    file_val = patch.get("file", "")
    if Path(file_val).name != Path(expected_file).name:
        errors.append(
            f"file: expected basename {Path(expected_file).name!r}, got {file_val!r}"
        )

    if patch.get("line") != target_line:
        errors.append(f"line: expected {target_line}, got {patch.get('line')!r}")

    if patch.get("old_identifier") != old_identifier:
        errors.append(
            f"old_identifier: expected {old_identifier!r}, "
            f"got {patch.get('old_identifier')!r}"
        )

    new_id = patch.get("new_identifier")
    if not isinstance(new_id, str) or not _IDENT_RE.fullmatch(new_id):
        errors.append(
            f"new_identifier {new_id!r} is not a valid Verilog identifier"
        )
    elif new_id == old_identifier:
        errors.append("new_identifier must differ from old_identifier")

    return patch, errors


def _validate_block_patch(
    obj: dict,
    *,
    block_bounds: Tuple[int, int],
    expected_file: str,
) -> "tuple[dict, list[str]]":
    errors: List[str] = []
    patch = obj.get("patch") if isinstance(obj, dict) else None
    if not isinstance(patch, dict):
        errors.append("missing patch object")
        return {}, errors

    file_val = patch.get("file", "")
    if Path(file_val).name != Path(expected_file).name:
        errors.append(
            f"file: expected basename {Path(expected_file).name!r}, got {file_val!r}"
        )

    expected_start, expected_end = block_bounds
    if patch.get("start_line") != expected_start:
        errors.append(
            f"start_line: expected {expected_start}, got {patch.get('start_line')!r}"
        )
    if patch.get("end_line") != expected_end:
        errors.append(
            f"end_line: expected {expected_end}, got {patch.get('end_line')!r}"
        )

    new_block = patch.get("new_block")
    if not isinstance(new_block, str) or not new_block.strip():
        errors.append("new_block must be a non-empty string")
    else:
        original_len = expected_end - expected_start + 1
        new_len = len(new_block.splitlines())
        if new_len <= 0 or new_len > max(1, original_len * 4 + 20):
            errors.append(
                f"new_block line count {new_len} is unreasonable for original length {original_len}"
            )

    return patch, errors


def propose_patch_via_llm(
    prompt: str,
    *,
    target_line: int,
    old_identifier: str,
    expected_file: str,
    repair_mode: str = "identifier",
    block_bounds: Tuple[int, int] | None = None,
) -> dict:
    """
    Call DeepSeek to propose a single-site patch for the given repair prompt.

    Reads DEEPSEEK_BASE_URL / DEEPSEEK_API_KEY / DEEPSEEK_MODEL from env.
    No file side effects.
    Raises RuntimeError on env / egress / validation failure.
    Returns {"patch": {...}, "rationale": str, "model": str}.
    """
    base = os.environ.get("DEEPSEEK_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    model = os.environ.get("DEEPSEEK_MODEL", "")
    if not base or not api_key or not model:
        raise RuntimeError(
            "missing env vars: DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_MODEL — "
            "run `source .env` first"
        )

    endpoint = _egress_guard(base)

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You repair RTL compile failures with minimal constrained edits. "
                    "Return JSON only."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "stream": False,
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    _MAX_ATTEMPTS = 3
    body = None
    for _attempt in range(_MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            break
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek HTTP {exc.code}: {body[:400]}") from exc
        except urllib.error.URLError as exc:
            if _attempt == _MAX_ATTEMPTS - 1:
                raise RuntimeError(
                    f"DeepSeek network error after {_MAX_ATTEMPTS} attempts: {exc}"
                ) from exc
            time.sleep(2 * (_attempt + 1))

    raw = json.loads(body)
    content = raw["choices"][0]["message"]["content"]
    parsed = _extract_json(content)
    if repair_mode in ("block", "structural_timing"):
        if block_bounds is None:
            raise RuntimeError(f"{repair_mode} repair requires block_bounds")
        patch, errors = _validate_block_patch(
            parsed,
            block_bounds=block_bounds,
            expected_file=expected_file,
        )
    elif repair_mode == "identifier":
        patch, errors = _validate_patch(
            parsed,
            target_line=target_line,
            old_identifier=old_identifier,
            expected_file=expected_file,
        )
    else:
        raise RuntimeError(f"unknown repair_mode {repair_mode!r}")
    if errors:
        raise RuntimeError(
            f"patch validation failed: {errors!r}  raw_content={content[:400]!r}"
        )
    return {
        "patch": patch,
        "rationale": parsed.get("rationale", ""),
        "model": raw.get("model", model),
    }


def _parse_equiv_log(log_text: str) -> dict:
    """Parse yosys equiv log; returns proven/unproven/total/asserted_ok."""
    m = _PROVEN_RE.search(log_text)
    if not m:
        return {"proven": None, "unproven": None, "total": None, "asserted_ok": False}
    proven = int(m.group(1))
    unproven = int(m.group(2))
    return {
        "proven": proven,
        "unproven": unproven,
        "total": proven + unproven,
        "asserted_ok": _EQUIV_OK_MSG in log_text,
    }


def verify_equiv(
    golden_sources: List[Path],
    deps: List[Path],
    patched_target: Path,
    top_module: str,
    work_dir: Path,
    *,
    equiv_config: str = "-undef -seq 4",
    equiv_method: str = "induct",
    seq_depth: int = 12,
    equiv_timeout_sec: int = 300,
    abstract_mul: bool = True,
    mask_bits: int = 3,
    yosys_bin: str | None = None,
) -> dict:
    """
    Run yosys formal equivalence check.

    gold = all golden_sources (three-file clean set).
    gate = deps (clean dependencies) + patched_target.

    equiv_method:
      - "induct"(默认): equiv_make + equiv_induct, 对齐寄存器. 组合/标识符/restructure 用.
      - "seq_miter": sat 时序 miter(按 I/O 对齐, bounded seq_depth 拍). retiming/时序变换用——
        equiv_induct 的 k-induction 对齐寄存器, 证不了 retiming(寄存器内容变); sat-miter 不要求
        寄存器对应 → 能证. bounded(N拍内无差异), N>>流水深度即强等价(诚实标注 bounded).

    Writes equiv_check.ys and equiv_check.log to work_dir.
    Returns {"proven", "unproven", "total", "asserted_ok", "yosys_exit", "log_path"}.
    seq_miter 模式: 证通 proven=total=1(兼容 success 公式), 否则 0.
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    resolved_yosys = yosys_bin or os.environ.get("YOSYS_BIN") or "yosys"

    gold_files = " ".join(str(Path(p).resolve()) for p in golden_sources)
    gate_files = " ".join(
        str(Path(p).resolve()) for p in list(deps) + [patched_target]
    )

    if equiv_method == "seq_miter":
        # 一致性抽象黑盒(abstract_mul): 宽变量乘法器 bit-blast 后 SAT 爆炸(实测4×8x8@depth8超时);
        # techmap 把 $mul 输入掩码到 mask_bits(gold/gate 一致) → 小位宽可解. 对 retiming sound:
        # 掩码一致、验的是寄存器结构(非乘法器函数), retiming 正确性是结构性、与数据位宽无关;
        # 小位宽乘积仍逐拍变化 → 错误retiming的错位值仍被 depth N 内捕获(实测正例证/负例拒).
        mask_step = ""
        if abstract_mul:
            mask = (1 << int(mask_bits)) - 1
            mask_map = work_dir / "mul_mask_map.v"
            mask_map.write_text(
                '(* techmap_celltype = "$mul" *)\n'
                "module _mul_mask (A, B, Y);\n"
                "  parameter A_SIGNED=0; parameter B_SIGNED=0;\n"
                "  parameter A_WIDTH=1; parameter B_WIDTH=1; parameter Y_WIDTH=1;\n"
                "  input [A_WIDTH-1:0] A; input [B_WIDTH-1:0] B; output [Y_WIDTH-1:0] Y;\n"
                f"  wire [A_WIDTH-1:0] Am = A & {mask};\n"
                f"  wire [B_WIDTH-1:0] Bm = B & {mask};\n"
                "  assign Y = Am * Bm;\n"
                "endmodule\n"
            )
            # -max_iter 1: 只掩一层(否则掩码产的新$mul被反复重映射→不停). 路径加引号(防空格拆断).
            mask_step = f'techmap -max_iter 1 -map "{mask_map}"; '
        ys_script = (
            f"read_verilog -sv {gold_files}\n"
            f"hierarchy -top {top_module}; proc; {mask_step}opt; flatten; "
            f"rename {top_module} gold; design -stash gold\n"
            f"read_verilog -sv {gate_files}\n"
            f"hierarchy -top {top_module}; proc; {mask_step}opt; flatten; "
            f"rename {top_module} gate; design -stash gate\n"
            f"design -copy-from gold -as gold gold\n"
            f"design -copy-from gate -as gate gate\n"
            # -make_assert: 生成 $assert(输出不等时触发); sat -prove-asserts 真证之(非空证).
            f"miter -equiv -flatten -make_assert gold gate miter\n"
            f"hierarchy -top miter\n"
            # reset 后同输入, 输出序列应一致(retiming 延迟相同); 证 N 拍内断言恒成立.
            f"sat -seq {int(seq_depth)} -prove-asserts -set-init-zero -verify miter\n"
        )
    elif equiv_method == "induct":
        ys_script = (
            f"# gold = all golden sources\n"
            f"read_verilog -sv {gold_files}\n"
            f"hierarchy -top {top_module}; proc; opt; design -stash gold\n"
            f"# gate = clean deps + patched target\n"
            f"read_verilog -sv {gate_files}\n"
            f"hierarchy -top {top_module}; proc; opt; design -stash gate\n"
            f"design -copy-from gold -as gold {top_module}\n"
            f"design -copy-from gate -as gate {top_module}\n"
            f"equiv_make gold gate equiv; hierarchy -top equiv; "
            f"equiv_induct {equiv_config}; equiv_status -assert\n"
        )
    else:
        raise ValueError(f"unknown equiv_method {equiv_method!r}")

    ys_path = work_dir / "equiv_check.ys"
    log_path = work_dir / "equiv_check.log"
    ys_path.write_text(ys_script)

    # seq_miter 的 sat 在乘法器上可能爆炸/挂死(证反例尤甚) → 必须超时, 超时判 formal 失败(安全).
    timed_out = False
    try:
        proc = subprocess.run(
            [str(resolved_yosys), "-l", str(log_path), str(ys_path)],
            capture_output=True,
            text=True,
            timeout=equiv_timeout_sec,
        )
        returncode = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        returncode = 124

    log_text = (
        log_path.read_text(errors="ignore")
        if log_path.exists()
        else ""
    )

    counterexample_found = False
    counterexample_hash = ""
    if equiv_method == "seq_miter":
        ok = (not timed_out) and (returncode == 0) and (_SAT_PROOF_OK_MSG in log_text)
        counterexample_found = (
            not timed_out
            and returncode != 0
            and _SAT_COUNTEREXAMPLE_MSG in log_text
            and _SAT_PROOF_OK_MSG not in log_text
        )
        if counterexample_found:
            counterexample_hash = (
                "sha256:" + hashlib.sha256(log_text.encode("utf-8")).hexdigest()
            )
        parsed = {"proven": (1 if ok else 0), "unproven": (0 if ok else 1),
                  "total": 1, "asserted_ok": ok}
    else:
        parsed = _parse_equiv_log(log_text)
        if returncode != 0:
            parsed["asserted_ok"] = False

    return {
        "proven": parsed.get("proven"),
        "unproven": parsed.get("unproven"),
        "total": parsed.get("total"),
        "asserted_ok": parsed.get("asserted_ok", False),
        "yosys_exit": returncode,
        "timed_out": timed_out,
        "log_path": str(log_path),
        "counterexample_found": counterexample_found,
        "counterexample_hash": counterexample_hash,
    }


# ── Stage C: repair_one_case orchestration ────────────────────────────────────
# (appended; do not edit the functions above this line)

from dataclasses import dataclass


@dataclass
class RepairResult:
    success: bool
    patch: dict
    rationale: str
    model: str
    proven: int | None
    unproven: int | None
    total: int | None
    asserted_ok: bool
    yosys_exit: int | None
    equiv_log_path: str
    repaired_rtl_path: str
    old_identifier: str
    target_line: int
    new_identifier: str
    repair_mode: str = "identifier"
    start_line: int | None = None
    end_line: int | None = None


def repair_one_case(case_spec: dict, work_dir: Path) -> RepairResult:
    """
    Orchestrate full frontend semantic repair for one case.

    Stages: build_repair_prompt → propose_patch_via_llm → apply_patch → verify_equiv.
    All intermediate files land in work_dir/rtl and work_dir/equiv; no memory writes.
    RuntimeError from propose propagates unchanged.
    Equiv failure returns success=False RepairResult (not raised).
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    buggy_rtl      = Path(case_spec["buggy_rtl"])
    repair_mode    = case_spec.get("repair_mode", "identifier")
    # structural_timing 用 framed block + rework_request, 不读 error_log/context_ranges(可选).
    error_log      = Path(case_spec["error_log"]) if case_spec.get("error_log") else buggy_rtl
    target_line    = int(case_spec.get("target_line", case_spec.get("start_line", 0)))
    old_identifier = case_spec.get("old_identifier", "")
    context_ranges = case_spec.get("context_ranges", [])
    rework_request = case_spec.get("rework_request")
    golden_sources = [Path(p) for p in case_spec["golden_sources"]]
    deps           = [Path(p) for p in case_spec["deps"]]
    top_module     = case_spec["top_module"]
    expected_file  = case_spec.get("expected_file", buggy_rtl.name)
    block_bounds = None
    if repair_mode in ("block", "structural_timing"):
        if "block_bounds" in case_spec:
            block_bounds = tuple(int(v) for v in case_spec["block_bounds"])
        else:
            block_bounds = (
                int(case_spec["start_line"]),
                int(case_spec["end_line"]),
            )

    prompt = build_repair_prompt(
        buggy_rtl,
        error_log,
        target_line,
        old_identifier,
        context_ranges,
        repair_mode,
        block_bounds,
        rework_request=rework_request,
    )

    # propose may raise RuntimeError — let it propagate
    if repair_mode in ("block", "structural_timing"):
        propose_result = propose_patch_via_llm(
            prompt,
            target_line=target_line,
            old_identifier=old_identifier,
            expected_file=expected_file,
            repair_mode=repair_mode,
            block_bounds=block_bounds,
        )
    else:
        propose_result = propose_patch_via_llm(
            prompt,
            target_line=target_line,
            old_identifier=old_identifier,
            expected_file=expected_file,
        )
    patch     = propose_result["patch"]
    rationale = propose_result.get("rationale", "")
    model     = propose_result.get("model", "")

    if repair_mode in ("block", "structural_timing"):
        patched_rtl = apply_block_patch(buggy_rtl, patch, work_dir / "rtl")
    else:
        patched_rtl = apply_patch(buggy_rtl, patch, work_dir / "rtl")

    # equiv_method: retiming/时序变换 case 用 seq_miter(sat时序miter); 默认 induct(组合/标识符).
    equiv_result = verify_equiv(
        golden_sources=golden_sources,
        deps=deps,
        patched_target=patched_rtl,
        top_module=top_module,
        work_dir=work_dir / "equiv",
        equiv_method=case_spec.get("equiv_method", "induct"),
        seq_depth=int(case_spec.get("seq_depth", 12)),
        equiv_timeout_sec=int(case_spec.get("equiv_timeout_sec", 300)),
        abstract_mul=bool(case_spec.get("abstract_mul", True)),
        mask_bits=int(case_spec.get("mask_bits", 3)),
    )

    proven      = equiv_result["proven"]
    total       = equiv_result["total"]
    asserted_ok = equiv_result["asserted_ok"]
    success = bool(
        asserted_ok and proven is not None and proven == total and proven > 0
    )

    return RepairResult(
        success=success,
        patch=patch,
        rationale=rationale,
        model=model,
        proven=proven,
        unproven=equiv_result["unproven"],
        total=total,
        asserted_ok=asserted_ok,
        yosys_exit=equiv_result["yosys_exit"],
        equiv_log_path=equiv_result["log_path"],
        repaired_rtl_path=str(patched_rtl),
        old_identifier=old_identifier,
        target_line=target_line,
        new_identifier=patch.get("new_identifier", ""),
        repair_mode=repair_mode,
        start_line=patch.get("start_line"),
        end_line=patch.get("end_line"),
    )
