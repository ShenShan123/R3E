"""Post-collection measurement audits for the continuous loop (CONTINUOUS_BLUE_INTERFACE.md).

- ``exposure_audit``: for every Blue trial (online, reference, anchor), which entries of the incoming
  snapshot -- and of the actually delivered subset -- share its exact faulty task, or carry a prior Blue
  repair touching the region the current mutation edited (same carrier, clean bytes and path), mapped
  to half-open character intervals of the frozen clean source. Unmappable cases are ``unknown``.
- ``source_audit``: each unique learning source (task hash, candidate hash) checked once against the
  frozen evaluator, linked to every snapshot, entry and delivery that used it.

Both only read the run and write new files under ``<run>/audits``; neither changes snapshots, journals or
delivery records. Tags are not semantic equivalence, mechanism novelty or causal evidence.
"""
from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

from experiments.loop_probes import red907_measure as measure
from r3e.loop import repo_blue as rb
from r3e.loop.red_regression import save_new
from r3e.protocol.hashing import hash_payload

GENERATED_FRACTION = 0.5  # a repair rewriting more than this share of the file is not mapped


def _read(path):
    return json.loads(Path(path).read_text())


def _commits(run: Path) -> list[dict]:
    rounds = sorted((int(p.name), p) for p in (run / "rounds").iterdir() if (p / "commit.json").exists())
    return [_read(p / "commit.json") for _, p in rounds]


def _trials(run: Path, plan: dict) -> list[dict[str, Any]]:
    out = []
    for line in (run / "slots.jsonl").read_text().splitlines():
        row = json.loads(line)
        for role, trial in row.get("trials", {}).items():
            directory = Path(trial["directory"])
            out.append({"role": role, "slot_id": row["slot_id"], "round": row["round"], "directory": directory,
                        "task_dir": directory.parent / "admission" / "blue_task"})
    for line in (run / "probes.jsonl").read_text().splitlines():
        row = json.loads(line)
        out.append({"role": "anchor", "probe": Path(row["directory"]).name,
                    "directory": Path(row["directory"]),
                    "task_dir": Path(plan["anchors"][row["anchor_index"]]["task_dir"])})
    return [t for t in out if (t["directory"] / "delivery.json").exists()]


def _editable(task: rb.RepoTask) -> str:
    (rel,) = task.task["editable"]
    return rel


def faulty_identity(task_dir: Path) -> dict[str, str]:
    """Canonical faulty task: carrier, build configuration and sorted RTL contents; plus raw RTL bytes."""
    task = rb.RepoTask.load(Path(task_dir))
    files = [*task.build["rtl"], *task.build.get("headers", [])]
    rtl = {f: (task.repo / f).read_text() for f in sorted(files)}
    carrier = task.task["task_id"].split("__")[0]
    return {"canonical": hash_payload({"carrier": carrier, "build": rb.build_configuration(task), "rtl": rtl}),
            "raw_rtl": hash_payload((task.repo / _editable(task)).read_text()), "carrier": carrier}


def _changes(a: str, b: str) -> list[tuple[str, int, int, int, int]]:
    return [op for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op[0] != "equal"]


def _map_to_clean(lo: int, hi: int, clean: str, mutant: str) -> list[tuple[int, int]]:
    """Map a [lo, hi) interval (or point lo == hi) of the mutant to clean-source intervals."""
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, clean, mutant, autojunk=False).get_opcodes():
        if tag == "equal":
            if lo == hi and j1 <= lo < j2:
                out.append((i1 + lo - j1,) * 2)
            elif max(lo, j1) < min(hi, j2):
                out.append((i1 + max(lo, j1) - j1, i1 + min(hi, j2) - j1))
        elif (max(lo, j1) < min(hi, j2)) or (lo == hi and j1 <= lo <= j2) or (j1 == j2 and lo <= j1 < hi):
            out.append((i1, i2))  # a change inside a mutated block maps to that block's clean interval
    return out


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    if a[0] == a[1] and b[0] == b[1]:
        return a[0] == b[0]
    if a[0] == a[1]:
        return b[0] <= a[0] < b[1]
    if b[0] == b[1]:
        return a[0] <= b[0] < a[1]
    return max(a[0], b[0]) < min(a[1], b[1])


