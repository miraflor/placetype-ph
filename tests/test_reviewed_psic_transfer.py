"""Acceptance tests for independently usable reviewed PSIC classification."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from placetype_ph.classifier import EntityClassifier
from placetype_ph.crosswalk import Crosswalk
from placetype_ph.models import CrosswalkEntry, MappingKind, TaxonomyNode
from placetype_ph.reviewed_psic import (
    bundled_psic_crosswalk_path,
    psic_source_evidence,
    trusted_psic_source_rule,
)
from placetype_ph.taxonomy import Taxonomy


def _taxonomy() -> Taxonomy:
    rows = [
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
        TaxonomyNode("psic", "rev5", code, level, code, parent) for code, level, parent in rows
    )


def _cw() -> Crosswalk:
    def rule(source: str, value: str, code: str) -> CrosswalkEntry:
        return CrosswalkEntry(source, value, "psic", "rev5", MappingKind.SUBTREE, (code,))

    return Crosswalk(
        [
            rule("overture", "bank", "641"),
            rule("overture", "pharmacy", "4772"),
            rule("osm", "amenity=bank", "641"),
            rule("osm", "shop=pharmacy", "4772"),
            rule("osm", "shop=car", "47"),
        ]
    )


def _classify(**kwargs: str):
    return EntityClassifier(_taxonomy(), _cw()).classify_row({"canonical_id": "one", **kwargs})


def test_reviewed_package_exists_with_complete_decision_types():
    with bundled_psic_crosswalk_path().open(encoding="utf-8", newline="") as f:
        rules = list(csv.DictReader(f))
    assert len(rules) == 328
    assert {r["mapping_kind"] for r in rules} == {
        "EXACT",
        "SUBTREE",
        "UNION",
        "NOT_ACTIVITY",
        "UNCODEABLE",
    }
    assert any(
        r["source"] == "overture" and r["source_value"] == "bank" and r["codes"] == "641"
        for r in rules
    )
    assert Crosswalk.load([bundled_psic_crosswalk_path()]).count_for("psic", "rev5") == 328


def test_osm_compound_auxiliary_does_not_create_another_activity():
    row = {"osm_category": "amenity=bank | atm=yes", "osm_name": "Branch"}
    evidence = psic_source_evidence(row)
    assert [e.category for e in evidence] == ["amenity=bank"]
    result = _classify(**row)
    assert result.code == "641"


def test_overture_primary_and_alternate_are_dependent():
    row = {
        "overture_primary_category": "bank",
        "overture_alternate_category": "pharmacy",
        "overture_category": "bank",
    }
    evidence = psic_source_evidence(row)
    assert [e.category for e in evidence] == ["bank", "pharmacy"]
    result = _classify(**row)
    assert result.code is None
    assert result.status in {"CONFLICT", "REVIEW_ENTITY_MATCH"}


def test_osm_multiple_real_activities_abstain():
    result = _classify(osm_category="amenity=bank | shop=pharmacy")
    assert result.code is None
    assert result.status in {"CONFLICT", "REVIEW_ENTITY_MATCH"}
    assert set(result.candidate_codes) >= {"641", "4772"}


def test_osm_exact_tags_do_not_substring_match():
    result = _classify(osm_category="shop=carpet")
    assert result.code != "47" or "ACCEPTED_CROSSWALK_RULE" not in result.flags
    result = _classify(osm_category="shop=car")
    assert result.code == "47"


def test_safeguards_for_uncodeable_and_nonactivity():
    taxonomy = _taxonomy()
    nonactivity = trusted_psic_source_rule(taxonomy, "overture", "road")
    assert nonactivity is not None
    assert nonactivity.mapping_kind == MappingKind.NOT_ACTIVITY
    unknown = trusted_psic_source_rule(taxonomy, "overture", "flowers_and_gifts_store")
    assert unknown is None


def test_reviewed_source_mappings_are_applied_to_primary_psic():
    result = _classify(overture_category="bank")
    assert result.code == "641"
    assert result.deciding_component == "FUSION"
    assert "ACCEPTED_CROSSWALK_RULE" in result.flags


def test_source_groups_preserve_lineage():
    row = {
        "canonical_id": "x",
        "fsq_category": "Bank",
        "overture_category": "bank",
        "overture_has_foursquare_provenance": True,
    }
    evidence = psic_source_evidence(row)
    assert {e.dependency_group for e in evidence} == {"fsq-lineage"}


def test_parquet_pipeline_uses_reviewed_decision(tmp_path: Path):
    pa = pytest.importorskip("pyarrow")
    del pa
    import pandas as pd

    from placetype_ph.pipeline import classify_openplaces

    path = tmp_path / "in.parquet"
    pd.DataFrame(
        [
            {"canonical_id": "bank-one", "overture_category": "bank"},
            {"canonical_id": "bank-two", "osm_category": "amenity=bank | atm=yes"},
        ]
    ).to_parquet(path, index=False)
    classification_path, summary_path = classify_openplaces(
        path,
        {"psic": EntityClassifier(_taxonomy(), _cw())},
        tmp_path / "out",
    )
    classified = pd.read_parquet(classification_path)
    summary = pd.read_parquet(summary_path)
    assert classified["code"].tolist() == ["641", "641"]
    assert summary["canonical_id"].tolist() == ["bank-one", "bank-two"]
    assert summary["psic_code"].tolist() == ["641", "641"]


def test_no_unsafe_file_creation_by_runtime(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _classify(overture_category="bank")
    assert result.code == "641"
    assert list(tmp_path.iterdir()) == []


def test_trusted_category_floor_is_not_exact_in_partial_taxonomy():
    floor = trusted_psic_source_rule(_taxonomy(), "osm", "amenity=school")
    assert floor is not None
    assert floor.mapping_kind == MappingKind.SUBTREE
    assert floor.codes == ("85",)


def test_reviewed_exact_category_is_not_overridden_by_administration():
    reviewed = CrosswalkEntry("osm", "amenity=school", "psic", "rev5", MappingKind.EXACT, ("85",))
    crosswalk = Crosswalk([*_cw().entries, reviewed])
    classifier = EntityClassifier(_taxonomy(), crosswalk)
    result = classifier.classify_row(
        {
            "canonical_id": "one",
            "name": "DepEd Schools Division Office",
            "osm_category": "amenity=school",
        }
    )
    assert result.code is None
    assert result.status in {"CONFLICT", "REVIEW_ENTITY_MATCH"}
    assert "ADMINISTRATIVE_ROLE_CONFLICT" in result.flags


def test_administrative_role_correction_is_narrow_and_explicit():
    result = _classify(name="DepEd Schools Division Office", osm_category="amenity=school")
    assert result.code == "84120"
    assert "ADMINISTRATIVE_ROLE_ACCEPTED" in result.flags
    assert result.audit["original_fusion_code"] in {None, "85"}


def test_administrative_name_conflicting_with_retail_abstains():
    result = _classify(name="DepEd Schools Division Office", osm_category="shop=car")
    assert result.code is None
    assert result.status in {"CONFLICT", "REVIEW_ENTITY_MATCH"}
    assert "ADMINISTRATIVE_ROLE_CONFLICT" in result.flags


def test_mixed_nonactivity_and_activity_in_one_osm_source_abstains():
    result = _classify(osm_category="natural=beach | shop=pharmacy")
    assert result.code is None
    assert result.status in {"CONFLICT", "REVIEW_ENTITY_MATCH"}


def test_crosswalk_fingerprint_is_stable_across_rule_order():
    from placetype_ph.reviewed_psic import crosswalk_fingerprint

    cw = _cw()
    reversed_rules = Crosswalk(list(reversed(cw.entries)))
    assert crosswalk_fingerprint(cw) == crosswalk_fingerprint(reversed_rules)
    assert len(crosswalk_fingerprint(cw)) == 64


def test_overture_basic_category_does_not_veto_primary():
    result = _classify(overture_primary_category="bank", overture_basic_category="pharmacy")
    assert result.code == "641"


def test_overture_basic_category_alone_remains_advisory():
    result = _classify(overture_basic_category="bank")
    assert result.code is None


def test_primary_only_overture_row_is_accepted_by_openplaces(tmp_path: Path):
    pytest.importorskip("pyarrow")
    import pandas as pd

    from placetype_ph.openplaces import read_openplaces

    path = tmp_path / "primary_only.parquet"
    pd.DataFrame([{"canonical_id": "one", "overture_primary_category": "bank"}]).to_parquet(
        path, index=False
    )
    row = read_openplaces(path).iloc[0].to_dict()
    assert psic_source_evidence(row)[0].category == "bank"
