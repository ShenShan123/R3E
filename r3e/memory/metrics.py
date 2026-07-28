"""Reconstructable RAAM storage, activation and replay metrics."""
from __future__ import annotations

from collections import Counter
from typing import Iterable

from .memory_store import MemoryStore
from .schema import ShadowPairedResult


def memory_inventory(store: MemoryStore) -> dict[str, int]:
    statuses = Counter()
    seen = set()
    from r3e.protocol.ledger import read_ledger

    for row in read_ledger(store.index_path):
        key = (row["memory_id"], int(row["memory_version"]))
        if key in seen:
            continue
        seen.add(key)
        statuses[store.current_status(*key)] += 1
    return {"stored": len(seen), **dict(sorted(statuses.items()))}


def replay_metrics(results: Iterable[ShadowPairedResult]) -> dict[str, float | int]:
    rows = list(results)
    counts = Counter(row.outcome for row in rows)
    total = len(rows)
    return {
        "paired_cases": total,
        "helped": counts["helped"],
        "harmed": counts["harmed"],
        "neutral_pass": counts["neutral_pass"],
        "neutral_fail": counts["neutral_fail"],
        "helped_rate": counts["helped"] / total if total else 0.0,
        "harmed_rate": counts["harmed"] / total if total else 0.0,
    }
