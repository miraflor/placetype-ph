from __future__ import annotations

import pandas as pd

from placetype_ph.evaluate import evaluate_predictions, tree_distance


def test_hierarchical_metrics_reward_correct_backoff(toy_psic):
    predictions = pd.DataFrame(
        [
            {"canonical_id": "a", "scheme": "psic", "code": "1011", "status": "SINGLE"},
            {"canonical_id": "b", "scheme": "psic", "code": "10", "status": "LLM_PARTIAL"},
        ]
    )
    gold = pd.DataFrame(
        [
            {"canonical_id": "a", "scheme": "psic", "gold_code": "10111"},
            {"canonical_id": "b", "scheme": "psic", "gold_code": "10210"},
        ]
    )
    metrics, summary = evaluate_predictions(predictions, gold, toy_psic)
    division = metrics[metrics["level"] == "division"].iloc[0]
    assert division["production_rate"] == 1.0
    assert division["accuracy"] == 1.0
    assert summary["coverage"] == 1.0
    assert tree_distance(toy_psic, "1011", "10111") == 1
