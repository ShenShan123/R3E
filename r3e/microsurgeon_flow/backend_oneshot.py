"""
backend_oneshot.py — CSR clean baseline 接线贯通（§8.2 Step 4，第一刀）。

只读产物，不真跑 OpenROAD。写入隔离根 artifacts/，不写真 backend_lib。
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_FLOW_DIR = Path(__file__).parent
_PROJECT_ROOT = _FLOW_DIR.parent
for _p in (str(_FLOW_DIR), str(_PROJECT_ROOT), str(_PROJECT_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from three_lib_gate import make_domain_io                            # noqa: E402
from microsurgeon_backend.tristate import (                          # noqa: E402
    detect_flow_signals, classify_tristate,
)
from tools.report_normalizer import _WNS_RE, parse_openroad_report  # noqa: E402


ORFS_ROOT = Path(os.environ.get("ORFS_ROOT", "/path/to/OpenROAD-flow-scripts")) / "flow"
PLATFORM  = "nangate45"
DESIGN    = "csr"
VARIANT   = "csr"

# 数值在 "slack (VIOLATED)" 同行前面，例: "                -0.40   slack (VIOLATED)"
_HOLD_RE = re.compile(r"([-+]?\d+\.?\d*)\s+slack\s*\(VIOLATED\)")


def _read_report(report_path: Path) -> str | None:
    if not report_path.is_file():
        return None
    return report_path.read_text(encoding="utf-8", errors="replace")


def _parse_wns_guarded(report_text: str) -> float | None:
    """解析自证闸：无 WNS 行 → None，绝不让默认 0.0 流入 classify。"""
    m = _WNS_RE.search(report_text)
    if m is None:
        return None
    return float(m.group(1))


def run_backend_oneshot(
    *,
    orfs_root: Path      = ORFS_ROOT,
    platform: str        = PLATFORM,
    design: str          = DESIGN,
    variant: str         = VARIANT,
    mem_root: Path | None = None,
    dry_run: bool        = False,
) -> dict:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # ── 1. 产物信号 ───────────────────────────────────────────────────────────
    results_root = orfs_root / "results"
    route_completed, gds_generated = detect_flow_signals(
        results_root, design, platform, variant=variant
    )
    route_odb = results_root / platform / design / variant / "5_2_route.odb"
    final_gds = results_root / platform / design / variant / "6_final.gds"

    # ── 2. 读报告 + 解析自证 ──────────────────────────────────────────────────
    report_path = orfs_root / "reports" / platform / design / variant / "6_finish.rpt"
    report_text = _read_report(report_path)

    final_wns: float | None = None
    final_tns: float | None = None
    if report_text is not None:
        final_wns = _parse_wns_guarded(report_text)
        if final_wns is not None:
            final_tns = parse_openroad_report(report_text).tns

    hold_worst: float | None = None
    if report_text is not None:
        hm = _HOLD_RE.search(report_text)
        hold_worst = float(hm.group(1)) if hm else None

    # ── 3. 判态 ───────────────────────────────────────────────────────────────
    tristate = classify_tristate(final_wns, route_completed, gds_generated)

    # ── 4. 组 payload ─────────────────────────────────────────────────────────
    case_id = f"csr_cleanbaseline_{ts}"
    bm_str = (
        f"setup_wns={final_wns} closed; hold={hold_worst} present; clean baseline real artifact"
        if final_wns is not None
        else "parse_failed: no WNS line found"
    )

    payload = {
        "skill_name": f"backend_csr_cleanbaseline_{ts}",
        "precondition": {
            "source":              "clean_baseline_real_artifact",
            "is_repair_increment": False,
            "design":              design,
            "platform":           platform,
            "variant":            variant,
        },
        "action_template": {
            "repair_strategy":        "observe_only",
            "allowed_edit_scope":     [f"openroad_eco:{design}"],
            "setup_wns":              final_wns,
            "tns":                    final_tns,
            "tristate":               tristate,
            "hold_violation_present": hold_worst is not None,
            "hold_worst":             hold_worst,
            "route_odb":              str(route_odb),
            "final_gds":              str(final_gds),
        },
        "validation": {
            "backend_metric": bm_str,
        },
        "rollback_condition": {
            "wns_degradation": False,
        },
        "case_id": case_id,
    }

    if dry_run:
        return payload

    # ── 5. 蒸馏 → 隔离根（mem_root 可注入；默认 artifacts/ 临时目录，不触真库）──
    iso_root = mem_root if mem_root is not None else (
        _PROJECT_ROOT / "artifacts" / f"backend_oneshot_csr_{ts}"
    )
    os.environ["MEMORY_ROOT"] = str(iso_root)

    mgr, guard = make_domain_io("backend")
    happened, vet_result, artifact = guard.vet_distill(mgr, **payload)

    return {
        **payload,
        "_distill_happened": happened,
        "_vet_approved":     vet_result.approved,
        "_vet_veto_code":    vet_result.veto_code,
        "_iso_root":         str(iso_root),
    }


if __name__ == "__main__":
    result = run_backend_oneshot()
    print(json.dumps(
        {k: v for k, v in result.items() if not k.startswith("_vet")},
        indent=2, default=str,
    ))
