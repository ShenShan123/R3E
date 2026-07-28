"""Small deterministic Verilog token AST for frozen Tier-1 operators.

This is not a full SystemVerilog frontend.  Icarus remains authoritative for
parse/elaboration.  The token AST provides exact, reconstructable node identity
and semantic edits for the explicitly supported comparator operator.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Iterable

from r3e.protocol.hashing import hash_payload


VERILOG_TOKEN_AST_VERSION = "r3e-verilog-token-ast-v1"
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


@dataclass(frozen=True)
class ComparisonNode:
    module: str
    ordinal: int
    operator: str
    left_token: str
    right_token: str
    operator_start: int
    operator_end: int

    @property
    def node_hash(self) -> str:
        return hash_payload({
            "schema_version": VERILOG_TOKEN_AST_VERSION,
            "node_type": "comparison_expression",
            "module": self.module,
            "ordinal": self.ordinal,
            "operator": self.operator,
            "left_token": self.left_token,
            "right_token": self.right_token,
        })


def source_hash(source: str) -> str:
    return "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest()


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


def normalized_ast_hash(source: str) -> str:
    return hash_payload({
        "schema_version": VERILOG_TOKEN_AST_VERSION,
        "tokens": [
            {"kind": token.kind, "value": token.value}
            for token in tokenize_verilog(source)
        ],
    })


def _module_ranges(tokens: list[VerilogToken]) -> list[tuple[str, int, int]]:
    ranges = []
    index = 0
    while index < len(tokens):
        if tokens[index].value != "module":
            index += 1
            continue
        if index + 1 >= len(tokens) or tokens[index + 1].kind != "identifier":
            raise VerilogAstViolation("module declaration lacks an identifier")
        name = tokens[index + 1].value
        end = next(
            (
                cursor for cursor in range(index + 2, len(tokens))
                if tokens[cursor].value == "endmodule"
            ),
            None,
        )
        if end is None:
            raise VerilogAstViolation(f"module is missing endmodule: {name}")
        ranges.append((name, index, end))
        index = end + 1
    if not ranges:
        raise VerilogAstViolation("no Verilog module was parsed")
    return ranges


def comparison_nodes(
    source: str,
    *,
    module: str,
) -> list[ComparisonNode]:
    tokens = tokenize_verilog(source)
    module_range = next(
        (item for item in _module_ranges(tokens) if item[0] == module),
        None,
    )
    if module_range is None:
        raise VerilogAstViolation(f"target module is unavailable: {module}")
    _, start, end = module_range
    nodes = []
    for cursor in range(start + 1, end):
        token = tokens[cursor]
        if token.value not in COMPARISON_OPERATORS:
            continue
        if cursor == 0 or cursor + 1 >= len(tokens):
            raise VerilogAstViolation("comparison operator lacks operands")
        left = tokens[cursor - 1]
        right = tokens[cursor + 1]
        if left.value in {"(", "[", "{", ",", ";"} or right.value in {
            ")",
            "]",
            "}",
            ",",
            ";",
        }:
            raise VerilogAstViolation(
                "Tier-1 comparator requires adjacent scalar operands"
            )
        nodes.append(
            ComparisonNode(
                module=module,
                ordinal=len(nodes),
                operator=token.value,
                left_token=left.value,
                right_token=right.value,
                operator_start=token.start,
                operator_end=token.end,
            )
        )
    return nodes


def require_comparison_node(
    source: str,
    *,
    module: str,
    node_hash: str,
) -> ComparisonNode:
    matches = [
        node for node in comparison_nodes(source, module=module)
        if node.node_hash == node_hash
    ]
    if len(matches) != 1:
        raise VerilogAstViolation(
            "target comparator AST node is missing or ambiguous"
        )
    return matches[0]


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
