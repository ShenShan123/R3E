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
_DECL_RE = re.compile(
    r"\b(input|output|inout)\b\s*(?:(?:wire|reg|logic)\s*)?"
    r"(\[[^\]]+\])?\s*([^;]+);",
    re.DOTALL,
)
_DIRECTION_RE = re.compile(
    r"^\s*(input|output|inout)\b\s*"
    r"(?:(?:wire|reg|logic)\s*)?(\[[^\]]+\])?\s*(.*)$",
    re.DOTALL,
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][\w$]*$")
_FORBIDDEN = {
    "oracle_file_read": re.compile(r"\$(?:fopen|readmem(?:b|h))\b"),
    "force_release": re.compile(r"\b(?:force|release)\b"),
    "new_file_include": re.compile(r"(?m)^\s*`include\b"),
    "external_path": re.compile(r"(?:/|[A-Za-z]:\\)[^\s\"']+"),
}


class ScopeError(ValueError):
    """Raised when scope inspection cannot produce an authoritative result."""


def _strip_comments(value: str) -> str:
    value = re.sub(r"//[^\n]*", "", value)
    return re.sub(r"/\*.*?\*/", "", value, flags=re.DOTALL)


def _port_names(segment: str) -> list[str]:
    names: list[str] = []
    for item in segment.split(","):
        item = item.strip()
        match = _DIRECTION_RE.match(item)
        if match:
            item = match.group(3).strip()
        item = re.sub(r"\[[^]]+\]", "", item)
        item = re.sub(r"\b(?:wire|reg|logic|signed|unsigned)\b", "", item)
        item = item.split("=")[0].strip()
        if _IDENTIFIER_RE.fullmatch(item):
            names.append(item)
    return names


def interface_signature(source: str) -> dict[str, Any]:
    """Return module and port identity without trusting candidate metadata."""
    clean = _strip_comments(source)
    modules: dict[str, dict[str, Any]] = {}
    declarations: dict[str, tuple[str, str]] = {}
    for match in _DECL_RE.finditer(clean):
        direction, width, names = match.groups()
        for name in _port_names(names):
            declarations[name] = (direction, width or "")
    for match in _MODULE_RE.finditer(clean):
        module_name, header = match.groups()
        ports: list[dict[str, str]] = []
        pending_direction = ""
        pending_width = ""
        for item in header.split(","):
            item = item.strip()
            direction_match = _DIRECTION_RE.match(item)
            if direction_match:
                pending_direction = direction_match.group(1)
                pending_width = direction_match.group(2) or ""
                names = _port_names(direction_match.group(3))
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
