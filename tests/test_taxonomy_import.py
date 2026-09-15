from __future__ import annotations

from pathlib import Path

import pandas as pd

from placetype_ph.taxonomy_import import import_excel


def test_import_excel_scans_simple_psic_structure(tmp_path: Path):
    path = tmp_path / "psic.xlsx"
    raw = pd.DataFrame(
        [
            ["A", "Agriculture"],
            ["10", "Food division"],
            ["101", "Processing group"],
            [
                "1011",
                "Bakery class This class includes: bread making "
                "This class excludes: retail sale",
            ],
            ["10111", "Bread subclass"],
        ]
    )
    raw.to_excel(path, index=False, header=False)
    taxonomy = import_excel(path, "psic", "rev5")
    assert taxonomy.parent("10") == "A"
    assert taxonomy.parent("101") == "10"
    assert taxonomy.parent("1011") == "101"
    assert taxonomy.get("1011").includes == "bread making"
    assert taxonomy.get("1011").excludes == "retail sale"


def test_raw_sheet_scanner_keeps_short_textual_titles(tmp_path: Path):
    path = tmp_path / "short-title.xlsx"
    raw = pd.DataFrame(
        [
            ["A", "Agriculture"],
            ["10", "Food division"],
            ["101", "IT"],
            ["1011", "Class title"],
            ["10111", "Subclass title"],
        ]
    )
    raw.to_excel(path, index=False, header=False)
    taxonomy = import_excel(path, "psic", "rev5", strict_levels=True)
    assert taxonomy.get("101").title == "IT"
    assert taxonomy.parent("1011") == "101"
