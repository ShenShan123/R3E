"""Classify Blue's own verified repair into a normalized bug type.

The input is the current buggy RTL and Blue's candidate RTL that passed the
declared verdict tier. No golden or reference source is used. The bug type
assigned here is a property of a verified repair. It is stored with the
knowledge, and it is what later soft type inference learns from.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from r3e.knowledge.verilog_ast import VerilogToken

from .structure import RtlStructure, analyze_rtl
from .verilog_utils import (
    COMPARISON_OPERATORS,
    is_identifier,
    line_of,
    operator_class,
    safe_tokenize,
)


_SEVERITY = (
    "missing_or_extra_logic",
    "sensitivity_list",
    "assignment_kind",
    "state_transition",
    "reset_or_enable",
    "condition_expression",
    "assignment_target",
    "width_or_index",
    "operator_compare",
    "operator_arith",
    "operator_logic",
    "operator_shift",
    "constant_value",
    "signal_reference",
    "expression_rewrite",
)


def _context(tokens: list[VerilogToken], index: int) -> dict[str, Any]:
    """Syntactic context of the token at ``index`` in the buggy source."""
    ctx = {
        "in_sensitivity": False,
        "in_condition": False,
        "in_brackets": False,
        "assignment_lhs": "",
        "on_lhs": False,
        "condition_identifiers": set(),
    }
    depth_paren = 0
    depth_bracket = 0
    cursor = index - 1
    while cursor >= 0:
        value = tokens[cursor].value
        if value == ")":
            depth_paren += 1
        elif value == "(":
            if depth_paren == 0:
                before = tokens[cursor - 1].value if cursor > 0 else ""
                if before == "@":
                    ctx["in_sensitivity"] = True
                elif before in {"if", "case", "casex", "casez", "while"}:
                    ctx["in_condition"] = True
                    depth = 0
                    for pos in range(cursor, len(tokens)):
                        if tokens[pos].value == "(":
                            depth += 1
                        elif tokens[pos].value == ")":
                            depth -= 1
                            if depth == 0:
                                break
                        elif is_identifier(tokens[pos]):
                            ctx["condition_identifiers"].add(tokens[pos].value)
            else:
                depth_paren -= 1
        elif value == "]":
            depth_bracket += 1
        elif value == "[":
            if depth_bracket == 0:
                ctx["in_brackets"] = True
            else:
                depth_bracket -= 1
        elif value == "?" and depth_paren == 0:
            ctx["in_condition"] = True
        elif value in {";", "begin", "end"}:
            break
        cursor -= 1
    # statement boundaries around ``index`` → assignment target
    start = index
    while start > 0 and tokens[start - 1].value not in {";", "begin", "end", "else", ")", ":", "assign"}:
        start -= 1
    stop = index
    while stop < len(tokens) and tokens[stop].value != ";":
        stop += 1
    for pos in range(start, min(stop, len(tokens))):
        if tokens[pos].value in {"=", "<="} and pos > start and is_identifier(tokens[start]):
            # "<=" inside parentheses is a comparison, not an assignment
            if tokens[pos].value == "<=" and _inside_parens(tokens, start, pos):
                continue
            ctx["assignment_lhs"] = tokens[start].value
            ctx["on_lhs"] = index < pos
            break
    return ctx


def _inside_parens(tokens: list[VerilogToken], start: int, pos: int) -> bool:
    depth = 0
    for cursor in range(start, pos):
        if tokens[cursor].value == "(":
            depth += 1
        elif tokens[cursor].value == ")":
            depth -= 1
    return depth > 0


def _classify_change(
    tag: str,
    before: list[VerilogToken],
    after: list[VerilogToken],
    ctx: dict[str, Any],
    structure: RtlStructure,
) -> str:
    if tag in {"insert", "delete"} or any(t.value == ";" for t in before + after):
        if any(t.value == ";" for t in before + after) or len(before + after) > 3:
            return "missing_or_extra_logic"
    if ctx["in_sensitivity"]:
        return "sensitivity_list"
    values_before = {t.value for t in before}
    values_after = {t.value for t in after}
    if (values_before | values_after) <= {"=", "<="} and values_before != values_after:
        return "assignment_kind"
    idents = {t.value for t in before + after if is_identifier(t)}
    lhs = ctx["assignment_lhs"]
    if ctx["in_condition"] and any(
        structure.is_reset_like(name)
        for name in idents | ctx["condition_identifiers"]
    ):
        return "reset_or_enable"
    if lhs and not ctx["on_lhs"] and (
        structure.is_state_like(lhs) or structure.feeds_state(lhs)
    ):
        return "state_transition"
    kinds = {t.kind for t in before + after}
    if kinds == {"number"}:
        return "width_or_index" if ctx["in_brackets"] else "constant_value"
    if kinds == {"operator"}:
        classes = {operator_class(t.value) for t in before + after}
        if classes <= {"comparison"} or (values_before | values_after) & COMPARISON_OPERATORS:
            return "operator_compare"
        if classes <= {"arithmetic"}:
            return "operator_arith"
        if classes <= {"shift"}:
            return "operator_shift"
        if classes <= {"logic"}:
            return "operator_logic"
        return "expression_rewrite"
    if kinds == {"identifier"}:
        if ctx["on_lhs"]:
            return "assignment_target"
        if ctx["in_condition"]:
            return "condition_expression"
        return "signal_reference"
    if ctx["in_condition"]:
        return "condition_expression"
    if ctx["in_brackets"]:
        return "width_or_index"
    return "expression_rewrite"


def _excerpt(source: str, spans: list[tuple[int, int]], limit: int = 6) -> list[str]:
    lines = source.splitlines()
    picked: list[int] = []
    for start, end in spans:
        for number in range(line_of(source, start), line_of(source, max(start, end - 1)) + 1):
            if number not in picked and number < len(lines):
                picked.append(number)
    return [lines[number].rstrip() for number in sorted(picked)[:limit]]


def classify_repair(
    buggy_source: str,
    repaired_source: str,
    *,
    structure: RtlStructure | None = None,
    failing_signals: list[str] | None = None,
) -> dict[str, Any]:
    """Return the bug type and edit features of a verified repair."""
    before_tokens = safe_tokenize(buggy_source)
    after_tokens = safe_tokenize(repaired_source)
    if not before_tokens or not after_tokens:
        return {"bug_type": "expression_rewrite", "secondary_types": [],
                "parsed": False, "edit_scope": "unknown", "changed_token_count": 0,
                "block_kind": "unknown", "edit_in_failing_cone": None,
                "assigned_signal_role": "unknown",
                "before_lines": [], "after_lines": []}
    structure = structure or analyze_rtl(buggy_source)
    matcher = SequenceMatcher(
        a=[(t.kind, t.value) for t in before_tokens],
        b=[(t.kind, t.value) for t in after_tokens],
        autojunk=False,
    )
    types: list[str] = []
    before_spans: list[tuple[int, int]] = []
    after_spans: list[tuple[int, int]] = []
    changed = 0
    block_kinds: set[str] = set()
    assigned_roles: set[str] = set()
    touched_identifiers: set[str] = set()
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        before = before_tokens[i1:i2]
        after = after_tokens[j1:j2]
        changed += max(len(before), len(after))
        anchor = min(i1, len(before_tokens) - 1)
        ctx = _context(before_tokens, anchor)
        types.append(_classify_change(tag, before, after, ctx, structure))
        offset = before_tokens[anchor].start
        before_spans.append((offset, before_tokens[max(i1, i2 - 1)].end if i2 > i1 else offset + 1))
        after_anchor = min(j1, len(after_tokens) - 1)
        after_spans.append((after_tokens[after_anchor].start,
                            after_tokens[max(j1, j2 - 1)].end if j2 > j1 else after_tokens[after_anchor].end))
        block = structure.block_at(offset)
        if block is not None:
            block_kinds.add(block.kind)
        if ctx["assignment_lhs"]:
            assigned_roles.add(structure.role(ctx["assignment_lhs"]))
            touched_identifiers.add(ctx["assignment_lhs"])
        touched_identifiers |= {t.value for t in before + after if is_identifier(t)}
    if not types:
        return {"bug_type": "expression_rewrite", "secondary_types": [],
                "parsed": True, "edit_scope": "no_change", "changed_token_count": 0,
                "block_kind": "unknown", "edit_in_failing_cone": None,
                "assigned_signal_role": "unknown",
                "before_lines": [], "after_lines": []}
    ordered = sorted(set(types), key=_SEVERITY.index)
    primary = ordered[0]
    hunks = len(types)
    lines_touched = len({line_of(buggy_source, s) for s, _ in before_spans})
    if changed <= 1 and hunks == 1:
        scope = "single_token"
    elif lines_touched <= 1:
        scope = "single_expression"
    elif lines_touched <= 3:
        scope = "single_statement"
    else:
        scope = "multi_statement"
    in_cone = None
    if failing_signals and structure.parsed:
        cone_members: set[str] = set()
        for signal in failing_signals:
            cone = structure.cone(signal)
            if cone.get("known"):
                cone_members |= set(cone["members"])
        in_cone = bool(cone_members & touched_identifiers)
    return {
        "bug_type": primary,
        "secondary_types": ordered[1:],
        "parsed": True,
        "edit_scope": scope,
        "changed_token_count": changed,
        "block_kind": (
            next(iter(block_kinds)) if len(block_kinds) == 1
            else "mixed" if block_kinds else "module_scope"
        ),
        "edit_in_failing_cone": in_cone,
        "assigned_signal_role": (
            next(iter(assigned_roles)) if len(assigned_roles) == 1
            else "mixed" if assigned_roles else "none"
        ),
        "before_lines": _excerpt(buggy_source, before_spans),
        "after_lines": _excerpt(repaired_source, after_spans),
    }
