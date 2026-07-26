#!/usr/bin/env python3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
for base in (
    root / "r3e" / "semantic_repair_bench",
    root / "experiments" / "backend",
    root / "experiments" / "public_external_benchmarks",
    root / "experiments" / "blue_team_system",
    root / "experiments" / "red_team_system",
):
    if not base.is_dir():
        continue
    print(f"[{base.relative_to(root)}]")
    for path in sorted(base.rglob("*.py")):
        if not path.name.startswith("test_") and path.name != "__init__.py":
            first = path.read_text(errors="ignore").splitlines()
            summary = next((x.strip(' \"') for x in first[:8] if x.strip().startswith(('\"\"\"', "'''"))), "")
            print(f"  {path.relative_to(base)!s:64} {summary[:72]}")
