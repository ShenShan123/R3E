"""Tolerant Verilog tokenization helpers shared by the knowledge package."""
from __future__ import annotations

import re

from r3e.knowledge.verilog_ast import (
    VerilogAstViolation,
    VerilogToken,
    tokenize_verilog,
)


VERILOG_KEYWORDS = frozenset({
    "always", "always_comb", "always_ff", "always_latch", "and", "assign",
    "automatic", "begin", "buf", "case", "casex", "casez", "default",
    "defparam", "disable", "edge", "else", "end", "endcase", "endfunction",
    "endgenerate", "endmodule", "endtask", "for", "forever", "function",
    "generate", "genvar", "if", "initial", "inout", "input", "integer",
    "localparam", "logic", "module", "nand", "negedge", "nor", "not", "or",
    "output", "parameter", "posedge", "real", "reg", "repeat", "signed",
    "task", "time", "tri", "unsigned", "wait", "while", "wire", "xor",
    "xnor", "bit", "byte", "int", "typedef", "enum", "struct", "packed",
    "unique", "priority",
})
COMPARISON_OPERATORS = frozenset({"<", "<=", ">", ">=", "==", "!=", "===", "!=="})
ARITHMETIC_OPERATORS = frozenset({"+", "-", "*", "/", "%", "**", "++", "--"})
LOGIC_OPERATORS = frozenset({"&", "|", "^", "~", "&&", "||", "!"})
SHIFT_OPERATORS = frozenset({"<<", ">>", "<<<", ">>>"})
RESET_NAME_RE = re.compile(
    r"^(n?rst|rst_?n|resetn?|reset_n|a?resetn?|arst_?n?|clr|clear)$",
    re.IGNORECASE,
)
TOKENIZER_VERSION = "verilog_source_tokens_v2_macros"
# A macro invocation is executable source, including at the start of a line.
# Only compiler directives are blanked; continuations belong to that directive.
_DIRECTIVES = (
    "begin_keywords", "end_keywords", "celldefine", "endcelldefine",
    "default_nettype", "define", "undef", "undefineall", "ifdef", "ifndef",
    "elsif", "else", "endif", "include", "line", "nounconnected_drive",
    "pragma", "resetall", "timescale", "unconnected_drive",
)
_DIRECTIVE_RE = re.compile(
    r"^[ \t]*`(?:" + "|".join(_DIRECTIVES) + r")(?![A-Za-z0-9_$])(?:[^\r\n]*\\\r?\n)*[^\r\n]*",
    re.MULTILINE,
)


def _directive_source(source: str) -> str:
    # Ignore directive-like text inside comments, preserving offsets and strings.
    return re.sub(r'"(?:\\.|[^"\\])*"|//[^\r\n]*|/\*[\s\S]*?\*/',
                  lambda m: m[0] if m[0].startswith('"') else
                  re.sub(r"[^\r\n]", " ", m[0]), source)


def compiler_directives(source: str) -> tuple[str, ...]:
    """Directive text without comments; macro invocations are not directives."""
    return tuple(m[0].strip() for m in _DIRECTIVE_RE.finditer(_directive_source(source)))


def strip_directives(source: str) -> str:
    """Blank out compiler directives while preserving character offsets."""
    masked = _directive_source(source)
    chars = list(source)
    for match in _DIRECTIVE_RE.finditer(masked):
        for i in range(match.start(), match.end()):
            # Keep comments intact, including ones crossing a directive's line.
            # Callers use the changed spans to identify directives, not comments.
            if masked[i] not in " \r\n":
                chars[i] = " "
    return "".join(chars)


_TOLERANT_RE = re.compile(
    r"(?P<whitespace>\s+)"
    r"|(?P<line_comment>//[^\r\n]*)"
    r"|(?P<block_comment>/\*.*?\*/)"
    r'|(?P<string>"(?:\\.|[^"\\])*")'
    r"|(?P<number>(?:\d+)?'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+|'[01xXzZ]|\d+(?:\.\d+)?)"
    r"|(?P<macro>`[A-Za-z_][A-Za-z0-9_$]*)"
    r"|(?P<identifier>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"|(?P<operator>===|!==|<<<|>>>|<=|>=|==|!=|&&|\|\||<<|>>|::|"
    r"\+\+|--|\*\*|[+\-*/%&|^~!<>=?:'])"
    r"|(?P<punctuation>[()\[\]{};,\.@#])",
    re.DOTALL,
)


def _tolerant_tokenize(source: str) -> list[VerilogToken]:
    """Superset of the frozen grammar: fill literals, casts and macro references.

    A macro name counts once; argument tokens remain separate. This is source
    accounting, not preprocessing or a claim that the macro is defined.
    """
    tokens = []
    offset = 0
    for match in _TOLERANT_RE.finditer(source):
        if match.start() != offset:
            return []
        offset = match.end()
        kind = str(match.lastgroup)
        if kind not in {"whitespace", "line_comment", "block_comment"}:
            tokens.append(VerilogToken(kind, match.group(), match.start(), match.end()))
    return tokens if offset == len(source) else []


def safe_tokenize(source: str) -> list[VerilogToken]:
    """Tokenize RTL; return [] when neither tokenizer can."""
    if not isinstance(source, str) or not source.strip():
        return []
    stripped = strip_directives(source)
    try:
        return tokenize_verilog(stripped)
    except VerilogAstViolation:
        return _tolerant_tokenize(stripped)


def is_identifier(token: VerilogToken) -> bool:
    return token.kind == "identifier" and token.value not in VERILOG_KEYWORDS


def line_of(source: str, offset: int) -> int:
    """Zero-based line index of a character offset."""
    return source.count("\n", 0, max(0, offset))


def operator_class(value: str) -> str:
    if value in COMPARISON_OPERATORS:
        return "comparison"
    if value in SHIFT_OPERATORS:
        return "shift"
    if value in ARITHMETIC_OPERATORS:
        return "arithmetic"
    if value in LOGIC_OPERATORS:
        return "logic"
    return "other"
