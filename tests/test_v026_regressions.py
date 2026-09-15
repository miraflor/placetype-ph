"""Regression tests for the defects found in the v0.2.5 review."""

from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd
import pytest

from placetype_ph.models import TaxonomyNode
from placetype_ph.taxonomy import Taxonomy, TaxonomyError
from placetype_ph.taxonomy_import import (
    ALIASES,
    EXPECTED_STRUCTURE,
    ImportReport,
    _find_column,
    canonical_code,
    import_excel,
    infer_level,
    structure_deviations,
)


def test_psic_revision_5_has_twenty_two_sections(tmp_path: Path):
    """PSA publishes 22 sections for Revision 5, so the letters run A to V.

    Recognising only A-U does not just drop section V: because section membership is
    tracked in workbook order, V's divisions silently re-parent under U and no
    structural check fires.
    """
    assert infer_level("psic", "V") == "section"
    assert canonical_code("psic", "v") == "V"
    assert infer_level("psic", "W") is None

    path = tmp_path / "psic_uv.xlsx"
    pd.DataFrame(
        [
            ["U", "Activities of extraterritorial organizations"],
            ["99", "A division under U"],
            ["V", "A twenty-second section"],
            ["96", "A division under V"],
        ]
    ).to_excel(path, index=False, header=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        taxonomy = import_excel(path, "psic", "rev5", strict=False)
    assert taxonomy.get("V").level == "section"
    assert taxonomy.parent("96") == "V"
    assert taxonomy.parent("99") == "U"


def test_qualified_code_headers_are_still_matched():
    """Excluding short aliases lost ordinary column names; exclude other roles instead."""
    assert _find_column(["Code (PSIC Rev. 5)", "Title"], ALIASES["code"]) == "Code (PSIC Rev. 5)"
    assert _find_column(["Industry Code", "Title"], ALIASES["code"]) == "Industry Code"
    assert _find_column(["Parent Code", "Activity Description"], ALIASES["code"]) is None
    assert _find_column(["Parent PSIC Code", "Title"], ALIASES["code"]) is None
    assert _find_column(["Code", "Parent Code", "Title"], ALIASES["code"]) == "Code"


def test_published_structure_is_compared_after_import():
    assert ("psic", "rev5") in EXPECTED_STRUCTURE
    taxonomy = Taxonomy(
        [
            TaxonomyNode("psic", "rev5", "A", "section", "Agriculture"),
            TaxonomyNode("psic", "rev5", "01", "division", "Crop", "A"),
        ]
    )
    deviations = structure_deviations(taxonomy)
    assert any("section: found 1" in line for line in deviations)
    # An unknown source has no published reference, so nothing is claimed about it.
    other = Taxonomy([TaxonomyNode("pscc", "2022", "10", "chapter", "Cereals")])
    assert structure_deviations(other) == []


def test_pcpc_width_mismatches_are_collected_not_raised_one_at_a_time(monkeypatch):
    import placetype_ph.taxonomy_import as ti

    class _Response:
        def __init__(self, payload: object):
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self._payload

    payload = {
        "sections": [{"section": "1", "title": "Food"}],
        "divisions": [{"division": "11", "title": "Cereals"}],
        "groups": [{"group": "111", "title": "Wheat"}],
        "classes": [{"class_code": "1111", "title": "Durum"}],
        "sub-classes": [{"subclasscode": "11111", "title": "Seed"}],
        "item": [
            {"itemcode": "1111111", "title": "Seven digits"},
            {"itemcode": "2222222", "title": "Also seven"},
        ],
    }

    def fake_get(url, params=None, headers=None, timeout=None):
        key = url.rstrip("/").rsplit("/", 1)[-1]
        page = int((params or {}).get("page", 1))
        return _Response(payload.get(key, []) if page == 1 else [])

    monkeypatch.setattr(ti.requests, "get", fake_get)
    with pytest.raises(TaxonomyError) as exc:
        ti.fetch_pcpc_api("token")
    message = str(exc.value)
    assert "1111111" in message and "2222222" in message


def test_import_report_records_title_variants(tmp_path: Path):
    path = tmp_path / "variants.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame([["Code", "Title"], ["1", "Food products"]]).to_excel(
            writer, sheet_name="a", index=False, header=False
        )
        pd.DataFrame([["Code", "Title"], ["1", "Food"]]).to_excel(
            writer, sheet_name="b", index=False, header=False
        )
    report = ImportReport()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import_excel(path, "pcpc", "2002", strict=False, report=report)
    assert report.title_variants
    assert any("title variants" in message for message in report.merge_messages())
