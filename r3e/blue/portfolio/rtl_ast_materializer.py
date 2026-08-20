"""Deterministic runner-owned semantic patches from RTL token-AST diffs."""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from r3e.protocol.hashing import hash_payload
from r3e.red.grounded.verilog_ast import (
    VerilogAstViolation,
    VerilogToken,
    normalized_ast_hash,
    tokenize_verilog,
)


AST_SEMANTIC_PATCH_SCHEMA = "r3e-runner-rtl-ast-semantic-patch-v1"
AST_REJECTION_PATCH_SCHEMA = "r3e-runner-rtl-ast-rejection-patch-v1"
MAX_AST_SOURCE_BYTES = 1_000_000
MAX_AST_TOKENS = 20_000
MAX_CHANGED_TOKENS = 4_096

_BLOCK_KEYWORDS = {
    "always",
    "always_comb",
    "always_ff",
    "always_latch",
    "assign",
    "case",
    "casex",
    "casez",
    "for",
    "generate",
    "if",
}
_DIRECTIONS = {"input", "output", "inout"}
_DECLARATION_QUALIFIERS = {
    "automatic",
    "integer",
    "logic",
    "reg",
    "signed",
    "supply0",
    "supply1",
    "tri",
    "unsigned",
    "var",
    "wire",
}
_VERILOG_KEYWORDS = _BLOCK_KEYWORDS | _DIRECTIONS | _DECLARATION_QUALIFIERS | {
    "begin",
    "default",
    "else",
    "end",
    "endcase",
    "endgenerate",
    "endmodule",
    "module",
    "or",
}


class RtlAstMaterializationViolation(RuntimeError):
    """Raised when a bounded semantic patch cannot be derived exactly."""


def _module_ranges(
    tokens: list[VerilogToken],
) -> list[tuple[str, int, int]]:
    ranges: list[tuple[str, int, int]] = []
    index = 0
    while index < len(tokens):
        if tokens[index].value != "module":
            index += 1
            continue
        if index + 1 >= len(tokens) or tokens[index + 1].kind != "identifier":
            raise RtlAstMaterializationViolation(
                "module declaration lacks an identifier"
            )
        name = tokens[index + 1].value
        end = next(
            (
                cursor
                for cursor in range(index + 2, len(tokens))
                if tokens[cursor].value == "endmodule"
            ),
            None,
        )
        if end is None:
            raise RtlAstMaterializationViolation(
                f"module is missing endmodule: {name}"
            )
        ranges.append((name, index, end))
        index = end + 1
    if not ranges:
        raise RtlAstMaterializationViolation("no RTL module was parsed")
    names = [name for name, _start, _end in ranges]
    if len(names) != len(set(names)):
        raise RtlAstMaterializationViolation(
            "duplicate module declarations are ambiguous"
        )
    return ranges


def _module_at(
    ranges: list[tuple[str, int, int]],
    index: int,
) -> str:
    for name, start, end in ranges:
        if start <= index <= end:
            return name
    return ""


def _representative_index(
    tokens: list[VerilogToken],
    start: int,
    end: int,
) -> int:
    if not tokens:
        return 0
    if start < end:
        return min(start, len(tokens) - 1)
    return min(max(start - 1, 0), len(tokens) - 1)


def _block_at(
    tokens: list[VerilogToken],
    ranges: list[tuple[str, int, int]],
    index: int,
) -> str:
    module_range = next(
        (
            (name, start, end)
            for name, start, end in ranges
            if start <= index <= end
        ),
        None,
    )
    if module_range is None:
        return ""
    module, start, _end = module_range
    keyword_index = next(
        (
            cursor
            for cursor in range(index, start, -1)
            if tokens[cursor].value in _BLOCK_KEYWORDS
        ),
        None,
    )
    if keyword_index is None:
        return f"module:{module}/module_scope:0"
    keyword = tokens[keyword_index].value
    ordinal = sum(
        tokens[cursor].value == keyword
        for cursor in range(start, keyword_index)
    )
    return f"module:{module}/{keyword}:{ordinal}"


