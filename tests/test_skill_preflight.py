import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "semantic_repair_bench"))

from skill_preflight import combine_recall_context  # noqa: E402


def test_preflight_suppresses_legacy_recall_context():
    def recall_fn(_case):
        return "RAW_HISTORY_CONTEXT"

    ctx = combine_recall_context(
        {},
        {"enabled": True, "strategy_context": "PROMOTED_STRATEGY"},
        recall_fn,
    )

    assert ctx == "PROMOTED_STRATEGY"


def test_legacy_ablation_can_still_use_recall_context():
    def recall_fn(_case):
        return "RAW_HISTORY_CONTEXT"

    ctx = combine_recall_context(
        {},
        {"enabled": False, "strategy_context": "PROMOTED_STRATEGY"},
        recall_fn,
    )

    assert ctx == "PROMOTED_STRATEGY\nRAW_HISTORY_CONTEXT"


def test_red_mutator_original_path_is_legacy_facade():
    from semantic_repair_bench import red_mutator

    assert red_mutator.mutate_once.__module__ == "r3e.legacy.red_mutator"
    assert (
        red_mutator.generate_repairable_poison.__module__
        == "r3e.legacy.red_mutator"
    )


def test_accumulation_curve_original_path_aliases_legacy_implementation():
    from semantic_repair_bench import correctness_gated_accumulation_curve as curve

    assert curve.LEGACY_ONLY is True
    assert (
        curve.gen_poison.__module__
        == "experiments.legacy.correctness_gated_accumulation_curve"
    )
