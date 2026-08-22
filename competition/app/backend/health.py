"""Health/readiness checks for the demo backend."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ...config import ConfigError, load_config
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
    required_tools = {
        name: bool(tools.get(name, {}).get("available"))
        for name in ("iverilog", "vvp", "yosys")
    }
    required_tools_ready = all(required_tools.values())
    try:
        config_status = load_config(root).version
    except (ConfigError, OSError) as exc:
        config_status = f"fail:{type(exc).__name__}"
    overall = case_status == "pass" and required_tools_ready and not config_status.startswith("fail:")
    return {
        "schema_version": "r3e-aic-health-v2",
        "status": "pass" if overall else "fail",
        "case_catalog": case_status,
        "case_count": len(cases),
        "config": config_status,
        "required_tools_ready": required_tools_ready,
        "required_tools": required_tools,
        "tools": tools,
        "git": git_provenance(root),
        "provider_ready": None,
        "message": (
            "EDA NOT READY: install all required tools before running a case."
            if not overall else
            "Provider readiness is checked only when live mode is explicitly selected."
        ),
    }
