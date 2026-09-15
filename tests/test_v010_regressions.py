from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from placetype_ph.cli import app
from placetype_ph.taxonomy_import import EXPECTED_STRUCTURE, ImportReport, import_excel


def test_visual_scanner_duplicates_reach_global_merger(tmp_path: Path):
    path = tmp_path / "visual.xlsx"
    pd.DataFrame(
        [
            ["1", "Products"],
            ["11", "Short"],
            ["11", "A much fuller division title"],
        ]
    ).to_excel(path, index=False, header=False)
    report = ImportReport()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        taxonomy = import_excel(path, "pcpc", "2002", strict=False, report=report)
    assert taxonomy.get("11").title == "A much fuller division title"
    assert report.duplicate_codes == 1


def test_duplicate_merges_are_not_counted_as_discards():
    report = ImportReport(rows_seen=10, missing_code_or_title=1, duplicate_codes=4)
    assert report.skipped == 1
    assert report.merge_messages() == ["4 duplicate definition(s) merged"]


def test_unparseable_summary_range_is_visible_but_not_fatal(tmp_path: Path):
    path = tmp_path / "summary.xlsx"
    pd.DataFrame(
        [
            ["Code", "Title"],
            ["1", "Products"],
            ["01-03", "Summary range, not a taxonomy node"],
            ["11", "Food and live animals"],
        ]
    ).to_excel(path, index=False, header=False)
    report = ImportReport()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        taxonomy = import_excel(path, "pcpc", "2002", strict=False, report=report)
    assert {"1", "11"} <= set(taxonomy.nodes)
    assert report.invalid_code_formats == ["01-03"]


def test_pcpc_published_structure_is_not_frozen_before_live_validation():
    assert ("pcpc", "2002") not in EXPECTED_STRUCTURE


def test_diagnose_reports_without_writing_taxonomy(tmp_path: Path):
    path = tmp_path / "partial.xlsx"
    pd.DataFrame(
        [["Code", "Title"], ["1", "Products"], ["11", "Food and live animals"]]
    ).to_excel(path, index=False, header=False)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "taxonomy",
            "diagnose",
            "pcpc",
            "--input",
            str(path),
            "--version",
            "2002",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Diagnostic only" in result.output
    assert "level histogram" in result.output
    assert not (tmp_path / "nodes.parquet").exists()


def test_diagnose_reports_missing_explicit_parent_without_stopping(tmp_path: Path):
    path = tmp_path / "bad_parent.xlsx"
    pd.DataFrame(
        [
            ["Code", "Title", "Parent Code"],
            ["1", "Products", ""],
            ["11", "Food and live animals", "99"],
        ]
    ).to_excel(path, index=False, header=False)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "taxonomy",
            "diagnose",
            "pcpc",
            "--input",
            str(path),
            "--version",
            "2002",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "invalid parent" in result.output.casefold()
    assert "Diagnostic only" in result.output