def _declared_signal_roles(
    tokens: list[VerilogToken],
) -> dict[str, str]:
    roles: dict[str, str] = {}
    active_direction = ""
    for token in tokens:
        value = token.value
        if value in _DIRECTIONS:
            active_direction = value
            continue
        if active_direction and value in {";", ")"}:
            active_direction = ""
            continue
        if active_direction and value in _DIRECTIONS:
            active_direction = value
            continue
        if (
            active_direction
            and token.kind == "identifier"
            and value not in _DECLARATION_QUALIFIERS
            and value not in _VERILOG_KEYWORDS
        ):
            roles[value] = {
                "input": "primary_input",
                "output": "observable_output",
                "inout": "bidirectional_signal",
            }[active_direction]
    return roles


def _context_signal_roles(
    tokens: list[VerilogToken],
    start: int,
    end: int,
) -> set[str]:
    roles = _declared_signal_roles(tokens)
    left = max(0, start - 3)
    right = min(len(tokens), max(end, start + 1) + 3)
    observed: set[str] = set()
    for token in tokens[left:right]:
        if token.kind != "identifier" or token.value in _VERILOG_KEYWORDS:
            continue
        observed.add(roles.get(token.value, "internal_signal"))
    return observed


def _operator_class(
    tag: str,
    before: list[VerilogToken],
    after: list[VerilogToken],
) -> str:
    if tag == "insert":
        return "token_sequence_insertion"
    if tag == "delete":
        return "token_sequence_deletion"
    kinds = {token.kind for token in before + after}
    if kinds == {"number"}:
        return "constant_replacement"
    if kinds == {"operator"}:
        return "operator_replacement"
    if kinds == {"identifier"}:
        return "identifier_replacement"
    return "expression_replacement"


def _normalized_tokens(tokens: list[VerilogToken]) -> list[dict[str, str]]:
    return [
        {"kind": token.kind, "value": token.value}
        for token in tokens
    ]


def materialize_rtl_ast_semantic_patch(
    *,
    buggy_source: str,
    candidate_source: str,
    expected_patch_scope: str,
    top_module: str = "",
) -> dict[str, Any]:
    """Derive one bounded, reconstructable semantic patch from two RTL ASTs."""
    if not isinstance(buggy_source, str) or not buggy_source:
        raise RtlAstMaterializationViolation(
            "buggy RTL source must be non-empty"
        )
    if not isinstance(candidate_source, str) or not candidate_source:
        raise RtlAstMaterializationViolation(
            "candidate RTL source must be non-empty"
        )
    if not isinstance(expected_patch_scope, str) or not expected_patch_scope:
        raise RtlAstMaterializationViolation(
            "expected patch scope must be non-empty"
        )
    if (
        len(buggy_source.encode("utf-8")) > MAX_AST_SOURCE_BYTES
        or len(candidate_source.encode("utf-8")) > MAX_AST_SOURCE_BYTES
    ):
        raise RtlAstMaterializationViolation(
            "RTL source exceeds AST materialization budget"
        )
    try:
        buggy_tokens = tokenize_verilog(buggy_source)
        candidate_tokens = tokenize_verilog(candidate_source)
        buggy_ast_hash = normalized_ast_hash(buggy_source)
        candidate_ast_hash = normalized_ast_hash(candidate_source)
    except VerilogAstViolation as exc:
        raise RtlAstMaterializationViolation(
            "RTL token-AST parsing failed"
        ) from exc
    if (
        len(buggy_tokens) > MAX_AST_TOKENS
        or len(candidate_tokens) > MAX_AST_TOKENS
    ):
        raise RtlAstMaterializationViolation(
            "RTL token count exceeds AST materialization budget"
        )
    buggy_ranges = _module_ranges(buggy_tokens)
    candidate_ranges = _module_ranges(candidate_tokens)
    if top_module and (
        top_module not in {name for name, _start, _end in buggy_ranges}
        or top_module
        not in {name for name, _start, _end in candidate_ranges}
    ):
        raise RtlAstMaterializationViolation(
            "top module is absent from an RTL artifact"
        )
    matcher = SequenceMatcher(
        a=[(token.kind, token.value) for token in buggy_tokens],
        b=[(token.kind, token.value) for token in candidate_tokens],
        autojunk=False,
    )
    opcodes = [
        opcode for opcode in matcher.get_opcodes()
        if opcode[0] != "equal"
    ]
    if not opcodes:
        raise RtlAstMaterializationViolation(
            "candidate RTL has no semantic token change"
        )
    changed_token_count = sum(
        max(old_end - old_start, new_end - new_start)
        for _tag, old_start, old_end, new_start, new_end in opcodes
    )
    if changed_token_count > MAX_CHANGED_TOKENS:
        raise RtlAstMaterializationViolation(
            "candidate AST diff exceeds materialization budget"
        )

    changed_modules: set[str] = set()
    changed_blocks: set[str] = set()
    changed_nodes: list[str] = []
    changed_roles: set[str] = set()
    operator_classes: set[str] = set()
    normalized_operations: list[dict[str, Any]] = []
    for ordinal, (
        tag,
        old_start,
        old_end,
        new_start,
        new_end,
    ) in enumerate(opcodes):
        old_index = _representative_index(
            buggy_tokens, old_start, old_end
        )
        new_index = _representative_index(
            candidate_tokens, new_start, new_end
        )
        old_module = _module_at(buggy_ranges, old_index)
        new_module = _module_at(candidate_ranges, new_index)
        changed_modules.update(
            module for module in (old_module, new_module) if module
        )
        old_block = _block_at(buggy_tokens, buggy_ranges, old_index)
        new_block = _block_at(
            candidate_tokens, candidate_ranges, new_index
        )
        changed_blocks.update(
            block for block in (old_block, new_block) if block
        )
        block_identity = new_block or old_block or "outside_module"
        changed_nodes.append(
            f"{block_identity}/token_diff:{ordinal}:{tag}"
        )
        changed_roles.update(
            _context_signal_roles(buggy_tokens, old_start, old_end)
        )
        changed_roles.update(
            _context_signal_roles(
                candidate_tokens, new_start, new_end
            )
        )
        before = buggy_tokens[old_start:old_end]
        after = candidate_tokens[new_start:new_end]
        operator_classes.add(_operator_class(tag, before, after))
        normalized_operations.append({
            "kind": tag,
            "old_token_span": [old_start, old_end],
            "new_token_span": [new_start, new_end],
            "before": _normalized_tokens(before),
            "after": _normalized_tokens(after),
            "old_module": old_module,
            "new_module": new_module,
            "old_block": old_block,
            "new_block": new_block,
        })
    if not changed_modules or not changed_blocks:
        raise RtlAstMaterializationViolation(
            "AST diff cannot be bound to a module and block"
        )
    normalized_patch = {
        "schema_version": AST_SEMANTIC_PATCH_SCHEMA,
        "buggy_ast_hash": buggy_ast_hash,
        "candidate_ast_hash": candidate_ast_hash,
        "changed_token_count": changed_token_count,
        "operations": normalized_operations,
    }
    return {
        "changed_modules": sorted(changed_modules),
        "changed_blocks": sorted(changed_blocks),
        "changed_ast_nodes": changed_nodes,
        "changed_signal_roles": sorted(changed_roles),
        "operator_classes": sorted(operator_classes),
        "patch_scope": expected_patch_scope,
        "normalized_ast_patch": normalized_patch,
    }


