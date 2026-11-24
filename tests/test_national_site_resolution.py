"""Test actual packaged records through policy-scoped ownership and I-O resolution (offline)."""

from dataclasses import replace

import pandas as pd
import pytest

from placetype_ph.classifier import EntityClassifier
from placetype_ph.crosswalk import Crosswalk
from placetype_ph.io_mapping import IOCrosswalk
from placetype_ph.io_resolution import resolve_io
from placetype_ph.models import TaxonomyNode
from placetype_ph.pipeline import classify_openplaces
from placetype_ph.reviewed_psic import bundled_psic_crosswalk_path
from placetype_ph.specialized.institutional_pipeline import (
    ENVIRONMENT_KEY,
    resolve_from_references,
    selected_reference_config,
)
from placetype_ph.specialized.institutional_promotion import resolve_institutional_ownership
from placetype_ph.specialized.institutional_registry import (
    Institution,
    InstitutionIndex,
    load_heida,
    load_hospitals,
    load_unifast,
)
from placetype_ph.specialized.institutional_shadow import InstitutionResolver
from placetype_ph.specialized.institutional_site import SITE_MATCH
from placetype_ph.specialized.ownership_io import refine_verified_institutional_io
from placetype_ph.taxonomy import Taxonomy


@pytest.fixture(scope="module")
def crosswalk():
    return IOCrosswalk.load_default()


@pytest.fixture
def references(monkeypatch):
    monkeypatch.delenv(ENVIRONMENT_KEY, raising=False)
    _, refs = selected_reference_config()
    assert set(refs) == {"beis", "unifast", "heida", "hospitals"}
    return refs


def mapped_row(cid, code):
    education = code.startswith("85")
    return {
        "canonical_id": cid,
        "psic_code": code,
        "psic_status": "SINGLE",
        "io_mapping_status": "MAPPED_DIVISION",
        "io_mapping_psic_level": "division",
        "io_mapping_psic_code": "85" if education else "86",
        "io_mapping_bridge_basis": "code_title_match",
        "io16_codes": "14" if education else "15",
        "io80_codes": "74; 75" if education else "76; 77",
        "io240_codes": "229; 230" if education else "231; 232",
        "io16_map_code": "14" if education else "15",
        "io16_map_status": "SINGLETON_CANDIDATE",
        "io80_map_code": "",
        "io240_map_code": "",
        "io80_map_status": "AMBIGUOUS_SET",
        "io240_map_status": "AMBIGUOUS_SET",
    }


def hospital_place(record, cid="hospital"):
    return {
        "canonical_id": cid, "name": record.name, "city": record.city,
        "address": record.address, "osm_category": "amenity=hospital",
    }


def hei_place(record, city, cid="hei"):
    return {
        "canonical_id": cid, "name": record.name, "city": city,
        "address": record.address, "ched_hei_code": record.source_id,
        "osm_category": "amenity=university",
    }


