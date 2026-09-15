"""Regression tests for the defects found in the v0.2.3 review."""

from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd
import pytest

from placetype_ph.taxonomy_import import (
    ImportReport,
    _api_get_all,
    import_excel,
)


class _FakeResponse:
    def __init__(self, payload: object):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


def _write(path: Path, rows: list[list[object]]) -> Path:
    pd.DataFrame(rows).to_excel(path, index=False, header=False)
    return path


def test_numeric_code_column_is_refused_instead_of_shifting_every_level(tmp_path: Path):
    """A spreadsheet that dropped leading zeroes does not merely lose rows.

    PSIC group 011 arrives as 11 and is then read as a division, so the whole branch
    moves up one level while orphan, gap, prefix and level-existence checks all pass.
    """
    path = _write(
        tmp_path / "numeric.xlsx",
        [
            ["A", "Agriculture"],
            [1, "Crop and animal production"],
            [11, "Growing of non-perennial crops"],
            [111, "Growing of cereals"],
            ["C", "Manufacturing"],
            [10, "Manufacture of food products"],
            [101, "Processing of meat"],
            [1011, "Processing of meat"],
            [10111, "Processing of meat, subclass"],
        ],
    )
    with pytest.raises(Exception) as exc:
        import_excel(path, "psic", "rev5")
    assert "shorter than the minimum" in str(exc.value)
    assert "--level-column" in str(exc.value)


def test_a_declared_level_repairs_the_lost_leading_zero(tmp_path: Path):
    path = _write(
        tmp_path / "levelled.xlsx",
        [
            ["Code", "Title", "Level"],
            ["A", "Agriculture", "Section"],
            [1, "Crop and animal production", "Division"],
            [11, "Growing of non-perennial crops", "Group"],
            [111, "Growing of cereals", "Class"],
            [1111, "Palay", "Sub-class"],
        ],
    )
    taxonomy = import_excel(path, "psic", "rev5")
    assert sorted(taxonomy.nodes) == ["01", "011", "0111", "01111", "A"]
    assert taxonomy.parent("011") == "01"
    assert taxonomy.structural_report().ok


def test_pcpc_one_digit_sections_are_still_valid(tmp_path: Path):
    """The minimum width is per scheme; PCPC sections really are one digit."""
    path = _write(
        tmp_path / "pcpc.xlsx",
        [["1", "Food products"], ["11", "Cereals"], ["111", "Wheat"]],
    )
    taxonomy = import_excel(path, "pcpc", "2002")
    assert taxonomy.get("1").level == "section"


def test_discarded_rows_are_reported(tmp_path: Path):
    rows: list[list[object]] = [
        ["A", "Agriculture"],
        ["01", "Crop production"],
        ["011", "Non-perennial"],
    ]
    rows += [[f"zz{i}", f"junk row {i}"] for i in range(40)]
    path = _write(tmp_path / "partial.xlsx", rows)
    report = ImportReport()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        import_excel(path, "psic", "rev5", report=report)
    assert report.skipped > 0
    assert report.rows_seen >= report.skipped
    assert any("discarded" in str(w.message) for w in caught)


def _paged_endpoint(total: int, server_cap: int, calls: list[int]):
    rows = [{"id": i} for i in range(total)]

    def fake_get(url, params, headers, timeout):
        page = int(params["page"])
        size = min(int(params["page_size"]), server_cap)
        calls.append(page)
        return _FakeResponse(rows[(page - 1) * size : (page - 1) * size + size])

    return fake_get


def test_pagination_is_not_truncated_by_a_capped_page_size(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(
        "placetype_ph.taxonomy_import.requests.get",
        _paged_endpoint(250, 100, calls),
    )
    assert len(_api_get_all("https://example.invalid/items", "token")) == 250


def test_crosswalk_uses_the_shared_source_and_scheme_sets():
    from placetype_ph.crosswalk import Crosswalk, CrosswalkError
    from placetype_ph.models import CrosswalkEntry, MappingKind
    from placetype_ph.openplaces import SOURCES
    from placetype_ph.taxonomy import SCHEMES

    assert set(SOURCES) == {"fsq", "overture", "osm"}
    assert SCHEMES == {"psic", "pcpc", "pscc"}
    with pytest.raises(CrosswalkError, match="unknown OpenPlaces source"):
        Crosswalk([CrosswalkEntry("gmaps", "x", "psic", "rev5", MappingKind.SUBTREE, ("10",))])