def semantic_patch_materialization_hash(patch: dict[str, Any]) -> str:
    """Return the canonical identity of a runner-derived semantic patch."""
    return hash_payload(patch)


def materialize_rtl_ast_rejection_patch(
    *,
    buggy_source: str,
    candidate_source: str,
    expected_patch_scope: str,
    top_module: str,
) -> dict[str, Any]:
    """Describe a candidate that cannot yield an admissible AST diff."""
    try:
        buggy_tokens = tokenize_verilog(buggy_source)
        buggy_ranges = _module_ranges(buggy_tokens)
        buggy_ast_hash = normalized_ast_hash(buggy_source)
    except (VerilogAstViolation, RtlAstMaterializationViolation) as exc:
        raise RtlAstMaterializationViolation(
            "runner-owned buggy RTL AST is unavailable"
        ) from exc
    available_modules = [name for name, _start, _end in buggy_ranges]
    if top_module and top_module not in available_modules:
        raise RtlAstMaterializationViolation(
            "runner-owned top module is unavailable"
        )
    target_module = top_module or (
        available_modules[0] if len(available_modules) == 1 else ""
    )
    return {
        "changed_modules": [target_module] if target_module else [],
        "changed_blocks": [],
        "changed_ast_nodes": ["runner:ast_materialization_rejected"],
        "changed_signal_roles": [],
        "operator_classes": ["ast_materialization_rejected"],
        "patch_scope": expected_patch_scope,
        "normalized_ast_patch": {
            "schema_version": AST_REJECTION_PATCH_SCHEMA,
            "buggy_ast_hash": buggy_ast_hash,
            "candidate_source_hash": hash_payload(candidate_source),
            "admission": "rejected",
        },
    }
