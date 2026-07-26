"""Aggregate backend action observations into transferable pattern templates."""
from __future__ import annotations

import json
import re
from pathlib import Path
from statistics import mean


_STRENGTH_RE = re.compile(r"_X(\d+)$")
_FAMILY_RE = re.compile(r"^([A-Za-z0-9]+)_X\d+$")


def load_action_records(path: str | Path | None) -> list[dict]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    records: list[dict] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("record_type") == "backend_template_action_observation":
            records.append(row)
    return records


def cell_family(master: str | None) -> str:
    text = str(master or "")
    match = _FAMILY_RE.match(text)
    if match:
        return match.group(1)
    return text.split("_", 1)[0] if text else "unknown"


def cell_group(family: str) -> str:
    upper = family.upper()
    if upper.startswith(("CLKBUF", "CLKINV")):
        return "clock_buffer"
    if upper.startswith(("BUF", "INV")):
        return "buffer_or_inverter"
    if upper.startswith(("DFF", "SDFF", "DF")):
        return "sequential"
    if upper.startswith(("AOI", "OAI")):
        return "complex_logic"
    if upper.startswith(("AND", "OR", "NAND", "NOR", "XOR", "XNOR", "MUX")):
        return "basic_logic"
    if upper.startswith(("DLL", "LATCH")):
        return "latch_or_clock_gate"
    return "other_comb"


def drive_strength(master: str | None) -> int | None:
    match = _STRENGTH_RE.search(str(master or ""))
    return int(match.group(1)) if match else None


def drive_move(old_master: str | None, new_master: str | None) -> str:
    old = drive_strength(old_master)
    new = drive_strength(new_master)
    if old is None or new is None:
        return "unknown_move"
    if new <= old:
        return "non_upsize"
    ratio = new / max(old, 1)
    if ratio <= 2.0:
        return "moderate_upsize"
    return "large_upsize"


def _confidence(pos: int, neg: int, neutral: int, mean_delta: float | None) -> str:
    if pos <= 0 and neg > 0:
        return "negative_blacklist"
    if pos > neg and mean_delta is not None and mean_delta > 0.0:
        return "positive_prior"
    if pos > 0:
        return "mixed_prior"
    if neutral > 0:
        return "neutral_prior"
    return "unknown"


def aggregate_pattern_templates(records: list[dict]) -> list[dict]:
    buckets: dict[tuple[str, str, str, str, str, str], list[dict]] = {}
    for record in records:
        case = record.get("case", {})
        action = record.get("action", {})
        template = record.get("template", {})
        platform = str(case.get("platform", "unknown"))
        template_id = str(template.get("template_id", "unknown"))
        family = cell_family(action.get("current_master"))
        group = cell_group(family)
        role = str(action.get("target_role") or "unknown")
        move = drive_move(action.get("current_master"), action.get("new_master"))
        key = (platform, template_id, role, group, move, str(action.get("action_type", "size_cell")))
        buckets.setdefault(key, []).append(record)

    patterns: list[dict] = []
    for (platform, template_id, role, group, move, action_type), items in sorted(buckets.items()):
        labels: dict[str, int] = {}
        deltas: list[float] = []
        families: set[str] = set()
        examples: list[dict] = []
        selected = 0
        for record in items:
            label = str(record.get("sta", {}).get("label", "unknown"))
            labels[label] = labels.get(label, 0) + 1
            delta = record.get("sta", {}).get("delta_wns")
            if delta is not None:
                try:
                    deltas.append(float(delta))
                except (TypeError, ValueError):
                    pass
            action = record.get("action", {})
            families.add(cell_family(action.get("current_master")))
            if record.get("sta", {}).get("selected_by_gate"):
                selected += 1
            examples.append({
                "case": record.get("case", {}).get("name"),
                "current_master": action.get("current_master"),
                "new_master": action.get("new_master"),
                "delta_wns": delta,
                "label": label,
            })
        pos = labels.get("positive_improved", 0)
        neg = labels.get("negative_degraded", 0)
        neutral = labels.get("neutral_no_effect", 0)
        mean_delta = mean(deltas) if deltas else None
        score = float(pos) - float(neg) - 0.1 * float(neutral)
        pattern_id = (
            f"{platform}:{role}:{group}:{move}:"
            f"{template_id.rsplit(':', 1)[-1]}"
        )
        examples.sort(key=lambda e: e.get("delta_wns") if e.get("delta_wns") is not None else -999.0,
                      reverse=True)
        patterns.append({
            "schema_version": 1,
            "pattern_id": pattern_id,
            "source_template_id": template_id,
            "platform": platform,
            "selector": {
                "target_role": role,
                "cell_group": group,
                "observed_cell_families": sorted(families),
                "drive_move": move,
            },
            "action_policy": {
                "action_type": action_type,
                "move": move,
            },
            "evidence": {
                "support": len(items),
                "positive": pos,
                "negative": neg,
                "neutral": neutral,
                "selected_positive_records": selected,
                "mean_delta_wns": mean_delta,
                "min_delta_wns": min(deltas) if deltas else None,
                "max_delta_wns": max(deltas) if deltas else None,
                "score": score,
                "examples": examples[:5],
            },
            "confidence": _confidence(pos, neg, neutral, mean_delta),
            "guard": {
                "sta_required": True,
                "commit_rule": "new_wns > old_wns",
                "fallback": "llm",
            },
            "usage": {
                "not_for_prompt_injection": True,
                "not_exact_replay": True,
            },
        })
    patterns.sort(
        key=lambda p: (
            p["confidence"] == "positive_prior",
            p["evidence"].get("score", 0.0),
            p["evidence"].get("mean_delta_wns") or -999.0,
        ),
        reverse=True,
    )
    return patterns


def write_pattern_templates(patterns: list[dict], out_path: str | Path) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "pattern_count": len(patterns),
        "patterns": patterns,
    }, indent=2, ensure_ascii=False))


def build_pattern_template_file(records_path: str | Path, out_path: str | Path) -> list[dict]:
    patterns = aggregate_pattern_templates(load_action_records(records_path))
    write_pattern_templates(patterns, out_path)
    return patterns
