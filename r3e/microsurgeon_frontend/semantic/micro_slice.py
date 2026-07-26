import json
import re
from pathlib import Path


FILE_LINE_RE = re.compile(
    r"(?P<file>(?:/|(?:\./|\.\./)?[A-Za-z0-9_.-]+/)[^:\n]+?\.(?:v|sv|vh|svh)):(?P<line>\d+):\s*(?P<msg>[^\n]*)"
)

INCLUDE_NOT_FOUND_RE = re.compile(
    r"Include file\s+(?P<include>[A-Za-z0-9_./$+-]+\.(?:v|sv|vh|svh))\s+not found"
)

UNKNOWN_MODULE_RE = re.compile(
    r"Unknown module type:\s*(?P<module>[A-Za-z_][A-Za-z0-9_$]*)"
)

LVALUE_WIRE_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_$]*)\s+is not a valid l-value"
)

SYNTAX_ERROR_RE = re.compile(r"\bsyntax error\b", re.IGNORECASE)


def read_text(path):
    path = Path(path)
    if not path.exists():
        return ""
    return path.read_text(errors="ignore")


def collect_design_logs(log_root, design_name):
    root = Path(log_root) / design_name
    chunks = []

    for fname in [
        "iverilog.stderr.log",
        "iverilog.stdout.log",
        "yosys.stderr.log",
        "yosys.stdout.log",
    ]:
        p = root / fname
        if p.exists():
            chunks.append(f"\n===== {fname} =====\n")
            chunks.append(read_text(p))

    return "\n".join(chunks)


def classify_log_signature(log_text):
    if INCLUDE_NOT_FOUND_RE.search(log_text):
        return "SOURCE_INCOMPLETE_INCLUDE_NOT_FOUND"

    if UNKNOWN_MODULE_RE.search(log_text):
        return "MODULE_CLOSURE_MISSING_MODULE"

    if LVALUE_WIRE_RE.search(log_text):
        return "PROCEDURAL_ASSIGN_TO_WIRE"

    if SYNTAX_ERROR_RE.search(log_text):
        return "LOCAL_SYNTAX_ERROR"

    return "UNKNOWN_FRONTEND_FAILURE"


def extract_include_missing(log_text):
    out = []
    seen = set()

    for m in INCLUDE_NOT_FOUND_RE.finditer(log_text):
        inc = m.group("include")
        if inc not in seen:
            out.append(inc)
            seen.add(inc)

    return out


def extract_unknown_modules(log_text):
    out = []
    seen = set()

    for m in UNKNOWN_MODULE_RE.finditer(log_text):
        mod = m.group("module")
        if mod not in seen:
            out.append(mod)
            seen.add(mod)

    return out


def extract_lvalue_names(log_text):
    out = []
    seen = set()

    for m in LVALUE_WIRE_RE.finditer(log_text):
        name = m.group("name")
        if name not in seen:
            out.append(name)
            seen.add(name)

    return out


def extract_error_locations(log_text):
    locs = []
    seen = set()

    for m in FILE_LINE_RE.finditer(log_text):
        file_path = m.group("file")
        line_no = int(m.group("line"))
        msg = m.group("msg").strip()

        key = (file_path, line_no, msg)
        if key in seen:
            continue

        locs.append(
            {
                "file": file_path,
                "line": line_no,
                "message": msg,
            }
        )
        seen.add(key)

    return locs

def resolve_existing_path(path):
    p = Path(path)

    if p.exists():
        return p

    s = str(path)

    # Repair common regex truncation:
    # /e2e_orchestrator... should usually be experiments/e2e_orchestrator...
    if s.startswith("/e2e_orchestrator"):
        cand = Path("experiments" + s)
        if cand.exists():
            return cand

    # If an absolute-looking path was accidentally created from a relative path,
    # try relative to current repo root.
    cand = Path(s.lstrip("/"))
    if cand.exists():
        return cand

    return p

def slice_file(path, center_line, radius=20):
    path = resolve_existing_path(path)

    if not path.exists():
        return {
            "file": str(path),
            "exists": False,
            "center_line": center_line,
            "start_line": None,
            "end_line": None,
            "text": "",
        }

    lines = path.read_text(errors="ignore").splitlines()
    n = len(lines)

    start = max(1, center_line - radius)
    end = min(n, center_line + radius)

    out_lines = []
    for i in range(start, end + 1):
        out_lines.append(f"{i:6d}: {lines[i - 1]}")

    return {
        "file": str(path),
        "exists": True,
        "center_line": center_line,
        "start_line": start,
        "end_line": end,
        "text": "\n".join(out_lines),
    }


def build_micro_slices(log_text, radius=20, max_slices=8):
    locs = extract_error_locations(log_text)

    slices = []
    seen = set()

    for loc in locs:
        key = (loc["file"], loc["line"])
        if key in seen:
            continue

        sl = slice_file(loc["file"], loc["line"], radius=radius)
        sl["message"] = loc["message"]
        slices.append(sl)

        seen.add(key)

        if len(slices) >= max_slices:
            break

    return slices


def analyze_design_failure(log_root, design_name, radius=20):
    log_text = collect_design_logs(log_root, design_name)

    signature = classify_log_signature(log_text)

    return {
        "design_name": design_name,
        "signature": signature,
        "missing_includes": extract_include_missing(log_text),
        "missing_modules": extract_unknown_modules(log_text),
        "lvalue_wire_names": extract_lvalue_names(log_text),
        "error_locations": extract_error_locations(log_text),
        "micro_slices": build_micro_slices(log_text, radius=radius),
    }


def write_json(obj, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False))