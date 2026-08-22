#!/usr/bin/env python3
"""Validate the four independent public evidence authorities."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FROZEN = ROOT / "competition" / "results" / "frozen"


def read(relative: str) -> dict:
    path = FROZEN / relative
    if not path.is_file():
        raise SystemExit(f"missing frozen evidence: {relative}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"frozen evidence must be an object: {relative}")
    return value


def main() -> int:
    demo = read("demo/manifest.json")
    benchmark = read("benchmark/manifest.json")
    evolution = read("evolution/manifest.json")
    memory = read("memory/manifest.json")
    if demo.get("available") is not True or len(demo.get("cases", [])) != 3:
        raise SystemExit("demo evidence must expose exactly three cases")
    for label, payload in (
        ("benchmark", benchmark), ("evolution", evolution), ("memory", memory)
    ):
        if payload.get("available") is True and not payload.get("result_hash"):
            raise SystemExit(f"{label} evidence is available without a result hash")
    metrics = read("benchmark/metrics.json")
    if benchmark.get("available") is not True and metrics.get("available") is True:
        raise SystemExit("benchmark manifest and metrics disagree")
    timeline = read("evolution/timeline.json")
    if evolution.get("available") is not True and timeline.get("available") is True:
        raise SystemExit("evolution manifest and timeline disagree")
    audit = read("memory/audit.json")
    if memory.get("available") is not True and audit.get("available") is True:
        raise SystemExit("memory manifest and audit disagree")
    print("Frozen evidence validation PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