def _mutation(task_dir: Path) -> dict[str, Any]:
    """Current task: clean intervals edited by its mutation (difflib on the exported clean/mutant pair)."""
    task = rb.RepoTask.load(task_dir)
    rel = _editable(task)
    clean_path = task_dir / "oracle" / "clean" / rel
    if not clean_path.exists():
        return {"mapping": "unknown", "reason": "clean source unavailable"}
    clean, mutant = clean_path.read_text(), (task.repo / rel).read_text()
    return {"mapping": "known", "path": rel, "clean_hash": hash_payload(clean),
            "intervals": [(i1, i2) for _, i1, i2, _, _ in _changes(clean, mutant)]}


def _repair(record: dict[str, Any]) -> dict[str, Any]:
    """A source's Blue repair, mapped to the clean source of the task it repaired."""
    task_dir = Path(record["task_dir"])
    if not record.get("archive_path") or not Path(record["archive_path"]).exists() or not task_dir.exists():
        return {"mapping": "unknown", "reason": "source archive or task unavailable"}
    task = rb.RepoTask.load(task_dir)
    rel = _editable(task)
    files = _read(record["archive_path"])["files"]
    clean_path = task_dir / "oracle" / "clean" / rel
    if set(files) != {rel} or not clean_path.exists():
        return {"mapping": "unknown", "reason": "repair outside the editable file or clean source unavailable"}
    clean, mutant, repaired = clean_path.read_text(), (task.repo / rel).read_text(), files[rel]
    ops = _changes(mutant, repaired)
    if sum(max(i2 - i1, j2 - j1) for _, i1, i2, j1, j2 in ops) > GENERATED_FRACTION * max(len(mutant), 1):
        return {"mapping": "unknown", "reason": "repair rewrites most of the file (generated text)"}
    intervals = sorted({iv for _, i1, i2, _, _ in ops for iv in _map_to_clean(i1, i2, clean, mutant)})
    return {"mapping": "known", "path": rel, "clean_hash": hash_payload(clean), "intervals": intervals}


def _group(tags: dict[str, list]) -> str:
    if tags["same_mutant"]:
        return "exact_repeat"
    if tags["repair_span_overlap"]:
        return "span_overlap_only"
    return "unknown" if tags["unknown"] else "neither_observed"


def exposure_audit(run: Path) -> dict[str, Any]:
    run = Path(run)
    plan = _read(run / "plan.json")
    commits = _commits(run)
    sources: dict[str, dict] = {}
    rows = []
    for t in _trials(run, plan):
        delivery = _read(t["directory"] / "delivery.json")
        snap = commits[int(delivery["snapshot_id"].split("_")[1])]["blue"]
        if hash_payload(snap) != delivery["snapshot_hash"]:
            raise measure.MeasurementRefused(f"{t['directory']}: delivery is bound to a different snapshot")
        current, mutation = faulty_identity(t["task_dir"]), _mutation(t["task_dir"])
        store = {"same_mutant": [], "raw_rtl_repeat": [], "repair_span_overlap": [], "unknown": []}
        for entry in snap["entries"]:
            src = entry["body"]["source"]
            key = src["encounter_hash"]
            if key not in sources:
                record = _read(run / "sources" / (key[7:] + ".json"))
                sources[key] = {"identity": faulty_identity(Path(record["task_dir"])), "repair": _repair(record)}
            prior, eid = sources[key], entry["entry_hash"]
            if prior["identity"]["canonical"] == current["canonical"]:
                store["same_mutant"].append(eid)
            if prior["identity"]["raw_rtl"] == current["raw_rtl"]:
                store["raw_rtl_repeat"].append(eid)
            repair = prior["repair"]
            if prior["identity"]["carrier"] != current["carrier"]:
                continue  # a different source family cannot overlap; known, not unknown
            if "unknown" in (repair["mapping"], mutation["mapping"]):
                store["unknown"].append({"entry": eid, "reason": repair.get("reason") or mutation.get("reason")})
                continue
            if (repair["path"], repair["clean_hash"]) != (mutation["path"], mutation["clean_hash"]):
                continue
            hits = [[list(a), list(b)] for a in repair["intervals"] for b in mutation["intervals"] if _overlap(a, b)]
            if hits:
                store["repair_span_overlap"].append({"entry": eid, "intersections": hits})
        chosen = set(delivery["selected_entry_hashes"])
        delivered = {k: [x for x in v if (x["entry"] if isinstance(x, dict) else x) in chosen]
                     for k, v in store.items()}
        rows.append({**{k: str(v) if isinstance(v, Path) else v for k, v in t.items()},
                     "snapshot_id": delivery["snapshot_id"], "store_entries": len(snap["entries"]),
                     "delivered_entries": len(chosen), "mutation_mapping": mutation["mapping"],
                     "store": store, "delivered": delivered,
                     "store_group": _group(store), "delivered_group": _group(delivered)})
    groups = ("exact_repeat", "span_overlap_only", "neither_observed", "unknown")
    summary = {role: {scope: {g: sum(r[scope + "_group"] == g for r in rows if r["role"] == role) for g in groups}
                      for scope in ("store", "delivered")} for role in sorted({r["role"] for r in rows})}
    report = {"audit": "continuous_exposure_v1", "run_plan_hash": _read(run / "plan_hash.json")["hash"],
              "summary": summary, "trials": rows,
              "note": "difflib character alignment; tags are not semantic equivalence or causal evidence"}
    (run / "audits").mkdir(exist_ok=True)
    save_new(run / "audits" / "exposure_audit.json", report)
    return report


