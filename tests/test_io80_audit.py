from __future__ import annotations

import pandas as pd
import pytest

from placetype_ph.io80_audit import (
    blind_audit_sample,
    error_upper_bound,
    finite_population_error_upper_bound,
    name_impact,
    name_rule_self_consistency,
    resolved_row_purity,
    summarize_blind_audit,
)
from placetype_ph.io80_resolver import resolve_io80_ambiguity


def _resolved_for_audit() -> pd.DataFrame:
    rows = []
    for i in range(3):
        rows.append({"canonical_id": f"a{i}", "canonical_name": "Alpha", "io80_codes": "55"})
    for i in range(3):
        rows.append({"canonical_id": f"b{i}", "canonical_name": "Beta", "io80_codes": "56"})
    for i in range(3):
        rows.append({"canonical_id": f"c{i}", "canonical_name": "Bank", "io80_codes": "66"})
    for i in range(8):
        rows.append({"canonical_id": f"ta{i}", "canonical_name": "Alpha", "io80_codes": "55; 56"})
    for i in range(4):
        rows.append({"canonical_id": f"tb{i}", "canonical_name": "Beta", "io80_codes": "55; 56"})
    for i in range(5):
        rows.append({"canonical_id": f"tc{i}", "canonical_name": "Bank", "io80_codes": "66; 67"})
    return resolve_io80_ambiguity(pd.DataFrame(rows))


def test_blind_sample_hides_prediction_and_method():
    worksheet, key = blind_audit_sample(_resolved_for_audit(), per_stratum=2, seed=3)
    assert "io80_map_code" not in worksheet.columns
    assert "predicted_code" not in worksheet.columns
    assert "io80_resolution_method" not in worksheet.columns
    assert "predicted_code" in key.columns
    assert set(worksheet["row"]) == set(key["row"])


def test_55_56_to_56_is_censused_by_default():
    resolved = _resolved_for_audit()
    worksheet, key = blind_audit_sample(resolved, per_stratum=1, seed=0)
    target = key[(key["pair"] == "55; 56") & (key["predicted_code"] == "56")]
    expected = int(
        (
            (resolved["io80_codes"] == "55; 56")
            & (resolved["io80_map_code"] == "56")
        ).sum()
    )
    assert len(target) == expected
    assert target["census"].all()


def test_incomplete_audit_is_rejected():
    worksheet, key = blind_audit_sample(_resolved_for_audit(), per_stratum=1)
    with pytest.raises(ValueError, match="incomplete"):
        summarize_blind_audit(worksheet, key)


def test_completed_audit_scores_against_separate_key():
    worksheet, key = blind_audit_sample(_resolved_for_audit(), per_stratum=2)
    predictions = key.set_index("row")["predicted_code"]
    worksheet["audit_label"] = worksheet["row"].map(predictions)
    first = worksheet.index[0]
    worksheet.loc[first, "audit_label"] = "other"
    per_stratum, overall = summarize_blind_audit(worksheet, key)
    assert overall["errors"] == 1
    assert overall["audited_rows"] == len(worksheet)
    assert (per_stratum["finite_population_upper_bound"] <= 1).all()


def test_finite_population_bound_is_exact_for_census():
    assert finite_population_error_upper_bound(2, 10, 10) == pytest.approx(0.2)


def test_finite_population_bound_not_larger_than_binomial_for_zero_errors():
    finite = finite_population_error_upper_bound(0, 25, 100)
    binomial = error_upper_bound(0, 25)
    assert finite <= binomial


def test_resolved_rows_have_global_purity_one_under_v4():
    purity = resolved_row_purity(_resolved_for_audit())
    assert not purity.empty
    assert purity.eq(1.0).all()


def test_name_impact_ranks_decisions():
    impact = name_impact(_resolved_for_audit())
    assert impact.iloc[0]["rows_decided"] >= impact.iloc[-1]["rows_decided"]
    assert (impact["rows_per_support"] > 0).all()


def test_self_consistency_detects_hidden_disagreement():
    frame = pd.DataFrame([
        {"canonical_id": "a1", "canonical_name": "Mixed", "io80_codes": "55"},
        {"canonical_id": "a2", "canonical_name": "Mixed", "io80_codes": "55"},
        {"canonical_id": "a3", "canonical_name": "Mixed", "io80_codes": "55"},
        {"canonical_id": "b", "canonical_name": "Mixed", "io80_codes": "56"},
    ])
    out = name_rule_self_consistency(frame, ("55", "56"))
    assert int(out["disagreements"].sum()) == 1


def test_audit_key_stratum_ids_do_not_reveal_code_text():
    _, key = blind_audit_sample(_resolved_for_audit(), per_stratum=1)
    assert key["stratum_id"].str.match(r"s-[0-9a-f]{12}").all()
