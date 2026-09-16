"""Regressions for v0.2.8.

Two defects, both about state that was derived repeatedly or normalized with the wrong
vocabulary:

* reviewed crosswalk values were folded with OpenPlaces null semantics, so two rules on
  different generic labels such as ``Other`` and ``unknown`` collided in the rule index,
  a ``contains`` rule built from one could never fire, and an ``exact`` rule built from
  one matched any record whose value was also a generic label;
* root paths and branch depths were recomputed on each call even though the tree is
  immutable once built.

The crosswalk fix keeps the two sides of the lookup on different vocabularies on purpose.
A reviewed rule target is authoritative and is indexed literally. An observed OpenPlaces
value keeps POI null semantics, because ``source_evidence`` already treats a bare generic
label as missing and ``Crosswalk`` must not contradict that policy from underneath. The
remaining inconsistent combination, an ``exact`` rule whose whole value is a generic
null label, is rejected at load rather than accepted with punctuation-dependent behaviour.
"""

from __future__ import annotations

import pytest

from placetype_ph.crosswalk import Crosswalk, CrosswalkError
from placetype_ph.models import CrosswalkEntry, MappingKind, SourceField, TaxonomyNode
from placetype_ph.taxonomy import Taxonomy, TaxonomyError
from placetype_ph.text import normalize_key, normalize_match_key


def test_generic_labels_survive_the_match_key_but_not_the_poi_key():
    for label in ("Other", "unknown", "n/a", "none"):
        assert normalize_key(label) == ""
        assert normalize_match_key(label) != ""


def test_two_rules_on_different_generic_labels_are_not_duplicates():
    """`Other` and `unknown` are distinct reviewed decisions, not one duplicated row."""
    crosswalk = Crosswalk(
        [
            CrosswalkEntry(
                "osm", "Other", "psic", "rev5", MappingKind.EXACT, ("2011",),
                match_type="contains",
            ),
            CrosswalkEntry(
                "osm", "unknown", "psic", "rev5", MappingKind.NOT_ACTIVITY, (),
                match_type="contains",
            ),
        ]
    )
    assert crosswalk.count_for("psic", "rev5") == 2
    other = crosswalk.match("osm", "psic", "rev5", category="Other clothing shop")
    unknown = crosswalk.match("osm", "psic", "rev5", category="unknown building type")
    assert other is not None and other.codes == ("2011",)
    assert unknown is not None and unknown.mapping_kind == MappingKind.NOT_ACTIVITY


def test_observed_generic_label_keeps_poi_null_semantics():
    """A raw POI value of `Other` stays missing, so no contains rule fires on it alone."""
    crosswalk = Crosswalk(
        [
            CrosswalkEntry(
                "osm", "Other", "psic", "rev5", MappingKind.EXACT, ("2011",),
                match_type="contains",
            ),
        ]
    )
    assert crosswalk.match("osm", "psic", "rev5", category="Other") is None


def test_exact_rule_on_generic_null_label_is_rejected():
    """Its canonical value is null-ish, so exact matching would be inconsistent."""
    with pytest.raises(CrosswalkError, match="generic null label"):
        Crosswalk(
            [CrosswalkEntry("osm", "Other", "psic", "rev5", MappingKind.EXACT, ("2011",))]
        )


def test_contains_rule_on_a_generic_label_still_matches():
    """A `contains` rule whose value folded to nothing used to be silently dead."""
    crosswalk = Crosswalk(
        [
            CrosswalkEntry(
                "osm", "Other", "psic", "rev5", MappingKind.SUBTREE, ("20",),
                match_type="contains",
            )
        ]
    )
    hit = crosswalk.match("osm", "psic", "rev5", category="Other clothing shop")
    assert hit is not None
    assert hit.codes == ("20",)


def test_rule_without_matchable_characters_is_rejected_at_load():
    """It would otherwise match every record whose value also folds to nothing."""
    with pytest.raises(CrosswalkError, match="empty match key"):
        Crosswalk(
            [CrosswalkEntry("osm", "***", "psic", "rev5", MappingKind.EXACT, ("2011",))]
        )


def test_regex_rules_are_exempt_from_the_match_key_check():
    crosswalk = Crosswalk(
        [
            CrosswalkEntry(
                "osm", r"^\W+$", "psic", "rev5", MappingKind.UNCODEABLE, (),
                match_type="regex", source_field=SourceField.NAME,
            )
        ]
    )
    assert crosswalk.match("osm", "psic", "rev5", name="***") is not None


def test_root_paths_survive_a_forest_and_an_out_of_order_parent(toy_psic):
    assert toy_psic.ancestors("10111") == ["10111", "1011", "101", "10", "A"]
    assert toy_psic.ancestors("10111", include_self=False) == ["1011", "101", "10", "A"]
    assert toy_psic.path_from_root("10111") == ["A", "10", "101", "1011", "10111"]
    assert toy_psic.depth("10111") == 5
    assert toy_psic.depth("A") == 1
    assert toy_psic.ancestors("B") == ["B"]

    # A child listed before its parent must still resolve to the same chain.
    reordered = Taxonomy(
        [
            TaxonomyNode("psic", "rev5", "10111", "subclass", "Bread", "1011"),
            TaxonomyNode("psic", "rev5", "1011", "class", "Bakery", "101"),
            TaxonomyNode("psic", "rev5", "101", "group", "Processing", "10"),
            TaxonomyNode("psic", "rev5", "10", "division", "Food", "A"),
            TaxonomyNode("psic", "rev5", "A", "section", "Section A"),
        ]
    )
    assert reordered.path_from_root("10111") == ["A", "10", "101", "1011", "10111"]


def test_unknown_code_still_raises_key_error(toy_psic):
    with pytest.raises(KeyError):
        toy_psic.depth("99999")
    with pytest.raises(KeyError):
        toy_psic.ancestors("99999")


def test_cycle_is_still_detected_when_root_paths_are_precomputed():
    with pytest.raises(TaxonomyError, match="cycle detected"):
        Taxonomy(
            [
                TaxonomyNode("psic", "rev5", "10", "division", "Food", "101"),
                TaxonomyNode("psic", "rev5", "101", "group", "Processing", "10"),
            ]
        )


def test_branch_max_depth_is_memoized_without_changing_its_answer(toy_psic):
    expected = {
        code: max(toy_psic.depth(leaf) for leaf in toy_psic.leaves(code))
        for code in toy_psic.nodes
    }
    first = {code: toy_psic.branch_max_depth(code) for code in toy_psic.nodes}
    second = {code: toy_psic.branch_max_depth(code) for code in toy_psic.nodes}
    assert first == expected
    assert second == expected
    assert toy_psic.branch_max_depth("A") == 5
    assert toy_psic.branch_max_depth("10112") == 5
