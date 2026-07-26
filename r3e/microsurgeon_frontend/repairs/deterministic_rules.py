import re
import shutil
from pathlib import Path
from typing import Dict, List, Tuple


LVAL_RE = re.compile(
    r"(?P<file>[^:\n]+):(?P<line>\d+):\s*error:\s*(?P<name>[A-Za-z_][A-Za-z0-9_$]*)\s+is not a valid l-value",
    re.I,
)


def copy_design_to_workspace(design_name: str, rtl_files: List[str], work_root: Path) -> Tuple[List[str], Dict[str, str]]:
    """
    Copy RTL files into a private repair workspace.
    Returns repaired RTL paths and original->new mapping.
    """
    dst_dir = work_root / design_name / "rtl"
    dst_dir.mkdir(parents=True, exist_ok=True)

    mapping = {}
    new_files = []

    for src in rtl_files:
        src_p = Path(src)
        dst_p = dst_dir / src_p.name

        # Avoid overwrite collision by prefixing parent directory if needed.
        if dst_p.exists() and src_p.resolve() != dst_p.resolve():
            dst_p = dst_dir / f"{src_p.parent.name}__{src_p.name}"

        shutil.copy2(src_p, dst_p)
        mapping[str(src_p)] = str(dst_p)
        new_files.append(str(dst_p))

    return new_files, mapping


def repair_qflexpress_trailing_formal_comma(path: Path) -> bool:
    """
    Fix pattern:
        localparam DW=32,
        `ifdef FORMAL
            , localparam F_...
        `endif
    into:
        localparam DW=32
        `ifdef FORMAL
            , localparam F_...
        `endif
    """
    txt = path.read_text(errors="ignore")
    old = txt

    txt = re.sub(
        r"(localparam\s+DW\s*=\s*32)\s*,\s*\n(\s*`ifdef\s+FORMAL)",
        r"\1\n\2",
        txt,
        flags=re.M,
    )

    if txt != old:
        path.write_text(txt)
        return True

    return False


def parse_wire_lvalue_names(stderr_text: str) -> List[str]:
    names = []
    seen = set()

    for m in LVAL_RE.finditer(stderr_text):
        n = m.group("name")
        if n not in seen:
            names.append(n)
            seen.add(n)

    return names


def repair_wire_to_reg_for_lvalues(path: Path, names: List[str]) -> int:
    """
    Conservative patch:
    only changes declarations for names explicitly reported by Icarus as:
      name is not a valid l-value
      name is declared here as wire

    Handles common forms:
      wire [W:0] a;
      wire a;
      wire [W:0] a, b, c;
    For multi-name declarations, changes whole declaration from wire to reg.
    This is acceptable for deterministic v0, because the names are assigned procedurally.
    """
    if not names:
        return 0

    txt = path.read_text(errors="ignore")
    old = txt
    name_alt = "|".join(re.escape(n) for n in names)

    # Change declaration lines containing one of the target names.
    def repl(m):
        line = m.group(0)
        if re.search(rf"\b({name_alt})\b", line):
            return re.sub(r"\bwire\b", "reg", line, count=1)
        return line

    txt = re.sub(
        r"^[ \t]*(?:wire)\b[^;\n]*;",
        repl,
        txt,
        flags=re.M,
    )

    if txt != old:
        path.write_text(txt)
        changed = 0
        for n in names:
            if re.search(rf"^\s*reg\b[^;\n]*\b{re.escape(n)}\b[^;\n]*;", txt, re.M):
                changed += 1
        return changed

    return 0


def stderr_points_to_missing_endmodule(stderr_text: str, path: Path) -> bool:
    if "syntax error" not in stderr_text:
        return False

    if "I give up." not in stderr_text:
        return False

    if "Invalid module item." in stderr_text:
        return False

    if re.search(r":\d+:\s+error:", stderr_text):
        return False

    line_count = len(path.read_text(errors="ignore").splitlines())
    syntax_lines = []

    for m in re.finditer(r":(?P<line>\d+):\s+syntax error", stderr_text):
        syntax_lines.append(int(m.group("line")))

    if not syntax_lines:
        return False

    return any(line_no >= line_count for line_no in syntax_lines)


def repair_append_missing_endmodule(path: Path) -> bool:
    old = path.read_text(errors="ignore")

    suffix = ""
    if old and not old.endswith("\n"):
        suffix += "\n"
    suffix += "endmodule\n"

    new = old + suffix
    if new == old:
        return False

    path.write_text(new)
    return True


def apply_rules(design_name: str, rtl_files: List[str], stderr_text: str, work_root: Path) -> Tuple[List[str], Dict]:
    """
    Copy design to workspace and apply deterministic repair rules.
    """
    new_files, mapping = copy_design_to_workspace(design_name, rtl_files, work_root)

    report = {
        "design_name": design_name,
        "rules_applied": [],
        "original_to_repaired": mapping,
    }

    # Rule 1: qflexpress parameter-list trailing comma.
    for f in new_files:
        p = Path(f)
        if p.name == "qflexpress.v":
            if repair_qflexpress_trailing_formal_comma(p):
                report["rules_applied"].append({
                    "rule": "repair_qflexpress_trailing_formal_comma",
                    "file": str(p),
                })

    # Rule 2: wire assigned in procedural block -> reg.
    lval_names = parse_wire_lvalue_names(stderr_text)
    if lval_names:
        for f in new_files:
            p = Path(f)
            changed = repair_wire_to_reg_for_lvalues(p, lval_names)
            if changed:
                report["rules_applied"].append({
                    "rule": "repair_wire_to_reg_for_lvalues",
                    "file": str(p),
                    "names": lval_names,
                    "changed_count": changed,
                })

    # Rule 3: missing final endmodule.
    if len(rtl_files) == 1 and len(new_files) == 1:
        p = Path(new_files[0])
        if stderr_points_to_missing_endmodule(stderr_text, p):
            if repair_append_missing_endmodule(p):
                report["rules_applied"].append({
                    "rule": "repair_append_missing_endmodule",
                    "file": str(p),
                })

    return new_files, report