def test_actual_bundled_records_change_only_eligible_resolved_io(references, crosswalk):
    hospitals = {r.source_id: r for r in load_hospitals(references["hospitals"])}
    heis = {r.source_id: r for r in load_heida(references["heida"])}
    raw = pd.DataFrame([
        hospital_place(hospitals["2026-09-30:row-1"], "public_hospital"),
        hospital_place(hospitals["2026-09-30:row-2"], "private_hospital"),
        hei_place(heis["1023"], "San Fernando City", "public_hei"),
        hei_place(heis["1102"], "Laoag City", "private_hei"),
    ])
    old = pd.DataFrame([mapped_row(cid, code) for cid, code in zip(
        raw["canonical_id"], ["861", "861", "854", "854"], strict=True
    )])
    baseline = resolve_io(old, raw, crosswalk)
    strict = resolve_from_references(old, raw, references=references)
    assert list(strict["institution_evidence_status"]) == [SITE_MATCH] * 4
    assert list(strict["institution_resolution_status"]) == ["PROVISIONAL"] * 4
    assert list(strict["institution_resolved_ownership"]) == [""] * 4
    strict_refined = refine_verified_institutional_io(strict)
    assert list(strict_refined["io_ownership_refinement_status"]) == ["ABSTAIN"] * 4
    strict_result = resolve_io(strict_refined, raw, crosswalk, policy="strict")
    fields = [c for c in baseline.columns if "_resolved_" in c]
    pd.testing.assert_frame_equal(strict_result[fields], baseline[fields])
    enriched = resolve_from_references(
        old, raw, references=references, allow_registry_site_match=True,
    )
    assert list(enriched["institution_evidence_status"]) == [SITE_MATCH] * 4
    assert list(enriched["institution_resolved_ownership"]) == [
        "public", "private", "public", "private"
    ]
    refined = refine_verified_institutional_io(enriched)
    # Defense in depth: strict refuses even a pre-refined non-strict input.
    pd.testing.assert_frame_equal(
        resolve_io(refined, raw, crosswalk, policy="strict")[fields], baseline[fields],
    )
    for policy in ("evidence-weighted", "extended"):
        result = resolve_io(refined, raw, crosswalk, policy=policy)
        pd.testing.assert_frame_equal(result[old.columns], old)
        assert list(result["io80_resolved_code"]) == ["76", "77", "74", "75"]
        assert list(result["io240_resolved_code"]) == ["231", "232", "229", "230"]
        assert list(result["io80_resolved_status"]) == [SITE_MATCH] * 4
        assert list(result["io240_resolved_status"]) == [SITE_MATCH] * 4
    assert list(baseline["io80_resolved_code"]) == [""] * 4


@pytest.mark.parametrize("changes", [
    {"address": ""}, {"city": "Other City"}, {"address": "Bangued"},
    {"address": "Different Road"}, {"osm_address": "Other Street"},
    {"osm_name": "Different Hospital"}, {"osm_ownership": "private"},
    {"nhfr_code": "unknown"}, {"psic_status": "CONFLICT"},
    {"psic_code": "862"}, {"psic_code": "86"}, {"name": "Hospital Ward"},
])
def test_hospital_missing_or_conflicting_evidence_never_promotes(references, changes):
    rec = load_hospitals(references["hospitals"])[0]
    raw = hospital_place(rec) | {"psic_code": "861", "psic_status": "SINGLE"} | changes
    assert not InstitutionResolver(InstitutionIndex([rec])).assess(raw).promotion_eligible


@pytest.mark.parametrize("changes", [
    {"ched_hei_code": ""}, {"address": ""}, {"city": "Other City"},
    {"ched_hei_code": "wrong"}, {"school_id": "123456"},
    {"osm_ownership": "private"}, {"psic_code": "852"}, {"psic_code": "853"},
])
def test_hei_needs_code_and_address(references, changes):
    rec = next(r for r in load_heida(references["heida"]) if r.source_id == "1023")
    raw = hei_place(rec, "San Fernando City") | {"psic_code": "854", "psic_status": "SINGLE"}
    assert not InstitutionResolver(InstitutionIndex([rec])).assess(raw | changes).promotion_eligible


def test_duplicate_registry_and_duplicate_poi_block_promotion(references):
    rec = load_hospitals(references["hospitals"])[0]
    raw = hospital_place(rec) | {"psic_code": "861", "psic_status": "SINGLE"}
    resolver = InstitutionResolver(InstitutionIndex([rec, replace(rec, source_id="other")]))
    assert resolver.assess(raw).status == "REVIEW"
    resolver = InstitutionResolver(InstitutionIndex([rec]))
    places = pd.DataFrame([hospital_place(rec, "a"), hospital_place(rec, "b")])
    old = pd.DataFrame([mapped_row("a", "861"), mapped_row("b", "861")])
    result = resolve_institutional_ownership(
        old, places, resolver=resolver, allow_registry_site_match=True,
    )
    assert list(result["institution_resolution_status"]) == ["REVIEW", "REVIEW"]
    assert list(result["institution_resolved_ownership"]) == ["", ""]


