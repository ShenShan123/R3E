"""
三库安全闸 + 按域实例化工厂 (§8.2 Step 4 / §5.2 双闸落地).

纯逻辑 — 不接执行体、不真跑、不写真库、不联网、不调 LLM。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 假设: 本文件恒位于主仓库根的直接子目录 microsurgeon_flow/。
# parent.parent 即主仓根; 真库根据此定位。跨 worktree 使用时不要移动本文件,
# 在本目录内显式指定路径或复制依赖。
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from memory_manager import SkillMemoryManager        # noqa: E402
from memory_integrity_guard import SkillVetting      # noqa: E402

_ALLOWED_DOMAINS = frozenset({"frontend", "backend", "loopback"})

# 以模块位置而非 CWD 算真库根 — 保证无论从哪里跑都稳定
_REAL_MEMORY_ROOT: Path = (_PROJECT_ROOT / ".micro_surgeon_memory").resolve()


def resolve_memory_root_or_abort(domain: str) -> Path:
    """三道安全闸，通过后返回子库路径 ``root/{domain}_lib``。

    Gate 1: MEMORY_ROOT 环境变量必须已设。
    Gate 2: domain 仅允许 frontend / backend / loopback。
    Gate 3: root 若等于真库根 *或其任一子目录*，
            且 ALLOW_REAL_MEMORY_WRITE != '1' → abort。
            （拦住「直接指真库子库」的误操作，参 run_batch:2226）
    """
    raw = os.getenv("MEMORY_ROOT")
    if not raw:
        sys.exit("ABORT: MEMORY_ROOT 未设")

    if domain not in _ALLOWED_DOMAINS:
        sys.exit(
            f"ABORT: 非法 domain='{domain}'，仅允许 {sorted(_ALLOWED_DOMAINS)}"
        )

    root = Path(raw).resolve()

    # Gate 3: root 等于真库根，或 root 落在真库根之内（直接指子库）
    _touches_real = (root == _REAL_MEMORY_ROOT) or (_REAL_MEMORY_ROOT in root.parents)
    if _touches_real and os.getenv("ALLOW_REAL_MEMORY_WRITE") != "1":
        sys.exit(
            f"ABORT: MEMORY_ROOT 指向真库根或其子目录（{_REAL_MEMORY_ROOT}）"
            "，且 ALLOW_REAL_MEMORY_WRITE 未设为 '1'"
        )

    return root / f"{domain}_lib"


def make_domain_io(domain: str) -> tuple[SkillMemoryManager, SkillVetting]:
    """通过安全闸后，按域实例化 (SkillMemoryManager, SkillVetting)。

    空子库传 _skip_integrity_check=True，避免 _verify_integrity_on_load
    因无签名文件报错（真库已有签名时不影响，这里始终指临时子库）。
    """
    sublib = resolve_memory_root_or_abort(domain)
    mgr = SkillMemoryManager(memory_root=sublib, _skip_integrity_check=True)
    guard = SkillVetting(domain=domain, audit_log=sublib / "guard_audit.jsonl")
    return mgr, guard