def source_audit(run: Path) -> dict[str, Any]:
    run = Path(run)
    plan = _read(run / "plan.json")
    commits = _commits(run)
    evaluators = {ident["carrier_id"]: c for ident, c in zip(plan["identities"], plan["carriers"])}
    verdicts: dict[tuple[str, str], dict] = {}
    for commit in commits:
        for entry in commit["blue"]["entries"]:
            src = entry["body"]["source"]
            key = (src["task_hash"], src["candidate_hash"])  # never candidate hash alone
            if key in verdicts:
                continue
            record = _read(run / "sources" / (src["encounter_hash"][7:] + ".json"))
            carrier = evaluators.get(src["carrier_id"])
            try:
                freeze = _read(carrier["evaluator_freeze"])
                root = Path(carrier["evaluator_root"])
                if any(hash_payload((root / r).read_text()) != h for r, h in freeze["files"].items()):
                    raise measure.MeasurementRefused("evaluator changed since its freeze")
                task = rb.RepoTask.load(Path(record["task_dir"]))
                files = _read(record["archive_path"])["files"]
                ev = measure._evaluate(task, files, freeze, root / Path(next(iter(freeze["files"]))).parent)
            except (OSError, KeyError, TypeError, ValueError, measure.MeasurementRefused) as exc:
                verdicts[key] = {"category": "unavailable", "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
                continue
            category = ("no_mismatch_within_scope" if ev["result"] == "pass" else
                        "checker_mismatch" if ev["result"] == "fail" and ev.get("failures") else "inconclusive")
            verdicts[key] = {"category": category, "evaluator_result": ev["result"], "failures": ev.get("failures", []),
                             "review": "PENDING" if category == "checker_mismatch" else None}
    deliveries: dict[str, list[str]] = {}
    for t in _trials(run, plan):
        d = _read(t["directory"] / "delivery.json")
        deliveries.setdefault(d["snapshot_id"], []).extend(d["selected_entry_hashes"])
    categories = ("no_mismatch_within_scope", "checker_mismatch", "inconclusive", "unavailable")
    per_snapshot = []
    for commit in commits:
        snap = commit["blue"]
        by_entry = {e["entry_hash"]: verdicts[(e["body"]["source"]["task_hash"], e["body"]["source"]["candidate_hash"])]
                    ["category"] for e in snap["entries"]}
        unique = {(e["body"]["source"]["task_hash"], e["body"]["source"]["candidate_hash"]) for e in snap["entries"]}
        delivered = deliveries.get(snap["snapshot_id"], [])
        per_snapshot.append({"snapshot_id": snap["snapshot_id"], "state_hash": snap["state_hash"],
            "unique_sources": {c: sum(verdicts[k]["category"] == c for k in unique) for c in categories},
            "entries": {c: sum(v == c for v in by_entry.values()) for c in categories},
            "deliveries": {c: sum(by_entry.get(h) == c for h in delivered) for c in categories}})
    report = {"audit": "continuous_source_quality_v1", "run_plan_hash": _read(run / "plan_hash.json")["hash"],
              "sources": [{"task_hash": k[0], "candidate_hash": k[1], **v} for k, v in sorted(verdicts.items())],
              "per_snapshot": per_snapshot,
              "note": "post-run only; no snapshot, prompt or label is changed; no-mismatch is bounded evidence"}
    (run / "audits").mkdir(exist_ok=True)
    save_new(run / "audits" / "source_audit.json", report)
    return report
