"""Runner-owned AST/interface scope authority for Competition candidates."""
from __future__ import annotations

from difflib import unified_diff
import re
from typing import Any

from r3e.blue.portfolio.rtl_ast_materializer import (
    RtlAstMaterializationViolation,
    materialize_rtl_ast_semantic_patch,
)

from .common import payload_hash


_MODULE_RE = re.compile(
    r"\bmodule\s+([A-Za-z_$][\w$]*)\s*(?:#\s*\(.*?\))?\s*\((.*?)\)\s*;",
    re.DOTALL,
)
_DECL_RE = re.compile(r"\b(input|output|inout)\b\s*([^;]+);", re.DOTALL)
_DIRECTION_RE = re.compile(r"^\s*(input|output|inout)\b\s*(.*)$", re.DOTALL)
_WIDTH_RE = re.compile(r"\[[^\]]+\]")
_PORT_KEYWORDS_RE = re.compile(
    r"\b(?:wire|reg|logic|signed|unsigned|var|tri|wand|wor|integer|time)\b"
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][\w$]*$")
_FORBIDDEN = {
    "oracle_file_read": re.compile(r"\$(?:fopen|readmem(?:b|h))\b"),
    "force_release": re.compile(r"\b(?:force|release)\b"),
    "new_file_include": re.compile(r"(?m)^\s*`include\b"),
    "external_path": re.compile(
        r"(?:^|(?<=[\s\"'(=,:]))/(?![/*\s])[^\s\"'`;,)]+",
        re.MULTILINE,
    ),
}


class ScopeError(ValueError):
    """Raised when scope inspection cannot produce an authoritative result."""


def _strip_comments(value: str) -> str:
    value = re.sub(r"//[^\n]*", "", value)
    return re.sub(r"/\*.*?\*/", "", value, flags=re.DOTALL)


def _split_top_level(segment: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote = ""
    escaped = False
    for index, char in enumerate(segment):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in "'\"`":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            parts.append(segment[start:index])
            start = index + 1
    parts.append(segment[start:])
    return parts


def _port_names(segment: str) -> list[str]:
    names: list[str] = []
    for item in _split_top_level(segment):
        item = item.strip()
        match = _DIRECTION_RE.match(item)
        if match:
            item = match.group(2).strip()
        item = item.split("=", 1)[0].strip()
        item = _WIDTH_RE.sub("", item)
        item = _PORT_KEYWORDS_RE.sub("", item).strip()
        if _IDENTIFIER_RE.fullmatch(item):
            names.append(item)
    return names


def interface_signature(source: str) -> dict[str, Any]:
    """Return module and port identity without trusting candidate metadata."""
    clean = _strip_comments(source)
    modules: dict[str, dict[str, Any]] = {}
    declarations: dict[str, tuple[str, str]] = {}
    for match in _DECL_RE.finditer(clean):
        direction, body = match.groups()
        width_match = _WIDTH_RE.search(body)
        width = width_match.group(0) if width_match else ""
        for name in _port_names(body):
            declarations[name] = (direction, width)
    for match in _MODULE_RE.finditer(clean):
        module_name, header = match.groups()
        ports: list[dict[str, str]] = []
        pending_direction = ""
        pending_width = ""
        for item in _split_top_level(header):
            item = item.strip()
            direction_match = _DIRECTION_RE.match(item)
            if direction_match:
                pending_direction = direction_match.group(1)
                width_match = _WIDTH_RE.search(direction_match.group(2))
                pending_width = width_match.group(0) if width_match else ""
                names = _port_names(direction_match.group(2))
            else:
                names = _port_names(item)
            for name in names:
                direction, width = declarations.get(
                    name, (pending_direction, pending_width)
                )
                ports.append({"name": name, "direction": direction, "width": width})
        modules[module_name] = {"ports": ports}
    return {"modules": modules}


def _changed_lines(before: str, after: str) -> int:
    diff = unified_diff(before.splitlines(), after.splitlines(), lineterm="")
    return sum(
        1 for line in diff
        if (line.startswith("+") or line.startswith("-"))
        and not line.startswith(("+++", "---"))
    )


class ScopeService:
    """Inspect candidate scope before functional verification."""

    def inspect(
        self,
        *,
        buggy_source: str,
        candidate_source: str,
        top_module: str,
        patch_scope: str = "local_block",
    ) -> dict[str, Any]:
        if not buggy_source or not candidate_source:
            raise ScopeError("buggy and candidate RTL are required")
        before = interface_signature(buggy_source)
        after = interface_signature(candidate_source)
        before_modules = set(before["modules"])
        after_modules = set(after["modules"])
        module_identity = before_modules == after_modules and top_module in before_modules
        interface_preserved = before == after
        executable_source = _strip_comments(candidate_source)
        forbidden = [
            name for name, pattern in _FORBIDDEN.items() if pattern.search(executable_source)
        ]
        no_op = buggy_source == candidate_source
        semantic_patch: dict[str, Any] | None = None
        ast_error = ""
        if not no_op and module_identity:
            try:
                semantic_patch = materialize_rtl_ast_semantic_patch(
                    buggy_source=buggy_source,
                    candidate_source=candidate_source,
                    expected_patch_scope=patch_scope,
                    top_module=top_module,
                )
            except RtlAstMaterializationViolation as exc:
                ast_error = str(exc)
        changed_lines = _changed_lines(buggy_source, candidate_source)
        changed_nodes = list((semantic_patch or {}).get("changed_ast_nodes", []))
        changed_modules = list((semantic_patch or {}).get("changed_modules", []))
        changed_blocks = list((semantic_patch or {}).get("changed_blocks", []))
        scope_ok = bool(
            module_identity
            and interface_preserved
            and not forbidden
            and (no_op or semantic_patch is not None)
            and changed_lines <= 32
        )
        result = {
            "schema_version": "r3e-aic-scope-gate-v1",
            "ok": scope_ok,
            "module_identity": module_identity,
            "module_preserved": module_identity,
            "interface_preserved": interface_preserved,
            "interface_changed": not interface_preserved,
            "module_names": sorted(after_modules),
            "changed_lines": changed_lines,
            "changed_ast_nodes": changed_nodes,
            "changed_modules": changed_modules,
            "changed_blocks": changed_blocks,
            "ast_edit_count": len(changed_nodes),
            "patch_scope": patch_scope,
            "forbidden_findings": forbidden,
            "ast_error": ast_error,
            "no_op": no_op,
        }
        result["scope_hash"] = payload_hash(result)
        return result
