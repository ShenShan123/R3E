"""Correctness-gated skill accumulation curve on external RTL.

This replaces the old ``learning_curve.py`` memory baseline.  The old runner
retrieved raw historical memories and appended them to the LLM prompt.  This
runner treats memory as a correctness-gated skill store:

1. generate repairable semantic poisons from formal-proven RTL designs;
2. repair each poison with the current registry policy;
3. write a training record only when formal equivalence says the patch is
   correct;
4. distill accepted records into shadow family-level pattern templates;
5. activate only templates that have been independently promoted;
6. evaluate a fixed held-out poison stream at checkpoints while reporting both
   repair rate and memory/template growth.

The accumulated context is a compact family-level method template, not raw
case replay text.  By default, newly distilled templates are shadow-only; use
``--template-stage legacy-active`` only to reproduce older passive-crawl runs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from formal_gate import formal_judge  # noqa: E402
from functional_repair import apply_block, propose  # noqa: E402
from red_mutator import mutate_once  # noqa: E402
from skill_registry import infer_bug_family, load_registry, route  # noqa: E402
from formal_protocol import (  # noqa: E402
    append_decision_ledger as formal_append_decision_ledger,
    activate as formal_activate,
    commit_registry_atomically,
    decide_promotion as formal_decide_promotion,
    distill_candidates as formal_distill_candidates,
    hash_payload as formal_hash_payload,
    load_promoted_registry,
    load_registry as load_formal_registry,
    policy_hash as formal_policy_hash,
    validation_manifest_hash as formal_validation_manifest_hash,
)

ROOT = Path(__file__).resolve().parents[2]
EB_DEFAULT = Path(os.environ.get(
    "RTL_DATA_ROOT",
    ROOT / "third_party" / "external_rtl",
))
S_DEFAULT = Path(os.environ.get(
    "R3E_ARTIFACT_ROOT",
    ROOT / "artifacts",
)) / "skill_accumulation_curve"
REGISTRY_DEFAULT = ROOT / "configs" / "skills.json"

BASE_POLICY = {"evidence_k": 1, "n_candidates": 1, "patch_scope": "local_block"}

FAMILY_HINTS = {
    "off_by_one": (
        "Pattern template: inspect index ranges, bit slices, loop/count boundary "
        "conditions, and feedback source offsets. Prefer the smallest line-level "
        "correction that restores the original boundary relation."
    ),
    "constant_error": (
        "Pattern template: inspect literal constants, parameter defaults, case "
        "labels, and reset/terminal values. Prefer replacing only the incorrect "
        "constant or label."
    ),
    "condition_error": (
        "Pattern template: inspect swapped branch bodies, inverted predicates, "
        "and case arm assignments. Prefer preserving the control structure and "
        "only correcting the branch-specific assignment."
    ),
    "operator_error": (
        "Pattern template: inspect comparison/arithmetic/logic operators that "
        "can compile but invert the intended behavior. Prefer a single-operator "
        "correction."
    ),
    "data_flow_error": (
        "Pattern template: inspect reversed shift direction, swapped assignment "
        "source/target, and feedback direction. Prefer restoring the original "
        "dataflow relation without adding state."
    ),
    "state_swap_error": (
        "Pattern template: inspect current-state/next-state swaps and nonblocking "
        "assignment timing. Prefer restoring the correct state update relation."
    ),
}


@dataclass
class RtlDesign:
    name: str
    golden: str
    top: str
    deps: list[str]


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sha256_path(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _split_count_rows(path: Path, *, external_keys: set[str]) -> dict:
    if not path.exists():
        return {"exists": False, "rows": 0, "external_key_hits": []}
    hits: list[str] = []
    rows = 0
    for line in path.read_text(errors="ignore").splitlines():
        if not line.strip():
            continue
        rows += 1
        for key in external_keys:
            if key and key in line:
                hits.append(key)
    return {
        "exists": True,
        "rows": rows,
        "external_key_hits": sorted(set(hits)),
    }


def load_baseline_designs(
    path: str | None,
    *,
    require_ok: bool,
    require_sequential: bool,
    max_instance_count: int = 0,
) -> tuple[list[str], dict[str, dict]]:
    if not path:
        return [], {}
    rows = list(csv.DictReader(open(path, newline="")))
    ordered: list[str] = []
    by_design: dict[str, dict] = {}
    for row in rows:
        design = row.get("design")
        if not design:
            continue
        if require_sequential and row.get("is_sequential") != "true":
            continue
        if require_ok:
            baseline_ok = row.get("baseline10_status", row.get("status")) == "ok"
            best_ok = row.get("best_status", row.get("status")) in {"ok", ""}
            if not (baseline_ok and best_ok):
                continue
        if max_instance_count > 0:
            raw_inst = row.get("instance_count") or row.get("baseline10_instance_count") or "0"
            try:
                if int(float(raw_inst or 0)) > max_instance_count:
                    continue
            except ValueError:
                continue
        if design not in by_design:
            ordered.append(design)
        by_design[design] = row
    return ordered, by_design


def golden_of(bench_root: Path, design: str) -> RtlDesign | None:
    base = bench_root / design
    vs = list((base / "rtl").glob("*.v")) or list(base.rglob("*.v"))
    vs = [v for v in vs if "_tb" not in v.name and "test" not in v.name.lower()]
    if not vs:
        return None
    golden = vs[0]
    m = re.search(r"^\s*module\s+(\w+)", golden.read_text(errors="ignore"), re.M)
    if not m:
        return None
    deps = [str(v) for v in vs if v != golden]
    return RtlDesign(design, str(golden), m.group(1), deps)


def _replace_once(text: str, start: int, end: int, repl: str) -> str:
    return text[:start] + repl + text[end:]


def deterministic_mutation_candidates(text: str) -> list[dict]:
    candidates: list[dict] = []
    op_pairs = [
        ("==", "!=", "算子翻转"),
        ("!=", "==", "算子翻转"),
        ("<=", "<", "索引/位宽偏移(off-by-one)"),
        (">=", ">", "索引/位宽偏移(off-by-one)"),
        ("&&", "||", "条件分支交换"),
        ("||", "&&", "条件分支交换"),
        ("+", "-", "算子翻转"),
        ("-", "+", "算子翻转"),
        ("&", "|", "算子翻转"),
        ("|", "&", "算子翻转"),
        ("^", "|", "算子翻转"),
    ]
    for idx, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith("//")
            or stripped.startswith(("module ", "endmodule"))
        ):
            continue
        search_line = line.split("//", 1)[0]
        for old, new, mut_type in op_pairs:
            pos = search_line.find(old)
            if pos < 0:
                continue
            if old in {"<=", ">="} and "<=" in search_line and re.search(r"\w+\s*<=", search_line):
                # Avoid corrupting nonblocking assignments; keep <=/>= flips for predicates.
                prefix = search_line[:pos]
                if not re.search(r"\b(if|while|for)\b|[?:]", prefix):
                    continue
            new_line = _replace_once(line, pos, pos + len(old), new)
            candidates.append({
                "start_line": idx,
                "end_line": idx,
                "new_code": new_line,
                "mutation_type": mut_type,
                "rationale": f"Fixed semantic mutator replaced {old} with {new}.",
            })
        for m in re.finditer(r"\[(\d+)(?::(\d+))?\]", search_line):
            hi = int(m.group(1))
            lo = int(m.group(2)) if m.group(2) is not None else None
            if lo is None and hi > 0:
                repl = f"[{hi - 1}]"
            elif lo is None:
                repl = f"[{hi + 1}]"
            elif hi > lo:
                repl = f"[{hi - 1}:{lo}]"
            else:
                repl = f"[{hi + 1}:{lo}]"
            new_line = _replace_once(line, m.start(), m.end(), repl)
            candidates.append({
                "start_line": idx,
                "end_line": idx,
                "new_code": new_line,
                "mutation_type": "索引/位宽偏移(off-by-one)",
                "rationale": f"Fixed semantic mutator changed bit/range select {m.group(0)} to {repl}.",
            })
        for m in re.finditer(r"(?<![\w'])\d+(?![\w'])", search_line):
            val = int(m.group(0))
            if val == 0:
                repl = "1"
            else:
                repl = str(val - 1)
            new_line = _replace_once(line, m.start(), m.end(), repl)
            candidates.append({
                "start_line": idx,
                "end_line": idx,
                "new_code": new_line,
                "mutation_type": "常量错误",
                "rationale": f"Fixed semantic mutator changed decimal literal {val} to {repl}.",
            })
    return candidates


def fixed_mutate_once(golden_rtl: str, work_dir: Path, *, offset: int = 0) -> dict:
    text = Path(golden_rtl).read_text(errors="ignore")
    candidates = deterministic_mutation_candidates(text)
    if not candidates:
        return {"error": "no_fixed_mutation_candidate"}
    cand = candidates[offset % len(candidates)]
    try:
        buggy = apply_block(
            golden_rtl,
            int(cand["start_line"]),
            int(cand["end_line"]),
            cand["new_code"],
            Path(work_dir) / "buggy.v",
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"apply_failed: {exc}"}
    return {
        "buggy_path": str(buggy),
        "mutation_type": cand.get("mutation_type", ""),
        "rationale": cand.get("rationale", ""),
        "range": [cand.get("start_line"), cand.get("end_line")],
    }


def build_pool(
    bench_root: Path,
    work_root: Path,
    *,
    n_target: int,
    max_lines: int,
    formal_timeout: int,
    design_order: list[str] | None = None,
) -> list[RtlDesign]:
    pool: list[RtlDesign] = []
    designs = design_order or sorted(os.listdir(bench_root))
    for design in designs:
        if not (bench_root / design).is_dir():
            continue
        item = golden_of(bench_root, design)
        if not item:
            continue
        if Path(item.golden).read_text(errors="ignore").count("\n") > max_lines:
            continue
        j = formal_judge(
            item.golden,
            item.deps,
            item.golden,
            item.top,
            work_root / "self" / item.name,
            timeout=formal_timeout,
        )
        if j.equiv:
            pool.append(item)
        if len(pool) >= n_target:
            break
    return pool


def gen_poison(
    design: RtlDesign,
    work_dir: Path,
    *,
    formal_timeout: int,
    max_tries: int = 4,
    mutator: str = "llm",
) -> dict | None:
    for attempt in range(max_tries):
        try:
            if mutator == "fixed":
                m = fixed_mutate_once(
                    design.golden,
                    work_dir / f"mut{attempt}",
                    offset=attempt,
                )
            else:
                m = mutate_once(design.golden, work_dir / f"mut{attempt}")
        except Exception as exc:  # noqa: BLE001
            print(
                json.dumps({
                    "event": "poison_mutation_exception",
                    "design": design.name,
                    "attempt": attempt,
                    "error": str(exc),
                    "traceback": traceback.format_exc(limit=3),
                }, ensure_ascii=False),
                flush=True,
            )
            continue
        if "error" in m:
            print(
                json.dumps({
                    "event": "poison_mutation_rejected",
                    "design": design.name,
                    "attempt": attempt,
                    "error": m.get("error"),
                }, ensure_ascii=False),
                flush=True,
            )
            continue
        try:
            j = formal_judge(
                design.golden,
                design.deps,
                m["buggy_path"],
                design.top,
                work_dir / f"judge{attempt}",
                timeout=formal_timeout,
            )
        except Exception as exc:  # noqa: BLE001
            print(
                json.dumps({
                    "event": "poison_formal_exception",
                    "design": design.name,
                    "attempt": attempt,
                    "error": str(exc),
                    "traceback": traceback.format_exc(limit=3),
                }, ensure_ascii=False),
                flush=True,
            )
            continue
        # Treat a negative verdict as an admitted red counterexample only when
        # the formal report contains a rebuildable failed proof obligation.
        # Parser/tool failures may also report equiv=False and must fail closed.
        has_counterexample = (
            not j.equiv
            and j.proven is not None
            and j.total is not None
            and j.total > j.proven
        )
        if has_counterexample:
            return {
                "design": design.name,
                "golden": design.golden,
                "golden_sha256": _sha256_file(design.golden),
                "top": design.top,
                "deps": design.deps,
                "buggy": m["buggy_path"],
                "buggy_sha256": _sha256_file(m["buggy_path"]),
                "mutator": mutator,
                "mutation_type": m.get("mutation_type", ""),
                "mutator_rationale": m.get("rationale", ""),
                "mutator_range": m.get("range"),
                "tries": attempt + 1,
                "admission": {
                    "equiv": j.equiv,
                    "proven": j.proven,
                    "total": j.total,
                    "yosys_exit": j.yosys_exit,
                    "criterion": "proven is not None and total > proven",
                },
            }
        print(
            json.dumps({
                "event": "poison_formal_admission_rejected",
                "design": design.name,
                "attempt": attempt,
                "mutation_type": m.get("mutation_type", ""),
                "equiv": j.equiv,
                "proven": j.proven,
                "total": j.total,
                "yosys_exit": j.yosys_exit,
            }, ensure_ascii=False),
            flush=True,
        )
    return None


class PatternAccumulator:
    def __init__(
        self,
        record_path: Path,
        template_path: Path,
        *,
        min_support: int,
        promoted_template_path: Path | None = None,
        template_stage: str = "promoted",
    ):
        self.record_path = record_path
        self.template_path = template_path
        self.promoted_template_path = promoted_template_path
        self.ingestion_ledger_path = record_path.with_name("shadow_ingestion_ledger.jsonl")
        self.min_support = min_support
        self.template_stage = template_stage
        self.records = _jsonl(record_path)
        self.templates = self._distill()
        self.promoted_templates = self._load_promoted()

    def family_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for rec in self.records:
            fam = rec.get("family") or "*"
            counts[fam] = counts.get(fam, 0) + 1
        return dict(sorted(counts.items()))

    def _template_for_family(self, fam: str, count: int, origin_designs: list[str],
                             source_record_ids: list[str]) -> dict:
        strategy = FAMILY_HINTS.get(
            fam,
            "Pattern template: use the formal failure as a boundary constraint "
            "and prefer the smallest behavior-restoring patch.",
        )
        prompt = json.dumps({
            "task": "distill a generic RTL repair strategy",
            "family": fam,
            "support": count,
            "origin_designs": origin_designs,
            "source_record_ids": source_record_ids,
        }, ensure_ascii=False, sort_keys=True)
        template = {
            "skill_type": "frontend_semantic_repair_template",
            "template_id": f"accum_{fam}_v1",
            "name": f"accum_{fam}_v1",
            "family": fam,
            "status": "shadow",
            "support": count,
            "trigger": {
                "bug_family": fam,
                "design_pattern": ["unknown"],
                "evidence_signature": [
                    "formal_equivalence_fail",
                    "single_line_semantic_mutation",
                    "family_label_from_fixed_mutator",
                ],
            },
            "binding_policy": {
                "localize": "line_level_patch_region_from_llm_candidate",
                "rank_features": [
                    "same_scope_signal",
                    "same_width_or_literal_shape",
                    "near_mutation_site",
                    "minimal_patch_span",
                ],
            },
            "action_policy": {
                "candidate_patch_ops": ["minimal_line_level_semantic_repair"],
                "evidence_k": 1,
                "n_candidates": 3,
                "candidate_budget": 3,
                "requires_oracle_gate": True,
            },
            "negative_guards": [
                "do_not_apply_if_patch_changes_interface",
                "do_not_apply_if_formal_gate_fails",
                "do_not_promote_without_design_heldout_validation",
            ],
            "origin": "automatic",
            "generation_mode": "deterministic_family_distillation",
            "source_record_ids": source_record_ids,
            "candidate_prompt": prompt,
            "candidate_prompt_hash": _sha256_text(prompt),
            "candidate_raw_output": strategy,
            "candidate_output_hash": _sha256_text(strategy),
            "strategy_context": strategy,
            "evidence": {
                "origin_designs": origin_designs,
                "origin_design_count": len(origin_designs),
                "records": count,
                "hits": 0,
                "passes": 0,
                "regressions": 0,
                "validated_designs": [],
                "promotion_gate": "shadow_only_until_design_heldout_validation",
            },
        }
        template["candidate_hash"] = _sha256_text(json.dumps(template, ensure_ascii=False, sort_keys=True))
        return template

    def _distill(self) -> list[dict]:
        templates = []
        by_family_designs: dict[str, set[str]] = {}
        by_family_record_ids: dict[str, set[str]] = {}
        for rec in self.records:
            fam = rec.get("family") or "*"
            design = rec.get("design")
            if design:
                by_family_designs.setdefault(fam, set()).add(str(design))
            rid = rec.get("case_id") or rec.get("case_hash")
            if rid:
                by_family_record_ids.setdefault(fam, set()).add(str(rid))
        for fam, count in self.family_counts().items():
            if fam == "*" or count < self.min_support:
                continue
            templates.append(
                self._template_for_family(
                    fam,
                    count,
                    sorted(by_family_designs.get(fam, set())),
                    sorted(by_family_record_ids.get(fam, set())),
                )
            )
        _write_json(self.template_path, templates)
        return templates

    def _load_promoted(self) -> list[dict]:
        if not self.promoted_template_path or not self.promoted_template_path.exists():
            return []
        data = json.loads(self.promoted_template_path.read_text())
        if isinstance(data, dict):
            data = data.get("templates", [])
        if not isinstance(data, list):
            return []
        return [t for t in data if t.get("status") == "promoted"]

    def ingest_trajectory(self, poison: dict, result: dict) -> bool:
        """Canonicalize one trajectory and admit only rebuildable successes."""
        fam = infer_bug_family(poison)
        rec = {
            "schema_version": "r3e-shadow-trajectory-v1",
            "trajectory_id": result.get("trajectory_id") or _sha256_text(json.dumps({
                "case_id": poison.get("case_id"),
                "prompt_hash": result.get("prompt_hash"),
                "response_hash": result.get("response_hash"),
                "patched_sha256": result.get("patched_sha256"),
            }, sort_keys=True)),
            "case_id": poison.get("case_id"),
            "case_hash": poison.get("case_hash"),
            "split": poison.get("split", "train"),
            "design": poison.get("design"),
            "top": poison.get("top"),
            "golden_sha256": poison.get("golden_sha256"),
            "buggy_sha256": poison.get("buggy_sha256"),
            "family": fam,
            "mutation_type": poison.get("mutation_type"),
            "patch_range": result.get("patch_range"),
            "patch_new_code": result.get("patch_new_code"),
            "patch_hash": result.get("patch_hash"),
            "patched_sha256": result.get("patched_sha256"),
            "prompt_hash": result.get("prompt_hash"),
            "response_hash": result.get("response_hash"),
            "rationale": result.get("rationale", ""),
            "formal_gate": "equivalent_to_golden",
            "oracle_result_hash": result.get("oracle_result_hash"),
            "status": "shadow",
            "active": False,
        }
        outcome_ok = bool(
            result.get("repaired")
            and rec["patched_sha256"]
            and rec["patch_hash"]
            and rec["oracle_result_hash"]
        )
        provenance_ok = bool(
            rec["case_id"] and rec["golden_sha256"] and rec["buggy_sha256"]
            and rec["prompt_hash"] and rec["response_hash"]
            and rec["patch_range"] and rec["patch_new_code"] is not None
        )
        ledger = {
            "trajectory_id": rec["trajectory_id"],
            "case_id": rec["case_id"],
            "outcome_ok": outcome_ok,
            "provenance_ok": provenance_ok,
            "status": "shadow-recorded" if outcome_ok and provenance_ok else "rejected",
            "reasons": [
                reason for ok, reason in (
                    (outcome_ok, "outcome-not-rebuildable"),
                    (provenance_ok, "incomplete-provenance"),
                ) if not ok
            ],
        }
        formal_append_decision_ledger(self.ingestion_ledger_path, ledger)
        if not (outcome_ok and provenance_ok):
            return False
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        with self.record_path.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.records.append(rec)
        self.templates = self._distill()
        self.promoted_templates = self._load_promoted()
        return True

    def add_success(self, poison: dict, result: dict) -> None:
        if not self.ingest_trajectory(poison, result):
            raise RuntimeError("successful trajectory is not rebuildable or lacks provenance")

    def template_for(self, poison: dict) -> dict | None:
        if self.template_stage == "shadow":
            return None
        fam = infer_bug_family(poison)
        if self.template_stage == "legacy-active":
            return next((t for t in self.templates if t.get("family") == fam), None)
        return next((t for t in self.promoted_templates if t.get("family") == fam), None)

    def shadow_template_for(self, poison: dict) -> dict | None:
        fam = infer_bug_family(poison)
        return next((t for t in self.templates if t.get("family") == fam), None)

    def stats(self) -> dict:
        active = [
            t for t in (
                self.templates if self.template_stage == "legacy-active"
                else self.promoted_templates
            )
        ] if self.template_stage != "shadow" else []
        return {
            "records": len(self.records),
            "templates": len(self.templates),
            "shadow_templates": len(self.templates),
            "promoted_templates": len(self.promoted_templates),
            "active_templates": len(active),
            "template_stage": self.template_stage,
            "family_counts": self.family_counts(),
            "template_ids": [t["template_id"] for t in self.templates],
            "promoted_template_ids": [t["template_id"] for t in self.promoted_templates],
            "active_template_ids": [t["template_id"] for t in active],
        }


def _base_route(poison: dict, registry: list[dict]) -> tuple[dict, str]:
    if not registry:
        return dict(BASE_POLICY), "baseline"
    policy, skill_id = route({"mutation_type": poison.get("mutation_type", "")}, registry)
    out = dict(BASE_POLICY)
    out.update(policy or {})
    return out, skill_id


def blue_repair_formal(
    poison: dict,
    work_dir: Path,
    *,
    registry: list[dict],
    accumulator: PatternAccumulator | None,
    use_accumulated_templates: bool,
    formal_timeout: int,
    candidate_budget: int | None = None,
) -> dict:
    policy, skill_id = _base_route(poison, registry)
    template = accumulator.template_for(poison) if (accumulator and use_accumulated_templates) else None
    shadow_template = (
        accumulator.shadow_template_for(poison)
        if (accumulator and use_accumulated_templates)
        else None
    )
    strategy_context = ""
    if template:
        policy.update(template.get("action_policy") or {})
        strategy_context = (
            "\n## Correctness-gated pattern template\n"
            f"{template.get('strategy_context', '')}\n"
        )
    if candidate_budget is not None and candidate_budget > 0:
        policy["n_candidates"] = candidate_budget

    evidence = (
        "Formal equivalence check says the candidate RTL is not equivalent to "
        "the golden RTL. Repair the semantic mutation with the smallest RTL "
        "line-level patch. Mutation label: "
        f"{poison.get('mutation_type') or 'unknown'}."
    )
    candidates = []
    n_candidates = int(policy.get("n_candidates", 1))
    prompt_chars = (
        len(Path(poison["buggy"]).read_text(errors="ignore"))
        + len(evidence)
        + len(strategy_context)
    )
    for idx in range(max(1, n_candidates)):
        cr = {"cand": idx}
        prop = propose(poison["buggy"], evidence, strategy_context)
        if isinstance(prop, list):
            prop = next((x for x in prop if isinstance(x, dict)), None) or {}
        if not isinstance(prop, dict) or "start_line" not in prop:
            cr["error"] = "bad_format"
            candidates.append(cr)
            continue
        cr["patch_range"] = [prop.get("start_line"), prop.get("end_line")]
        cr["rationale"] = prop.get("rationale", "")
        cr["prompt_hash"] = prop.get("_formal_prompt_hash")
        cr["response_hash"] = prop.get("_formal_response_hash")
        cr["patch_new_code"] = str(prop.get("new_code", ""))
        cr["patch_hash"] = _sha256_text(json.dumps({
            "start_line": prop.get("start_line"),
            "end_line": prop.get("end_line"),
            "new_code": cr["patch_new_code"],
        }, ensure_ascii=False, sort_keys=True))
        try:
            patched = apply_block(
                poison["buggy"],
                int(prop["start_line"]),
                int(prop["end_line"]),
                prop["new_code"],
                work_dir / f"patched_c{idx}.v",
            )
        except Exception as exc:  # noqa: BLE001
            cr["apply_error"] = str(exc)
            candidates.append(cr)
            continue
        j = formal_judge(
            poison["golden"],
            poison["deps"],
            patched,
            poison["top"],
            work_dir / f"judge_c{idx}",
            timeout=formal_timeout,
        )
        cr["formal_equiv"] = j.equiv
        cr["formal_proven"] = j.proven
        cr["formal_total"] = j.total
        cr["formal_error"] = j.err
        cr["patched_sha256"] = _sha256_file(patched)
        cr["oracle_result_hash"] = formal_hash_payload({
            "equiv": j.equiv,
            "proven": j.proven,
            "total": j.total,
            "error": j.err,
        })
        candidates.append(cr)
        if j.equiv:
            break

    win = next((c for c in candidates if c.get("formal_equiv")), None)
    pass_at_1 = any(c.get("formal_equiv") for c in candidates[:1])
    pass_at_3 = any(c.get("formal_equiv") for c in candidates[:3])
    chosen = win or (candidates[-1] if candidates else {})
    applied = [c for c in candidates if "formal_equiv" in c]
    blocked_harmful = [c for c in applied if not c.get("formal_equiv")]
    return {
        "repaired": win is not None,
        "pass_at_1": pass_at_1,
        "pass_at_3": pass_at_3,
        "skill_id": skill_id,
        "family": infer_bug_family(poison),
        "used_accumulated_template": bool(template),
        "template_id": template.get("template_id") if template else None,
        "active_strategy_ids": [template.get("template_id")] if template else [],
        "active_policy_hash": template.get("formal_policy_hash") if template else None,
        "shadow_template_hit": bool(shadow_template),
        "shadow_template_id": shadow_template.get("template_id") if shadow_template else None,
        "template_stage": accumulator.template_stage if accumulator else "none",
        "effective_policy": policy,
        "n_candidates_tried": len(candidates),
        "patch_range": chosen.get("patch_range"),
        "patch_new_code": chosen.get("patch_new_code"),
        "patch_hash": chosen.get("patch_hash"),
        "patched_sha256": chosen.get("patched_sha256"),
        "prompt_hash": chosen.get("prompt_hash"),
        "response_hash": chosen.get("response_hash"),
        "oracle_result_hash": chosen.get("oracle_result_hash"),
        "rationale": chosen.get("rationale", ""),
        "candidates": candidates,
        "cost": {
            "llm_calls": len(candidates),
            "formal_checks": len(applied),
            "applied_candidates": len(applied),
            "blocked_harmful_candidates": len(blocked_harmful),
            "estimated_prompt_tokens": (prompt_chars * len(candidates) + 3) // 4,
        },
    }


def repair_many(
    poisons: list[dict],
    work_dir: Path,
    *,
    registry: list[dict],
    accumulator: PatternAccumulator | None,
    use_accumulated_templates: bool,
    formal_timeout: int,
    reps: int,
    candidate_budget: int | None = None,
) -> dict:
    rates = []
    pass1_rates = []
    pass3_rates = []
    rows = []
    totals = {
        "llm_calls": 0,
        "formal_checks": 0,
        "applied_candidates": 0,
        "blocked_harmful_candidates": 0,
        "estimated_prompt_tokens": 0,
        "template_hits": 0,
        "accepted_harmful_hits": 0,
        "shadow_template_hits": 0,
    }
    for rep in range(reps):
        ok = 0
        pass1 = 0
        pass3 = 0
        for idx, poison in enumerate(poisons):
            res = blue_repair_formal(
                poison,
                work_dir / f"rep{rep}" / f"case{idx}",
                registry=registry,
                accumulator=accumulator,
                use_accumulated_templates=use_accumulated_templates,
                formal_timeout=formal_timeout,
                candidate_budget=candidate_budget,
            )
            ok += int(res.get("repaired"))
            pass1 += int(res.get("pass_at_1"))
            pass3 += int(res.get("pass_at_3"))
            cost = res.get("cost") or {}
            for key in (
                "llm_calls",
                "formal_checks",
                "applied_candidates",
                "blocked_harmful_candidates",
                "estimated_prompt_tokens",
            ):
                totals[key] += int(cost.get(key) or 0)
            if res.get("used_accumulated_template"):
                totals["template_hits"] += 1
                if not res.get("repaired"):
                    totals["accepted_harmful_hits"] += 1
            if res.get("shadow_template_hit"):
                totals["shadow_template_hits"] += 1
            rows.append({
                "rep": rep,
                "idx": idx,
                "case_id": poison.get("case_id"),
                "case_hash": poison.get("case_hash"),
                "design": poison.get("design"),
                "family": res.get("family"),
                "repaired": res.get("repaired"),
                "pass_at_1": res.get("pass_at_1"),
                "pass_at_3": res.get("pass_at_3"),
                "used_accumulated_template": res.get("used_accumulated_template"),
                "template_id": res.get("template_id"),
                "active_strategy_ids": res.get("active_strategy_ids", []),
                "active_policy_hash": res.get("active_policy_hash"),
                "shadow_template_hit": res.get("shadow_template_hit"),
                "shadow_template_id": res.get("shadow_template_id"),
                "template_stage": res.get("template_stage"),
                "llm_calls": cost.get("llm_calls", 0),
                "formal_checks": cost.get("formal_checks", 0),
                "estimated_prompt_tokens": cost.get("estimated_prompt_tokens", 0),
                "blocked_harmful_candidates": cost.get("blocked_harmful_candidates", 0),
            })
        rates.append(ok / len(poisons) if poisons else 0.0)
        pass1_rates.append(pass1 / len(poisons) if poisons else 0.0)
        pass3_rates.append(pass3 / len(poisons) if poisons else 0.0)
    denom = len(poisons) * reps if poisons else 0
    return {
        "rates": rates,
        "mean": round(sum(rates) / len(rates), 4) if rates else 0.0,
        "pass_at_1_mean": round(sum(pass1_rates) / len(pass1_rates), 4) if pass1_rates else 0.0,
        "pass_at_3_mean": round(sum(pass3_rates) / len(pass3_rates), 4) if pass3_rates else 0.0,
        "pass_at_1_rates": pass1_rates,
        "pass_at_3_rates": pass3_rates,
        "n": len(poisons),
        "rows": rows,
        "metrics": {
            **totals,
            "template_hit_rate": round(totals["template_hits"] / denom, 4) if denom else 0.0,
            "shadow_template_hit_rate": round(totals["shadow_template_hits"] / denom, 4)
            if denom else 0.0,
            "accepted_harmful_hit_rate": round(
                totals["accepted_harmful_hits"] / max(1, totals["template_hits"]), 4
            ),
            "blocked_harmful_candidate_rate": round(
                totals["blocked_harmful_candidates"] / max(1, totals["applied_candidates"]), 4
            ),
            "avg_candidates": round(totals["llm_calls"] / denom, 4) if denom else 0.0,
        },
    }


def repair_many_with_stage(
    poisons: list[dict],
    work_dir: Path,
    *,
    registry: list[dict],
    accumulator: PatternAccumulator | None,
    template_stage: str,
    use_accumulated_templates: bool,
    formal_timeout: int,
    reps: int,
    candidate_budget: int | None = None,
) -> dict:
    if accumulator is None:
        return repair_many(
            poisons,
            work_dir,
            registry=registry,
            accumulator=None,
            use_accumulated_templates=use_accumulated_templates,
            formal_timeout=formal_timeout,
            reps=reps,
            candidate_budget=candidate_budget,
        )
    old_stage = accumulator.template_stage
    try:
        accumulator.template_stage = template_stage
        return repair_many(
            poisons,
            work_dir,
            registry=registry,
            accumulator=accumulator,
            use_accumulated_templates=use_accumulated_templates,
            formal_timeout=formal_timeout,
            reps=reps,
            candidate_budget=candidate_budget,
        )
    finally:
        accumulator.template_stage = old_stage


class _CandidateReplayAccumulator:
    """Read-only single-strategy view used for candidate-isolated replay."""

    template_stage = "legacy-active"

    def __init__(self, template: dict):
        self.template = template

    def template_for(self, poison: dict) -> dict | None:
        return self.template if infer_bug_family(poison) == self.template.get("family") else None

    def shadow_template_for(self, poison: dict) -> dict | None:
        return self.template_for(poison)


def _paired_formal_rows(
    baseline_rows: list[dict], candidate_rows: list[dict], *, split: str,
) -> list[dict]:
    def keyed(rows: list[dict]) -> dict[tuple[int, str], dict]:
        result = {}
        for row in rows:
            case_id = str(row.get("case_id") or "")
            if not case_id:
                raise RuntimeError("promotion replay row is missing case_id")
            key = (int(row.get("rep") or 0), case_id)
            if key in result:
                raise RuntimeError(f"duplicate promotion replay row: {key}")
            result[key] = row
        return result

    base = keyed(baseline_rows)
    candidate = keyed(candidate_rows)
    if set(base) != set(candidate):
        raise RuntimeError("baseline/candidate replay case IDs are not paired")
    rows = []
    for key in sorted(base):
        br, cr = base[key], candidate[key]
        if br.get("design") != cr.get("design"):
            raise RuntimeError(f"baseline/candidate design mismatch: {key}")
        common = {
            "case_id": key[1], "rep": key[0], "split": split,
            "design": cr.get("design"),
        }
        rows.append({**common, "arm": "baseline", "oracle_ok": bool(br.get("repaired")),
                     "strategy_hit": False})
        rows.append({**common, "arm": "candidate", "oracle_ok": bool(cr.get("repaired")),
                     "strategy_hit": bool(cr.get("used_accumulated_template"))})
    return rows


def promotion_validation_gate(
    *,
    label: str,
    validation_poisons: list[dict],
    non_target_poisons: list[dict],
    work_dir: Path,
    registry: list[dict],
    accumulator: PatternAccumulator,
    use_accumulated_templates: bool,
    formal_timeout: int,
    reps: int,
    candidate_budget: int | None,
    min_hits: int,
    min_designs: int,
    min_hit_to_pass_at_3: float,
    min_target_gain: float,
    non_target_epsilon: float,
) -> dict:
    """Run each shadow strategy alone on frozen target and disjoint Rnon sets."""
    if not validation_poisons or not non_target_poisons:
        _write_json(accumulator.promoted_template_path, [])
        accumulator.promoted_templates = accumulator._load_promoted()
        report = {
            "label": label, "enabled": False,
            "reason": "missing_target_replay" if not validation_poisons else "missing_non_target_replay",
            "validation_n": len(validation_poisons),
            "non_target_n": len(non_target_poisons),
            "promoted_count": 0, "promoted_template_ids": [], "decisions": [],
        }
        _write_json(work_dir / "promotion_validation_report.json", report)
        return report
    if not accumulator.templates:
        _write_json(accumulator.promoted_template_path, [])
        accumulator.promoted_templates = accumulator._load_promoted()
        report = {
            "label": label, "enabled": False, "reason": "no_shadow_templates",
            "validation_n": len(validation_poisons), "non_target_n": len(non_target_poisons),
            "promoted_count": 0, "promoted_template_ids": [], "decisions": [],
        }
        _write_json(work_dir / "promotion_validation_report.json", report)
        return report

    baseline_target = repair_many_with_stage(
        validation_poisons, work_dir / "baseline_target", registry=registry,
        accumulator=accumulator, template_stage="shadow", use_accumulated_templates=False,
        formal_timeout=formal_timeout, reps=reps, candidate_budget=candidate_budget,
    )
    baseline_non_target = repair_many_with_stage(
        non_target_poisons, work_dir / "baseline_non_target", registry=registry,
        accumulator=accumulator, template_stage="shadow", use_accumulated_templates=False,
        formal_timeout=formal_timeout, reps=reps, candidate_budget=candidate_budget,
    )
    promoted: list[dict] = []
    decisions: list[dict] = []
    replay_rows_by_template: dict[str, list[dict]] = {}
    candidate_rows_all: list[dict] = []

    for template in sorted(accumulator.templates, key=lambda row: str(row.get("template_id"))):
        tid = str(template.get("template_id"))
        family = template.get("family")
        target_poisons = [p for p in validation_poisons if infer_bug_family(p) == family]
        target_ids = {str(p.get("case_id")) for p in target_poisons}
        baseline_target_rows = [
            row for row in baseline_target.get("rows", []) if str(row.get("case_id")) in target_ids
        ]
        view = _CandidateReplayAccumulator(template)
        candidate_target = repair_many(
            target_poisons, work_dir / "candidate" / tid / "target", registry=registry,
            accumulator=view, use_accumulated_templates=use_accumulated_templates,
            formal_timeout=formal_timeout, reps=reps, candidate_budget=candidate_budget,
        ) if target_poisons else {"rows": [], "metrics": {}}
        candidate_non_target = repair_many(
            non_target_poisons, work_dir / "candidate" / tid / "non_target", registry=registry,
            accumulator=view, use_accumulated_templates=use_accumulated_templates,
            formal_timeout=formal_timeout, reps=reps, candidate_budget=candidate_budget,
        )
        target_rows = candidate_target.get("rows", [])
        non_target_rows = candidate_non_target.get("rows", [])
        candidate_rows_all.extend([{**row, "candidate_template_id": tid} for row in target_rows])
        candidate_rows_all.extend([{**row, "candidate_template_id": tid} for row in non_target_rows])

        if target_rows:
            formal_rows = _paired_formal_rows(
                baseline_target_rows, target_rows, split="target",
            ) + _paired_formal_rows(
                baseline_non_target.get("rows", []), non_target_rows, split="non_target",
            )
            replay_rows_by_template[tid] = formal_rows
            target_candidate = [r for r in formal_rows if r["split"] == "target" and r["arm"] == "candidate"]
            target_baseline = [r for r in formal_rows if r["split"] == "target" and r["arm"] == "baseline"]
            non_candidate = [r for r in formal_rows if r["split"] == "non_target" and r["arm"] == "candidate"]
            non_baseline = [r for r in formal_rows if r["split"] == "non_target" and r["arm"] == "baseline"]
        else:
            formal_rows = []
            target_candidate = target_baseline = non_candidate = non_baseline = []
        hits = sum(bool(r.get("strategy_hit")) for r in target_candidate)
        passes = sum(
            bool(r.get("oracle_ok")) and bool(r.get("strategy_hit"))
            for r in target_candidate
        )
        target_successes = sum(bool(r.get("oracle_ok")) for r in target_candidate)
        baseline_passes = sum(bool(r.get("oracle_ok")) for r in target_baseline)
        recovery_ratio = passes / hits if hits else 0.0
        target_gain = (
            target_successes / len(target_candidate) - baseline_passes / len(target_baseline)
            if target_candidate and target_baseline else 0.0
        )
        non_rate = sum(bool(r.get("oracle_ok")) for r in non_candidate) / len(non_candidate) if non_candidate else 0.0
        baseline_non_rate = sum(bool(r.get("oracle_ok")) for r in non_baseline) / len(non_baseline) if non_baseline else 0.0
        non_delta = non_rate - baseline_non_rate
        designs = sorted({str(r.get("design")) for r in target_candidate if r.get("strategy_hit")})
        gates = {
            "support_ok": hits >= min_hits,
            "coverage_ok": len(designs) >= min_designs,
            "recovery_ok": recovery_ratio >= min_hit_to_pass_at_3,
            "gain_ok": target_gain > min_target_gain,
            "regression_ok": non_delta >= -non_target_epsilon,
        }
        reasons = [name.removesuffix("_ok") for name, ok in gates.items() if not ok]
        decision = {
            "template_id": tid, "family": family,
            "promote": all(gates.values()), "reasons": reasons, **gates,
            "validation_hits": hits, "validated_designs": designs,
            "validated_design_count": len(designs),
            "target_successes": target_successes, "target_recovered_hits": passes,
            "target_total": len(target_candidate),
            "target_recovery_ratio": round(recovery_ratio, 6),
            "target_gain": round(target_gain, 6),
            "non_target_total": len(non_candidate),
            "non_target_delta": round(non_delta, 6),
            "harmful_hits": sum(
                bool(br.get("oracle_ok")) and not bool(cr.get("oracle_ok"))
                for br, cr in zip(non_baseline, non_candidate)
            ),
            "thresholds": {
                "min_validation_hits": min_hits,
                "min_covered_designs": min_designs,
                "min_target_recovery_ratio": min_hit_to_pass_at_3,
                "min_target_gain": min_target_gain,
                "non_target_regression_tolerance": non_target_epsilon,
            },
            "validation_manifest_hash": (
                formal_validation_manifest_hash(formal_rows) if formal_rows else None
            ),
        }
        decision["decision_hash"] = _sha256_text(
            json.dumps(decision, ensure_ascii=False, sort_keys=True)
        )
        decisions.append(decision)
        if decision["promote"]:
            promoted_template = dict(template)
            promoted_template["status"] = "promoted"
            promoted_template["promotion_decision_hash"] = decision["decision_hash"]
            promoted_template.setdefault("evidence", {}).update({
                "promotion_gate": "candidate_isolated_target_and_non_target_replay",
                "checkpoint": label, "validation_hits": hits,
                "validated_designs": designs, "target_recovery_ratio": recovery_ratio,
                "target_gain": target_gain, "non_target_delta": non_delta,
            })
            promoted.append(promoted_template)

    _write_json(accumulator.promoted_template_path, promoted)
    accumulator.promoted_templates = accumulator._load_promoted()
    report = {
        "label": label, "enabled": True,
        "validation_n": len(validation_poisons), "non_target_n": len(non_target_poisons),
        "baseline_pass_at_1": baseline_target.get("pass_at_1_mean"),
        "baseline_pass_at_3": baseline_target.get("pass_at_3_mean"),
        "promoted_count": len(promoted),
        "promoted_template_ids": [t.get("template_id") for t in promoted],
        "decisions": decisions,
        "baseline_rows": baseline_target.get("rows", []),
        "non_target_baseline_rows": baseline_non_target.get("rows", []),
        "shadow_rows": candidate_rows_all,
        "candidate_validation_rows": replay_rows_by_template,
        "policy": (
            "Each candidate is replayed alone on its frozen target subset and on a "
            "disjoint frozen non-target set. External-Frozen is evaluation-only."
        ),
    }
    _write_json(work_dir / "promotion_validation_report.json", report)
    return report


def commit_gate_positive_templates_to_formal_registry(
    *,
    registry_path: Path,
    decisions_path: Path,
    accumulator: PatternAccumulator,
    promotion_report: dict,
) -> dict:
    """Commit gate-positive templates through the formal atomic registry API.

    The legacy promoted-template JSON remains telemetry only. Runtime authority
    is reconstructed exclusively from the validated formal registry.
    """
    registry = load_formal_registry(registry_path, formal_mode=True)
    persisted = json.loads(decisions_path.read_text()) if decisions_path.exists() else []
    decision_map = {d["decision_hash"]: d for d in persisted}
    active, before_policy = load_promoted_registry(registry_path, decision_map)
    existing_families = {a.get("trigger", {}).get("bug_family") for a in active}
    replay_rows_by_template = promotion_report.get("candidate_validation_rows") or {}
    ledger_path = decisions_path.with_name("formal_decision_ledger.jsonl")
    committed = []
    for gate_decision in promotion_report.get("decisions") or []:
        tid = gate_decision.get("template_id")
        template = next((t for t in accumulator.templates if t.get("template_id") == tid), None)
        if not template:
            raise RuntimeError(f"shadow template missing: {tid}")
        family = template.get("family")
        if family in existing_families:
            formal_append_decision_ledger(ledger_path, {
                "artifact_id": tid, "status": "inactive-existing-family",
                "family": family,
            })
            continue
        validation_rows = replay_rows_by_template.get(str(tid)) or []
        if not validation_rows:
            formal_append_decision_ledger(ledger_path, {
                "artifact_id": tid, "status": "rejected",
                "family": family, "reasons": ["missing-paired-replay-evidence"],
            })
            continue
        validation_hash = formal_validation_manifest_hash(validation_rows)
        residuals = []
        for rec in accumulator.records:
            if (rec.get("family") or "*") != family:
                continue
            residuals.append({
                "residual_id": str(rec.get("case_id") or rec.get("case_hash")),
                "family": family,
                "failure_signature": "formal repair accepted and distilled",
                "mutation_type": rec.get("mutation_type"),
            })
        current = load_formal_registry(registry_path, formal_mode=True)
        parent_policy = formal_policy_hash(current)
        parent_registry = formal_hash_payload(current)
        candidates = formal_distill_candidates(
            residuals,
            parent_policy_hash=parent_policy,
            registry_parent_hash=parent_registry,
            validation_manifest_hash=validation_hash,
            distiller=lambda _prompt, _rows, strategy=template.get("strategy_context", ""): strategy,
        )
        candidate = next((c for c in candidates if c.get("trigger", {}).get("bug_family") == family), None)
        if not candidate:
            raise RuntimeError(f"formal candidate missing for family: {family}")
        thresholds = gate_decision.get("thresholds") or {}
        decision = formal_decide_promotion(
            candidate,
            validation_rows,
            parent_policy_hash=parent_policy,
            validation_manifest_hash=validation_hash,
            min_hits=int(thresholds.get("min_validation_hits", 1)),
            min_designs=int(thresholds.get("min_covered_designs", 1)),
            min_recovery_ratio=float(thresholds.get("min_target_recovery_ratio", 0.0)),
            min_gain=float(thresholds.get("min_target_gain", 0.0)),
            tau_r=float(thresholds.get("min_target_gain", 0.0)),
            epsilon=float(thresholds.get("non_target_regression_tolerance", 0.0)),
        )
        if decision["decision_hash"] not in decision_map:
            persisted.append(decision)
            decision_map[decision["decision_hash"]] = decision
        before_hash, after_hash = commit_registry_atomically(
            registry_path, candidate, decision, ledger_path=ledger_path,
        )
        if decision.get("promote"):
            committed.append({"artifact_id": candidate["artifact_id"], "family": family,
                              "decision_hash": decision["decision_hash"],
                              "registry_hash_before": before_hash, "registry_hash_after": after_hash})
            existing_families.add(family)
    _write_json(decisions_path, persisted)
    active, after_policy = load_promoted_registry(registry_path, decision_map)
    runtime_templates = []
    for artifact in active:
        family = artifact.get("trigger", {}).get("bug_family")
        source = next((t for t in accumulator.templates if t.get("family") == family), {})
        runtime = dict(source)
        runtime.update({
            "template_id": artifact["artifact_id"],
            "family": family,
            "status": "promoted",
            "strategy_context": artifact.get("normalized_strategy", ""),
            "action_policy": artifact.get("action_policy", {}),
            "promotion_decision_hash": artifact.get("promotion_decision_hash"),
            "formal_registry_authority": True,
            "formal_policy_hash": after_policy,
        })
        runtime_templates.append(runtime)
    _write_json(accumulator.promoted_template_path, runtime_templates)
    accumulator.promoted_templates = accumulator._load_promoted()
    return {"committed": committed, "active_artifact_ids": [a["artifact_id"] for a in active],
            "policy_hash_before": before_policy, "policy_hash_after": after_policy,
            "registry_hash": formal_hash_payload(load_formal_registry(registry_path, formal_mode=True))}


def frozen_fingerprint(poisons: list[dict]) -> str:
    payload = []
    for p in poisons:
        payload.append({
            "design": p.get("design"),
            "top": p.get("top"),
            "golden_sha256": p.get("golden_sha256"),
            "buggy_sha256": p.get("buggy_sha256"),
            "mutation_type": p.get("mutation_type"),
            "mutator_range": p.get("mutator_range"),
        })
    return _sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def poison_case_id(poison: dict) -> str:
    payload = {
        "design": poison.get("design"),
        "top": poison.get("top"),
        "golden_sha256": poison.get("golden_sha256"),
        "buggy_sha256": poison.get("buggy_sha256"),
        "mutation_type": poison.get("mutation_type"),
        "mutator_range": poison.get("mutator_range"),
    }
    return _sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))[:16]


def attach_case_identity(poison: dict, *, split: str) -> dict:
    out = dict(poison)
    out["split"] = split
    out["case_id"] = out.get("case_id") or f"{split}:{out.get('design')}:{poison_case_id(out)}"
    out["case_hash"] = out.get("case_hash") or poison_case_id(out)
    return out


def poison_fingerprint_rows(poisons: list[dict]) -> list[dict]:
    rows = []
    for p in poisons:
        rows.append({
            "split": p.get("split", ""),
            "case_id": p.get("case_id", ""),
            "case_hash": p.get("case_hash", ""),
            "design": p.get("design", ""),
            "top": p.get("top", ""),
            "golden_sha256": p.get("golden_sha256", ""),
            "buggy_sha256": p.get("buggy_sha256", ""),
            "mutator": p.get("mutator", ""),
            "mutation_type": p.get("mutation_type", ""),
            "mutator_range": json.dumps(p.get("mutator_range"), ensure_ascii=False),
        })
    return rows


def design_fingerprint_rows(
    designs: list[RtlDesign],
    *,
    split: str,
    baseline_rows: dict[str, dict],
) -> list[dict]:
    rows = []
    for d in designs:
        b = baseline_rows.get(d.name, {})
        rows.append({
            "split": split,
            "design": d.name,
            "top": d.top,
            "golden_sha256": _sha256_file(d.golden),
            "golden": d.golden,
            "instance_count": b.get("instance_count") or b.get("baseline10_instance_count"),
            "is_sequential": b.get("is_sequential"),
            "status": b.get("status") or b.get("baseline10_status"),
            "clock_period_ns": b.get("clock_period_ns") or b.get("best_clock_period_ns"),
        })
    return rows


def split_pool(
    pool: list[RtlDesign],
    *,
    train_n: int,
    promotion_n: int,
    external_n: int,
    sanity_n: int,
) -> tuple[list[RtlDesign], list[RtlDesign], list[RtlDesign], list[RtlDesign]]:
    train = pool[:train_n]
    promotion = pool[train_n: train_n + promotion_n]
    external = pool[train_n + promotion_n: train_n + promotion_n + external_n]
    sanity = pool[
        train_n + promotion_n + external_n:
        train_n + promotion_n + external_n + sanity_n
    ]
    return train, promotion, external, sanity


def memory_fingerprint(
    *,
    registry_path: Path,
    record_path: Path,
    template_path: Path,
    promoted_template_path: Path | None = None,
) -> dict:
    return {
        "registry": str(registry_path),
        "registry_sha256": _sha256_path(registry_path),
        "records": str(record_path),
        "records_sha256": _sha256_path(record_path),
        "templates": str(template_path),
        "templates_sha256": _sha256_path(template_path),
        "promoted_templates": str(promoted_template_path) if promoted_template_path else None,
        "promoted_templates_sha256": (
            _sha256_path(promoted_template_path) if promoted_template_path else None
        ),
    }


def leakage_audit(
    *,
    external_poisons: list[dict],
    train_poisons: list[dict],
    promotion_poisons: list[dict] | None = None,
    sanity_design_names: list[str] | None = None,
    registry_path: Path,
    record_path: Path,
    template_path: Path,
    promoted_template_path: Path | None = None,
) -> dict:
    external_case_ids = {str(p.get("case_id")) for p in external_poisons if p.get("case_id")}
    external_hashes = {str(p.get("case_hash")) for p in external_poisons if p.get("case_hash")}
    external_buggy = {str(p.get("buggy_sha256")) for p in external_poisons if p.get("buggy_sha256")}
    external_designs = {str(p.get("design")) for p in external_poisons if p.get("design")}
    train_case_ids = {str(p.get("case_id")) for p in train_poisons if p.get("case_id")}
    train_designs = {str(p.get("design")) for p in train_poisons if p.get("design")}
    promotion_poisons = promotion_poisons or []
    promotion_case_ids = {
        str(p.get("case_id")) for p in promotion_poisons if p.get("case_id")
    }
    promotion_designs = {
        str(p.get("design")) for p in promotion_poisons if p.get("design")
    }
    sanity_designs = {str(d) for d in (sanity_design_names or []) if d}
    key_hits = {
        "records": _split_count_rows(record_path, external_keys=external_case_ids | external_hashes | external_buggy),
        "templates": _split_count_rows(template_path, external_keys=external_case_ids | external_hashes | external_buggy),
        "registry": _split_count_rows(registry_path, external_keys=external_case_ids | external_hashes | external_buggy),
    }
    if promoted_template_path:
        key_hits["promoted_templates"] = _split_count_rows(
            promoted_template_path,
            external_keys=external_case_ids | external_hashes | external_buggy,
        )
    design_overlap = sorted(external_designs & train_designs)
    promotion_design_overlap = sorted(external_designs & promotion_designs)
    sanity_design_overlap = sorted(external_designs & sanity_designs)
    train_promotion_design_overlap = sorted(train_designs & promotion_designs)
    train_sanity_design_overlap = sorted(train_designs & sanity_designs)
    promotion_sanity_design_overlap = sorted(promotion_designs & sanity_designs)
    case_overlap = sorted(external_case_ids & train_case_ids)
    promotion_case_overlap = sorted(external_case_ids & promotion_case_ids)
    leaked_keys = []
    for name, row in key_hits.items():
        for key in row.get("external_key_hits", []):
            leaked_keys.append({"artifact": name, "key": key})
    return {
        "external_cases": len(external_poisons),
        "train_cases": len(train_poisons),
        "promotion_validation_cases": len(promotion_poisons),
        "external_designs": len(external_designs),
        "train_designs": len(train_designs),
        "promotion_validation_designs": len(promotion_designs),
        "sanity_designs": len(sanity_designs),
        "design_overlap_train_external": design_overlap,
        "design_overlap_promotion_external": promotion_design_overlap,
        "design_overlap_sanity_external": sanity_design_overlap,
        "design_overlap_train_promotion": train_promotion_design_overlap,
        "design_overlap_train_sanity": train_sanity_design_overlap,
        "design_overlap_promotion_sanity": promotion_sanity_design_overlap,
        "case_id_overlap_train_external": case_overlap,
        "case_id_overlap_promotion_external": promotion_case_overlap,
        "artifact_key_hits": key_hits,
        "leaked_external_keys": leaked_keys,
        "pass": (
            not design_overlap
            and not promotion_design_overlap
            and not sanity_design_overlap
            and not train_promotion_design_overlap
            and not train_sanity_design_overlap
            and not promotion_sanity_design_overlap
            and not case_overlap
            and not promotion_case_overlap
            and not leaked_keys
        ),
        "policy": (
            "External-Frozen case_id/case_hash/buggy_sha256 must not appear in "
            "records, templates, promoted templates, registry, or train stream."
        ),
    }


def per_case_delta(curve: list[dict]) -> list[dict]:
    if len(curve) < 2:
        return []
    first = curve[0]
    latest = curve[-1]
    m0 = {
        r.get("case_id") or f"{r.get('design')}:{r.get('idx')}": r
        for r in first.get("eval_rows", [])
    }
    m_last = {
        r.get("case_id") or f"{r.get('design')}:{r.get('idx')}": r
        for r in latest.get("eval_rows", [])
    }
    out = []
    for key in sorted(m0):
        a = m0.get(key, {})
        b = m_last.get(key, {})
        if not b:
            continue
        before = bool(a.get("repaired"))
        after = bool(b.get("repaired"))
        if before and after:
            change = "stable_pass"
        elif (not before) and (not after):
            change = "stable_fail"
        elif after:
            change = "gain"
        else:
            change = "regression"
        out.append({
            "case_id": key,
            "design": b.get("design") or a.get("design"),
            "bug_family": b.get("family") or a.get("family"),
            "m0_repaired": before,
            f"{latest.get('label')}_repaired": after,
            "change": change,
            "memory_template_hit": bool(b.get("used_accumulated_template")),
            "template_id": b.get("template_id"),
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench-root", default=str(EB_DEFAULT))
    ap.add_argument("--work", default=str(S_DEFAULT))
    ap.add_argument("--registry", default=str(REGISTRY_DEFAULT))
    ap.add_argument("--formal-registry", default=None,
                    help="Formal JSON registry with atomic promotion authority (Auto-Promote only).")
    ap.add_argument("--formal-decisions", default=None,
                    help="Persisted formal promotion-decision list bound to --formal-registry.")
    ap.add_argument("--out", default=str(ROOT / ".iso_semrepair/skill_accumulation_curve.json"))
    ap.add_argument("--baseline-csv", default=None,
                    help="External baseline CSV used to freeze/order the design universe.")
    ap.add_argument("--allow-baseline-failures", action="store_true",
                    help="Do not require baseline10/best status ok when reading --baseline-csv.")
    ap.add_argument("--require-sequential-baseline", action="store_true",
                    help="When reading --baseline-csv, keep only sequential designs with clocks.")
    ap.add_argument("--split-mode", choices=["legacy", "design-crawl"], default="legacy",
                    help="legacy uses one design pool with train/test poisons; design-crawl splits designs into train/promotion/external/sanity pools.")
    ap.add_argument("--train-design-count", type=int, default=80)
    ap.add_argument("--promotion-design-count", type=int, default=0)
    ap.add_argument("--external-design-count", type=int, default=30)
    ap.add_argument("--sanity-design-count", type=int, default=20)
    ap.add_argument("--max-instance-count", type=int, default=0,
                    help="When reading --baseline-csv, drop designs above this instance count; 0 disables.")
    ap.add_argument("--n-target", type=int, default=8)
    ap.add_argument("--max-lines", type=int, default=350)
    ap.add_argument("--train-per-design", type=int, default=6)
    ap.add_argument("--test-per-design", type=int, default=4)
    ap.add_argument("--promotion-per-design", type=int, default=1)
    ap.add_argument("--no-regression-per-design", type=int, default=1)
    ap.add_argument("--eval-every", type=int, default=10)
    ap.add_argument("--checkpoint-labels", default="M0,M1,M2,M3,M4")
    ap.add_argument("--checkpoint-train-counts", default=None,
                    help="Comma-separated train counts for checkpoint evaluation, e.g. 0,10,20,40,80.")
    ap.add_argument("--eval-reps", type=int, default=2)
    ap.add_argument("--eval-candidate-budget", type=int, default=0,
                    help="Override eval n_candidates, e.g. 3 for external pass@1/pass@3.")
    ap.add_argument("--train-candidate-budget", type=int, default=0,
                    help="Override train n_candidates; 0 keeps registry/template policy.")
    ap.add_argument("--mutator", choices=["llm", "fixed"], default="llm",
                    help="Poison generator: llm red mutator or fixed deterministic semantic mutator.")
    ap.add_argument("--poison-max-tries", type=int, default=4,
                    help="Mutation attempts per design/slot before giving up.")
    ap.add_argument("--formal-timeout", type=int, default=40)
    ap.add_argument("--distill-min-support", type=int, default=2)
    ap.add_argument(
        "--template-stage",
        choices=["promoted", "shadow", "legacy-active"],
        default="promoted",
        help=(
            "promoted: only design-heldout promoted templates affect repair; "
            "shadow: record hits but do not affect repair; "
            "legacy-active: reproduce older runs where distilled templates become active immediately."
        ),
    )
    ap.add_argument(
        "--promoted-template-file",
        default=None,
        help="Optional JSON file of status=promoted frontend templates allowed to affect repair.",
    )
    ap.add_argument("--promotion-min-hits", type=int, default=2)
    ap.add_argument("--promotion-min-designs", type=int, default=2)
    ap.add_argument("--promotion-min-hit-to-pass-at-3", type=float, default=0.5)
    ap.add_argument("--promotion-min-target-gain", type=float, default=0.0,
                    help="Strict minimum target gain; promotion requires gain > threshold.")
    ap.add_argument("--promotion-non-target-epsilon", type=float, default=0.0,
                    help="Maximum tolerated loss on the disjoint non-target replay set.")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-accumulated-template-use", action="store_true")
    ap.add_argument("--disable-distillation", action="store_true",
                    help="Fixed-Blue control: do not add successful repairs to the candidate/template accumulator.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Only build the formal-proven pool and emit the plan.")
    args = ap.parse_args()

    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    registry = load_registry(args.registry) if args.registry else []
    baseline_order, baseline_rows = load_baseline_designs(
        args.baseline_csv,
        require_ok=not args.allow_baseline_failures,
        require_sequential=args.require_sequential_baseline,
        max_instance_count=args.max_instance_count,
    )
    n_target = args.n_target
    if args.split_mode == "design-crawl":
        n_target = max(
            n_target,
            args.train_design_count
            + args.promotion_design_count
            + args.external_design_count
            + args.sanity_design_count,
        )

    pool = build_pool(
        Path(args.bench_root),
        work,
        n_target=n_target,
        max_lines=args.max_lines,
        formal_timeout=args.formal_timeout,
        design_order=baseline_order or None,
    )
    if args.split_mode == "design-crawl":
        train_designs, promotion_designs, external_designs, sanity_designs = split_pool(
            pool,
            train_n=args.train_design_count,
            promotion_n=args.promotion_design_count,
            external_n=args.external_design_count,
            sanity_n=args.sanity_design_count,
        )
    else:
        train_designs, promotion_designs, external_designs, sanity_designs = pool, [], pool, []

    design_fields = [
        "split",
        "design",
        "top",
        "golden_sha256",
        "golden",
        "instance_count",
        "is_sequential",
        "status",
        "clock_period_ns",
    ]
    _write_csv(
        work / "train_design_fingerprint.csv",
        design_fingerprint_rows(train_designs, split="train", baseline_rows=baseline_rows),
        design_fields,
    )
    _write_csv(
        work / "external_design_fingerprint.csv",
        design_fingerprint_rows(external_designs, split="external", baseline_rows=baseline_rows),
        design_fields,
    )
    _write_csv(
        work / "promotion_validation_design_fingerprint.csv",
        design_fingerprint_rows(
            promotion_designs,
            split="promotion_validation",
            baseline_rows=baseline_rows,
        ),
        design_fields,
    )
    _write_csv(
        work / "no_regression_design_fingerprint.csv",
        design_fingerprint_rows(sanity_designs, split="no_regression", baseline_rows=baseline_rows),
        design_fields,
    )
    plan = {
        "bench_root": args.bench_root,
        "baseline_csv": args.baseline_csv,
        "baseline_require_ok": not args.allow_baseline_failures,
        "baseline_require_sequential": args.require_sequential_baseline,
        "baseline_max_instance_count": args.max_instance_count,
        "baseline_designs_seen": len(baseline_order),
        "split_mode": args.split_mode,
        "mutator": args.mutator,
        "poison_max_tries": args.poison_max_tries,
        "split_counts": {
            "train_designs": len(train_designs),
            "promotion_validation_designs": len(promotion_designs),
            "external_designs": len(external_designs),
            "no_regression_designs": len(sanity_designs),
        },
        "split_fingerprint_paths": {
            "train_designs": str(work / "train_design_fingerprint.csv"),
            "promotion_validation_designs": str(
                work / "promotion_validation_design_fingerprint.csv"
            ),
            "external_designs": str(work / "external_design_fingerprint.csv"),
            "no_regression_designs": str(work / "no_regression_design_fingerprint.csv"),
        },
        "pool": [d.__dict__ for d in pool],
        "train_pool": [d.__dict__ for d in train_designs],
        "promotion_validation_pool": [d.__dict__ for d in promotion_designs],
        "external_pool": [d.__dict__ for d in external_designs],
        "no_regression_pool": [d.__dict__ for d in sanity_designs],
        "pool_baseline_rows": {
            d.name: baseline_rows.get(d.name, {}) for d in pool
        },
        "n_pool": len(pool),
        "registry": args.registry,
        "formal_registry": args.formal_registry,
        "formal_decisions": args.formal_decisions,
        "distill_min_support": args.distill_min_support,
        "distillation_enabled": not args.disable_distillation,
    }
    _write_json(work / "plan.json", plan)
    if args.dry_run:
        _write_json(Path(args.out), {"dry_run": True, **plan})
        print(json.dumps({"dry_run": True, "n_pool": len(pool)}, ensure_ascii=False, indent=2))
        return
    if len(pool) < 2:
        raise SystemExit("not enough formal-proven designs for accumulation")
    if args.split_mode == "design-crawl" and (not train_designs or not external_designs):
        raise SystemExit("not enough formal-proven designs for design-crawl train/external split")

    poison_fingerprint_fields = [
        "split",
        "case_id",
        "case_hash",
        "design",
        "top",
        "golden_sha256",
        "buggy_sha256",
        "mutation_type",
        "mutator",
        "mutator_range",
    ]

    def generate_split_poisons(
        *,
        split: str,
        designs: list[RtlDesign],
        per_design: int,
        poison_dir: str,
        final_name: str,
        partial_name: str,
        fingerprint_name: str,
        event_prefix: str,
    ) -> list[dict]:
        final_path = work / final_name
        partial_path = work / partial_name
        if final_path.exists():
            poisons = json.loads(final_path.read_text())
        else:
            poisons = (
                json.loads(partial_path.read_text())
                if partial_path.exists()
                else []
            )
            print(
                json.dumps({
                    "event": f"{event_prefix}_resume",
                    "partial_n": len(poisons),
                    "target_n": len(designs) * per_design,
                }, ensure_ascii=False),
                flush=True,
            )
            done_keys = {
                (p.get("design"), tuple(p.get("mutator_range") or []), p.get("buggy_sha256"))
                for p in poisons
            }
            for design in designs:
                existing_for_design = sum(1 for p in poisons if p.get("design") == design.name)
                for k in range(existing_for_design, per_design):
                    print(
                        json.dumps({
                            "event": f"{event_prefix}_generate_start",
                            "design": design.name,
                            "slot": k,
                            "current_n": len(poisons),
                        }, ensure_ascii=False),
                        flush=True,
                    )
                    p = gen_poison(
                        design,
                        work / poison_dir / f"{design.name}_{k}",
                        formal_timeout=args.formal_timeout,
                        mutator=args.mutator,
                        max_tries=args.poison_max_tries,
                    )
                    if p:
                        p = attach_case_identity(p, split=split)
                        key = (
                            p.get("design"),
                            tuple(p.get("mutator_range") or []),
                            p.get("buggy_sha256"),
                        )
                        if key not in done_keys:
                            poisons.append(p)
                            done_keys.add(key)
                            _write_json(partial_path, poisons)
                            print(
                                json.dumps({
                                    "event": f"{event_prefix}_generate_ok",
                                    "design": design.name,
                                    "slot": k,
                                    "n": len(poisons),
                                    "mutation_type": p.get("mutation_type"),
                                }, ensure_ascii=False),
                                flush=True,
                            )
                    else:
                        print(
                            json.dumps({
                                "event": f"{event_prefix}_generate_fail",
                                "design": design.name,
                                "slot": k,
                                "n": len(poisons),
                            }, ensure_ascii=False),
                            flush=True,
                        )
            _write_json(final_path, poisons)
        poisons = [attach_case_identity(p, split=split) for p in poisons]
        _write_json(final_path, poisons)
        _write_csv(
            work / fingerprint_name,
            poison_fingerprint_rows(poisons),
            poison_fingerprint_fields,
        )
        return poisons

    promotion_validation_poisons = generate_split_poisons(
        split="promotion_validation",
        designs=promotion_designs,
        per_design=args.promotion_per_design,
        poison_dir="promotion_validation_poisons",
        final_name="promotion_validation_poisons.json",
        partial_name="promotion_validation_poisons.partial.json",
        fingerprint_name="promotion_validation_fingerprint.csv",
        event_prefix="promotion_validation",
    ) if promotion_designs else []
    promotion_validation_fingerprint = frozen_fingerprint(promotion_validation_poisons)

    no_regression_poisons = generate_split_poisons(
        split="non_target",
        designs=sanity_designs,
        per_design=args.no_regression_per_design,
        poison_dir="no_regression_poisons",
        final_name="no_regression_poisons.json",
        partial_name="no_regression_poisons.partial.json",
        fingerprint_name="no_regression_fingerprint.csv",
        event_prefix="no_regression",
    ) if sanity_designs else []
    no_regression_fingerprint = frozen_fingerprint(no_regression_poisons)

    final_test_path = work / "test_poisons.json"
    partial_test_path = work / "test_poisons.partial.json"
    if final_test_path.exists():
        test_poisons = json.loads(final_test_path.read_text())
    else:
        test_poisons = (
            json.loads(partial_test_path.read_text())
            if partial_test_path.exists()
            else []
        )
        print(
            json.dumps({
                "event": "frozen_test_resume",
                "partial_n": len(test_poisons),
                "target_n": len(external_designs) * args.test_per_design,
            }, ensure_ascii=False),
            flush=True,
        )
        done_keys = {
            (p.get("design"), tuple(p.get("mutator_range") or []), p.get("buggy_sha256"))
            for p in test_poisons
        }
        for design in external_designs:
            existing_for_design = sum(1 for p in test_poisons if p.get("design") == design.name)
            for k in range(existing_for_design, args.test_per_design):
                print(
                    json.dumps({
                        "event": "frozen_test_generate_start",
                        "design": design.name,
                        "slot": k,
                        "current_n": len(test_poisons),
                    }, ensure_ascii=False),
                    flush=True,
                )
                p = gen_poison(
                    design,
                    work / "test_poisons" / f"{design.name}_{k}",
                    formal_timeout=args.formal_timeout,
                    mutator=args.mutator,
                    max_tries=args.poison_max_tries,
                )
                if p:
                    p = attach_case_identity(p, split="external")
                    key = (p.get("design"), tuple(p.get("mutator_range") or []), p.get("buggy_sha256"))
                    if key not in done_keys:
                        test_poisons.append(p)
                        done_keys.add(key)
                        _write_json(partial_test_path, test_poisons)
                        print(
                            json.dumps({
                                "event": "frozen_test_generate_ok",
                                "design": design.name,
                                "slot": k,
                                "frozen_n": len(test_poisons),
                                "mutation_type": p.get("mutation_type"),
                            }, ensure_ascii=False),
                            flush=True,
                        )
                else:
                    print(
                        json.dumps({
                            "event": "frozen_test_generate_fail",
                            "design": design.name,
                            "slot": k,
                            "frozen_n": len(test_poisons),
                        }, ensure_ascii=False),
                        flush=True,
                    )
        _write_json(final_test_path, test_poisons)
    test_poisons = [attach_case_identity(p, split="external") for p in test_poisons]
    _write_json(final_test_path, test_poisons)
    _write_csv(
        work / "external_fingerprint.csv",
        poison_fingerprint_rows(test_poisons),
        [
            "split",
            "case_id",
            "case_hash",
            "design",
            "top",
            "golden_sha256",
            "buggy_sha256",
            "mutator",
            "mutation_type",
            "mutator_range",
        ],
    )
    test_fingerprint = frozen_fingerprint(test_poisons)

    train_stream = [design for design in train_designs for _ in range(args.train_per_design)]
    random.seed(args.seed)
    random.shuffle(train_stream)

    accumulator = PatternAccumulator(
        work / "correctness_gated_records.jsonl",
        work / "distilled_pattern_templates.json",
        min_support=args.distill_min_support,
        promoted_template_path=(
            Path(args.promoted_template_file)
            if args.promoted_template_file
            else work / "promoted_pattern_templates.json"
        ),
        template_stage=args.template_stage,
    )
    use_templates = not args.no_accumulated_template_use

    curve = []
    checkpoint_labels = [x.strip() for x in args.checkpoint_labels.split(",") if x.strip()]
    checkpoint_counts = None
    if args.checkpoint_train_counts:
        checkpoint_counts = [
            int(x.strip()) for x in args.checkpoint_train_counts.split(",")
            if x.strip()
        ]
        if 0 not in checkpoint_counts:
            checkpoint_counts.insert(0, 0)
    train_seen = 0
    poison_ok = 0
    repaired = 0
    train_pass1 = 0
    train_pass3 = 0
    window_seen = 0
    window_repaired = 0
    train_poisons: list[dict] = []
    train_fingerprint_path = work / "train_fingerprint.csv"
    poison_fingerprint_fields = [
        "split",
        "case_id",
        "case_hash",
        "design",
        "top",
        "golden_sha256",
        "buggy_sha256",
        "mutation_type",
        "mutator",
        "mutator_range",
    ]
    _write_csv(train_fingerprint_path, [], poison_fingerprint_fields)

    def checkpoint(label: str) -> None:
        promotion_report = promotion_validation_gate(
            label=label,
            validation_poisons=promotion_validation_poisons,
            non_target_poisons=no_regression_poisons,
            work_dir=work / "promotion_validation" / label,
            registry=registry,
            accumulator=accumulator,
            use_accumulated_templates=use_templates,
            formal_timeout=args.formal_timeout,
            reps=args.eval_reps,
            candidate_budget=args.eval_candidate_budget or None,
            min_hits=args.promotion_min_hits,
            min_designs=args.promotion_min_designs,
            min_hit_to_pass_at_3=args.promotion_min_hit_to_pass_at_3,
            min_target_gain=args.promotion_min_target_gain,
            non_target_epsilon=args.promotion_non_target_epsilon,
        )
        formal_commit = None
        if args.formal_registry:
            if not args.formal_decisions:
                raise SystemExit("--formal-decisions is required with --formal-registry")
            formal_commit = commit_gate_positive_templates_to_formal_registry(
                registry_path=Path(args.formal_registry),
                decisions_path=Path(args.formal_decisions),
                accumulator=accumulator,
                promotion_report=promotion_report,
            )
        eval_res = repair_many(
            test_poisons,
            work / "eval" / label,
            registry=registry,
            accumulator=accumulator,
            use_accumulated_templates=use_templates,
            formal_timeout=args.formal_timeout,
            reps=args.eval_reps,
            candidate_budget=args.eval_candidate_budget or None,
        )
        mem_fp = memory_fingerprint(
            registry_path=Path(args.formal_registry or args.registry),
            record_path=accumulator.record_path,
            template_path=accumulator.template_path,
            promoted_template_path=accumulator.promoted_template_path,
        )
        leak = leakage_audit(
            external_poisons=test_poisons,
            train_poisons=train_poisons,
            promotion_poisons=promotion_validation_poisons,
            sanity_design_names=[d.name for d in sanity_designs],
            registry_path=Path(args.formal_registry or args.registry),
            record_path=accumulator.record_path,
            template_path=accumulator.template_path,
            promoted_template_path=accumulator.promoted_template_path,
        )
        row = {
            "label": label,
            "train_seen": train_seen,
            "train_poison_ok": poison_ok,
            "train_repaired": repaired,
            "train_repair_rate": round(repaired / poison_ok, 4) if poison_ok else 0.0,
            "train_pass_at_1": round(train_pass1 / poison_ok, 4) if poison_ok else 0.0,
            "train_pass_at_3": round(train_pass3 / poison_ok, 4) if poison_ok else 0.0,
            "window_seen": window_seen,
            "window_repaired": window_repaired,
            "window_repair_rate": round(window_repaired / window_seen, 4)
            if window_seen else 0.0,
            "memory": accumulator.stats(),
            "template_stage": args.template_stage,
            "promotion_validation": {
                k: v for k, v in promotion_report.items()
                if k not in {
                    "baseline_rows", "non_target_baseline_rows", "shadow_rows",
                    "candidate_validation_rows",
                }
            },
            "formal_registry_commit": formal_commit,
            "memory_fingerprint": mem_fp,
            "leakage_audit": leak,
            "external_repair_rate": eval_res["mean"],
            "external_pass_at_1": eval_res["pass_at_1_mean"],
            "external_pass_at_3": eval_res["pass_at_3_mean"],
            "test_pass_mean": eval_res["mean"],
            "test_pass_rates": eval_res["rates"],
            "test_pass_at_1_rates": eval_res["pass_at_1_rates"],
            "test_pass_at_3_rates": eval_res["pass_at_3_rates"],
            "test_n": eval_res["n"],
            "frozen_external_test_fingerprint": test_fingerprint,
            "template_hit_rate": eval_res["metrics"]["template_hit_rate"],
            "template_hits": eval_res["metrics"]["template_hits"],
            "shadow_template_hits": eval_res["metrics"]["shadow_template_hits"],
            "shadow_template_hit_rate": eval_res["metrics"]["shadow_template_hit_rate"],
            "accepted_harmful_hits": eval_res["metrics"]["accepted_harmful_hits"],
            "accepted_harmful_hit_rate": eval_res["metrics"]["accepted_harmful_hit_rate"],
            "blocked_harmful_candidates": eval_res["metrics"]["blocked_harmful_candidates"],
            "token_tool_cost": {
                "llm_calls": eval_res["metrics"]["llm_calls"],
                "formal_checks": eval_res["metrics"]["formal_checks"],
                "estimated_prompt_tokens": eval_res["metrics"]["estimated_prompt_tokens"],
                "avg_candidates": eval_res["metrics"]["avg_candidates"],
            },
            "eval_rows": eval_res["rows"],
        }
        curve.append(row)
        mem_fp_rows = [
            {
                "label": r["label"],
                **r.get("memory_fingerprint", {}),
            }
            for r in curve
        ]
        _write_json(work / "registry_fingerprint_after_each_checkpoint.json", mem_fp_rows)
        _write_json(work / "leakage_audit.json", leak)
        _write_json(Path(args.out), {
            "plan": plan,
            "use_accumulated_templates": use_templates,
            "template_stage": args.template_stage,
            "promoted_template_file": str(accumulator.promoted_template_path),
            "frozen_external_test": {
                "n": len(test_poisons),
                "fingerprint": test_fingerprint,
                "path": str(work / "test_poisons.json"),
                "fingerprint_csv": str(work / "external_fingerprint.csv"),
                "registry_leakage_policy": (
                    "External-Frozen cases are fingerprinted before accumulation and "
                    "never enter records/templates/registry/promotion evidence."
                ),
            },
            "blue_crawl_train": {
                "fingerprint_csv": str(train_fingerprint_path),
                "design_fingerprint_csv": str(work / "train_design_fingerprint.csv"),
            },
            "promotion_validation": {
                "n": len(promotion_validation_poisons),
                "fingerprint": promotion_validation_fingerprint,
                "path": str(work / "promotion_validation_poisons.json"),
                "fingerprint_csv": str(work / "promotion_validation_fingerprint.csv"),
                "design_fingerprint_csv": str(
                    work / "promotion_validation_design_fingerprint.csv"
                ),
                "latest_report": str(
                    work / "promotion_validation" / label / "promotion_validation_report.json"
                ),
                "policy": (
                    "Promotion-Validation may promote shadow templates but is "
                    "design-heldout from origin training. External-Frozen never "
                    "enters promotion evidence."
                ),
            },
            "no_regression_sanity": {
                "n": len(no_regression_poisons),
                "fingerprint": no_regression_fingerprint,
                "path": str(work / "no_regression_poisons.json"),
                "fingerprint_csv": str(work / "no_regression_fingerprint.csv"),
                "design_fingerprint_csv": str(work / "no_regression_design_fingerprint.csv"),
            },
            "registry_fingerprint_after_each_checkpoint": str(
                work / "registry_fingerprint_after_each_checkpoint.json"
            ),
            "leakage_audit": str(work / "leakage_audit.json"),
            "curve": curve,
            "per_case_delta": per_case_delta(curve),
        })
        print(json.dumps(row, ensure_ascii=False), flush=True)

    checkpoint(checkpoint_labels[0] if checkpoint_labels else "train0")
    checkpoint_idx = 1
    for idx, design in enumerate(train_stream, 1):
        train_seen += 1
        p = gen_poison(
            design,
            work / "train_poisons" / f"{idx}_{design.name}",
            formal_timeout=args.formal_timeout,
            mutator=args.mutator,
            max_tries=args.poison_max_tries,
        )
        print(
            json.dumps({
                "event": "train_poison_done",
                "idx": idx,
                "design": design.name,
                "poison_ok": bool(p),
            }, ensure_ascii=False),
            flush=True,
        )
        if p:
            p = attach_case_identity(p, split="train")
            train_poisons.append(p)
            _write_csv(
                train_fingerprint_path,
                poison_fingerprint_rows(train_poisons),
                poison_fingerprint_fields,
            )
            poison_ok += 1
            window_seen += 1
            res = blue_repair_formal(
                p,
                work / "train_repairs" / f"{idx}_{design.name}",
                registry=registry,
                accumulator=accumulator,
                use_accumulated_templates=use_templates,
                formal_timeout=args.formal_timeout,
                candidate_budget=args.train_candidate_budget or None,
            )
            train_pass1 += int(res.get("pass_at_1"))
            train_pass3 += int(res.get("pass_at_3"))
            if res.get("repaired"):
                repaired += 1
                window_repaired += 1
            if not args.disable_distillation:
                accumulator.ingest_trajectory(p, res)
        should_eval = (
            idx in set(checkpoint_counts or [])
            if checkpoint_counts is not None
            else idx % args.eval_every == 0
        )
        if should_eval:
            label = (
                checkpoint_labels[checkpoint_idx]
                if checkpoint_idx < len(checkpoint_labels)
                else f"train{idx}"
            )
            checkpoint(label)
            checkpoint_idx += 1
            window_seen = 0
            window_repaired = 0
    if checkpoint_counts is None and train_stream and len(train_stream) % args.eval_every:
        label = (
            checkpoint_labels[checkpoint_idx]
            if checkpoint_idx < len(checkpoint_labels)
            else f"train{len(train_stream)}"
        )
        checkpoint(label)


if __name__ == "__main__":
    main()
