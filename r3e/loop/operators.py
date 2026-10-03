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
from typing import Any, Mapping

from r3e.knowledge.verilog_ast import VerilogToken

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
    "logic_width_swap": "swap a logical operator with its bitwise twin (&& <-> &, || <-> |); differs only on multi-bit operands",
    "ternary_swap": "swap the two result branches of a ?: expression",
    "concat_swap": "reorder the two parts of a {a, b} concatenation",
    "condition_force": "replace an if condition with a constant (always or never taken)",
    "case_label_replace": "change the label of one case item to another label of the same case",
    "unary_drop": "remove a bitwise or logical inversion (~ or !) in front of an operand",
}
_COMPARE = {"<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "=="}
_ARITH = {"+": "-", "-": "+"}
_LOGIC = {"&": "|", "|": "&", "&&": "||", "||": "&&"}
_LOGIC_WIDTH = {"&&": "&", "||": "|", "&": "&&", "|": "||"}
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
    replacements: tuple[tuple[str, str], ...] = ()  # option -> exact replacement text

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


def _expr_end(tokens: list[VerilogToken], start: int) -> int:
    """Index one past the end of an expression starting at ``start``."""
    depth = 0
    cursor = start
    while cursor < len(tokens):
        value = tokens[cursor].value
        if value in {"(", "[", "{"}:
            depth += 1
        elif value in {")", "]", "}"}:
            if depth == 0:
                return cursor
            depth -= 1
        elif depth == 0 and value in {";", ",", ":", "?"}:
            return cursor
        cursor += 1
    return cursor


def _ternary_parts(tokens: list[VerilogToken], q: int) -> tuple[int, int, int, int] | None:
    """Token index spans (t0, t1, f0, f1) of the true/false branches of ``?`` at q."""
    depth = 0
    cursor = q + 1
    while cursor < len(tokens):
        value = tokens[cursor].value
        if value in {"(", "[", "{"}:
            depth += 1
        elif value in {")", "]", "}"}:
            if depth == 0:
                return None
            depth -= 1
        elif depth == 0 and value == "?":
            return None  # nested ternary in the true branch: skip
        elif depth == 0 and value == ":":
            f1 = _expr_end(tokens, cursor + 1)
            if f1 <= cursor + 1 or q + 1 >= cursor:
                return None
            return q + 1, cursor, cursor + 1, f1
        elif depth == 0 and value == ";":
            return None
        cursor += 1
    return None


def enumerate_sites(carrier: Carrier) -> list[Site]:
    source = carrier.clean_rtl
    tokens = safe_tokenize(source)
    lines = source.splitlines()
    identifiers = sorted({t.value for t in tokens if is_identifier(t)})
    sites: list[Site] = []
    in_decl = False

    def add(op: str, tok: VerilogToken, options: tuple[str, ...], end: int | None = None,
            replacements: tuple[tuple[str, str], ...] = ()) -> None:
        if not options:
            return
        line = line_of(source, tok.start)
        # stable id: operator and source offset, so adding catalog operators
        # never renumbers the sites recorded in earlier runs
        sites.append(Site(f"{op}@{tok.start}", op, tok.start, end if end is not None else tok.end,
                          line, lines[line].strip()[:160], options, replacements))

    text = lambda a, b: source[tokens[a].start:tokens[b - 1].end]
    case_labels = _case_labels(tokens)

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
                add("logic_width_swap", tok, (_LOGIC_WIDTH[value],))
            elif value in _SHIFT:
                add("shift_swap", tok, (_SHIFT[value],))
            if value in {"=", "<="} and _is_assignment(tokens, index) and not in_decl:
                add("assign_kind_swap", tok, ("<=" if value == "=" else "=",))
            if value in {"~", "!"} and index + 1 < len(tokens) and (
                    is_identifier(tokens[index + 1]) or tokens[index + 1].value == "("):
                add("unary_drop", tok, ("drop",), replacements=(("drop", ""),))
        elif tok.kind == "number" and prev not in {"#"} and not in_decl:
            add("constant_shift", tok, _number_options(value))
        elif value == "if" and index + 1 < len(tokens) and tokens[index + 1].value == "(":
            close = _matching(tokens, index + 1)
            if close is not None:
                add("condition_negate", tokens[index + 1], ("negate",), end=tokens[close].end)
                add("condition_force", tokens[index + 1], ("always", "never"), end=tokens[close].end,
                    replacements=(("always", "(1'b1)"), ("never", "(1'b0)")))
        elif is_identifier(tok) and _in_assignment_rhs(tokens, index):
            others = tuple(n for n in identifiers if n != value)[:6]
            add("operand_replace", tok, others)
        if value == "?" and not in_decl:
            parts = _ternary_parts(tokens, index)
            if parts:
                t0, t1, f0, f1 = parts
                swapped = f"{text(f0, f1)} : {text(t0, t1)}"
                add("ternary_swap", tokens[t0], ("swap",), end=tokens[f1 - 1].end,
                    replacements=(("swap", swapped),))
        if value == "{" and not in_decl:
            close = _matching_brace(tokens, index)
            # exactly one top-level comma: {a, b}; replications {n{x}} have none
            commas = _top_level_commas(tokens, index, close) if close else []
            if close and len(commas) == 1:
                a, b = text(index + 1, commas[0]), text(commas[0] + 1, close)
                add("concat_swap", tokens[index + 1], ("swap",), end=tokens[close - 1].end,
                    replacements=(("swap", f"{b}, {a}"),))
        if index in case_labels:
            others = tuple(lbl for lbl in case_labels[index] if lbl != value)[:4]
            add("case_label_replace", tok, others)
    return sites[:MAX_SITES]


def _matching_brace(tokens: list[VerilogToken], index: int) -> int | None:
    depth = 0
    for cursor in range(index, len(tokens)):
        if tokens[cursor].value == "{":
            depth += 1
        elif tokens[cursor].value == "}":
            depth -= 1
            if depth == 0:
                return cursor
    return None


def _top_level_commas(tokens: list[VerilogToken], start: int, end: int) -> list[int]:
    depth, out = 0, []
    for cursor in range(start + 1, end):
        value = tokens[cursor].value
        if value in {"(", "[", "{"}:
            depth += 1
        elif value in {")", "]", "}"}:
            depth -= 1
        elif value == "," and depth == 0:
            out.append(cursor)
    return out


def _case_labels(tokens: list[VerilogToken]) -> dict[int, tuple[str, ...]]:
    """Map label-token index -> all single-token labels of the same case statement."""
    out: dict[int, tuple[str, ...]] = {}
    index = 0
    while index < len(tokens):
        if tokens[index].value in {"case", "casex", "casez"} and index + 1 < len(tokens) \
                and tokens[index + 1].value == "(":
            close = _matching(tokens, index + 1)
            if close is None:
                break
            depth, labels, positions = 0, [], []
            cursor = close + 1
            while cursor < len(tokens) and tokens[cursor].value != "endcase":
                value = tokens[cursor].value
                if value in {"begin", "case", "casex", "casez"}:
                    depth += 1
                elif value in {"end", "endcase"}:
                    depth -= 1
                prev = tokens[cursor - 1].value
                if (depth == 0 and cursor + 1 < len(tokens) and tokens[cursor + 1].value == ":"
                        and value != "default" and tokens[cursor].kind in {"number", "identifier"}
                        and prev in {";", "end", ")"}):
                    labels.append(value)
                    positions.append(cursor)
                cursor += 1
            if len(set(labels)) >= 2:
                for pos in positions:
                    out[pos] = tuple(dict.fromkeys(labels))
            index = cursor
        index += 1
    return out


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


def _replacement(source: str, site: Site, option: str) -> str:
    if option not in site.options:
        raise ValueError("option is not offered at this site")
    mapped = dict(site.replacements)
    if option in mapped:
        return mapped[option]
    if site.operator == "condition_negate":
        return f"(!{source[site.start:site.end]})"
    return option


def apply_edits(carrier: Carrier, edits: list[tuple[Site, str]]) -> str:
    """Apply several non-overlapping edits to the clean carrier (a compound bug)."""
    ordered = sorted(edits, key=lambda e: e[0].start)
    for (a, _), (b, _) in zip(ordered, ordered[1:]):
        if b.start < a.end:
            raise ValueError("edits overlap")
    source = carrier.clean_rtl
    for site, option in reversed(ordered):
        source = source[:site.start] + _replacement(carrier.clean_rtl, site, option) + source[site.end:]
    return source


def resolve_recorded_edits(carrier: Carrier, chosen: Mapping[str, Any],
                           expected_hash: str | None = None) -> list[tuple[Site, str]]:
    """Sites and options of a recorded Red choice, for rebuilding its bug.

    Records from before stable site ids (positional ``S<n>``) are resolved
    from their recorded edit text (operator, line, snippet, option). When a
    line holds several identical candidates, ``expected_hash`` (the recorded
    mutant hash) picks the combination that reproduces the bug.
    """
    import itertools

    from r3e.protocol.hashing import hash_payload

    sites = enumerate_sites(carrier)
    by_id = {s.site_id: s for s in sites}
    edits = list(chosen.get("edits") or [])
    if all(e.get("site_id") in by_id for e in edits):
        return [(by_id[e["site_id"]], str(e["option"])) for e in edits]
    texts = list(chosen.get("edit_text") or [])
    if len(texts) != len(edits):
        raise ValueError("recorded edits cannot be resolved: no edit text")
    options = []
    for edit, text in zip(edits, texts):
        match = [s for s in sites if s.operator == text.get("operator") and s.line + 1 == text.get("line")
                 and s.snippet == text.get("snippet") and str(edit["option"]) in s.options]
        if not match:
            raise ValueError(f"recorded edit at line {text.get('line')} has no matching site")
        options.append([(m, str(edit["option"])) for m in match])
    for combo in itertools.product(*options):
        if len({site.site_id for site, _ in combo}) != len(combo):
            continue
        if expected_hash is None and all(len(o) == 1 for o in options):
            return list(combo)
        try:
            if expected_hash is not None and hash_payload(apply_edits(carrier, list(combo))) == expected_hash:
                return list(combo)
        except ValueError:
            continue
    raise ValueError("recorded edits are ambiguous and no expected hash resolves them")
