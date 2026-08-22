#!/usr/bin/env python3
"""Validate the public competition package before creating an archive."""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUBMISSION = ROOT / "competition" / "docs" / "submission"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--strict", action="store_true", help="require final binary video/PPT artifacts")
    args = parser.parse_args()
    required = [
        SUBMISSION / "作品简介.md",
        SUBMISSION / "技术报告.md",
        SUBMISSION / "佐证材料索引.md",
        ROOT / "competition" / "docs" / "demo_script.md",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("missing submission material: " + ", ".join(missing))
    if args.strict:
        binary = [
            SUBMISSION / "作品演示视频.mp4",
            SUBMISSION / "作品汇报PPT.pdf",
        ]
        missing_binary = [str(path.relative_to(ROOT)) for path in binary if not path.is_file()]
        if missing_binary:
            raise SystemExit("strict submission material is missing: " + ", ".join(missing_binary))
    report = (SUBMISSION / "技术报告.md").read_text(encoding="utf-8")
    if "R³E：自进化RTL智能纠错系统" not in report:
        raise SystemExit("technical report does not use the formal work name")
    if "Formal Verification" in report and "formal proof" not in report.lower():
        raise SystemExit("technical report overclaims formal verification")
    print("Submission validation PASS" + (" (strict)" if args.strict else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
