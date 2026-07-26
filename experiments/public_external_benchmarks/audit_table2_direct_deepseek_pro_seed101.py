#!/usr/bin/env python3
"""Aggregate and formally audit the one-seed Direct DeepSeek Pro run."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPECTED = {"cirfix39": 39, "literature32": 32, "strider14": 14}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def task_id(row: dict) -> str:
    return str(row.get("task_id") or row.get("case_id"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    args = parser.parse_args()
    protocol_path = args.protocol.resolve()
    protocol = json.loads(protocol_path.read_text())
    run_root = ROOT / protocol["run_root"]
    problems: list[str] = []
    aggregate: dict[str, dict] = {}
    for benchmark, expected_n in EXPECTED.items():
        manifest = ROOT / protocol["manifests"][benchmark]["path"]
        expected_ids = [task_id(row) for row in jsonl(manifest)]
        run_dir = run_root / benchmark / "seed_101"
        config_path = run_dir / "run_config.json"
        canonical_path = run_dir / "canonical_candidates.jsonl"
        heartbeat_path = run_dir / "heartbeat.json"
        if not all(path.is_file() for path in (config_path, canonical_path, heartbeat_path)):
            problems.append(f"{benchmark}: missing config/canonical/heartbeat")
            continue
        config = json.loads(config_path.read_text())
        heartbeat = json.loads(heartbeat_path.read_text())
        rows = jsonl(canonical_path)
        actual_ids = [str(row.get("case_id")) for row in rows]
        checks = {
            "method": config.get("method") == "direct_llm",
            "model": config.get("model") == "deepseek-v4-pro",
            "seed": config.get("seed") == 101,
            "temperature": config.get("temperature") == 0.2,
            "max_output_tokens": config.get("max_output_tokens") == 8192,
            "model_timeout_sec": config.get("model_timeout_sec") == 120,
            "one_call": config.get("max_semantic_model_calls_per_case") == 1,
            "state_off": not any(config.get("state_features", {}).values()),
            "manifest_hash": config.get("manifest_hash") == sha(manifest),
            "complete": heartbeat.get("status") == "complete" and heartbeat.get("completed_cases") == expected_n,
            "row_count": len(rows) == expected_n,
            "case_set": len(set(actual_ids)) == expected_n and set(actual_ids) == set(expected_ids),
            "candidate_zero": all(row.get("candidate_index") == 0 for row in rows),
            "row_model": all(row.get("model") == "deepseek-v4-pro" for row in rows),
            "row_seed": all(row.get("seed") == 101 for row in rows),
            "row_state_off": all(not row.get("used_memory") and not row.get("red_enabled")
                                 and row.get("population_size") == 1
                                 and not row.get("registry_enabled") and not row.get("template_enabled")
                                 and not row.get("promotion_enabled") and not row.get("cross_case_state")
                                 for row in rows),
        }
        for key, ok in checks.items():
            if not ok:
                problems.append(f"{benchmark}: {key}")
        for row in rows:
            for kind in ("prompt", "response"):
                p = Path(row.get(f"{kind}_path", ""))
                if not p.is_file() or sha(p) != row.get(f"{kind}_hash"):
                    problems.append(f"{benchmark}:{row.get('case_id')}: {kind} hash")
            patch_path = row.get("patch_path")
            if patch_path:
                p = Path(patch_path)
                if not p.is_file() or sha(p) != row.get("patch_hash"):
                    problems.append(f"{benchmark}:{row.get('case_id')}: patch hash")
        repaired = sum(bool(row.get("oracle_ok")) for row in rows)
        usage = Counter()
        for row in rows:
            usage.update({k: int(v or 0) for k, v in (row.get("token_usage") or {}).items() if isinstance(v, (int, float))})
        aggregate[benchmark] = {
            "seed": 101, "repaired": repaired, "total": expected_n,
            "percent": round(100.0 * repaired / expected_n, 2),
            "status_counts": dict(sorted(Counter(str(row.get("status")) for row in rows).items())),
            "token_usage": dict(sorted(usage.items())),
            "canonical_jsonl": str(canonical_path.relative_to(ROOT)),
            "canonical_sha256": sha(canonical_path),
        }
    audit = {
        "schema": "r3e-table2-direct-deepseek-v4-pro-seed101-audit-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "PASS" if not problems and len(aggregate) == len(EXPECTED) else "FAIL",
        "protocol": str(protocol_path.relative_to(ROOT)),
        "protocol_sha256": sha(protocol_path),
        "problems": problems,
        "aggregate": aggregate,
        "claim_scope": "one-seed descriptive backbone-swap diagnostic; no variance estimate",
    }
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "AGGREGATE.json").write_text(json.dumps(aggregate, indent=2, sort_keys=True) + "\n")
    (run_root / "FORMAL_AUDIT.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    print(json.dumps(audit, indent=2, sort_keys=True))
    return 0 if audit["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
