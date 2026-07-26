import hashlib
import json

from semantic_repair_bench.correctness_gated_accumulation_curve import (
    PatternAccumulator,
    commit_gate_positive_templates_to_formal_registry,
)
from semantic_repair_bench.formal_protocol import (
    empty_registry,
    hash_payload,
    load_promoted_registry,
    load_registry,
    policy_hash,
)


def test_automatic_template_has_reconstructable_candidate_provenance(tmp_path):
    records = tmp_path / "records.jsonl"
    rows = [
        {"case_id": "case-a", "family": "constant_error", "design": "d1"},
        {"case_id": "case-b", "family": "constant_error", "design": "d2"},
    ]
    records.write_text("\n".join(json.dumps(x) for x in rows) + "\n")
    acc = PatternAccumulator(
        records,
        tmp_path / "templates.json",
        min_support=2,
        promoted_template_path=tmp_path / "promoted.json",
        template_stage="shadow",
    )
    assert len(acc.templates) == 1
    t = acc.templates[0]
    assert t["origin"] == "automatic"
    assert t["source_record_ids"] == ["case-a", "case-b"]
    assert hashlib.sha256(t["candidate_prompt"].encode()).hexdigest() == t["candidate_prompt_hash"]
    assert hashlib.sha256(t["candidate_raw_output"].encode()).hexdigest() == t["candidate_output_hash"]
    body = dict(t)
    candidate_hash = body.pop("candidate_hash")
    expected = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert candidate_hash == expected


def test_fixed_blue_without_records_has_no_candidate(tmp_path):
    records = tmp_path / "records.jsonl"
    records.write_text("")
    acc = PatternAccumulator(records, tmp_path / "templates.json", min_support=2,
                             promoted_template_path=tmp_path / "promoted.json",
                             template_stage="shadow")
    assert acc.templates == []


def test_gate_positive_candidate_atomically_changes_formal_registry(tmp_path):
    records = tmp_path / "records.jsonl"
    records.write_text("\n".join(json.dumps(x) for x in [
        {"case_id": "train-a", "family": "constant_error", "design": "d1", "mutation_type": "constant"},
        {"case_id": "train-b", "family": "constant_error", "design": "d2", "mutation_type": "constant"},
    ]) + "\n")
    acc = PatternAccumulator(records, tmp_path / "templates.json", min_support=2,
                             promoted_template_path=tmp_path / "promoted.json",
                             template_stage="promoted")
    registry = tmp_path / "registry.json"
    decisions = tmp_path / "decisions.json"
    empty_registry(registry)
    decisions.write_text("[]\n")
    before = load_registry(registry)
    out = commit_gate_positive_templates_to_formal_registry(
        registry_path=registry,
        decisions_path=decisions,
        accumulator=acc,
        promotion_report={
            "decisions": [{
                "template_id": "accum_constant_error_v1", "promote": True,
                "thresholds": {
                    "min_validation_hits": 1, "min_covered_designs": 1,
                    "min_target_recovery_ratio": 1.0, "min_target_gain": 0.0,
                    "non_target_regression_tolerance": 0.0,
                },
            }],
            "candidate_validation_rows": {"accum_constant_error_v1": [
                {"case_id": "target", "rep": 0, "design": "target-design",
                 "arm": "baseline", "split": "target", "oracle_ok": False,
                 "strategy_hit": False},
                {"case_id": "target", "rep": 0, "design": "target-design",
                 "arm": "candidate", "split": "target", "oracle_ok": True,
                 "strategy_hit": True},
                {"case_id": "other", "rep": 0, "design": "other-design",
                 "arm": "baseline", "split": "non_target", "oracle_ok": True,
                 "strategy_hit": False},
                {"case_id": "other", "rep": 0, "design": "other-design",
                 "arm": "candidate", "split": "non_target", "oracle_ok": True,
                 "strategy_hit": False},
            ]},
        },
    )
    after = load_registry(registry)
    assert hash_payload(after) != hash_payload(before)
    assert out["policy_hash_after"] != policy_hash(before)
    persisted = json.loads(decisions.read_text())
    active, active_hash = load_promoted_registry(
        registry, {d["decision_hash"]: d for d in persisted}
    )
    assert len(active) == 1
    assert active_hash == out["policy_hash_after"]
    assert acc.promoted_templates[0]["formal_registry_authority"] is True
