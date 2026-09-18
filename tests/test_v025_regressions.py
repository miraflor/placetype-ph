"""Regression tests for defects found in the v0.2.4 review."""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from placetype_ph.classifier import EntityClassifier
from placetype_ph.crosswalk import Crosswalk
from placetype_ph.models import CrosswalkEntry, MappingKind, SourceField, TaxonomyNode
from placetype_ph.taxonomy import TaxonomyError
from placetype_ph.taxonomy_import import (
    ImportReport,
    _api_get_all,
    _build,
    fetch_pcpc_api,
    import_excel,
)
from placetype_ph.text import clean_literal, clean_text


def _write(path: Path, rows: list[list[object]]) -> Path:
    pd.DataFrame(rows).to_excel(path, index=False, header=False)
    return path


def test_pandas_missing_scalars_do_not_become_semantic_text():
    assert clean_text(np.nan) is None
    assert clean_text(pd.NA) is None
    assert clean_literal(np.nan) is None
    assert clean_literal(pd.NA) is None


def test_official_taxonomy_title_other_is_not_treated_as_missing(tmp_path: Path):
    path = _write(
        tmp_path / "other.xlsx",
        [
            ["Code", "Title"],
            ["1", "Products"],
            ["11", "Other"],
            ["111", "Other products"],
        ],
    )
    taxonomy = import_excel(path, "pcpc", "2002", strict=False)
    assert taxonomy.get("11").title == "Other"


def test_allow_orphans_does_not_disable_strict_levels():
    nodes = [
        TaxonomyNode("psic", "rev5", "A", "section", "A"),
        TaxonomyNode("psic", "rev5", "101", "group", "G", "A"),
        TaxonomyNode("psic", "rev5", "1011", "class", "C", "101"),
        TaxonomyNode("psic", "rev5", "10111", "subclass", "S", "1011"),
    ]
    with pytest.raises(TaxonomyError, match="missing"):
        _build(nodes, strict=False, strict_levels=True)


def test_allow_orphans_does_not_accept_leading_zero_corruption(tmp_path: Path):
    path = _write(
        tmp_path / "numeric.xlsx",
        [
            ["A", "Agriculture"],
            [1, "Crop production"],
            [11, "Non-perennial crops"],
        ],
    )
    with pytest.raises(TaxonomyError, match="shorter than the minimum"):
        import_excel(path, "psic", "rev5", strict=False)


def test_declared_level_rejects_a_code_that_is_too_wide(tmp_path: Path):
    path = _write(
        tmp_path / "bad-level.xlsx",
        [
            ["Code", "Title", "Level"],
            ["A", "Agriculture", "Section"],
            ["999", "Impossible division", "Division"],
        ],
    )
    with pytest.raises(TaxonomyError, match="contradict their declared hierarchy level"):
        import_excel(path, "psic", "rev5", strict=False)


def test_explicit_missing_parent_is_not_silently_rederived(tmp_path: Path):
    path = _write(
        tmp_path / "bad-parent.xlsx",
        [
            ["Code", "Title", "Parent code"],
            ["A", "Agriculture", ""],
            ["10", "Food", "ZZ"],
            ["101", "Group", "10"],
        ],
    )
    with pytest.raises(TaxonomyError, match="explicit parent code"):
        import_excel(path, "psic", "rev5", parent_column="Parent code", strict=False)


def test_structurally_conflicting_duplicate_codes_still_fail():
    """A repeated code at another level is a real contradiction, not a wording variant."""
    from placetype_ph.taxonomy_import import _merge_duplicate_nodes

    with pytest.raises(TaxonomyError, match="conflicting duplicate code"):
        _merge_duplicate_nodes(
            [
                TaxonomyNode("pcpc", "2002", "11", "division", "Food", "1"),
                TaxonomyNode("pcpc", "2002", "11", "group", "Food", "1"),
            ]
        )
    with pytest.raises(TaxonomyError, match="conflicting duplicate code"):
        _merge_duplicate_nodes(
            [
                TaxonomyNode("pcpc", "2002", "11", "division", "Food", "1"),
                TaxonomyNode("pcpc", "2002", "11", "division", "Food", "2"),
            ]
        )


def test_a_repeated_code_with_different_wording_is_recorded_not_fatal(tmp_path: Path):
    """PSA ships a summary sheet beside the detailed one, so titles differ legitimately."""
    path = tmp_path / "duplicate.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame(
            [["Code", "Title"], ["1", "Products"], ["11", "Food and live animals"]]
        ).to_excel(writer, sheet_name="detailed", index=False, header=False)
        pd.DataFrame([["Code", "Title"], ["11", "Food"]]).to_excel(
            writer, sheet_name="summary", index=False, header=False
        )
    report = ImportReport()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        taxonomy = import_excel(path, "pcpc", "2002", strict=False, report=report)
    assert taxonomy.get("11").title == "Food and live animals"
    assert any(variant.startswith("11:") for variant in report.title_variants)


