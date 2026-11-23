from __future__ import annotations

import json

import pandas as pd
import pytest

from placetype_ph.specialized import assess, enrich_summary, fingerprint
from placetype_ph.specialized.activity import load_rules
from placetype_ph.specialized.registry import RegistryRecord, match_registry
from placetype_ph.specialized.source_evidence import compound_status


def test_packaged_rules_and_fingerprint():
    assert len(load_rules()) >= 140
    assert len(fingerprint()) == 64


def test_broad_category_disagreement_is_not_overridden():
    a = assess(
        {"name": "ABC Pharmacy", "overture_category": "professional_service"},
        psic_code="N",
        psic_method="crosswalk",
    )
    assert "4772" in a.candidate_codes
    assert a.status == "REVIEW"
    assert a.audit["original_psic_code"] == "N"


def test_compatible_compound_and_incompatible_compound():
    assert compound_status("osm", "amenity=pharmacy | healthcare=pharmacy") == "COMPATIBLE"
    assert compound_status("osm", "amenity=cinema | shop=mall") == "MULTIPLE_PRIMARY"
    a = assess({"name": "The Mall", "osm_category": "amenity=cinema | shop=mall"})
    assert a.status in {"NO_EVIDENCE", "REVIEW"}
    assert "COMPOUND_CONFLICT_OSM" in a.flags


def test_beauty_treatment_not_cosmetics_retail():
    a = assess({"osm_category": "shop=beauty"})
    assert "962" in a.candidate_codes
    assert "4772" not in a.candidate_codes
    b = assess({"osm_category": "shop=cosmetics"})
    assert "4772" in b.candidate_codes


def test_admin_function_is_distinct_from_school_and_clinic():
    assert assess({"name": "DepEd Schools Division Office"}).administrative_code == "84120"
    assert not assess({"name": "Riverside National High School"}).administrative_code
    assert assess({"name": "DepEd Division Office Health Clinic"}).administrative_status == "REVIEW"
    assert assess({"name": "Department of Health"}).administrative_status == "REVIEW"


def test_school_names_provisional_only_and_religious_prefix_not_private():
    public = assess({"name": "Riverside National High School", "overture_category": "school"})
    assert public.ownership_status == "PROVISIONAL"
    assert public.ownership_candidate == "public"
    unproven = assess({"name": "Our Lady of Peace Hospital", "overture_category": "hospital"})
    assert unproven.ownership_candidate == ""


def test_strict_registry_promotable_only_with_site_identity():
    r = RegistryRecord(
        "hospital_registry",
        "HF-11",
        "Metro General Hospital",
        "Quezon City",
        "health",
        "public",
        "https://example.org/hf-11",
    )
    exact = match_registry(
        {
            "name": "Metro General Hospital",
            "city": "Quezon City",
            "official_id": "HF-11",
            "official_id_source": "hospital_registry",
        },
        (r,),
        "health",
    )
    assert exact.status == "EXTERNAL_ID_SITE_MATCH"
    only_name = match_registry(
        {"name": "Metro General Hospital", "city": "Quezon City"}, (r,), "health"
    )
    assert only_name.status == "NAME_CITY_PROVISIONAL"
    wrong = match_registry(
        {
            "name": "Metro General Hospital",
            "city": "Manila",
            "official_id": "HF-11",
            "official_id_source": "hospital_registry",
        },
        (r,),
        "health",
    )
    assert wrong.status == "EXTERNAL_ID_LOCALITY_REVIEW"


def test_all_pois_preserved_and_old_columns_immutable():
    places = pd.DataFrame(
        [
            {
                "canonical_id": "1",
                "name": "Mila Pharmacy",
                "overture_category": "professional_service",
            },
            {
                "canonical_id": "2",
                "name": "DepEd Schools Division Office",
                "overture_category": "government_office",
            },
            {"canonical_id": "3", "name": "Bridge", "osm_category": "man_made=bridge"},
        ]
    )
    before = pd.DataFrame(
        {
            "canonical_id": ["1", "2", "3"],
            "psic_code": ["N", "84", None],
            "psic_method": ["crosswalk", "crosswalk", None],
        }
    )
    result = enrich_summary(before, places)
    assert result["canonical_id"].tolist() == before["canonical_id"].tolist()
    assert result["psic_code"].equals(before["psic_code"])
    assert result.loc[0, "specialized_status"] == "REVIEW"
    assert result.loc[1, "administrative_candidate_code"] == "84120"
    assert len(json.loads(result.loc[0, "specialized_audit"][0:])) > 0
    with pytest.raises(ValueError, match="already present"):
        enrich_summary(result, places)


def test_duplicate_ids_fail_closed():
    places = pd.DataFrame({"canonical_id": ["a", "a"]})
    summary = pd.DataFrame({"canonical_id": ["a"]})
    with pytest.raises(ValueError, match="unique"):
        enrich_summary(summary, places)


def test_migration_is_review_required(tmp_path):
    from placetype_ph.specialized.migrate import migrate_proposals

    inp = tmp_path / "old.csv"
    inp.write_text(
        "source,source_value,source_field,mapping_kind,codes\noverture,pharmacy,category,SUBTREE,4772\n"
    )
    output = tmp_path / "review.csv"
    assert migrate_proposals(inp, output) == 1
    assert "REVIEW_REQUIRED" in output.read_text()
    with pytest.raises(ValueError, match="output must be new"):
        migrate_proposals(inp, output)
