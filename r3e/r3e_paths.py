"""Portable filesystem configuration for the open-source R3E package."""
from __future__ import annotations

import os
from pathlib import Path


SOURCE_ROOT = Path(os.environ.get("R3E_SOURCE_ROOT", Path(__file__).resolve().parent))
REPO_ROOT = Path(os.environ.get("R3E_ROOT", SOURCE_ROOT.parent))
ARTIFACT_ROOT = Path(os.environ.get("R3E_ARTIFACT_ROOT", REPO_ROOT / "artifacts"))
CIRFIX_ROOT = Path(os.environ.get("CIRFIX_ROOT", REPO_ROOT / "third_party" / "cirfix"))
ORFS_ROOT = Path(os.environ.get("ORFS_ROOT", REPO_ROOT / "third_party" / "OpenROAD-flow-scripts"))
RTL_DATA_ROOT = Path(os.environ.get("RTL_DATA_ROOT", REPO_ROOT / "third_party" / "rtl-data"))