def test_small_flat_table_is_not_counted_twice_by_fallback_scanner(tmp_path: Path):
    path = _write(
        tmp_path / "small.xlsx",
        [["Code", "Title"], ["A", "Agriculture"], ["10", "Food"], ["101", "Group"]],
    )
    report = ImportReport()
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        import_excel(path, "psic", "rev5", strict=False, report=report)
    assert report.rows_seen == 3
    assert report.nodes_built == 3


def test_uncodeable_evidence_does_not_cancel_not_activity(toy_psic):
    crosswalk = Crosswalk(
        [
            CrosswalkEntry("fsq", "monument", "psic", "rev5", MappingKind.NOT_ACTIVITY),
            CrosswalkEntry(
                "fsq",
                "Rizal",
                "psic",
                "rev5",
                MappingKind.UNCODEABLE,
                source_field=SourceField.NAME,
            ),
        ]
    )
    result = EntityClassifier(toy_psic, crosswalk).classify_row(
        {"canonical_id": "x", "fsq_category": "monument", "fsq_name": "Rizal"}
    )
    assert result.status == "NOT_PSIC_ACTIVITY"


def test_pcpc_api_level_repairs_numeric_leading_zero(monkeypatch):
    from placetype_ph import taxonomy_import as ti

    payloads = {
        "sections": [{"section": "0", "title": "Products"}],
        "divisions": [{"division": 1, "title": "Division 01"}],
        "groups": [{"group": 11, "title": "Group 011"}],
        "classes": [{"class_code": 111, "title": "Class 0111"}],
        "sub-classes": [{"subclasscode": 1111, "title": "Subclass 01111"}],
        "item": [{"itemcode": 11111, "title": "Item 011111"}],
    }

    def fake_all(url: str, token: str):
        return payloads[url.rsplit("/", 1)[-1]]

    monkeypatch.setattr(ti, "_api_get_all", fake_all)
    taxonomy = fetch_pcpc_api("token", strict_levels=True)
    assert {"0", "01", "011", "0111", "01111", "011111"} <= set(taxonomy.nodes)


def test_cursor_next_url_is_followed(monkeypatch):
    from placetype_ph import taxonomy_import as ti

    calls: list[tuple[str, object]] = []

    class Response:
        def __init__(self, payload: object):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_get(url, params, headers, timeout):
        calls.append((url, params))
        if len(calls) == 1:
            return Response(
                {
                    "results": [{"id": 1}],
                    "next": "https://classification.psa.gov.ph/pcpc/2002/items?cursor=abc",
                }
            )
        return Response({"results": [{"id": 2}], "next": None})

    monkeypatch.setattr(ti.requests, "get", fake_get)
    rows = _api_get_all("https://classification.psa.gov.ph/pcpc/2002/items", "secret")
    assert rows == [{"id": 1}, {"id": 2}]
    assert "cursor=abc" in calls[1][0]
    assert "token=secret" in calls[1][0]
    assert calls[1][1] is None


def test_generic_code_alias_does_not_select_parent_code_column():
    from placetype_ph.taxonomy_import import ALIASES, _find_column

    assert _find_column(["Parent Code", "Activity Description"], ALIASES["code"]) is None
    assert (
        _find_column(["PSIC Code 2026", "Activity Description"], ALIASES["code"])
        == "PSIC Code 2026"
    )


def test_explicit_column_typo_is_not_hidden_by_fallback_scanner(tmp_path: Path):
    path = _write(
        tmp_path / "explicit.xlsx",
        [["Code", "Title"], ["A", "Agriculture"], ["10", "Food"]],
    )
    with pytest.raises(TaxonomyError, match="explicit column"):
        import_excel(path, "psic", "rev5", code_column="PSIC COD")


def test_pcpc_api_does_not_silently_drop_malformed_rows(monkeypatch):
    from placetype_ph import taxonomy_import as ti

    payloads = {
        "sections": [{"section": "0", "title": "Products"}],
        "divisions": [{"division": "01", "title": "Division"}],
        "groups": [{"group": "011", "title": "Group"}],
        "classes": [{"class_code": "0111", "title": "Class"}],
        "sub-classes": [{"subclasscode": "01111", "title": "Subclass"}],
        "item": [
            {"id": 1, "itemcode": "011111", "title": "Item"},
            {"id": 2, "itemcode": "011112", "title": ""},
        ],
    }

    def fake_all(url: str, token: str):
        return payloads[url.rsplit("/", 1)[-1]]

    monkeypatch.setattr(ti, "_api_get_all", fake_all)
    with pytest.raises(TaxonomyError, match="silently incomplete official taxonomy"):
        fetch_pcpc_api("token", strict_levels=True)