def test_agreeing_hei_source_is_corroboration_not_an_independent_site(references):
    rec = next(r for r in load_heida(references["heida"]) if r.source_id == "1023")
    # Use a controlled agreeing UniFAST row to exercise cross-source reconciliation.
    original = load_unifast(references["unifast"])[0]
    peer = replace(original, name=rec.name, ownership=rec.ownership, region="01")
    raw = hei_place(rec, "San Fernando City") | {
        "psic_code": "854", "psic_status": "SINGLE", "region_code": "01"
    }
    decision = InstitutionResolver(InstitutionIndex([rec, peer])).assess(raw)
    assert decision.status == SITE_MATCH and decision.promotion_eligible
    assert set(decision.source_references) == {rec.reference, peer.reference}
    bad = replace(peer, ownership="private")
    assert InstitutionResolver(InstitutionIndex([rec, bad])).assess(raw).status == "REVIEW"


def test_agreeing_school_rows_do_not_cancel_a_valid_official_id():
    beis = Institution("deped_beis", "123456", "Example Elementary School", "education",
                       "public", town_psgc="1380001000",
                       evidence_type="unverified_beis_prefix_hint")
    official = Institution("deped_masterlist", "123456", beis.name, "education", "public",
                           city="Example City", source_url="https://www.deped.gov.ph/schools",
                           as_of="2026-11-01", id_kind="official_code",
                           evidence_type="explicit_sector")
    raw = {"canonical_id": "x", "name": beis.name, "deped_school_id": "123456",
           "city": "Example City", "town_psgc": beis.town_psgc,
           "psic_code": "852", "psic_status": "SINGLE"}
    decision = InstitutionResolver(InstitutionIndex([beis, official])).assess(raw)
    assert decision.status == "EXACT_EXTERNAL_ID" and decision.promotion_eligible
    assert set(decision.source_references) == {beis.reference, official.reference}
    assert not InstitutionResolver(InstitutionIndex([beis])).assess(raw).promotion_eligible
    for changes in ({"town_psgc": ""}, {"town_psgc": "1380002000"}, {"city": "Wrong"}):
        result = InstitutionResolver(InstitutionIndex([beis, official])).assess(raw | changes)
        assert not result.promotion_eligible
    assert not InstitutionResolver(InstitutionIndex([
        beis, replace(official, ownership="private")
    ])).assess(raw).promotion_eligible


def test_actual_reviewed_tertiary_category_in_pipeline(references, tmp_path):
    """Use the actual reviewed category row, not a manually assigned PSIC code."""
    taxonomy = Taxonomy([
        TaxonomyNode("psic", "rev5", "Q", "section", "Education"),
        TaxonomyNode("psic", "rev5", "85", "division", "Education", "Q"),
        TaxonomyNode("psic", "rev5", "854", "group", "Tertiary education", "85"),
    ])
    packaged = Crosswalk.load([bundled_psic_crosswalk_path()])
    relevant = [entry for entry in packaged.entries if entry.source == "osm"
                and entry.source_value == "amenity=university"]
    assert len(relevant) == 1 and relevant[0].codes == ("854",)
    classifier = EntityClassifier(taxonomy, Crosswalk(relevant))
    record = next(r for r in load_heida(references["heida"]) if r.source_id == "1023")
    raw = hei_place(record, "San Fernando City") | {"lon": 120.3, "lat": 16.6}
    src = tmp_path / "places.parquet"
    pd.DataFrame([raw]).to_parquet(src, index=False)
    for policy in ("strict", "evidence-weighted", "extended"):
        _, out = classify_openplaces(
            src, {"psic": classifier}, tmp_path / policy,
            io_resolution_policy=policy, psic_title_search=False,
        )
        actual = pd.read_parquet(out).iloc[0]
        assert actual["psic_code"] == "854"
        assert actual["institution_evidence_status"] == SITE_MATCH
        if policy == "strict":
            assert actual["institution_resolved_ownership"] == ""
            assert actual["io80_resolved_status"] != SITE_MATCH
        else:
            assert actual["institution_resolved_ownership"] == "public"
            # The real version bridge may withhold I-O refinement; do not bypass it.
            if actual["io_ownership_refinement_status"] == "REFINED":
                assert actual["io80_resolved_code"] == "74"
                assert actual["io240_resolved_code"] == "229"
                assert actual["io80_resolved_status"] == SITE_MATCH
