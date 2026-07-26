"""
distill_loopback.py — loopback_lib 蒸馏: 跨域因果链(后端违例→前端修法引用).

一条 loopback skill 记录的知识:
  后端 failure_signature(因) → 退回前端做 repair Y(果) → 重跑 PnR closed.
对齐 distill_frontend.py 结构: 同 make_domain_io 入口, 同 vet_distill 蒸馏出口.
"""
from __future__ import annotations

import sys
from pathlib import Path

_FLOW_DIR = Path(__file__).parent
if str(_FLOW_DIR) not in sys.path:
    sys.path.insert(0, str(_FLOW_DIR))

from three_lib_gate import make_domain_io  # noqa: E402


def _frontend_scope_from_skill_name(frontend_skill_name: str) -> str:
    prefix = "frontend_"
    suffix_parts = frontend_skill_name.removeprefix(prefix).split("_")
    if len(suffix_parts) >= 5 and suffix_parts[-1].lower().startswith("l"):
        scope_key = "_".join(suffix_parts[:-2])
    else:
        scope_key = frontend_skill_name.removeprefix(prefix) or "unknown_frontend_repair"
    return f"frontend_rtl_block:{scope_key}"


def distill_loopback_skill(
    *,
    case_id: str,
    violating_endpoints: list,
    frontend_skill_name: str,
    backend_failure_class: str,
) -> tuple:
    """
    蒸一条 loopback skill 入 loopback_lib.

    payload schema 红线(对齐 §3.6.2):
    - precondition.is_loopback_skill = True (召回标记)
    - validation.loopback_metric 不用 _stub 后缀(干净语义串, 防 hollow 校验冲突)
    - skill_name 不带 design (对齐 failed skill, 便于跨设计合并)
    Returns (happened: bool, vet_result: VetResult, artifact_or_None).
    """
    mgr, guard = make_domain_io("loopback")
    payload = {
        "skill_name": f"loopback_{backend_failure_class}_to_frontend",
        "precondition": {
            "is_loopback_skill": True,
            "backend_failure_signature": {
                "violating_endpoints": violating_endpoints,
                "failure_class": backend_failure_class,
            },
        },
        "action_template": {
            "repair_domain": "frontend",
            "frontend_skill_ref": frontend_skill_name,
            "allowed_edit_scope": [_frontend_scope_from_skill_name(frontend_skill_name)],
        },
        "validation": {
            "loopback_metric": "reclosed",
        },
    }
    return guard.vet_distill(mgr, **payload, case_id=case_id)
