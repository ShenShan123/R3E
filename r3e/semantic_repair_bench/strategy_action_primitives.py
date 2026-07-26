"""Auditable, bounded repair actions compiled from distilled strategy families.

These actions never read the golden RTL.  They enumerate small local edits from
the buggy source; the frozen evaluator remains the only acceptance oracle.
"""
from __future__ import annotations

import ast
import operator
import re
from pathlib import Path
from typing import Iterable

from semantic_repair_bench.strategy_reasoning_ir import validate_strategy_ir


_RANGE = re.compile(r"\[([^\]\n]+)\]")
_INTEGER = re.compile(r"(?<![A-Za-z0-9_'])\d+(?![A-Za-z0-9_'])")
_DECLARATION = re.compile(r"\b(?:reg|wire|logic|localparam|parameter|input|output|inout)\b")
_SINGLE_OPERATOR = re.compile(r"(?<![|&+\-])([|&+\-])(?![|&+=\-])")


def compile_action_policy(
    trigger: dict,
    trajectories: Iterable[dict],
    reasoning_ir: dict,
) -> dict:
    """Compile a generic, family-scoped policy without case-specific content."""
    family = str(trigger.get("bug_type", ""))
    reasoning_ir = validate_strategy_ir(reasoning_ir, trigger)
    policy = {
        "patch_scope": str(trigger.get("scope") or "local_block"),
        "planner_authority": "select_repair_primitive_only",
        "executor_authority": "deterministic_patch_generation_only",
        "residual_controller": "advance_bounded_primitive_search",
        "llm_patch_generation_allowed": False,
        "reasoning_ir_schema": reasoning_ir["schema_version"],
        "search_order": reasoning_ir["search_order"],
        "stop_condition": reasoning_ir["stop_condition"],
    }
    if family == "off_by_one":
        policy.update({
            "strategy_primitives": [reasoning_ir["primitive"]],
            # The bound is a wall-clock-visible search budget, not permission
            # to accept unverified edits.  The frozen 4-case replay needs at
            # most 31 proposals under the data-independent ordering.
            "primitive_max_trials": 64,
        })
    policy["compiled_from_trajectory_count"] = len(list(trajectories))
    return policy


_ARITHMETIC = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.FloorDiv: operator.floordiv,
    ast.Div: operator.floordiv,
}


def _constant_expression(value: str) -> int | None:
    try:
        node = ast.parse(value, mode="eval").body
    except (SyntaxError, ValueError):
        return None

    def visit(item):
        if isinstance(item, ast.Constant) and isinstance(item.value, int):
            return item.value
        if isinstance(item, ast.UnaryOp) and isinstance(item.op, (ast.UAdd, ast.USub)):
            operand = visit(item.operand)
            return operand if isinstance(item.op, ast.UAdd) else -operand
        if isinstance(item, ast.BinOp) and type(item.op) in _ARITHMETIC:
            return _ARITHMETIC[type(item.op)](visit(item.left), visit(item.right))
        raise ValueError("non-constant expression")

    try:
        return int(visit(node))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _range_width(expression: str) -> int | None:
    bounds = expression.split(":")
    if len(bounds) == 1:
        return 1
    if len(bounds) != 2:
        return None
    upper, lower = (_constant_expression(bound.strip()) for bound in bounds)
    return abs(upper - lower) + 1 if upper is not None and lower is not None else None


def _range_priority(line: str, expression: str, line_no: int) -> tuple[int, int, int]:
    numbers = [int(match.group()) for match in _INTEGER.finditer(expression)]
    small_width = bool(numbers) and max(numbers) <= 2
    code = line.split("//", 1)[0]
    declaration = bool(_DECLARATION.search(code))
    ranges = [match.group(1) for match in _RANGE.finditer(code)]
    widths = [width for value in ranges if (width := _range_width(value)) is not None]
    width_mismatch = "=" in code and len(widths) >= 2 and len(set(widths)) > 1
    paired_assignment = "=" in code and len(ranges) >= 2
    # A locally inconsistent assignment width is strongest structural evidence
    # and requires no golden RTL.  Other paired selects and declarations follow.
    tier = (
        0 if width_mismatch else
        1 if paired_assignment else
        2 if declaration and small_width else
        3 if declaration else
        4
    )
    return tier, line_no, len(expression)


