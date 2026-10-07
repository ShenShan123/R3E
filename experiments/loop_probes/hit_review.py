"""Review forms for candidate hits: does Blue have what it needs to know the intended repair?

A candidate hit is a bug Blue failed reproducibly (``screens``: at least
``reproducible_fails`` of its runs). It becomes a valid hit only after review,
because expected outputs do not always pin down the intended behaviour:

- ``PASS``: the specification and the visible evidence Blue saw are enough to
  identify what must be repaired;
- ``EXCLUDED``: the correct intent depends on information Blue cannot see, or
  the specification contradicts itself;
- ``PENDING``: the material is insufficient to decide; not counted for now.

For each candidate hit the form shows: the specification, the actual change
(clean vs buggy design; the reviewer's view only, never Blue's), Red's claimed
specification basis and hypothesis, the visible evidence exactly as Blue
received it (recomputed by simulation; no model call), and Blue's attempts and
why they failed in every run. Decisions go into a JSON file, one entry per
challenge: ``{"decision": "PASS|EXCLUDED|PENDING", "reason": "..."}``; existing
decisions are kept when the form is regenerated.

Usage:
  python -m experiments.loop_probes.hit_review STATE_DIR [--manifests M.jsonl ...] [--no-register-trace]

The form is written to ``STATE_DIR/hit_review.md`` and the decisions file is
``STATE_DIR/hit_decisions.json`` (``red_hits.py`` reads it from there).
"""
from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path

from r3e.loop.blue import feedback_summary
from r3e.loop.carriers import DEFAULT_CARRIER_MANIFEST, REPO_ROOT, load_carrier_manifest
from r3e.loop.corpus import load_public_manifest
from r3e.loop.operators import apply_edits, resolve_recorded_edits
from r3e.loop.sim import Simulator
from r3e.loop.state import RunState

DECISIONS = ("PASS", "EXCLUDED", "PENDING")


def decisions_path(state_dir: Path) -> Path:
    return Path(state_dir) / "hit_decisions.json"


def cut_screens(state: RunState, reproducible_fails: int = 2) -> list[dict]:
    """Screens a call cap cut short: admitted bugs with no ``screens`` row whose
    completed runs already contain ``reproducible_fails`` failures, so the rule
    is met whatever the missing runs would show. Derived from the recorded
    runs, marked ``cut_by_call_cap``; nothing is written to the ledgers."""
    screened = {s["challenge_id"] for s in state.read("screens")}
    red = {r["challenge_id"]: r for r in state.read("red") if r.get("admitted")}
    runs: dict[str, list[bool]] = {}
    for row in state.read("encounters"):
        e = row["encounter"]
        if e["challenge_id"] in red and e["challenge_id"] not in screened and not e.get("inconclusive"):
            runs.setdefault(e["challenge_id"], []).append(bool(e["solved_within_budget"]))
    cluster = {rec["state"]["challenge_id"]: rec["state"]["design_cluster"] for rec in state.read("population")}
    out = []
    for cid, solved in runs.items():
        fails = solved.count(False)
        if fails >= reproducible_fails:
            r = red[cid]
            decision = r.get("decision") or {}
            out.append({"round": r["round"], "challenge_id": cid, "carrier_id": r["carrier_id"],
                        "cluster_id": cluster.get(cid, ""), "red_mode": r.get("red_mode") or r.get("mode"),
                        "lineage_id": decision.get("lineage_id"), "direction": decision.get("direction", "explore"),
                        "edit_kinds": (r.get("chosen") or {}).get("edit_kinds", []), "runs": len(solved),
                        "fails": fails, "reproducible": True, "cut_by_call_cap": True})
    return out


def buggy_rtl(row: dict, carrier, public: dict) -> str:
    """The admitted bug of a Red ledger row: stored (free-form), public (pool) or rebuilt (catalog)."""
    chosen = row.get("chosen") or {}
    if chosen.get("buggy_rtl"):
        return chosen["buggy_rtl"]
    if row["challenge_id"] in public:
        return public[row["challenge_id"]].buggy_rtl
    return apply_edits(carrier, resolve_recorded_edits(carrier, chosen, row.get("mutant_hash")))


