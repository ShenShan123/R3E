"""Lightweight structural model of buggy RTL for causal features.

This is a heuristic, token-level model. It is not a full elaborator; Icarus
and Yosys remain authoritative for semantics. It answers questions such as:
- Which block drives a signal, and is that block sequential or combinational?
- Which signals feed a signal, through data and through control?
- How many register stages separate an output from a register?
- Does the output's cone contain counters, state machines or reset logic?

When the token grammar cannot parse the source, the model is empty and every
causal feature resolves to ``"unknown"``.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from r3e.knowledge.verilog_ast import VerilogToken

from .verilog_utils import RESET_NAME_RE, is_identifier, safe_tokenize


_TOP_LEVEL = frozenset({
    "always", "always_comb", "always_ff", "always_latch", "assign", "initial",
    "endmodule", "module", "input", "output", "inout", "wire", "reg",
    "parameter", "localparam", "function", "task", "generate", "endgenerate",
    "integer", "genvar", "logic",
})
_OPEN = {"begin": "end", "case": "endcase", "casex": "endcase",
         "casez": "endcase", "fork": "join", "function": "endfunction",
         "task": "endtask"}
_CLOSE = {"end", "endcase", "join", "endfunction", "endtask"}
_STATEMENT_START = frozenset({";", "begin", "else", ")", ":", "end", "assign"})


@dataclass
class Driver:
    block_id: str
    kind: str  # "sequential" | "combinational"
    data_deps: set[str] = field(default_factory=set)
    control_deps: set[str] = field(default_factory=set)
    start: int = 0
    end: int = 0


@dataclass
class Block:
    block_id: str
    kind: str
    start: int  # character offsets in the source
    end: int
    assigned: set[str] = field(default_factory=set)
    control_ids: set[str] = field(default_factory=set)
    case_selectors: set[str] = field(default_factory=set)
    edge_signals: list[str] = field(default_factory=list)


@dataclass
class RtlStructure:
    parsed: bool
    inputs: set[str] = field(default_factory=set)
    outputs: set[str] = field(default_factory=set)
    parameters: set[str] = field(default_factory=set)
    drivers: dict[str, list[Driver]] = field(default_factory=dict)
    blocks: list[Block] = field(default_factory=list)

    # ------------------------------------------------------------------ roles
    def is_register(self, signal: str) -> bool:
        return any(d.kind == "sequential" for d in self.drivers.get(signal, []))

    def is_counter(self, signal: str) -> bool:
        return any(signal in d.data_deps for d in self.drivers.get(signal, []))

    def is_state_like(self, signal: str) -> bool:
        if not self.is_register(signal):
            return False
        return any(signal in block.case_selectors for block in self.blocks)

    def feeds_state(self, signal: str) -> bool:
        """True when ``signal`` is the direct next-value source of a state register."""
        return any(
            self.is_state_like(target)
            and any(signal in d.data_deps for d in drivers if d.kind == "sequential")
            for target, drivers in self.drivers.items()
        )

    def is_reset_like(self, signal: str) -> bool:
        if RESET_NAME_RE.match(signal):
            return True
        return any(signal in block.edge_signals[1:] for block in self.blocks)

    def role(self, signal: str) -> str:
        if signal in self.parameters:
            return "parameter"
        if signal in self.inputs:
            return "input"
        if signal in self.outputs:
            return "output"
        if self.is_state_like(signal):
            return "state"
        if self.is_register(signal):
            return "register"
        return "wire"

    def block_at(self, offset: int) -> Block | None:
        for block in self.blocks:
            if block.start <= offset <= block.end:
                return block
        return None

    # ------------------------------------------------------------------- cone
    def cone(self, signal: str, max_signals: int = 64) -> dict[str, object]:
        """Backward cone of ``signal`` through data and control dependencies."""
        if not self.parsed or signal not in self.drivers:
            return {"known": False}
        seen: set[str] = {signal}
        queue: deque[tuple[str, int]] = deque([(signal, 0)])
        nearest_register: int | None = None
        has_control = False
        while queue and len(seen) < max_signals:
            current, stages = queue.popleft()
            # ``stages`` counts signal hops from the queried output.
            for driver in self.drivers.get(current, []):
                if driver.kind == "sequential":
                    if nearest_register is None or stages < nearest_register:
                        nearest_register = stages
                next_stage = stages + 1
                if driver.control_deps:
                    has_control = True
                for dep in driver.data_deps | driver.control_deps:
                    if dep not in seen and dep not in self.parameters:
                        seen.add(dep)
                        queue.append((dep, next_stage))
        members = seen - {signal}
        return {
            "known": True,
            "size": len(members),
            "nearest_register_stage": nearest_register,
            "has_counter": any(self.is_counter(s) for s in seen),
            "has_state_machine": any(self.is_state_like(s) for s in seen),
            "has_reset_logic": any(self.is_reset_like(s) for s in seen),
            "has_control_dependency": has_control,
            "members": seen,
        }


def _ternary_condition_ids(tokens: list[VerilogToken], start: int, end: int) -> set[str]:
    """Identifiers in the condition of every ``?`` between ``start`` and ``end``.

    A condition runs back from its ``?`` to the nearest ``=``, ``(``, ``,``,
    ``:`` or ``?`` at the same bracket depth.
    """
    out: set[str] = set()
    depth = 0
    opened: list[int] = []  # start index of the current condition at each depth
    begin = {0: start}
    for k in range(start, end + 1):
        value = tokens[k].value
        if value in "([{":
            depth += 1
            opened.append(k)
            begin[depth] = k + 1
        elif value in ")]}":
            depth = max(0, depth - 1)
            if opened:
                opened.pop()
        elif value == "?":
            out |= _identifiers(tokens[begin.get(depth, start):k])
            begin[depth] = k + 1
        elif value in {"=", ",", ":"}:
            begin[depth] = k + 1
    return out


def _identifiers(tokens: list[VerilogToken]) -> set[str]:
    return {tok.value for tok in tokens if is_identifier(tok)}


def _matching_paren(tokens: list[VerilogToken], index: int) -> int:
    depth = 0
    for cursor in range(index, len(tokens)):
        if tokens[cursor].value == "(":
            depth += 1
        elif tokens[cursor].value == ")":
            depth -= 1
            if depth == 0:
                return cursor
    return len(tokens) - 1


def _block_extent(tokens: list[VerilogToken], start: int) -> int:
    """Index of the last token of the construct beginning at ``start``."""
    depth = 0
    cursor = start + 1
    while cursor < len(tokens):
        value = tokens[cursor].value
        if value in _OPEN:
            depth += 1
        elif value in _CLOSE:
            depth -= 1
            if depth == 0:
                # the construct may continue (e.g. "end else begin")
                nxt = tokens[cursor + 1].value if cursor + 1 < len(tokens) else ""
                if nxt != "else":
                    return cursor
        elif depth == 0 and value in _TOP_LEVEL and cursor > start:
            return cursor - 1
        cursor += 1
    return len(tokens) - 1


def _parse_declarations(tokens: list[VerilogToken], structure: RtlStructure) -> None:
    direction = ""
    for index, tok in enumerate(tokens):
        value = tok.value
        if value in {"input", "output", "inout"}:
            direction = value
            continue
        if value in {"parameter", "localparam"}:
            # name follows optional range/type tokens; take identifier before "="
            cursor = index + 1
            while cursor < len(tokens) and tokens[cursor].value not in {";", ")"}:
                if (
                    is_identifier(tokens[cursor])
                    and cursor + 1 < len(tokens)
                    and tokens[cursor + 1].value == "="
                ):
                    structure.parameters.add(tokens[cursor].value)
                cursor += 1
            continue
        if direction and value in {";", ")"}:
            direction = ""
            continue
        if direction and is_identifier(tok):
            nxt = tokens[index + 1].value if index + 1 < len(tokens) else ""
            if nxt in {",", ";", ")", "=", "["} or nxt in {"input", "output", "inout"}:
                if direction == "input":
                    structure.inputs.add(value)
                elif direction == "output":
                    structure.outputs.add(value)
                else:
                    structure.inputs.add(value)
                    structure.outputs.add(value)


def _parse_assignments(
    tokens: list[VerilogToken],
    block: Block,
    first: int,
    last: int,
    structure: RtlStructure,
) -> None:
    cursor = first
    while cursor <= last:
        tok = tokens[cursor]
        prev = tokens[cursor - 1].value if cursor > 0 else ";"
        if tok.value == "{" and prev in _STATEMENT_START:
            # concatenated targets: {a, b, c} = expr;
            close = cursor
            depth = 0
            while close <= last:
                if tokens[close].value == "{":
                    depth += 1
                elif tokens[close].value == "}":
                    depth -= 1
                    if depth == 0:
                        break
                close += 1
            if close + 1 <= last and tokens[close + 1].value in {"=", "<="}:
                end = close + 2
                while end <= last and tokens[end].value != ";":
                    end += 1
                rhs = _identifiers(tokens[close + 2:end])
                for target in sorted(_identifiers(tokens[cursor + 1:close])):
                    block.assigned.add(target)
                    structure.drivers.setdefault(target, []).append(Driver(
                        block_id=block.block_id,
                        kind=block.kind,
                        data_deps=rhs,
                        control_deps=set(block.control_ids),
                        start=tok.start,
                        end=tokens[min(end, last)].end,
                    ))
                cursor = end
            cursor += 1
            continue
        if is_identifier(tok) and prev in _STATEMENT_START:
            target = tok.value
            look = cursor + 1
            if look <= last and tokens[look].value == "[":
                depth = 0
                while look <= last:
                    if tokens[look].value == "[":
                        depth += 1
                    elif tokens[look].value == "]":
                        depth -= 1
                        if depth == 0:
                            look += 1
                            break
                    look += 1
            if look <= last and tokens[look].value in {"=", "<="}:
                end = look + 1
                while end <= last and tokens[end].value != ";":
                    end += 1
                rhs = _identifiers(tokens[look + 1:end])
                block.assigned.add(target)
                structure.drivers.setdefault(target, []).append(Driver(
                    block_id=block.block_id,
                    kind=block.kind,
                    data_deps=rhs,
                    control_deps=set(block.control_ids),
                    start=tok.start,
                    end=tokens[min(end, last)].end,
                ))
                cursor = end
        cursor += 1


def analyze_rtl(source: str) -> RtlStructure:
    tokens = safe_tokenize(source)
    structure = RtlStructure(parsed=bool(tokens))
    if not tokens:
        return structure
    _parse_declarations(tokens, structure)
    counters: dict[str, int] = {}
    index = 0
    while index < len(tokens):
        value = tokens[index].value
        if value in {"always", "always_ff", "always_comb", "always_latch"}:
            end = _block_extent(tokens, index)
            body_start = index + 1
            edges: list[str] = []
            kind = "combinational"
            if value == "always_ff":
                kind = "sequential"
            if body_start < len(tokens) and tokens[body_start].value == "@":
                if body_start + 1 < len(tokens) and tokens[body_start + 1].value == "(":
                    close = _matching_paren(tokens, body_start + 1)
                    sens = tokens[body_start + 2:close]
                    for pos, tok in enumerate(sens):
                        if tok.value in {"posedge", "negedge"} and pos + 1 < len(sens):
                            edges.append(sens[pos + 1].value)
                    if edges:
                        kind = "sequential"
                    body_start = close + 1
                else:
                    body_start += 2  # "@*"
            ordinal = counters.get(value, 0)
            counters[value] = ordinal + 1
            block = Block(
                block_id=f"{value}:{ordinal}",
                kind=kind,
                start=tokens[index].start,
                end=tokens[end].end,
                edge_signals=edges,
            )
            # control identifiers: if/case conditions inside the block
            cursor = body_start
            while cursor <= end:
                tv = tokens[cursor].value
                if tv in {"if", "case", "casex", "casez"} and cursor + 1 <= end \
                        and tokens[cursor + 1].value == "(":
                    close = _matching_paren(tokens, cursor + 1)
                    ids = _identifiers(tokens[cursor + 2:close])
                    block.control_ids |= ids
                    if tv != "if":
                        block.case_selectors |= ids
                    cursor = close
                cursor += 1
            _parse_assignments(tokens, block, body_start, end, structure)
            structure.blocks.append(block)
            index = end + 1
            continue
        if value == "assign":
            end = index + 1
            while end < len(tokens) and tokens[end].value != ";":
                end += 1
            ordinal = counters.get("assign", 0)
            counters["assign"] = ordinal + 1
            block = Block(
                block_id=f"assign:{ordinal}",
                kind="combinational",
                start=tokens[index].start,
                end=tokens[min(end, len(tokens) - 1)].end,
            )
            # The condition of "assign lhs = c ? a : b" selects the path, like an
            # if/case guard, so it is a control dependency (a swapped ternary and
            # a negated if then share ``cone_has_control_dependency``).
            block.control_ids |= _ternary_condition_ids(tokens, index + 1, min(end, len(tokens) - 1))
            _parse_assignments(tokens, block, index + 1, min(end, len(tokens) - 1), structure)
            structure.blocks.append(block)
            index = end + 1
            continue
        index += 1
    return structure
