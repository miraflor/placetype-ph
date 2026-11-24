"""Regression coverage for reviewed PSIC compatibility and Express cache safety."""

from __future__ import annotations

from placetype_ph import express
from placetype_ph.classifier import EntityClassifier
from placetype_ph.crosswalk import Crosswalk
from placetype_ph.models import CrosswalkEntry, MappingKind, TaxonomyNode
from placetype_ph.openplaces import source_evidence
from placetype_ph.reviewed_psic import (
    accepted_administrative_role,
    trusted_psic_source_rule,
)
from placetype_ph.taxonomy import Taxonomy


def _psic(version: str = "rev5") -> Taxonomy:
    entries = [
        ("K", "section", None),
        ("64", "division", "K"),
        ("641", "group", "64"),
        ("G", "section", None),
        ("47", "division", "G"),
        ("4772", "group", "47"),
        ("O", "section", None),
        ("84", "division", "O"),
        ("84120", "subclass", "84"),
        ("P", "section", None),
        ("85", "division", "P"),
    ]
    return Taxonomy(
        TaxonomyNode("psic", version, code, level, code, parent) for code, level, parent in entries
    )


def _one(classifier: EntityClassifier, **fields: object):
    return classifier.classify_row({"canonical_id": "one", **fields})


def test_new_policy_can_be_disabled_without_disabling_custom_crosswalk():
    taxonomy = _psic()
    category = CrosswalkEntry("overture", "bank", "psic", "rev5", MappingKind.SUBTREE, ("641",))
    legacy = EntityClassifier(taxonomy, Crosswalk([category]), reviewed_psic_policy=False)
    result = _one(legacy, overture_category="bank")
    assert result.code == "641"
    assert "ACCEPTED_CROSSWALK_RULE" not in result.flags


def test_legacy_disables_trusted_ontology_floor_and_negative_policy():
    taxonomy = _psic()
    current = EntityClassifier(taxonomy)
    legacy = EntityClassifier(taxonomy, reviewed_psic_policy=False)
    assert _one(current, overture_category="pharmacy").code == "4772"
    assert _one(legacy, overture_category="pharmacy").code is None
    assert _one(current, overture_category="road").status == "NOT_PSIC_ACTIVITY"
    assert _one(legacy, overture_category="road").status != "NOT_PSIC_ACTIVITY"


def test_legacy_does_not_use_structured_overture_instead_of_original_category():
    taxonomy = _psic()
    reviewed = EntityClassifier(taxonomy)
    legacy = EntityClassifier(taxonomy, reviewed_psic_policy=False)
    row = {"overture_primary_category": "pharmacy", "overture_alternate_category": "bank"}
    assert [e.category for e in source_evidence(row)] == ["pharmacy"]
    assert source_evidence(row, include_structured_overture=False) == []
    assert _one(legacy, **row).code is None
    assert _one(reviewed, overture_primary_category="pharmacy").code == "4772"


def test_legacy_disables_unreviewed_administrative_correction():
    row = {"name": "DepEd Schools Division Office", "osm_category": "amenity=school"}
    reviewed = _one(EntityClassifier(_psic()), **row)
    legacy = _one(EntityClassifier(_psic(), reviewed_psic_policy=False), **row)
    assert reviewed.code == "84120"
    assert legacy.code is None
    assert "ADMINISTRATIVE_ROLE_ACCEPTED" not in legacy.flags


def test_legacy_does_not_expand_compound_osm_into_multiple_activities():
    taxonomy = _psic()
    crosswalk = Crosswalk(
        [
            CrosswalkEntry("osm", "amenity=bank", "psic", "rev5", MappingKind.SUBTREE, ("641",)),
            CrosswalkEntry("osm", "shop=pharmacy", "psic", "rev5", MappingKind.SUBTREE, ("4772",)),
        ]
    )
    row = {"osm_category": "amenity=bank | shop=pharmacy"}
    reviewed = _one(EntityClassifier(taxonomy, crosswalk), **row)
    legacy = _one(EntityClassifier(taxonomy, crosswalk, reviewed_psic_policy=False), **row)
    assert reviewed.code is None and reviewed.status in {"CONFLICT", "REVIEW_ENTITY_MATCH"}
    assert legacy.code is None and "COMPOUND_OSM_MULTI_PRIMARY" not in legacy.flags


def test_source_floors_and_administrative_correction_are_rev5_only():
    old = _psic("2019")
    assert trusted_psic_source_rule(old, "overture", "pharmacy") is None
    code, reason = accepted_administrative_role(old, {"name": "DepEd Schools Division Office"}, [])
    assert code is None
    assert reason == "unavailable_administration_code"
    result = _one(EntityClassifier(old), overture_category="pharmacy")
    assert result.code is None


def test_other_schemes_keep_structured_source_adapter():
    evidence = source_evidence({"overture_primary_category": "bank"})
    assert len(evidence) == 1
    assert evidence[0].source == "overture" and evidence[0].category == "bank"


def test_express_cache_includes_packaged_rule_contents(tmp_path, monkeypatch):
    fake = tmp_path / "reviewed.csv"
    fake.write_text("value-a", encoding="utf-8")
    monkeypatch.setattr(express, "bundled_psic_crosswalk_path", lambda: fake)
    assert express.CLASSIFY_PARAMS["reviewed_psic"] is True
    assert "joint-v9.4" in express.EXPRESS_CACHE_VERSION
    key_before = express.pipeline_state()
    fake.write_text("value-b", encoding="utf-8")
    key_after = express.pipeline_state()
    assert key_before != key_after
    monkeypatch.setitem(express.CLASSIFY_PARAMS, "reviewed_psic", False)
    assert express.pipeline_state() != key_after


def test_package_version_coheres_with_distribution():
    from placetype_ph import __version__

    assert __version__ == "0.1.2"
