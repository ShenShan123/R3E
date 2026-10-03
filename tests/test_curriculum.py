from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from r3e.knowledge import KnowledgeMatcher
from r3e.loop.blue import BlueConfig, BlueRunner
from r3e.loop.budget import BudgetedClient
from r3e.loop.corpus import load_public_manifest
from r3e.loop.curriculum import CurriculumConfig, CurriculumLoop, holdout_variants, lineages
from r3e.loop.env import build_client
from r3e.loop.fakes import FakeBlueTransport, FakeRedTransport, knowledge_types, nearest_clean_resolver
from r3e.loop.orchestrator import LoopConfig
from r3e.loop.qualification import QualificationConfig
from r3e.loop.sim import Simulator
from r3e.loop.state import RunState


ROOT = Path(__file__).resolve().parents[1]
needs_icarus = pytest.mark.skipif(not all(shutil.which(n) for n in ("iverilog", "vvp")),
                                  reason="Icarus is unavailable")
LAYOUT = {"discovery": {"decoder_3_to_8", "flip_flop", "lshift_reg", "mux_4_1", "sdram_controller"},
          "qualification": set(),
          "holdout": {"first_counter_overflow", "fsm_full"}}


@pytest.fixture(scope="module")
def corpus():
    return load_public_manifest(ROOT / "datasets/manifests/cirfix39.jsonl", ROOT)


def _loop(corpus, root, *, succeed, follow, rounds=4, curriculum=None, cross=None):
    carriers, public = corpus
    splits = {name: [c for c in carriers if c.cluster_id in fams] for name, fams in LAYOUT.items()}
    blue_t = FakeBlueTransport(nearest_clean_resolver([c.clean_rtl for c in carriers]), succeed=succeed)
    red_t = FakeRedTransport(follow=follow)

    def transport(**request):
        user = json.loads(request["messages"][-1]["content"])
        return (blue_t if "current_buggy_rtl" in user else red_t)(**request)

    budget = BudgetedClient(build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=transport), max_calls=5000)
    sim = Simulator(root / "sim")
    cfg = LoopConfig(rounds=rounds, proposals_per_round=3, seed=0, blue=BlueConfig(),
                     qualification=QualificationConfig(max_cases=6))
    # flow test: retrieval always returns the pool (matcher quality is tested elsewhere)
    state = RunState(root / "run", matcher=KnowledgeMatcher(threshold=0.0))
    blue = BlueRunner(json_client=budget, simulator=sim, project_root=ROOT, config=cfg.blue)
    cross_blues = {}
    for label, cross_succeed in (cross or {}).items():
        other = FakeBlueTransport(nearest_clean_resolver([c.clean_rtl for c in carriers]), succeed=cross_succeed)
        client = budget.sibling(build_client({"DEEPSEEK_MODEL": label}, transport=other))
        cross_blues[label] = BlueRunner(json_client=client, simulator=sim, project_root=ROOT, config=cfg.blue)
    loop = CurriculumLoop(config=cfg, state=state, splits=splits, public_challenges=public, blue=blue,
                          red_client=budget, simulator=sim, curriculum=curriculum or CurriculumConfig(),
                          cross_model_blues=cross_blues)
    return loop, state, blue_t, red_t, splits


def _pursue(view):
    return "simpler" if view["status"] == "stuck" else "harder" if view["status"] == "too_easy" else "same"


