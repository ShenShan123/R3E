"""Verilog tokenizer shared by the knowledge package and the Red operators.

``tokenize_verilog`` turns source text into ``VerilogToken``s with exact
offsets, so structure analysis, diff classification and catalog edits all
work on the same tokens. It is not a full SystemVerilog frontend: Icarus
remains authoritative for parsing and elaboration.
"""
from __future__ import annotations

from dataclasses import dataclass
import re



COMPARISON_OPERATORS = {"<", "<=", ">", ">=", "==", "!="}
_TOKEN_RE = re.compile(
    r"(?P<whitespace>\s+)"
    r"|(?P<line_comment>//[^\r\n]*)"
    r"|(?P<block_comment>/\*.*?\*/)"
    r'|(?P<string>"(?:\\.|[^"\\])*")'
    r"|(?P<number>(?:\d+)?'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+|\d+)"
    r"|(?P<identifier>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"|(?P<operator>===|!==|<<<|>>>|<=|>=|==|!=|&&|\|\||<<|>>|"
    r"\+\+|--|\*\*|[+\-*/%&|^~!<>=?:])"
    r"|(?P<punctuation>[()\[\]{};,\.@#])",
    re.DOTALL,
)


class VerilogAstViolation(RuntimeError):
    """Raised when the constrained token AST cannot prove an exact edit."""


@dataclass(frozen=True)
class VerilogToken:
    kind: str
    value: str
    start: int
    end: int


def tokenize_verilog(source: str) -> list[VerilogToken]:
    tokens = []
    offset = 0
    for match in _TOKEN_RE.finditer(source):
        if match.start() != offset:
            snippet = source[offset:match.start()]
            raise VerilogAstViolation(
                f"unsupported Verilog token near: {snippet[:20]!r}"
            )
        offset = match.end()
        kind = str(match.lastgroup)
        if kind not in {"whitespace", "line_comment", "block_comment"}:
            tokens.append(
                VerilogToken(kind, match.group(), match.start(), match.end())
            )
    if offset != len(source):
        raise VerilogAstViolation(
            f"unsupported Verilog token near: {source[offset:offset + 20]!r}"
        )
    if not tokens:
        raise VerilogAstViolation("Verilog source has no semantic tokens")
    return tokens


def changed_token_count(clean: str, poison: str) -> int:
    clean_tokens = [(token.kind, token.value) for token in tokenize_verilog(clean)]
    poison_tokens = [
        (token.kind, token.value) for token in tokenize_verilog(poison)
    ]
    if len(clean_tokens) != len(poison_tokens):
        return max(len(clean_tokens), len(poison_tokens))
    return sum(
        clean_token != poison_token
        for clean_token, poison_token in zip(clean_tokens, poison_tokens)
    )
