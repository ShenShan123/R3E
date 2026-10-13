"""Post-collection exposure and source-quality audits on a fake-client continuous run."""
import json

import pytest

from experiments.loop_probes import continuous_audit as ca, continuous_loop as loop
from tests.test_continuous_loop import fake_client, plan_for
from tests.test_repo_blue import WordCounter


def test_interval_mapping_points_and_half_open_overlap():
    clean, mutant = "abcdefgh", "abXYefgh"           # cd -> XY at clean [2, 4)
    assert ca._map_to_clean(2, 3, clean, mutant) == [(2, 4)]  # inside a mutated block: the block's clean span
    assert ca._map_to_clean(5, 6, clean, mutant) == [(5, 6)]  # unchanged text shifts exactly
    assert ca._overlap((2, 4), (3, 3)) and not ca._overlap((2, 4), (4, 4))  # start included, end excluded
    assert ca._overlap((4, 4), (4, 4)) and not ca._overlap((1, 2), (2, 3))


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    root = tmp_path_factory.mktemp("continuous")
    plan = plan_for(root)
    client, _ = fake_client(plan["max_calls"])
    loop.collect(plan, root / "run", client=client, counter=WordCounter())
    return root / "run"


def test_exposure_audit_tags_store_and_delivered_entries(run):
    report = ca.exposure_audit(run)
    rows = {(t["role"], t.get("slot_id") or t.get("probe")): t for t in report["trials"]}
    assert all(t["store_group"] == "neither_observed" for t in report["trials"] if t["snapshot_id"] == "B_0")
    # anchor 0 (human defect on carrier 0) vs B_1's round-1 repair on carrier 0: same line, different mutant
    a = rows[("anchor", "b1_a0")]
    assert a["store_group"] == "span_overlap_only" and not a["store"]["same_mutant"]
    assert a["store"]["repair_span_overlap"][0]["intersections"]
    # each online round targets a different carrier: its experience cannot overlap it
    assert all(rows[("online", f"r{r}_s0")]["store_group"] == "neither_observed" for r in (1, 2, 3))
    assert sum(report["summary"]["anchor"]["store"].values()) == 12
    with pytest.raises(FileExistsError):
        ca.exposure_audit(run)  # new artifacts only, never overwritten


def test_unknown_mapping_is_not_counted_as_unexposed(run, tmp_path):
    src = next((run / "sources").glob("*.json"))
    record = json.loads(src.read_text())
    assert ca._repair({**record, "archive_path": str(tmp_path / "missing.json")})["mapping"] == "unknown"
    files = json.loads(open(record["archive_path"]).read())
    files["files"] = {k: "module toy; endmodule\n" + "x" * 500 for k in files["files"]}
    (tmp_path / "gen.json").write_text(json.dumps(files))
    assert ca._repair({**record, "archive_path": str(tmp_path / "gen.json")})["reason"].startswith("repair rewrites")
    assert ca._group({"same_mutant": [], "repair_span_overlap": [], "unknown": [{"entry": "e"}]}) == "unknown"


def test_source_audit_joins_task_and_candidate_and_links_deliveries(run):
    report = ca.source_audit(run)
    assert [s["category"] for s in report["sources"]] == ["no_mismatch_within_scope"] * 3
    per = {p["snapshot_id"]: p for p in report["per_snapshot"]}
    assert [sum(per[f"B_{i}"]["unique_sources"].values()) for i in range(4)] == [0, 1, 2, 3]
    assert per["B_1"]["deliveries"]["no_mismatch_within_scope"] >= 1
    plan = json.loads((run / "plan.json").read_text())
    freeze = json.loads(open(plan["carriers"][0]["evaluator_freeze"]).read())
    tb = next(iter(freeze["files"]))
    path = run.parent / "case0" / tb  # a changed evaluator makes its sources unavailable, never a pass
    path.write_text(path.read_text() + "\n// changed\n")
    (run / "audits" / "source_audit.json").unlink()
    assert "unavailable" in {s["category"] for s in ca.source_audit(run)["sources"]}