@needs_icarus
def test_curriculum_weak_point_is_followed_across_designs_and_mastered(corpus, tmp_path):
    # Blue fails without knowledge, escalation fixes it (learnable), and
    # matched knowledge makes it succeed within budget.
    loop, state, blue_t, red_t, splits = _loop(
        corpus, tmp_path, succeed=lambda u, n: n >= 3 or bool(knowledge_types(u)), follow=_pursue)
    loop.run()
    lins = lineages(state)
    assert lins, "unsolved round-0 bugs should open weak points"
    assert all(lin["steps"][0]["blue_outcome"] == "fixed_only_by_escalation" for lin in lins.values())
    follow_ups = [row for row in state.read("red") if (row.get("decision") or {}).get("lineage_id")]
    assert follow_ups, "Red should pursue weak points in later rounds"
    for lin in lins.values():  # referee: every variant lives on a new design cluster
        assert len(lin["clusters"]) == len(set(lin["clusters"]))
    # gate cases are Red's bugs, never the candidate's own source bugs or designs
    assert state.read("qualification") and state.active_items()
    items = {(i.item_id, i.version): i for i in state.store.items_with_status(
        "candidate", "qualified", "active", "rejected", "suspended", "retired")}
    for row in state.read("qualification"):
        sources = loop._source_challenges(items[(row["item_id"], row["version"])])
        clusters = loop._source_clusters(sources)
        for outcome in row["outcomes"]:
            assert outcome["challenge_id"] not in sources
            assert loop._challenges[outcome["challenge_id"]].carrier.cluster_id not in clusters
    mastered = [lin for lin in lins.values() if lin["status"] == "mastered"]
    assert mastered
    m = mastered[0]["steps"][-1]["mastery"]
    assert m["memory_used"] and m["with_memory"].startswith("3/") and m["without_memory"].startswith("0/")
    # every learning encounter is classified; offered cases carry their track record
    assert all("memory_outcomes" in row for row in state.read("rounds"))
    assert state.read("memory_usage")
    offered = [json.loads(r["messages"][-1]["content"]) for r in blue_t.requests
               if "reference_cases" in r["messages"][-1]["content"]]
    assert offered and all("track_record" in item for u in offered for item in u["reference_cases"]["items"])
    # information boundary: Blue never sees lineage, operator or Red's reasoning
    for request in blue_t.requests:
        text = request["messages"][-1]["content"]
        assert "lineage" not in text and "experience_base" not in text and "scripted weakness" not in text
    # Red's later prompts carry its experience base
    later = [json.loads(r["messages"][-1]["content"]) for r in red_t.requests[3:]]
    assert any(u["experience_base"]["open_weak_points"] for u in later)
    # resume: nothing re-runs
    calls = loop.blue.budget.total_calls
    loop.run()
    assert loop.blue.budget.total_calls == calls
    # holdout: one Red variant per weak point on untouched designs, nothing written back
    encounters = len(state.read("encounters"))
    held = holdout_variants(loop, splits["holdout"], seed=1)
    assert held and all(ch.carrier.cluster_id in LAYOUT["holdout"] for ch in held)
    assert len(state.read("encounters")) == encounters
    calls = loop.blue.budget.total_calls
    again = holdout_variants(loop, splits["holdout"], seed=1)  # resume: same variants, no calls
    assert [c.challenge_id for c in again] == [c.challenge_id for c in held]
    assert loop.blue.budget.total_calls == calls


@needs_icarus
def test_curriculum_stuck_weak_point_steps_down_and_closes(corpus, tmp_path):
    loop, state, *_ = _loop(corpus, tmp_path, succeed=lambda u, n: False, follow=_pursue, rounds=3,
                            curriculum=CurriculumConfig(max_generations=1))
    loop.run()
    lins = lineages(state).values()
    assert lins and all(lin["steps"][0]["blue_outcome"] == "not_fixed" for lin in lins)
    simpler = [row for row in state.read("red") if (row.get("decision") or {}).get("direction") == "simpler"
               and row.get("admitted")]
    assert simpler and all(len(row["chosen"]["edits"]) == 1 for row in simpler)
    assert any(lin["status"] == "unmastered" for lin in lins)
    assert state.read("qualification") == []  # no verified fix, nothing to learn from


