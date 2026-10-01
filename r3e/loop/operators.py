"""Small library of legal RTL edits used to normalize Red's proposals.

Red states a weakness hypothesis and chooses an operator, a site and an option
from this catalog. The library applies the edit on token boundaries, so every
proposal is well-formed text; whether it is a *valid challenge* is decided
afterwards by the admission gate.

Operator names describe *edit kinds*, not bug families. They are Red's action
space only and are never shown to Blue.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from r3e.red.grounded.verilog_ast import VerilogToken

from .corpus import Carrier
from r3e.knowledge.verilog_utils import is_identifier, line_of, safe_tokenize


CATALOG = {
    "compare_swap": "replace a comparison operator (e.g. < with <=, == with !=)",
    "arith_swap": "replace + with - or - with +",
    "logic_swap": "replace & with |, | with &, && with ||, || with &&",
    "shift_swap": "replace << with >> or >> with <<",
    "constant_shift": "add +1 or -1 to an integer literal",
    "condition_negate": "negate the condition of an if statement",
    "assign_kind_swap": "turn a nonblocking assignment into a blocking one or vice versa",
    "operand_replace": "replace a right-hand-side signal with another signal of the module",
}
_COMPARE = {"<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "=="}
_ARITH = {"+": "-", "-": "+"}
_LOGIC = {"&": "|", "|": "&", "&&": "||", "||": "&&"}
_SHIFT = {"<<": ">>", ">>": "<<"}
_STATEMENT_START = {";", "begin", "else", ")", ":", "end", "assign"}
MAX_SITES = 80


@dataclass(frozen=True)
class Site:
    site_id: str
    operator: str
    start: int
    end: int
    line: int
    snippet: str
    options: tuple[str, ...]

    def public(self) -> dict[str, Any]:
        return {"site_id": self.site_id, "operator": self.operator, "line": self.line + 1,
                "snippet": self.snippet, "options": list(self.options)}


def _in_assignment_rhs(tokens: list[VerilogToken], index: int) -> bool:
    cursor = index - 1
    depth = 0
    while cursor >= 0 and tokens[cursor].value != ";":
        value = tokens[cursor].value
        if value == ")":
            depth += 1
        elif value == "(":
            depth -= 1
        elif value in {"=", "<="} and depth == 0:
            return True
        elif value in {"begin", "end"}:
            return False
        cursor -= 1
    return False


def _number_options(text: str) -> tuple[str, ...]:
    if "'" in text:
        width, rest = text.split("'", 1)
        base = rest[0].lower() if rest else ""
        digits = rest[1:].replace("_", "")
        radix = {"b": 2, "o": 8, "d": 10, "h": 16}.get(base)
        if radix is None or any(c in "xXzZ?" for c in digits):
            return ()
        if not digits:
            return ()
        value = int(digits, radix)
        fmt = {2: "b", 8: "o", 10: "d", 16: "x"}[radix]
        out = []
        for delta in (1, -1):
            new = value + delta
            if new < 0 or (width and new >= 2 ** int(width)):
                continue
            out.append(f"{width}'{rest[0]}{format(new, fmt)}")
        return tuple(out)
    value = int(text)
    return tuple(str(v) for v in (value + 1, value - 1) if v >= 0)


def enumerate_sites(carrier: Carrier) -> list[Site]:
    source = carrier.clean_rtl
    tokens = safe_tokenize(source)
    lines = source.splitlines()
    identifiers = sorted({t.value for t in tokens if is_identifier(t)})
    sites: list[Site] = []
    in_decl = False

    def add(op: str, tok: VerilogToken, options: tuple[str, ...], end: int | None = None) -> None:
        if not options:
            return
        line = line_of(source, tok.start)
        sites.append(Site(f"S{len(sites)}", op, tok.start, end if end is not None else tok.end,
                          line, lines[line].strip()[:160], options))

    for index, tok in enumerate(tokens):
        value = tok.value
        prev = tokens[index - 1].value if index else ";"
        if value in {"input", "output", "inout", "wire", "reg", "parameter", "localparam", "integer"}:
            in_decl = True
        elif value == ";":
            in_decl = False
        if in_decl and value not in {"parameter", "localparam"}:
            continue
        if tok.kind == "operator":
            if value in _COMPARE and not (value == "<=" and _is_assignment(tokens, index)):
                add("compare_swap", tok, (_COMPARE[value],))
            elif value in _ARITH and prev not in {"(", "=", "<=", ",", "["}:
                add("arith_swap", tok, (_ARITH[value],))
            elif value in _LOGIC:
                add("logic_swap", tok, (_LOGIC[value],))
            elif value in _SHIFT:
                add("shift_swap", tok, (_SHIFT[value],))
            if value in {"=", "<="} and _is_assignment(tokens, index) and not in_decl:
                add("assign_kind_swap", tok, ("<=" if value == "=" else "=",))
        elif tok.kind == "number" and prev not in {"#"} and not in_decl:
            add("constant_shift", tok, _number_options(value))
        elif value == "if" and index + 1 < len(tokens) and tokens[index + 1].value == "(":
            close = _matching(tokens, index + 1)
            if close is not None:
                add("condition_negate", tokens[index + 1], ("negate",), end=tokens[close].end)
        elif is_identifier(tok) and _in_assignment_rhs(tokens, index):
            others = tuple(n for n in identifiers if n != value)[:6]
            add("operand_replace", tok, others)
    return sites[:MAX_SITES]


def _is_assignment(tokens: list[VerilogToken], index: int) -> bool:
    cursor = index - 1
    if cursor >= 0 and tokens[cursor].value == "]":
        depth = 0
        while cursor >= 0:
            if tokens[cursor].value == "]":
                depth += 1
            elif tokens[cursor].value == "[":
                depth -= 1
                if depth == 0:
                    cursor -= 1
                    break
            cursor -= 1
    if cursor < 0 or not is_identifier(tokens[cursor]):
        return False
    before = tokens[cursor - 1].value if cursor else ";"
    return before in _STATEMENT_START


def _matching(tokens: list[VerilogToken], index: int) -> int | None:
    depth = 0
    for cursor in range(index, len(tokens)):
        if tokens[cursor].value == "(":
            depth += 1
        elif tokens[cursor].value == ")":
            depth -= 1
            if depth == 0:
                return cursor
    return None


def apply_edit(carrier: Carrier, site: Site, option: str) -> str:
    if option not in site.options:
        raise ValueError("option is not offered at this site")
    source = carrier.clean_rtl
    original = source[site.start:site.end]
    if site.operator == "condition_negate":
        replacement = f"(!{original})"
    else:
        replacement = option
    return source[:site.start] + replacement + source[site.end:]
