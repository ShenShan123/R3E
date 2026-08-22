#!/usr/bin/env python3
"""Fail closed on common identity and machine-path leaks in competition files."""
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEXT = {".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".py", ".sh", ".html", ".js", ".css", ".svg"}
PATTERNS = {
    "private path": re.compile(r"/(?:data\d+|home|Users|scratch|mnt)/[^\s\"']+"),
    "identity email": re.compile(r"(?i)\b(?!anonymous@users\.noreply\.github\.com\b)[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    "credential": re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,})\b"),
    "institution marker": re.compile(r"(?i)(?:university|school name|指导教师|学校名称|学校 logo)"),
}

violations: list[str] = []
for path in (ROOT / "competition").rglob("*"):
    if not path.is_file() or path.suffix.lower() not in TEXT:
        continue
    if path.resolve() == Path(__file__).resolve():
        continue
    text = path.read_text(encoding="utf-8", errors="ignore")
    for label, pattern in PATTERNS.items():
        if pattern.search(text):
            violations.append(f"{label}: {path.relative_to(ROOT)}")

if violations:
    print("Anonymization check FAILED")
    print("\n".join(sorted(set(violations))))
    sys.exit(1)
print("Anonymization check PASS")
print("Binary images/video still require manual OCR/desktop review before submission.")
