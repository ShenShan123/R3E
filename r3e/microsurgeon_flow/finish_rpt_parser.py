"""
6_finish.rpt 解析: 从 'max reg to reg' 段提取那 1 条最差违例端点对.

§4.4 端点诊断信号 N=1 实现:
  - 段头严格锁 "finish report_checks -path_delay max reg to reg"
    (不会误匹配端口段的 "finish report_checks -path_delay max")
  - 段内取 Startpoint / Endpoint / slack (VIOLATED) 三个值
  - 段内无 VIOLATED (全 MET) 返 None, 不当违例
  - module_prefix = startpoint 第一段 (xxx.yyy.zzz → "xxx")

边界(已知):
  - 6_finish.rpt 默认每段只 -nworst 1, 故每次最多返 1 条
  - 多条 reg-to-reg 违例采集需 report_checks -nworst 35 → 进 §4.4 丰度优化 backlog
"""

from __future__ import annotations

import re
from typing import Optional

_SECTION_HEADER = re.compile(
    r"^finish report_checks -path_delay max reg to reg\s*$", re.MULTILINE
)
_NEXT_SECTION = re.compile(r"^finish report_checks", re.MULTILINE)
_STARTPOINT = re.compile(r"^Startpoint:\s+(\S+)", re.MULTILINE)
_ENDPOINT = re.compile(r"^Endpoint:\s+(\S+)", re.MULTILINE)
_SLACK_VIOLATED = re.compile(r"([-+]?\d+\.?\d*)\s+slack\s*\(VIOLATED\)")


def parse_reg2reg_endpoint(rpt_text: str) -> Optional[dict]:
    """从 finish.rpt 提取 max reg-to-reg 段那 1 条违例端点对.

    Returns
    -------
    dict | None
        成功: {"startpoint": str, "endpoint": str, "slack": float, "module_prefix": str}
        失败: None (段缺 / 段内全 MET / 字段缺 / slack 解析失败)
    """
    if not rpt_text:
        return None

    # 1) 锁段头
    hdr = _SECTION_HEADER.search(rpt_text)
    if not hdr:
        return None
    section_start = hdr.end()

    # 2) 段尾: 下一个 "finish report_checks" 或文件末
    nxt = _NEXT_SECTION.search(rpt_text, pos=section_start)
    section_end = nxt.start() if nxt else len(rpt_text)
    section = rpt_text[section_start:section_end]

    # 3) 段内 grep 三个字段
    sp_match = _STARTPOINT.search(section)
    ep_match = _ENDPOINT.search(section)
    sl_match = _SLACK_VIOLATED.search(section)
    if not (sp_match and ep_match and sl_match):
        return None  # 段内无违例(MET) 或 字段缺

    startpoint = sp_match.group(1)
    endpoint = ep_match.group(1)
    try:
        slack = float(sl_match.group(1))
    except ValueError:
        return None

    # 4) module_prefix: startpoint 第一段
    module_prefix = startpoint.split(".", 1)[0] if "." in startpoint else ""

    return {
        "startpoint": startpoint,
        "endpoint": endpoint,
        "slack": slack,
        "module_prefix": module_prefix,
    }


# 方案 D 刀1: reg-to-reg 段保序 cell 链解析(零改现有 parser).
_PATH_CELL_RE = re.compile(
    r"^\s*([\d.]+)\s+([\d.]+)\s+[v^]\s+(\S+)/(\S+)\s+\((\S+)\)"
)
_PATH_EDGE_RE = re.compile(r"\s([v^])\s")


def parse_reg2reg_cell_chain(report_text: str) -> list[dict]:
    """从 'reg to reg' 段抽保序 cell 链(data path 部分, 到 data arrival 止).

    返回 list(保序), 每元素:
        {"inst": str, "pin": str, "master": str, "edge": "^"|"v",
         "delay": float, "time": float}

    忠实抽取全链(含 launch/capture DFF、clkbuf、rebuffer), 不做过滤。
    若找不到 reg-to-reg 段或 data arrival 边界, 返回 []。
    """
    if not report_text:
        return []

    section = _SECTION_HEADER.search(report_text)
    if not section:
        return []

    chain: list[dict] = []
    saw_data_arrival = False
    for line in report_text[section.end():].splitlines():
        if "data arrival time" in line:
            saw_data_arrival = True
            break
        match = _PATH_CELL_RE.match(line)
        if not match:
            continue
        delay_s, time_s, inst, pin, master = match.groups()
        edge_match = _PATH_EDGE_RE.search(line)
        chain.append({
            "inst": inst,
            "pin": pin,
            "master": master,
            "edge": edge_match.group(1) if edge_match else "",
            "delay": float(delay_s),
            "time": float(time_s),
        })

    return chain if saw_data_arrival else []