@needs_icarus
def test_curriculum_referee_rejects_off_mechanism_variants(corpus, tmp_path):
    loop, state, *_ = _loop(corpus, tmp_path, succeed=lambda u, n: n >= 3, follow=None, rounds=1)
    loop.run()
    lin = next(iter(lineages(state).values()))
    carrier = next(c for c in loop.splits["discovery"] if c.cluster_id not in lin["clusters"])
    from r3e.loop.operators import enumerate_sites
    off = next(s for s in enumerate_sites(carrier) if s.operator not in lin["operators"])

    class OffMechanism:
        def __call__(self, **request):
            from r3e.loop.fakes import _reply
            return _reply({"decision": {"lineage_id": lin["lineage_id"], "direction": "same", "reason": "x"},
                           "candidates": [{"hypothesis": "h", "edits": [{"site_id": off.site_id,
                                                                         "option": off.options[0]}]}]}, 1)

    loop.red.client = BudgetedClient(build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=OffMechanism()),
                                     max_calls=5)
    proposal = loop.red.propose_curriculum(carrier, state=state, offered=[lin], seed=3, round_index=9)
    assert proposal.challenge is None
    assert proposal.record["candidates"][0]["reason"] == "not_lineage_mechanism"


@needs_icarus
def test_curriculum_mastery_is_repeated_with_other_blue_models(corpus, tmp_path):
    # the loop's Blue learns; a second model uses the same memory and seeds,
    # but only as a measurement (it never changes lineage status or Red's view)
    loop, state, blue_t, red_t, splits = _loop(
        corpus, tmp_path, succeed=lambda u, n: n >= 3 or bool(knowledge_types(u)), follow=_pursue,
        cross={"other-model": lambda u, n: n >= 1})
    loop.run()
    steps = [s for lin in lineages(state).values() for s in lin["steps"] if s.get("mastery")]
    used = [s["mastery"] for s in steps if s["mastery"]["memory_used"]]
    assert used and all({"with_memory", "without_memory"} <= set(m["by_model"]["other-model"]) for m in used)
    assert loop.blue.budget.report()["by_phase"]["cross_model"]["calls"] > 0
    for request in red_t.requests:
        assert "by_model" not in request["messages"][-1]["content"]
    # resume with a different model set is refused (frozen)
    loop2 = None
    with pytest.raises(RuntimeError, match="resume refused"):
        loop2 = _loop(corpus, tmp_path, succeed=lambda u, n: True, follow=_pursue)
    assert loop2 is None
    # holdout evaluation rows name the Blue model
    from r3e.loop.evaluate import evaluate_holdout
    held = holdout_variants(loop, splits["holdout"], seed=1)
    report = evaluate_holdout(state, loop.cross_model_blues["other-model"], held, modes=("none", "matched"),
                              rounds=[2], repeats=1, blue_model="other-model")
    assert report["snapshots"][0]["blue_model"] == "other-model"


@needs_icarus
def test_red_upgrade_harder_composes_up_to_three_edits_and_sees_coverage(corpus, tmp_path):
    from r3e.loop.curriculum import MAX_EDITS_HARDER, mechanism_coverage
    from r3e.loop.operators import CATALOG, enumerate_sites
    assert MAX_EDITS_HARDER == 3 and "unary_drop" in CATALOG
    loop, state, blue_t, red_t, splits = _loop(corpus, tmp_path, succeed=lambda u, n: n >= 3, follow=None, rounds=1)
    loop.run()
    coverage = mechanism_coverage(state)
    assert coverage and all(v["bugs"] == sum(v[k] for k in ("fixed_first_try", "fixed_later",
                                                              "fixed_by_escalation", "not_fixed"))
                            for v in coverage.values())
    lin = next(iter(lineages(state).values()))
    carrier = next(c for c in loop.splits["discovery"] if c.cluster_id not in lin["clusters"]
                   and sum(s.operator in lin["operators"] for s in enumerate_sites(c)) >= 1
                   and len(enumerate_sites(c)) >= 3)
    sites = [s for s in enumerate_sites(carrier)]
    mine = next(s for s in sites if s.operator in lin["operators"])
    others = [s for s in sites if s.site_id != mine.site_id][:2]

    class ThreeEdits:
        def __call__(self, **request):
            from r3e.loop.fakes import _reply
            user = json.loads(request["messages"][-1]["content"])
            assert "mechanism_coverage" in user["experience_base"]
            edits = [{"site_id": s.site_id, "option": s.options[0]} for s in [mine, *others]]
            return _reply({"decision": {"lineage_id": lin["lineage_id"], "direction": "harder", "reason": "x"},
                           "candidates": [{"hypothesis": "h", "edits": edits}]}, 1)

    loop.red.client = BudgetedClient(build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=ThreeEdits()),
                                     max_calls=5)
    proposal = loop.red.propose_curriculum(carrier, state=state, offered=[lin], seed=3, round_index=9)
    assert len(proposal.record["candidates"][0]["edits"]) == 3  # a 'harder' variant keeps all three edits


