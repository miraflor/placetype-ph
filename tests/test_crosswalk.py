from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from placetype_ph.crosswalk import Crosswalk, CrosswalkError
from placetype_ph.models import CrosswalkEntry, MappingKind


def test_partially_reviewed_crosswalk_skips_blanks(tmp_path: Path):
    path = tmp_path / "cw.csv"
    pd.DataFrame(
        [
            {
                "source": "fsq",
                "source_value": "Bakery",
                "scheme": "psic",
                "version": "rev5",
                "mapping_kind": "CLASS",
                "codes": "1011",
            },
            {
                "source": "fsq",
                "source_value": "Other",
                "scheme": "psic",
                "version": "rev5",
                "mapping_kind": "",
                "codes": "",
            },
        ]
    ).to_csv(path, index=False)
    cw = Crosswalk.load([path])
    assert len(cw.entries) == 1
    assert cw.entries[0].mapping_kind == MappingKind.EXACT


def test_unknown_match_type_is_rejected():
    with pytest.raises(CrosswalkError):
        Crosswalk(
            [
                CrosswalkEntry(
                    "fsq",
                    "bakery",
                    "psic",
                    "rev5",
                    MappingKind.SUBTREE,
                    ("1011",),
                    match_type="excat",
                )
            ]
        )


def test_conflicting_duplicate_exact_rows_are_rejected():
    with pytest.raises(CrosswalkError):
        Crosswalk(
            [
                CrosswalkEntry(
                    "fsq", "bakery", "psic", "rev5", MappingKind.SUBTREE, ("1011",)
                ),
                CrosswalkEntry(
                    "fsq", "Bakery", "psic", "rev5", MappingKind.SUBTREE, ("1021",)
                ),
            ]
        )


def _overlapping_rules() -> list[CrosswalkEntry]:
    return [
        CrosswalkEntry(
            "fsq", "bakery", "psic", "rev5", MappingKind.SUBTREE, ("10",), "contains"
        ),
        CrosswalkEntry(
            "fsq", r"bakery$", "psic", "rev5", MappingKind.EXACT, ("1011",), "regex"
        ),
    ]


def test_overlapping_scanned_rules_are_reported_without_deciding():
    crosswalk = Crosswalk(_overlapping_rules())
    found = crosswalk.matches("fsq", "psic", "rev5", category="corner bakery")
    assert found.entries == ()
    assert len(found.ambiguities) == 1
    assert "ambiguous crosswalk rules" in found.ambiguities[0]
    # Unambiguous values in the same crosswalk keep working.
    other = crosswalk.matches("fsq", "psic", "rev5", category="bakery counter")
    assert other.entries and other.entries[0].codes == ("10",)


def test_overlapping_scanned_rules_can_be_made_fatal_on_request():
    crosswalk = Crosswalk(_overlapping_rules(), strict_ambiguity=True)
    with pytest.raises(CrosswalkError, match="ambiguous crosswalk rules"):
        crosswalk.matches("fsq", "psic", "rev5", category="corner bakery")


def test_duplicate_exact_same_decision_may_differ_in_review_metadata():
    crosswalk = Crosswalk(
        [
            CrosswalkEntry(
                "fsq", "Bakery", "psic", "rev5", MappingKind.SUBTREE, ("10",),
                confidence=0.8, notes="first review"
            ),
            CrosswalkEntry(
                "fsq", " bakery ", "psic", "rev5", MappingKind.SUBTREE, ("10",),
                confidence=1.0, notes="second review"
            ),
        ]
    )
    hit = crosswalk.match("fsq", "psic", "rev5", category="BAKERY")
    assert hit is not None
    assert hit.codes == ("10",)
