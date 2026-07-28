"""Deterministic token-AST nodes for the frozen GRD-2 operator portfolio."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable

from r3e.protocol.hashing import hash_payload

from .verilog_ast import VerilogAstViolation, VerilogToken, tokenize_verilog


OPERATOR_AST_VERSION = "r3e-verilog-operator-ast-v2"
SUPPORTED_OPERATOR_IDS = {
    "replace_comparator",
    "negate_predicate",
    "shift_boundary_constant",
    "shift_slice",
    "change_signedness_cast",
    "change_assignment_kind",
    "change_reset_semantics",
    "change_enable_condition",
    "change_counter_terminal",
    "change_fsm_transition",
}
_COMPARATORS = {"<", "<=", ">", ">=", "==", "!="}
_NUMBER = re.compile(r"^[0-9]+$")
_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


@dataclass(frozen=True)
class OperatorAstNode:
    operator_id: str
    node_kind: str
    module: str
    ordinal: int
    start: int
    end: int
    old_text: str
    metadata: dict[str, Any]

    @property
    def node_hash(self) -> str:
        return hash_payload({
            "schema_version": OPERATOR_AST_VERSION,
            "operator_id": self.operator_id,
            "node_kind": self.node_kind,
            "module": self.module,
            "ordinal": self.ordinal,
            "old_text": self.old_text,
            "metadata": self.metadata,
        })


def _module_tokens(
    source: str,
    module: str,
) -> tuple[list[VerilogToken], int, int]:
    tokens = tokenize_verilog(source)
    cursor = 0
    while cursor < len(tokens):
        if tokens[cursor].value != "module":
            cursor += 1
            continue
        if (
            cursor + 1 >= len(tokens)
            or tokens[cursor + 1].kind != "identifier"
        ):
            raise VerilogAstViolation(
                "module declaration lacks an identifier"
            )
        name = tokens[cursor + 1].value
        end = next(
            (
                index for index in range(cursor + 2, len(tokens))
                if tokens[index].value == "endmodule"
            ),
            None,
        )
        if end is None:
            raise VerilogAstViolation(
                f"module is missing endmodule: {name}"
            )
        if name == module:
            return tokens, cursor, end
        cursor = end + 1
    raise VerilogAstViolation(
        f"target module is unavailable: {module}"
    )


def _simple_if_predicates(
    tokens: list[VerilogToken],
    start: int,
    end: int,
) -> Iterable[tuple[int, int, VerilogToken, bool]]:
    for index in range(start + 1, end - 3):
        if tokens[index].value != "if" or tokens[index + 1].value != "(":
            continue
        negated = tokens[index + 2].value == "!"
        signal_index = index + 3 if negated else index + 2
        closing_index = signal_index + 1
        if (
            closing_index >= end
            or tokens[signal_index].kind != "identifier"
            or tokens[closing_index].value != ")"
        ):
            continue
        expression_start = index + 2
        yield (
            expression_start,
            closing_index,
            tokens[signal_index],
            negated,
        )


def _is_reset(name: str) -> bool:
    lowered = name.lower()
    return "reset" in lowered or lowered.startswith("rst")


def _is_enable(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in {"en", "enable"}
        or lowered.endswith("_en")
        or "enable" in lowered
    )


def _node(
    *,
    source: str,
    operator_id: str,
    node_kind: str,
    module: str,
    ordinal: int,
    start_token: VerilogToken,
    end_token: VerilogToken,
    metadata: dict[str, Any],
) -> OperatorAstNode:
    return OperatorAstNode(
        operator_id=operator_id,
        node_kind=node_kind,
        module=module,
        ordinal=ordinal,
        start=start_token.start,
        end=end_token.end,
        old_text=source[start_token.start:end_token.end],
        metadata=metadata,
    )


def operator_nodes(
    source: str,
    *,
    module: str,
    operator_id: str,
) -> list[OperatorAstNode]:
    """Return all frozen operator nodes in deterministic source order."""
    if operator_id not in SUPPORTED_OPERATOR_IDS:
        raise VerilogAstViolation(
            f"unsupported GRD-2 operator: {operator_id}"
        )
    tokens, start, end = _module_tokens(source, module)
    nodes: list[OperatorAstNode] = []

    def append(
        node_kind: str,
        first: VerilogToken,
        last: VerilogToken,
        metadata: dict[str, Any],
    ) -> None:
        nodes.append(_node(
            source=source,
            operator_id=operator_id,
            node_kind=node_kind,
            module=module,
            ordinal=len(nodes),
            start_token=first,
            end_token=last,
            metadata=metadata,
        ))

    if operator_id == "replace_comparator":
        for index in range(start + 1, end):
            token = tokens[index]
            if token.value in _COMPARATORS:
                append(
                    "comparison_operator",
                    token,
                    token,
                    {
                        "operator": token.value,
                        "left": tokens[index - 1].value,
                        "right": tokens[index + 1].value,
                    },
                )
        return nodes

    if operator_id in {
        "negate_predicate",
        "change_reset_semantics",
        "change_enable_condition",
    }:
        for expression_start, closing, signal, negated in (
            _simple_if_predicates(tokens, start, end)
        ):
            if operator_id == "negate_predicate" and (
                _is_reset(signal.value) or _is_enable(signal.value)
            ):
                continue
            if (
                operator_id == "change_reset_semantics"
                and not _is_reset(signal.value)
            ):
                continue
            if (
                operator_id == "change_enable_condition"
                and not _is_enable(signal.value)
            ):
                continue
            append(
                {
                    "negate_predicate": "boolean_predicate",
                    "change_reset_semantics": "reset_predicate",
                    "change_enable_condition": "enable_predicate",
                }[operator_id],
                tokens[expression_start],
                tokens[closing - 1],
                {
                    "signal": signal.value,
                    "negated": negated,
                },
            )
        return nodes

    if operator_id in {
        "shift_boundary_constant",
        "change_counter_terminal",
    }:
        for index in range(start + 1, end - 1):
            token = tokens[index]
            right = tokens[index + 1]
            if token.value not in _COMPARATORS or not _NUMBER.fullmatch(
                right.value
            ):
                continue
            left = tokens[index - 1]
            if (
                operator_id == "change_counter_terminal"
                and not any(
                    part in left.value.lower()
                    for part in ("count", "counter", "index")
                )
            ):
                continue
            append(
                (
                    "boundary_constant"
                    if operator_id == "shift_boundary_constant"
                    else "counter_terminal_constant"
                ),
                right,
                right,
                {
                    "comparison_operator": token.value,
                    "left": left.value,
                    "value": int(right.value),
                },
            )
        return nodes

    if operator_id == "shift_slice":
        for index in range(start + 1, end - 5):
            if (
                tokens[index].kind == "identifier"
                and tokens[index + 1].value == "["
                and _NUMBER.fullmatch(tokens[index + 2].value)
                and tokens[index + 3].value == ":"
                and _NUMBER.fullmatch(tokens[index + 4].value)
                and tokens[index + 5].value == "]"
            ):
                append(
                    "part_select",
                    tokens[index + 1],
                    tokens[index + 5],
                    {
                        "base": tokens[index].value,
                        "msb": int(tokens[index + 2].value),
                        "lsb": int(tokens[index + 4].value),
                    },
                )
        return nodes

    if operator_id == "change_signedness_cast":
        for token in tokens[start + 1:end]:
            if token.value in {"$signed", "$unsigned"}:
                append(
                    "signedness_cast",
                    token,
                    token,
                    {"cast": token.value},
                )
        return nodes

    if operator_id == "change_assignment_kind":
        in_procedural = False
        for index in range(start + 1, end - 1):
            token = tokens[index]
            if token.value in {"always", "always_ff", "always_comb"}:
                in_procedural = True
                continue
            if not in_procedural or token.value not in {"=", "<="}:
                continue
            left = tokens[index - 1]
            if left.kind != "identifier":
                continue
            append(
                "procedural_assignment",
                token,
                token,
                {
                    "assignment": token.value,
                    "left": left.value,
                    "right": tokens[index + 1].value,
                },
            )
        return nodes

    if operator_id == "change_fsm_transition":
        state_labels = sorted({
            tokens[index].value
            for index in range(start + 1, end - 1)
            if (
                tokens[index].kind == "identifier"
                and tokens[index + 1].value == ":"
            )
        })
        for index in range(start + 2, end - 1):
            if (
                tokens[index - 1].value not in {"=", "<="}
                or tokens[index - 2].kind != "identifier"
                or "state" not in tokens[index - 2].value.lower()
                or tokens[index].kind != "identifier"
            ):
                continue
            alternatives = [
                value for value in state_labels
                if value != tokens[index].value
            ]
            if not alternatives:
                continue
            append(
                "fsm_transition_target",
                tokens[index],
                tokens[index],
                {
                    "state_signal": tokens[index - 2].value,
                    "target": tokens[index].value,
                    "alternatives": alternatives,
                },
            )
        return nodes

    raise VerilogAstViolation(
        f"operator node discovery is unavailable: {operator_id}"
    )


def require_operator_node(
    source: str,
    *,
    module: str,
    operator_id: str,
    node_hash: str,
) -> OperatorAstNode:
    matches = [
        node for node in operator_nodes(
            source, module=module, operator_id=operator_id
        )
        if node.node_hash == node_hash
    ]
    if len(matches) != 1:
        raise VerilogAstViolation(
            "target operator AST node is missing or ambiguous"
        )
    return matches[0]