@needs_icarus
def test_pool_red_chooses_real_bugs_and_pursues_weak_points_on_new_designs(corpus, tmp_path):
    from r3e.loop.pool import PoolCurriculumLoop, fix_type, holdout_pool
    carriers, public = corpus
    splits = {name: [c for c in carriers if c.cluster_id in fams] for name, fams in LAYOUT.items()}
    blue_t = FakeBlueTransport(nearest_clean_resolver([c.clean_rtl for c in carriers]),
                               succeed=lambda u, n: n >= 3 or bool(knowledge_types(u)))
    red_t = FakeRedTransport(follow=_pursue)

    def transport(**request):
        user = json.loads(request["messages"][-1]["content"])
        return (blue_t if "current_buggy_rtl" in user else red_t)(**request)

    budget = BudgetedClient(build_client({"DEEPSEEK_MODEL": "fake-local"}, transport=transport), max_calls=5000)
    sim = Simulator(tmp_path / "sim")
    cfg = LoopConfig(rounds=3, proposals_per_round=3, seed=0, blue=BlueConfig(),
                     qualification=QualificationConfig(max_cases=6))
    state = RunState(tmp_path / "run", matcher=KnowledgeMatcher(threshold=0.0))
    blue = BlueRunner(json_client=budget, simulator=sim, project_root=ROOT, config=cfg.blue)
    loop = PoolCurriculumLoop(pool=public, config=cfg, state=state, splits=splits, public_challenges=public,
                              blue=blue, red_client=budget, simulator=sim)
    loop.run()
    rows = [r for r in state.read("red") if r.get("admitted")]
    chosen = [r["challenge_id"] for r in rows]
    assert chosen and len(chosen) == len(set(chosen))  # each real bug at most once
    pool = {ch.challenge_id: ch for ch in public}
    assert all(r["challenge_id"] in r["offered_candidates"] for r in rows)  # only offered bugs
    discovery = {c.cluster_id for c in splits["discovery"]}
    assert all(pool[c].carrier.cluster_id in discovery for c in chosen)
    for lin in lineages(state).values():  # pursuits: same fix type, new design each step
        assert len(lin["clusters"]) == len(set(lin["clusters"]))
    follow_ups = [r for r in rows if (r.get("decision") or {}).get("lineage_id")]
    assert follow_ups, "Red should pursue a weak point with another real bug"
    for r in follow_ups:
        lin = lineages(state)[r["decision"]["lineage_id"]]
        assert f"type:{fix_type(pool[r['challenge_id']])}" in lin["operators"]
    # Blue never sees Red's view of the bug (fix type, changed lines, reasons)
    for request in blue_t.requests:
        text = request["messages"][-1]["content"]
        assert "fix_type" not in text and "changed_lines" not in text and "experience_base" not in text
    # holdout bugs are on designs the loop never touched
    held = holdout_pool(loop)
    assert held and not {h.challenge_id for h in held} & set(chosen)
