"""Regression tests for defects found while reviewing v0.2.6."""

from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd
import pytest
import typer

from placetype_ph.cli import _require_official_structure
from placetype_ph.evaluate import evaluate_predictions
from placetype_ph.models import TaxonomyNode
from placetype_ph.taxonomy import Taxonomy, TaxonomyError
from placetype_ph.taxonomy_import import ImportReport, import_excel


def test_same_sheet_duplicate_reaches_global_merge(tmp_path: Path):
    path = tmp_path / "duplicates.xlsx"
    pd.DataFrame(
        [
            ["Code", "Title", "Parent Code", "Notes"],
            ["1", "Food", None, ""],
            ["11", "Short", "1", ""],
            ["11", "Much fuller title", "1", "Detailed explanatory note"],
        ]
    ).to_excel(path, index=False, header=False)

    report = ImportReport()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        taxonomy = import_excel(path, "pcpc", "2002", strict=False, report=report)

    node = taxonomy.get("11")
    assert node.title == "Much fuller title"
    assert node.description == "Detailed explanatory note"
    assert report.duplicate_codes == 1
    assert report.title_variants


def test_same_sheet_duplicate_cannot_hide_conflicting_parent(tmp_path: Path):
    path = tmp_path / "conflicting_parent.xlsx"
    pd.DataFrame(
        [
            ["Code", "Title", "Parent Code"],
            ["1", "Root one", None],
            ["2", "Root two", None],
            ["11", "First", "1"],
            ["11", "Second", "2"],
        ]
    ).to_excel(path, index=False, header=False)

    with pytest.raises(TaxonomyError, match="conflicting duplicate code '11'.*parents"):
        import_excel(path, "pcpc", "2002", strict=False)


def test_duplicate_rows_merge_complementary_metadata(tmp_path: Path):
    path = tmp_path / "complementary.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame(
            [["Code", "Title", "Parent Code", "Notes"], ["1", "Food", None, ""],
             ["11", "A fuller division title", "1", ""]]
        ).to_excel(writer, sheet_name="summary", index=False, header=False)
        pd.DataFrame(
            [["Code", "Title", "Notes"], ["11", "Food div.", "Detailed note"]]
        ).to_excel(writer, sheet_name="detail", index=False, header=False)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        taxonomy = import_excel(path, "pcpc", "2002", strict=False)
    node = taxonomy.get("11")
    assert node.parent_code == "1"
    assert node.title == "A fuller division title"
    assert node.description == "Detailed note"


def test_official_fetch_gate_rejects_known_incomplete_structure():
    taxonomy = Taxonomy(
        [
            TaxonomyNode("psic", "rev5", "A", "section", "Agriculture"),
            TaxonomyNode("psic", "rev5", "01", "division", "Crop", "A"),
        ]
    )
    with pytest.raises(typer.Exit):
        _require_official_structure(taxonomy, allow_structure_deviation=False)
    _require_official_structure(taxonomy, allow_structure_deviation=True)


def test_invalid_gold_code_is_not_silently_dropped(toy_psic):
    predictions = pd.DataFrame(
        [{"canonical_id": "a", "scheme": "psic", "code": "10111"}]
    )
    gold = pd.DataFrame(
        [{"canonical_id": "a", "scheme": "psic", "gold_code": "99999"}]
    )
    with pytest.raises(ValueError, match="unknown psic code"):
        evaluate_predictions(predictions, gold, toy_psic)


def test_explicit_not_codeable_gold_measures_false_positive(toy_psic):
    predictions = pd.DataFrame(
        [
            {"canonical_id": "a", "scheme": "psic", "code": "10111"},
            {"canonical_id": "b", "scheme": "psic", "code": "20110"},
            {"canonical_id": "c", "scheme": "psic", "code": None},
        ]
    )
    gold = pd.DataFrame(
        [
            {
                "canonical_id": "a",
                "scheme": "psic",
                "gold_code": "10111",
                "gold_status": "CODED",
            },
            {
                "canonical_id": "b",
                "scheme": "psic",
                "gold_code": None,
                "gold_status": "NOT_CODEABLE",
            },
            {
                "canonical_id": "c",
                "scheme": "psic",
                "gold_code": None,
                "gold_status": "NON_ECONOMIC_POI",
            },
        ]
    )
    _, summary = evaluate_predictions(predictions, gold, toy_psic)
    assert summary["gold_rows"] == 3
    assert summary["coded_gold_rows"] == 1
    assert summary["not_codeable_gold_rows"] == 2
    assert summary["not_codeable_false_positive_rows"] == 1
    assert summary["not_codeable_code_avoidance"] == 0.5
    assert summary["coverage"] == 1.0


def test_malformed_decimal_or_signed_codes_are_not_rewritten_as_valid_codes(tmp_path: Path):
    from placetype_ph.taxonomy_import import canonical_code

    assert canonical_code("psic", "1.5") == ""
    assert canonical_code("pcpc", "-11") == ""
    assert canonical_code("pscc", "10.06") == "1006"

    path = tmp_path / "bad_decimal.xlsx"
    pd.DataFrame(
        [["Code", "Title"], ["1", "Section"], ["1.5", "Would become 15 in v0.2.6"]]
    ).to_excel(path, index=False, header=False)
    report = ImportReport()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        taxonomy = import_excel(path, "pcpc", "2002", strict=False, report=report)
    assert "15" not in taxonomy.nodes
    assert report.invalid_code_formats == ["1.5"]