def candidates(state_dir: Path, manifests: list[Path], *, registers: bool) -> list[dict]:
    state = RunState(state_dir)
    carriers = {c.carrier_id: c for c in load_carrier_manifest(DEFAULT_CARRIER_MANIFEST)}
    public = {}
    for manifest in manifests:
        cs, chs = load_public_manifest(REPO_ROOT / manifest, REPO_ROOT)
        carriers.update({c.carrier_id: c for c in cs})
        public.update({ch.challenge_id: ch for ch in chs})
    red = {r["challenge_id"]: r for r in state.read("red") if r.get("admitted")}
    runs: dict[str, list] = {}
    for row in state.read("encounters"):
        e = row["encounter"]
        runs.setdefault(e["challenge_id"], []).append({
            "run": "confirmation" if row.get("confirmation") else "primary",
            "attempts": [{"attempt": a.get("index"), "edit": a.get("edit"), "verdict": a.get("verdict_tier"),
                          "feedback": a.get("feedback")} for a in e["attempts"] if not a.get("infra_failure")]})
    sim = Simulator(state_dir / "review_sim")
    out = []
    for screen in [*state.read("screens"), *cut_screens(state)]:
        if not screen["reproducible"]:
            continue
        cid = screen["challenge_id"]
        row = red[cid]
        carrier = carriers[row["carrier_id"]]
        rtl = buggy_rtl(row, carrier, public)
        verdict = sim.verdict(rtl, carrier, registers=registers)
        chosen = row.get("chosen") or {}
        out.append({
            "challenge_id": cid, "round": screen["round"], "red_mode": screen["red_mode"],
            "design": carrier.cluster_id, "direction": screen["direction"], "lineage_id": screen["lineage_id"],
            "blue_failed": f"{screen['fails']} of {screen['runs']} runs"
                           + (" (screening cut by the call cap; the rule is already met)"
                              if screen.get("cut_by_call_cap") else ""),
            "specification": carrier.spec,
            "red_claims": {k: chosen.get(k) for k in ("spec_basis", "hypothesis", "change_summary",
                                                     "expected_symptom") if chosen.get(k)},
            "change": "".join(difflib.unified_diff(carrier.clean_rtl.splitlines(keepends=True),
                                                   rtl.splitlines(keepends=True), "correct", "buggy", n=2)),
            "evidence_blue_saw": feedback_summary(verdict.feedback, window=verdict.window),
            "blue_runs": runs.get(cid, []),
        })
    return out


def render(items: list[dict], decisions: dict) -> str:
    lines = ["# Candidate hits for review", "",
             "Decide per hit: **PASS** (spec + visible evidence identify the repair target), **EXCLUDED** "
             "(intent needs information Blue cannot see, or the spec contradicts itself), **PENDING** "
             "(insufficient material; not counted). Record the decision and reason in the decisions file.", ""]
    for it in items:
        d = decisions.get(it["challenge_id"], {})
        lines += [f"## {it['challenge_id']} — {it['design']}",
                  f"- round {it['round']}, Red {it['red_mode']}, direction {it['direction']}, "
                  f"lineage {it['lineage_id']}; Blue failed {it['blue_failed']}",
                  f"- current decision: **{d.get('decision') or 'none (counts as PENDING)'}**"
                  + (f" — {d['reason']}" if d.get("reason") else ""), "",
                  "### Specification", "", "```text", it["specification"].strip() or "(this design has no specification)",
                  "```", "",
                  "### Actual change (reviewer only; Blue never sees this)", "", "```diff", it["change"].rstrip(), "```", ""]
        if it["red_claims"]:
            lines += ["### Red's claims (not verified)", ""] + [f"- **{k}**: {v}" for k, v in it["red_claims"].items()] + [""]
        lines += ["### Visible evidence Blue received", "", "```json",
                  json.dumps(it["evidence_blue_saw"], indent=1)[:6000], "```", "", "### Blue's runs", ""]
        for run in it["blue_runs"]:
            lines.append(f"- {run['run']} run:")
            for a in run["attempts"]:
                fb = a.get("feedback") or {}
                why = fb.get("message") or "; ".join(f"{x.get('signal')} first wrong at cycle {x.get('first_cycle')}"
                                                     for x in (fb.get("first_divergences") or [])[:3])
                lines.append(f"  - attempt {a['attempt']}: {a['verdict']} — {str(a.get('edit') or '')[:300]}"
                             + (f" — result: {why}" if why else ""))
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("state_dir", type=Path)
    p.add_argument("--out", type=Path, default=None, help="default: STATE_DIR/hit_review.md")
    p.add_argument("--decisions", type=Path, default=None, help="default: STATE_DIR/hit_decisions.json")
    p.add_argument("--manifests", nargs="*", type=Path, default=[])
    p.add_argument("--no-register-trace", action="store_true", help="the run did not use the register trace")
    args = p.parse_args()
    args.out = args.out or args.state_dir / "hit_review.md"
    args.decisions = args.decisions or decisions_path(args.state_dir)
    items = candidates(args.state_dir, args.manifests, registers=not args.no_register_trace)
    decisions = json.loads(args.decisions.read_text()) if args.decisions.exists() else {}
    for it in items:  # keep existing decisions; add empty entries for new candidates
        decisions.setdefault(it["challenge_id"], {"decision": "", "reason": ""})
    bad = {k: v.get("decision") for k, v in decisions.items() if v.get("decision") not in ("", *DECISIONS)}
    if bad:
        raise SystemExit(f"unknown decisions (use {', '.join(DECISIONS)}): {bad}")
    args.decisions.write_text(json.dumps(decisions, indent=1, ensure_ascii=False) + "\n")
    args.out.write_text(render(items, decisions), encoding="utf-8")
    print(json.dumps({"candidate_hits": len(items), "form": str(args.out), "decisions": str(args.decisions)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
