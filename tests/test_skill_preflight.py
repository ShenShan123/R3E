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
