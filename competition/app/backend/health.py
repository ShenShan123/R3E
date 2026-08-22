"""Health/readiness checks for the demo backend."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ...services.case_service import CaseCatalog
from ...services.common import executable_versions, git_provenance


def health(repo_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    try:
        cases = CaseCatalog(root).summaries()
        case_status = "pass"
    except Exception as exc:  # noqa: BLE001
        cases = []
        case_status = f"fail:{type(exc).__name__}"
    tools = executable_versions()
    return {
        "schema_version": "r3e-aic-health-v1",
        "status": "pass" if case_status == "pass" else "fail",
        "case_catalog": case_status,
        "case_count": len(cases),
        "required_tools": {
            name: bool(tools.get(name, {}).get("available"))
            for name in ("iverilog", "vvp", "yosys")
        },
        "tools": tools,
        "git": git_provenance(root),
        "message": "Provider readiness is checked only when live mode is explicitly selected.",
    }
