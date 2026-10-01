"""Carrier designs, challenges and clone-aware splits.

A *carrier* is a clean design with its visible testbench, and optionally a
hidden testbench. A *challenge* is a buggy variant of a carrier, produced by
Red or taken from a public dataset.

Splits are made by **design cluster**. Clones and variants of one source
design never span two splits. Clusters are merged when they share a design
family name or a normalized token structure, which catches copies such as
Strider's reuse of CirFix designs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from r3e.protocol.hashing import hash_payload
from r3e.knowledge.verilog_utils import is_identifier, safe_tokenize


SPLITS = ("discovery", "qualification", "holdout")


@dataclass(frozen=True)
class Carrier:
    carrier_id: str
    cluster_id: str
    clean_rtl: str
    top_module: str
    visible_tb: tuple[Path, ...]
    hidden_tb: tuple[Path, ...] = ()
    deps: tuple[Path, ...] = ()
    sim_timeout: float = 7.0
    source: Mapping[str, Any] = field(default_factory=dict)

    @property
    def carrier_hash(self) -> str:
        return hash_payload({
            "clean_rtl": hash_payload(self.clean_rtl),
            "visible_tb": [str(p) for p in self.visible_tb],
            "hidden_tb": [str(p) for p in self.hidden_tb],
            "deps": [str(p) for p in self.deps],
            "top": self.top_module,
        })


@dataclass(frozen=True)
class Challenge:
    challenge_id: str
    carrier: Carrier
    buggy_rtl: str
    origin: str  # "public" | "red"
    provenance: Mapping[str, Any] = field(default_factory=dict)

    @property
    def buggy_hash(self) -> str:
        return hash_payload(self.buggy_rtl)


def normalized_structure_hash(rtl: str) -> str:
    tokens = safe_tokenize(rtl)
    shape = [
        "ID" if is_identifier(t) else "NUM" if t.kind == "number" else t.value
        for t in tokens
    ]
    return hash_payload(shape)


def _family(design: str) -> str:
    return design.split("__", 1)[0]


def load_public_manifest(
    manifest: Path, project_root: Path
) -> tuple[list[Carrier], list[Challenge]]:
    """Carriers (one per clean design) and the dataset's buggy challenges."""
    rows = [json.loads(line) for line in Path(manifest).read_text(encoding="utf-8").splitlines() if line.strip()]
    carriers: dict[str, Carrier] = {}
    challenges: list[Challenge] = []
    for row in rows:
        golden = row.get("golden_rtl") or row.get("reference_path")
        if not golden or not row.get("tb_sources"):
            continue
        golden_path = project_root / golden
        if not golden_path.is_file():
            continue
        tb = tuple(project_root / p for p in row["tb_sources"])
        key = str(golden_path) + "|" + "|".join(map(str, tb))
        if key not in carriers:
            carriers[key] = Carrier(
                carrier_id="CR_" + hash_payload(key).split(":", 1)[1][:12],
                cluster_id=_family(str(row.get("design", golden_path.stem))),
                clean_rtl=golden_path.read_text(encoding="utf-8"),
                top_module=str(row.get("top_module") or "top"),
                visible_tb=tb,
                deps=tuple(project_root / p for p in row.get("deps") or []),
                sim_timeout=float(row.get("sim_timeout") or 7.0),
                source={"benchmark": row.get("benchmark"), "golden": str(golden)},
            )
        carrier = carriers[key]
        buggy_path = project_root / (row.get("buggy_rtl") or row.get("buggy_path") or "")
        if buggy_path.is_file():
            challenges.append(Challenge(
                challenge_id=str(row.get("case_id") or row.get("task_id")),
                carrier=carrier,
                buggy_rtl=buggy_path.read_text(encoding="utf-8"),
                origin="public",
                provenance={"design": row.get("design"), "benchmark": row.get("benchmark")},
            ))
    return list(carriers.values()), challenges


def merge_clusters(carriers: Sequence[Carrier]) -> dict[str, str]:
    """Union carriers that share a family name or a normalized structure."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for carrier in carriers:
        union(f"carrier:{carrier.carrier_id}", f"family:{carrier.cluster_id}")
        union(f"carrier:{carrier.carrier_id}", f"shape:{normalized_structure_hash(carrier.clean_rtl)}")
    return {c.carrier_id: find(f"carrier:{c.carrier_id}") for c in carriers}


def split_by_cluster(
    carriers: Sequence[Carrier],
    *,
    fractions: tuple[float, float, float] = (0.5, 0.2, 0.3),
    seed: int = 0,
) -> dict[str, list[Carrier]]:
    """Deterministic cluster-level split; returns {split: [carriers]}."""
    clusters = merge_clusters(carriers)
    roots = sorted(set(clusters.values()), key=lambda r: hash_payload({"seed": seed, "cluster": r}))
    n = len(roots)
    cut1 = max(1, round(fractions[0] * n)) if n >= 3 else n
    cut2 = cut1 + (max(1, round(fractions[1] * n)) if n >= 3 else 0)
    assignment = {}
    for index, root in enumerate(roots):
        assignment[root] = SPLITS[0] if index < cut1 else SPLITS[1] if index < cut2 else SPLITS[2]
    out: dict[str, list[Carrier]] = {s: [] for s in SPLITS}
    for carrier in carriers:
        out[assignment[clusters[carrier.carrier_id]]].append(carrier)
    return out


def challenges_in(challenges: Iterable[Challenge], carriers: Iterable[Carrier]) -> list[Challenge]:
    ids = {c.carrier_id for c in carriers}
    return [ch for ch in challenges if ch.carrier.carrier_id in ids]
