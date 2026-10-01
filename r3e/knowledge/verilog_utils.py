"""Tolerant Verilog tokenization helpers shared by the knowledge package."""
from __future__ import annotations

import re

from r3e.red.grounded.verilog_ast import (
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
_DIRECTIVE_RE = re.compile(r"^[ \t]*`[^\r\n]*", re.MULTILINE)


def strip_directives(source: str) -> str:
    """Blank out compiler directives while preserving character offsets."""
    return _DIRECTIVE_RE.sub(lambda match: " " * len(match.group()), source)


def safe_tokenize(source: str) -> list[VerilogToken]:
    """Tokenize RTL; return [] when the constrained tokenizer cannot."""
    if not isinstance(source, str) or not source.strip():
        return []
    try:
        return tokenize_verilog(strip_directives(source))
    except VerilogAstViolation:
        return []


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
