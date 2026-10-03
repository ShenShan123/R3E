"""Replace design-specific identifiers in example snippets with role names.

The example shown to Blue keeps operators, numbers and structure. Every
identifier becomes a role placeholder such as ``out_1``, ``reg_2`` or
``state_1``. This keeps the example transferable and removes design names.
The mapping is consistent within one before/after pair.
"""
from __future__ import annotations

import re

from .structure import RtlStructure
from .verilog_utils import VERILOG_KEYWORDS


_IDENT = re.compile(r"(?<![\w$'])([A-Za-z_][A-Za-z0-9_$]*)")
_PREFIX = {
    "input": "in",
    "output": "out",
    "state": "state",
    "register": "reg",
    "parameter": "param",
    "wire": "sig",
}


def anonymize_pair(
    before: list[str],
    after: list[str],
    structure: RtlStructure,
) -> tuple[list[str], list[str], int]:
    mapping: dict[str, str] = {}
    counters: dict[str, int] = {}

    def name_for(identifier: str) -> str:
        if identifier not in mapping:
            prefix = _PREFIX.get(structure.role(identifier), "sig")
            counters[prefix] = counters.get(prefix, 0) + 1
            mapping[identifier] = f"{prefix}_{counters[prefix]}"
        return mapping[identifier]

    def rewrite(line: str) -> str:
        def repl(match: re.Match[str]) -> str:
            word = match.group(1)
            if word in VERILOG_KEYWORDS:
                return word
            return name_for(word)
        # leave sized literals such as 4'd9 / 8'hFF untouched
        parts = re.split(r"(\d*'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+)", line)
        return "".join(
            part if index % 2 else _IDENT.sub(repl, part)
            for index, part in enumerate(parts)
        )

    return [rewrite(line) for line in before], [rewrite(line) for line in after], len(mapping)


def identifier_roles(lines: list[str], structure: RtlStructure) -> dict[str, str]:
    """Role of every design identifier appearing in ``lines`` (input, state, ...)."""
    roles: dict[str, str] = {}
    for line in lines:
        code = re.sub(r"\d*'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+", " ", line)
        for match in _IDENT.finditer(code):
            word = match.group(1)
            if word not in VERILOG_KEYWORDS and word not in roles:
                roles[word] = structure.role(word)
    return roles