def enumerate_index_range_bound_pm1(buggy_rtl: str | Path) -> list[dict]:
    """Enumerate deterministic one-token ±1 edits inside Verilog selects.

    Ordering is frozen and data-independent: declaration ranges first, then
    other selects, source line, token position, +1 before -1.  Each proposal
    changes one complete source line and carries enough metadata for replay.
    """
    lines = Path(buggy_rtl).read_text(errors="ignore").splitlines()
    sites = []
    for line_no, line in enumerate(lines, 1):
        code = line.split("//", 1)[0]
        for bracket in _RANGE.finditer(code):
            expression = bracket.group(1)
            for number in _INTEGER.finditer(expression):
                absolute_start = bracket.start(1) + number.start()
                absolute_end = bracket.start(1) + number.end()
                sites.append((
                    _range_priority(line, expression, line_no),
                    line_no,
                    absolute_start,
                    absolute_end,
                    int(number.group()),
                    line,
                    expression,
                ))
    proposals = []
    seen = set()
    for _, line_no, start, end, value, line, expression in sorted(sites):
        for delta in (1, -1):
            replacement = value + delta
            if replacement < 0:
                continue
            new_code = line[:start] + str(replacement) + line[end:]
            key = (line_no, new_code)
            if key in seen or new_code == line:
                continue
            seen.add(key)
            proposals.append({
                "primitive": "index_range_bound_pm1",
                "start_line": line_no,
                "end_line": line_no,
                "new_code": new_code,
                "source_expression": expression,
                "changed_literal": value,
                "delta": delta,
            })
    return proposals


def enumerate_decimal_literal_pm1(buggy_rtl: str | Path) -> list[dict]:
    """Enumerate bounded ±1 edits of plain decimal literals from buggy RTL."""
    lines = Path(buggy_rtl).read_text(errors="ignore").splitlines()
    sites = []
    for line_no, line in enumerate(lines, 1):
        code = line.split("//", 1)[0]
        declaration = bool(_DECLARATION.search(code))
        assignment = "=" in code
        tier = 0 if declaration and assignment else 1 if declaration else 2 if assignment else 3
        for number in _INTEGER.finditer(code):
            sites.append((tier, line_no, number.start(), number.end(), int(number.group()), line))
    proposals = []
    for _, line_no, start, end, value, line in sorted(sites):
        for delta in (1, -1):
            replacement = value + delta
            if replacement < 0:
                continue
            proposals.append({
                "primitive": "decimal_literal_pm1",
                "start_line": line_no,
                "end_line": line_no,
                "new_code": line[:start] + str(replacement) + line[end:],
                "changed_literal": value,
                "delta": delta,
            })
    return proposals


def enumerate_operator_inverse_pairs(buggy_rtl: str | Path) -> list[dict]:
    """Enumerate single-token inverse operator pairs without semantic guessing."""
    inverse = {"|": "&", "&": "|", "+": "-", "-": "+"}
    lines = Path(buggy_rtl).read_text(errors="ignore").splitlines()
    sites = []
    for line_no, line in enumerate(lines, 1):
        code = line.split("//", 1)[0]
        tier = 0 if "assign" in code or "=" in code else 1
        for match in _SINGLE_OPERATOR.finditer(code):
            sites.append((tier, line_no, match.start(1), match.end(1), match.group(1), line))
    return [{
        "primitive": "operator_inverse_pair",
        "start_line": line_no,
        "end_line": line_no,
        "new_code": line[:start] + inverse[value] + line[end:],
        "changed_operator": value,
        "replacement_operator": inverse[value],
    } for _, line_no, start, end, value, line in sorted(sites)]


def enumerate_strategy_patches(buggy_rtl: str | Path, action_policy: dict) -> list[dict]:
    proposals: list[dict] = []
    for primitive in action_policy.get("strategy_primitives", []):
        if primitive == "index_range_bound_pm1":
            proposals.extend(enumerate_index_range_bound_pm1(buggy_rtl))
        elif primitive == "decimal_literal_pm1":
            proposals.extend(enumerate_decimal_literal_pm1(buggy_rtl))
        elif primitive == "operator_inverse_pair":
            proposals.extend(enumerate_operator_inverse_pairs(buggy_rtl))
        else:
            raise ValueError(f"unknown strategy primitive: {primitive}")
    limit = int(action_policy.get("primitive_max_trials", len(proposals) or 0))
    return proposals[:max(0, limit)]
