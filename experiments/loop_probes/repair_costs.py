"""Per-repair cost table from a run: LLM calls, tokens, time, memory use, outcome.

Reads either a loop state directory (its ``encounters`` ledger) or a probe's
``results.json`` (one ``accounting`` per arm). Prints one JSON line per repair
and a summary per group: repairs, fixed, and for fixed repairs the mean and
median calls, input and output tokens, plus how many used memory.

Usage: python -m experiments.loop_probes.repair_costs PATH [--csv OUT.csv]
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path

FIELDS = ("group", "case", "fixed", "fixed_at_attempt", "fixed_by_escalation", "llm_calls", "repair_attempts",
          "provider_failures", "input_tokens", "output_tokens", "model_seconds", "memory_delivered",
          "memory_offered_from_attempt")


def repairs(path: Path) -> list[dict]:
    if path.is_dir():
        rows = []
        for line in (path / "encounters.jsonl").read_text().splitlines():
            if line.strip():
                enc = json.loads(line)["encounter"]
                if "accounting" in enc:
                    rows.append({"group": f"round {json.loads(line)['round']}", "case": enc["challenge_id"],
                                 **enc["accounting"]})
        return rows
    rows = []
    for result in json.loads(path.read_text()):
        for arm, value in result.items():
            if isinstance(value, dict) and "accounting" in value:
                rows.append({"group": arm, "case": result["case"], **value["accounting"]})
    return rows


def summary(rows: list[dict]) -> dict:
    out = {}
    for group in sorted({r["group"] for r in rows}):
        g = [r for r in rows if r["group"] == group]
        fixed = [r for r in g if r["fixed"]]
        stat = lambda key: ({"mean": round(statistics.mean(r[key] for r in fixed), 1),
                             "median": statistics.median(r[key] for r in fixed)} if fixed else None)
        out[group] = {"repairs": len(g), "fixed": len(fixed), "fixed_with_memory": sum(r["memory_delivered"] for r in fixed),
                      "calls_per_fix": stat("llm_calls"), "input_tokens_per_fix": stat("input_tokens"),
                      "output_tokens_per_fix": stat("output_tokens"),
                      "total_calls": sum(r["llm_calls"] for r in g),
                      "total_tokens": sum(r["input_tokens"] + r["output_tokens"] for r in g)}
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("path", type=Path)
    p.add_argument("--csv", type=Path)
    args = p.parse_args()
    rows = repairs(args.path)
    if args.csv:
        with args.csv.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    for r in rows:
        print(json.dumps({k: r.get(k) for k in FIELDS}))
    print(json.dumps(summary(rows), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
