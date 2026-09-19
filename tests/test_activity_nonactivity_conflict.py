from __future__ import annotations

from placetype_ph.classifier import EntityClassifier
from placetype_ph.crosswalk import Crosswalk
from placetype_ph.models import CrosswalkEntry, MappingKind, TaxonomyNode
from placetype_ph.taxonomy import Taxonomy


def _taxonomy() -> Taxonomy:
    return Taxonomy(
        [
            TaxonomyNode("psic", "rev5", "I", "section", "Accommodation"),
            TaxonomyNode("psic", "rev5", "55", "division", "Accommodation", "I"),
        ]
    )


def _crosswalk() -> Crosswalk:
    return Crosswalk(
        [
            CrosswalkEntry(
                source="fsq",
                source_value="[Community and Government > Housing Development]",
                scheme="psic",
                version="rev5",
                mapping_kind=MappingKind.NOT_ACTIVITY,
            ),
            CrosswalkEntry(
                source="overture",
                source_value="lodging",
                scheme="psic",
                version="rev5",
                mapping_kind=MappingKind.SUBTREE,
                codes=("55",),
            ),
        ]
    )


def test_independent_nonactivity_and_activity_are_a_conflict():
    classifier = EntityClassifier(_taxonomy(), _crosswalk())
    result = classifier.classify_row(
        {
            "canonical_id": "gabriel",
            "fsq_category": "[Community and Government > Housing Development]",
            "overture_category": "lodging",
            "overture_has_foursquare_provenance": False,
        }
    )
    assert result.code is None
    assert result.status == "CONFLICT"
    assert "ACTIVITY_NON_ACTIVITY_CONFLICT" in result.flags


def test_same_lineage_does_not_claim_independent_conflict():
    classifier = EntityClassifier(_taxonomy(), _crosswalk())
    result = classifier.classify_row(
        {
            "canonical_id": "same-lineage",
            "fsq_category": "[Community and Government > Housing Development]",
            "overture_category": "lodging",
            "overture_has_foursquare_provenance": True,
        }
    )
    assert result.code == "55"
    assert result.status == "SINGLE"
    assert "ACTIVITY_NON_ACTIVITY_CONFLICT" not in result.flags
