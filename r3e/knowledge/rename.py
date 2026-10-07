"""Rename a design's internal signals and drop its comments (behaviour unchanged).

Used to test whether a repair model relies on remembering a public design's
source text: the same bug, with the same ports and the same behaviour, but
without its original internal names and comments.

Renamed: identifiers declared inside the module as ``reg``, ``wire``,
``logic``, ``integer``, ``genvar``, ``bit`` or ``int``. Kept: ports,
parameters, module and instance names, named port connections (``.port``),
keywords, and anything on a compiler-directive line. Comments are removed.
Callers must check behaviour by simulation (the visible trace of the renamed
design must equal the original's).
"""
from __future__ import annotations

from .structure import analyze_rtl
from .verilog_utils import _TOLERANT_RE, VERILOG_KEYWORDS, strip_directives

DECLARATION_KEYWORDS = {"reg", "wire", "logic", "integer", "genvar", "bit", "int"}
PORT_KEYWORDS = {"input", "output", "inout"}


def _tokens(source: str) -> list[tuple[str, str, int, int]]:
    return [(m.lastgroup or "", m.group(), m.start(), m.end()) for m in _TOLERANT_RE.finditer(source)]


def rename_internal_signals(source: str, *, prefix: str = "n") -> tuple[str, dict[str, str]]:
    """Return ``(renamed source, {old: new})``."""
    structure = analyze_rtl(source)
    kept = structure.inputs | structure.outputs | structure.parameters
    toks = _tokens(source)
    code = [t for t in toks if t[0] not in {"whitespace", "line_comment", "block_comment"}]
    directive = strip_directives(source)
    in_directive = lambda start: directive[start] == " " and source[start] != " "

    declared: list[str] = []
    for i, (kind, value, start, _) in enumerate(code):
        if value not in DECLARATION_KEYWORDS or in_directive(start):
            continue
        previous = code[i - 1][1] if i else ""
        if previous in PORT_KEYWORDS:  # "output reg q": a port, kept
            continue
        depth, j = 0, i + 1
        while j < len(code) and not (code[j][1] == ";" and depth == 0) and code[j][1] != ")":
            k, v = code[j][0], code[j][1]
            depth += v in "[({"
            depth -= v in "])}" and depth > 0
            nxt = code[j + 1][1] if j + 1 < len(code) else ""
            if (k == "identifier" and depth == 0 and v not in VERILOG_KEYWORDS and v not in kept
                    and nxt in {",", ";", "=", "["} and v not in declared):
                declared.append(v)
            j += 1

    used = {t[1] for t in toks if t[0] == "identifier"}
    mapping, counter = {}, 0
    for name in declared:
        counter += 1
        while f"{prefix}{counter}" in used:
            counter += 1
        mapping[name] = f"{prefix}{counter}"

    out, previous = [], ""
    for kind, value, start, end in toks:
        if kind in {"line_comment", "block_comment"} and not in_directive(start):
            out.append("\n" * value.count("\n"))
            continue
        if kind == "identifier" and value in mapping and previous != "." and not in_directive(start):
            out.append(mapping[value])
        else:
            out.append(value)
        if kind not in {"whitespace", "line_comment", "block_comment"}:
            previous = value
    renamed = "".join(out)
    if len(renamed) == 0 or "".join(t[1] for t in toks) != source:
        raise ValueError("source could not be tokenized completely; refusing to rename")
    return renamed, mapping
