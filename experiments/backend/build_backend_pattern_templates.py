"""Build transferable backend pattern templates from action-level records."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "r3e"))

from microsurgeon_flow.backend_pattern_templates import build_pattern_template_file  # noqa: E402


ROOT = REPO_ROOT
DEFAULT_RECORDS = ROOT / ".iso_semrepair/backend_template_training_records.jsonl"
DEFAULT_OUT = ROOT / ".iso_semrepair/backend_pattern_templates.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", default=str(DEFAULT_RECORDS))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    patterns = build_pattern_template_file(args.records, args.out)
    summary = {
        "records": args.records,
        "out": args.out,
        "pattern_count": len(patterns),
        "patterns": [
            {
                "pattern_id": p.get("pattern_id"),
                "confidence": p.get("confidence"),
                "selector": p.get("selector"),
                "evidence": {
                    k: v for k, v in (p.get("evidence") or {}).items()
                    if k != "examples"
                },
            }
            for p in patterns
        ],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
