"""
确定性分诊 (§4.3-1, §2.1): 调 yosys 综合判成败 → 域字符串。

纯逻辑 — 不写库、不真跑后端、不联网、不调 LLM。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 假设: 本文件恒位于主仓库根的直接子目录 microsurgeon_flow/。
# parent.parent 即主仓根; 真库根据此定位。跨 worktree 使用时不要移动本文件,
# 在本目录内显式指定路径或复制依赖。
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# tools/ 无 __init__.py — 直接把 tools/ 加入 path 后按模块名导入
_TOOLS_ROOT = _PROJECT_ROOT / "tools"
if str(_TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOLS_ROOT))

from synthesis_bridge import check_rtl_synthesizable  # noqa: E402


def triage(rtl_path: Path, design_name: str, work_dir: Path) -> str:
    """
    确定性分诊 (§4.3-1, §2.1): 调 yosys 综合判成败 → 域字符串。
    不由 LLM 决域。

    Returns: "frontend" (综合失败=RTL语义错) | "backend" (综合通过=进时序闭合)
    Raises:  RuntimeError 当综合失败源于环境而非 RTL (yosys 缺失 / 输出未生成),
             以免把环境问题误判成 RTL 语义 bug 喂进前端域。
    """
    success, message, _ = check_rtl_synthesizable(rtl_path, design_name, work_dir)

    if success:
        return "backend"

    # 白名单式: 只有明确的 "Synthesis check failed" 才判前端域; 前缀须与
    # synthesis_bridge:78 的 "Synthesis check failed:" 文案保持一致,改文案需同步此处。
    # 其余 (Yosys not found / Output not generated / 任何未识别 message) 一律 abort —
    # 工具失败不可错分类成内容失败 (对齐 frontend_gate:290 教训)。
    if message.startswith("Synthesis check failed"):
        return "frontend"

    raise RuntimeError(f"分诊无法判定(环境失败): {message}")
