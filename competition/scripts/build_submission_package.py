#!/usr/bin/env python3
"""Build a portable competition zip without run logs or credentials."""
from __future__ import annotations

import argparse
import subprocess
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="/tmp/r3e-aic-submission.zip")
    args = parser.parse_args()
    subprocess.run(["python3", str(ROOT / "competition/scripts/check_anonymization.py")], check=True)
    subprocess.run(["python3", str(ROOT / "scripts/check_release.py")], check=True)
    subprocess.run(["python3", str(ROOT / "scripts/verify_datasets.py")], check=True)
    subprocess.run(["python3", str(ROOT / "competition/scripts/validate_frozen_evidence.py")], check=True)
    subprocess.run(["python3", str(ROOT / "competition/scripts/validate_submission.py"), "--check-only"], check=True)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    prefixes = ("competition/", "r3e/", "configs/", "datasets/", "README.md", "LICENSE", "pyproject.toml", "requirements.txt")
    forbidden = ("__pycache__", ".pytest_cache", ".env", ".log", ".vcd", ".fst", ".db", ".zip")
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file() or path == output:
                continue
            relative = path.relative_to(ROOT).as_posix()
            if not relative.startswith(prefixes) or any(token in relative for token in forbidden):
                continue
            archive.write(path, relative)
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
