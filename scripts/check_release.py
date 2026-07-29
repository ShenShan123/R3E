#!/usr/bin/env python3
"""Fail closed on common privacy, secret, and generated-artifact leaks."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".py", ".sh", ".md", ".json", ".jsonl", ".toml", ".txt", ".tcl",
    ".sdc", ".example", ".v", ".sv", ".vh",
}
PATTERNS = {
    "private workspace path": re.compile(r"/data\d+/[^/\s]+/"),
    "private user path": re.compile(r"/(?:home|Users)/[^/\s]+/"),
    "machine-specific tool path": re.compile(r"/(?:opt|mnt|scratch)/[^\s\"']+"),
    "identity email": re.compile(
        r"(?i)\b(?!anonymous@users\.noreply\.github\.com\b)"
        r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"
    ),
    "shell prompt identity": re.compile(
        r"(?m)^\s*[A-Za-z0-9._-]+@[A-Za-z0-9._-]+:[^\n]*[$#]\s"
    ),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    "provider access token": re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|"
        r"AIza[A-Za-z0-9_-]{20,}|sk-ant-[A-Za-z0-9_-]{20,})\b"
    ),
    "PEM private key": re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    "assigned secret": re.compile(r"(?i)(?:api[_-]?key|token|secret|password)[ \t]*[:=][ \t]*['\"][^'\"\r\n]{8,}['\"]"),
}
FORBIDDEN_NAMES = {
    ".env", ".deepseek_env", ".llm_env", "__pycache__", ".pytest_cache",
    "artifacts", "results", ".formal_r3e", ".iso_semrepair",
    ".micro_surgeon_memory",
}
FORBIDDEN_SUFFIXES = {".pyc", ".log", ".odb", ".db", ".spef", ".vcd", ".sh"}
FORBIDDEN_TRACKED_PREFIXES = {
    "docs/",
    "runtime/",
    "results/",
    "artifacts/",
    "logs/",
    "local_data/",
    "private_data/",
    "experiment_data/",
}
FORBIDDEN_EXPERIMENT_DATA_SUFFIXES = {
    ".jsonl", ".log", ".out", ".vcd", ".fst", ".csv", ".parquet",
    ".npy", ".npz", ".pt", ".pth",
}

violations = []
tracked = subprocess.run(
    ["git", "-C", str(ROOT), "ls-files", "-z"],
    check=True,
    stdout=subprocess.PIPE,
).stdout.decode("utf-8").split("\0")
for relative in tracked:
    if not relative:
        continue
    if any(relative.startswith(prefix) for prefix in FORBIDDEN_TRACKED_PREFIXES):
        violations.append(f"forbidden tracked path: {relative}")
    path = Path(relative)
    if (
        path.parts[:1] == ("experiments",)
        and path.suffix.lower() in FORBIDDEN_EXPERIMENT_DATA_SUFFIXES
    ):
        violations.append(f"tracked local experiment data: {relative}")

for path in ROOT.rglob("*"):
    rel = path.relative_to(ROOT)
    generated_result_dir = any(
        part.startswith(".cross_benchmark_r3e_") for part in rel.parts
    )
    generated_result_file = (
        path.suffix in {".json", ".jsonl"}
        and re.search(r"(?:^|[_-])(result|results|summary|response|candidate)(?:[_-]|$)",
                      path.stem, re.IGNORECASE)
        is not None
    )
    frozen_protocol_config = rel.parts[:2] in {
        ("configs", "evolution"),
        ("configs", "blue"),
    }
    if (
        any(part in FORBIDDEN_NAMES for part in rel.parts)
        or generated_result_dir
        or (generated_result_file and not frozen_protocol_config)
        or path.suffix in FORBIDDEN_SUFFIXES
        or path.name.startswith(("run_", "launch_", "resume_", "rerun_"))
    ):
        violations.append(f"forbidden artifact: {rel}")
        continue
    if not path.is_file() or (path.suffix not in TEXT_SUFFIXES and path.name not in {"LICENSE"}):
        continue
    text = path.read_text(errors="ignore")
    for label, pattern in PATTERNS.items():
        # Public benchmark sources retain upstream copyright/contact notices.
        # They are required attribution, not identities of this artifact's
        # authors. All other privacy and secret rules still apply to them.
        third_party_input = rel.parts[:2] in {
            ("datasets", "cases"),
            ("datasets", "licenses"),
        }
        if label == "identity email" and third_party_input:
            continue
        if pattern.search(text):
            violations.append(f"{label}: {rel}")

if violations:
    raise SystemExit("Release audit FAILED:\n" + "\n".join(sorted(set(violations))))
print("Release audit PASS")
