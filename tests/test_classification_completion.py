"""Title search, reviewed title-search rows, ownership PSIC proposals and registry tools."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pandas as pd
import pytest

from placetype_ph.augment import augment_places, read_supplied
from placetype_ph.classifier import EntityClassifier
from placetype_ph.crosswalk import Crosswalk
from placetype_ph.io_mapping import IOCrosswalk
from placetype_ph.openplaces import infer_legacy_source_fields
from placetype_ph.pipeline import classify_openplaces
from placetype_ph.reviewed_psic import bundled_psic_crosswalk_path
from placetype_ph.specialized import institutional_tools as tools
from placetype_ph.specialized.__main__ import main as specialized_main
from placetype_ph.specialized.institutional_pipeline import (
    ENVIRONMENT_KEY,
    load_reference_config,
)
from placetype_ph.specialized.institutional_psic import ProposalBuilder
from placetype_ph.specialized.institutional_registry import (
    OFFICIAL_FIELDS,
    load_official_records,
)
from placetype_ph.taxonomy import Taxonomy
from placetype_ph.title_search import title_search_columns

ROOT = Path(__file__).resolve().parents[1]
PSIC_TREE = ROOT / "reference" / "psic_rev5" / "nodes.parquet"
REGISTRY = ROOT / "src" / "placetype_ph" / "data" / "registry"
pytestmark = pytest.mark.skipif(not PSIC_TREE.is_file(), reason="PSIC Rev. 5 tree not present")


@pytest.fixture(scope="module")
def taxonomy() -> Taxonomy:
    return Taxonomy.load(PSIC_TREE)


@pytest.fixture(scope="module")
def classifier(taxonomy: Taxonomy) -> EntityClassifier:
    return EntityClassifier(taxonomy, Crosswalk.load([bundled_psic_crosswalk_path()]))


@pytest.fixture(scope="module")
def io_crosswalk() -> IOCrosswalk:
    return IOCrosswalk.load_default()


def _run(tmp_path: Path, classifier: EntityClassifier, rows: list[dict], **kwargs) -> pd.DataFrame:
    source = tmp_path / "places.parquet"
    pd.DataFrame(rows).to_parquet(source, index=False)
    _, summary = classify_openplaces(source, {"psic": classifier}, tmp_path / "out", **kwargs)
    return pd.read_parquet(summary).set_index("canonical_id")


# Reviewed rows for the categories that only title search coded -------------------------


@pytest.mark.parametrize(
    "source, value, code, status",
    [
        ("osm", "shop=department_store", "47191", "SINGLE"),
        ("osm", "shop=gift", "47192", "SINGLE"),
        ("overture", "architectural_designer", "71101", "SINGLE"),
        ("osm", "shop=motorcycle_parts", "47832", "SINGLE"),
        ("overture", "solar_installation", "43211", "SINGLE"),
        ("osm", "shop=seafood", "47215", "SINGLE"),
        ("osm", "shop=eggs", "47212", "SINGLE"),
        ("overture", "telecommunications_service", "61", "SINGLE"),
        # Title search proposed water transport, animal production and equipment
        # manufacture for the next three; the reviewed rows correct them.
        ("overture", "water_park", "93210", "SINGLE"),
        ("overture", "animal_shelter", None, "UNCODEABLE"),
        ("overture", "bowling_alley", "93297", "SINGLE"),
        ("overture", "motorcycle_manufacturer", "3091", "SINGLE"),
        ("osm", "shop=trade", "G", "UNION"),
        ("osm", "office=administrative", None, "UNCODEABLE"),
    ],
)
def test_reviewed_title_search_rows(classifier, source, value, code, status):
    result = classifier.classify_row({"canonical_id": "x", f"{source}_category": value})
    assert (result.code, result.status) == (code, status)


def test_pharmaceutical_company_is_manufacture_or_wholesale(classifier):
    result = classifier.classify_row(
        {"canonical_id": "x", "overture_category": "pharmaceutical_company"}
    )
    assert result.status == "UNION"
    assert sorted(result.admissible_roots) == ["21", "46430"]


# Title search columns -------------------------------------------------------------------


def _searched(taxonomy, rows):
    summary = pd.DataFrame(
        {"canonical_id": [r["canonical_id"] for r in rows], "psic_status": "EMPTY",
         "psic_code": ""}
    )
    return title_search_columns(summary, pd.DataFrame(rows), taxonomy).set_index("canonical_id")


def test_title_search_reports_acceptable_code_without_setting_it(taxonomy):
    out = _searched(taxonomy, [{"canonical_id": "a", "osm_category": "shop=department_store"}])
    assert out.loc["a", "psic_search_status"] == "ACCEPTABLE"
    assert out.loc["a", "psic_search_code"] == "47191"
    assert out.loc["a", "psic_search_basis"] == "category:osm"
    assert out.loc["a", "psic_code"] == ""
    assert out.loc["a", "psic_search_candidates"].startswith("47191 (")


def test_title_search_guards_and_weak_hits(taxonomy):
    out = _searched(taxonomy, [
        {"canonical_id": "suffix", "overture_category": "kite_store"},
        {"canonical_id": "pet", "osm_category": "shop=pet_grooming"},
        {"canonical_id": "weak", "overture_category": "rice_mill"},
    ])
    assert out.loc["suffix", "psic_search_status"] == "GUARDED"
    assert out.loc["suffix", "psic_search_reason"] == "heuristic_store_suffix"
    assert out.loc["pet", "psic_search_reason"] == "ambiguous_pet_shop"
    assert out.loc["weak", "psic_search_status"] == "BELOW_THRESHOLD"
    assert (out["psic_search_code"] == "").all()


def test_name_only_place_gets_candidates_but_never_a_code(tmp_path, classifier):
    out = _run(tmp_path, classifier, [
        {"canonical_id": "n", "canonical_name": "Aling Nena Bakery and Pastry Shop"},
        {"canonical_id": "coded", "osm_category": "amenity=bank", "osm_name": "Bank"},
    ])
    assert out.loc["n", "psic_search_status"] == "NAME_ONLY_CANDIDATES"
    assert out.loc["n", "psic_search_basis"] == "name"
    assert out.loc["n", "psic_search_code"] == ""
    assert pd.isna(out.loc["n", "psic_code"]) or out.loc["n", "psic_code"] == ""
    assert out.loc["coded", "psic_search_status"] == ""
    manifest = json.loads((tmp_path / "out" / "run.json").read_text(encoding="utf-8"))
    assert manifest["psic_title_search"]["status_counts"] == {"NAME_ONLY_CANDIDATES": 1}


def test_title_search_can_be_switched_off(tmp_path, classifier):
    out = _run(tmp_path, classifier, [{"canonical_id": "n", "osm_name": "Shop"}],
               psic_title_search=False)
    assert "psic_search_status" not in out.columns


# Rows with only category and sources ----------------------------------------------------


def test_legacy_single_source_row_is_inferred(classifier):
    row = {"canonical_id": "x", "name": "Smile Dental", "category": "amenity=dentist",
           "sources": "osm"}
    result = classifier.classify_row(row)
    assert result.code == "862"
    assert "LEGACY_SOURCE_FIELDS_INFERRED" in result.flags


def test_legacy_input_file_is_read_and_classified(tmp_path, classifier):
    out = _run(tmp_path, classifier, [
        {"canonical_id": "x", "name": "Smile Dental", "category": "amenity=dentist",
         "sources": "osm"},
    ])
    assert out.loc["x", "psic_code"] == "862"


def test_legacy_two_source_row_splits_tags_from_overture_labels():
    row = {"category": "amenity=dentist | dental_clinic", "sources": "osm|overture", "name": "S"}
    inferred, flags = infer_legacy_source_fields(row)
    assert inferred["osm_category"] == "amenity=dentist"
    assert inferred["overture_category"] == "dental_clinic"
    assert flags == ["LEGACY_SOURCE_FIELDS_PARTIALLY_RECOVERED"]
    unchanged, none = infer_legacy_source_fields({"osm_category": "amenity=bank",
                                                  "category": "x", "sources": "osm"})
    assert none == [] and "overture_category" not in unchanged


# Public or private PSIC subtree ---------------------------------------------------------


def test_ownership_tag_proposes_public_or_private_hospital_class(tmp_path, classifier):
    out = _run(tmp_path, classifier, [
        {"canonical_id": "pub", "osm_category": "amenity=hospital | ownership=public",
         "osm_name": "City General Hospital"},
        {"canonical_id": "pri", "osm_category": "amenity=hospital | ownership=private",
         "osm_name": "Private Hospital"},
        {"canonical_id": "school", "osm_category": "amenity=school | ownership=public",
         "osm_name": "Central School"},
    ])
    assert out.loc["pub", "institution_psic_proposal_status"] == "MAPPED_FROM_PSIC"
    assert out.loc["pub", "institution_psic_proposal_single"] == "8611"
    assert (out.loc["pub", "institution_psic_proposal_io80"],
            out.loc["pub", "institution_psic_proposal_io240"]) == ("76", "231")
    assert out.loc["pub", "institution_psic_proposal_note"] == "io_narrowing_basis=draft"
    assert out.loc["pri", "institution_psic_proposal_single"] == "8612"
    assert out.loc["school", "institution_psic_proposal_status"] == "MAPPED_IO_ONLY_PSIC_COARSE"
    assert out.loc["school", "institution_psic_proposal_codes"] == ""
    assert out.loc["school", "institution_psic_proposal_io80"] == "74"
    # Diagnostic only: the PSIC code and resolved I-O codes are unchanged.
    assert out.loc["pub", "psic_code"] == "861"
    assert out.loc["school", "io80_resolved_code"] == ""


def test_proposal_needs_full_pair_and_a_side(taxonomy, io_crosswalk):
    builder = ProposalBuilder(taxonomy, io_crosswalk)
    partial = builder.propose({
        "psic_code": "85", "io80_codes": "74; 75", "io240_codes": "229",
        "io_ownership_inferred_status": "INFERRED", "io80_ownership_inferred_code": "74",
        "io_ownership_inferred_basis": "explicit_source_attribute",
    })
    assert partial["institution_psic_proposal_status"] == "ROW_IO_PAIR_INCOMPLETE"
    assert builder.propose({"psic_code": "861", "io80_codes": "76; 77",
                            "io240_codes": "231; 232"})["institution_psic_proposal_status"] == ""
    verified = builder.propose({
        "psic_code": "861", "io80_codes": "76; 77", "io240_codes": "231; 232",
        "institution_resolved_ownership": "private",
        "institution_resolution_basis": "verified_canonical_registry_link",
    })
    assert verified["institution_psic_proposal_side_basis"] == (
        "institution_resolved:verified_canonical_registry_link"
    )
    assert verified["institution_psic_proposal_single"] == "8612"


# Bundled registry data and official records ---------------------------------------------


def test_bundled_registry_snapshots_match_manifest():
    report = tools.verify_bundled()
    assert report["level3_hospitals_20260930.csv"]["rows"] == 125
    assert report["deped_schools_national.csv.gz"]["rows"] == 30364
    assert report["unifast_hei_ra10931_20261104.csv.gz"]["rows"] == 1955
    assert report["official_records_quezon_city_seed.csv"]["rows"] == 80


def test_hospital_reference_rebuilds_from_its_source_table(tmp_path):
    rows, stats = tools.level3_hospital_rows(
        tools.read_table(REGISTRY / "philhealth_level3_source_20260930.csv"),
        source="philhealth_hospitals",
        url="https://www.philhealth.gov.ph/partners/providers/facilities/accredited/HOSP_093026.pdf",
        as_of="2026-09-30",
    )
    assert stats["included"] == 125
    target = tmp_path / "hospitals.csv"
    from placetype_ph.specialized.institutional_registry import HOSPITAL_FIELDS, load_hospitals

    assert tools.write_reference(target, rows, HOSPITAL_FIELDS, load_hospitals) == 125
    assert target.read_bytes() == (REGISTRY / "level3_hospitals_20260930.csv").read_bytes()
    with pytest.raises(FileExistsError):
        tools.write_reference(target, rows, HOSPITAL_FIELDS, load_hospitals)


def test_hospital_row_without_explicit_sector_is_refused():
    with pytest.raises(ValueError, match="explicit G/P ownership"):
        tools.level3_hospital_rows(
            [{"No": "1", "NAME OF HEALTH FACILITY": "A", "CAT": "LEVEL 3", "SEC": ""}],
            source="philhealth_hospitals", url="https://www.philhealth.gov.ph/x.pdf",
            as_of="2026-09-30",
        )


def _write(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    path.write_text("\n".join(",".join(r) for r in [header, *rows]) + "\n", encoding="utf-8")
    return path


def test_official_records_follow_their_source_rules(tmp_path):
    assert len(load_official_records(REGISTRY / "official_records_quezon_city_seed.csv")) == 80
    good = ["deped_public", "136539", "A School", "education", "public", "Quezon City", "", "",
            "https://www.depedncr.com.ph/directory-of-public-schools/", "2026-11-01",
            "official_code", "official_public_directory"]
    bad_side = [*good[:4], "private", *good[5:]]
    bad_host = [*good[:8], "https://example.com/list", *good[9:]]
    no_city = [*good[:5], "", *good[6:]]
    for row, message in ((bad_side, "ownership"), (bad_host, "official"), (no_city, "city")):
        path = _write(tmp_path / f"{message}.csv", list(OFFICIAL_FIELDS), [row])
        with pytest.raises(ValueError, match=message):
            load_official_records(path)


def test_nhfr_export_keeps_hospitals_with_explicit_ownership(tmp_path):
    export = _write(tmp_path / "nhfr.csv", [
        "Health Facility Code", "Health Facility Name", "Ownership Major Classification",
        "Health Facility Type", "City/Municipality", "Street Address",
    ], [
        ["DOH000001", "General Hospital A", "Government", "Hospital", "Quezon City", "Road 1"],
        ["DOH000002", "Clinic B", "Private", "Infirmary", "Quezon City", ""],
        ["DOH000003", "Hospital C", "", "Hospital", "Quezon City", ""],
        ["DOH000004", "Hospital D", "Private", "Hospital", "Cebu City", ""],
    ])
    rows = tools.import_official_export(
        export, "doh_nhfr", as_of="2026-11-01", source_url="https://nhfr.doh.gov.ph/facilities"
    )
    assert [r["source_id"] for r in rows] == ["DOH000001", "DOH000004"]
    qc = tools.import_official_export(
        export, "doh_nhfr", as_of="2026-11-01", source_url="https://nhfr.doh.gov.ph/facilities",
        city="Quezon City",
    )
    assert [r["ownership"] for r in qc] == ["public"]
    with pytest.raises(ValueError, match="official HTTPS"):
        tools.import_official_export(
            export, "doh_nhfr", as_of="2026-11-01", source_url="https://example.com"
        )


def test_deped_export_requires_explicit_sector(tmp_path):
    export = _write(tmp_path / "deped.csv", ["School ID", "School Name", "Sector", "City"], [
        ["136539", "Bagong Silangan Elementary School", "Public", "Quezon City"],
        ["400001", "Private Academy", "", "Quezon City"],
    ])
    rows = tools.import_official_export(
        export, "deped_masterlist", as_of="2026-11-01",
        source_url="https://www.deped.gov.ph/schools",
    )
    assert [(r["source_id"], r["ownership"]) for r in rows] == [("136539", "public")]


def test_official_school_code_resolves_ownership_in_a_run(tmp_path, classifier, monkeypatch):
    records = _write(tmp_path / "official.csv", list(OFFICIAL_FIELDS), [[
        "deped_masterlist", "136539", "Bagong Silangan Elementary School", "education",
        "public", "Quezon City", "", "", "https://www.deped.gov.ph/schools", "2026-11-01",
        "official_code", "explicit_sector",
    ]])
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "schema": "placetype-ph-institution-reference-config-v1",
        "references": {"official_records": str(records)},
    }), encoding="utf-8")
    monkeypatch.setenv(ENVIRONMENT_KEY, str(config))
    out = _run(tmp_path, classifier, [{
        "canonical_id": "s", "osm_name": "Bagong Silangan Elementary School",
        "osm_category": "amenity=school", "city": "Quezon City", "deped_school_id": "136539",
    }])
    assert out.loc["s", "institution_evidence_status"] == "EXACT_EXTERNAL_ID"
    assert out.loc["s", "institution_resolved_ownership"] == "public"
    assert out.loc["s", "institution_psic_proposal_side_basis"].startswith("institution_resolved")


def test_install_references_writes_a_valid_configuration(tmp_path):
    config = tools.install_references(tmp_path / "refs", include=["hospitals", "unifast"])
    references = load_reference_config(config)
    assert set(references) == {"hospitals", "unifast"}
    assert references["unifast"].read_bytes() == (
        REGISTRY / "unifast_hei_ra10931_20261104.csv.gz"
    ).read_bytes()
    with pytest.raises(FileExistsError):
        tools.install_references(tmp_path / "refs", include=["hospitals"])


def test_bundled_school_list_is_gzip_without_embedded_file_name():
    raw = (REGISTRY / "deped_schools_national.csv.gz").read_bytes()
    assert raw[3] & 0x08 == 0  # no FNAME field in the gzip header
    assert gzip.decompress(raw).startswith(b"school_id,school_name,town_psgc\n")


# Verified links in the registry layout --------------------------------------------------


def _registry_links(schema: str, host: str = "https://www.deped.gov.ph/") -> dict:
    return {"schema": schema, "links": [{
        "canonical_id": "s1", "verified": True, "domain": "education", "ownership": "public",
        "source_url": host, "registry_id": "305001", "verified_as_of": "2026-11-01",
    }]}


def test_registry_layout_links_convert_to_documented_format(tmp_path):
    source = tmp_path / "links.json"
    source.write_text(json.dumps(_registry_links("example-links-v1")), encoding="utf-8")
    output = tmp_path / "converted.json"
    assert specialized_main(["convert-links", "--input", str(source), "--schema",
                             "example-links-v1", "--output", str(output)]) == 0
    converted = json.loads(output.read_text(encoding="utf-8"))
    assert converted["schema"] == "placetype-ph-verified-institution-links-v1"
    assert converted["links"][0]["source"] == "deped_beis"
    assert converted["links"][0]["source_id"] == "305001"


def test_link_conversion_refuses_other_schemas_and_hosts():
    with pytest.raises(ValueError, match="schema identifier"):
        tools.convert_registry_links(_registry_links("a-v1"), schema="b-v1")
    with pytest.raises(ValueError, match="official registry host"):
        tools.convert_registry_links(_registry_links("a-v1", "https://example.com/"),
                                     schema="a-v1")


# Review queue ---------------------------------------------------------------------------


def test_review_queue_marks_conflicts_first_and_never_verifies(tmp_path):
    from placetype_ph.specialized.institutional_registry import Institution

    record = Institution(
        "doh_nhfr", "DOH1", "City General Hospital", "health", "public", city="Quezon City",
        source_url="https://nhfr.doh.gov.ph/x", as_of="2026-11-01", id_kind="official_code",
        evidence_type="explicit_sector",
    )
    classified = pd.DataFrame([
        {"canonical_id": "a", "name": "City General Hospital", "psic_code": "861",
         "psic_status": "SINGLE", "io80_codes": "76; 77", "io80_resolved_code": "",
         "institution_evidence_status": "PROVISIONAL", "institution_resolution_status": "",
         "institution_resolution_reason": "", "institution_candidate_ownership": "public",
         "institution_resolution_reference": "doh_nhfr:DOH1"},
        {"canonical_id": "b", "name": "=City General Hospital Annex", "psic_code": "861",
         "psic_status": "SINGLE", "io80_codes": "76; 77", "io80_resolved_code": "77",
         "institution_evidence_status": "PROVISIONAL", "institution_resolution_status": "",
         "institution_resolution_reason": "", "institution_candidate_ownership": "public",
         "institution_resolution_reference": "doh_nhfr:DOH1"},
    ])
    places = pd.DataFrame([
        {"canonical_id": "a", "osm_name": "City General Hospital", "city": "Quezon City"},
        {"canonical_id": "b", "osm_name": "City General Hospital Annex", "city": "Quezon City",
         "osm_category": "amenity=hospital | ownership=private"},
    ])
    rows, grouped, summary = tools.review_institutions(classified, places, [record])
    by_id = {r["canonical_id"]: r for r in rows}
    assert rows[0]["canonical_id"] == "b"
    assert by_id["b"]["evidence_priority"] == "HIGH_CONFLICT"
    assert "possible_subunit_or_branch" in by_id["b"]["candidate_review_reasons"]
    assert by_id["a"]["ownership_opportunity"] == "CURRENT_IO80_UNASSIGNED"
    assert by_id["a"]["relationship_hypothesis"] == "SAME_LOCALITY_SITE_CANDIDATE"
    assert {r["promotion_allowed"] for r in rows} == {"false"}
    assert grouped[0]["candidate_observations"] == "2"
    assert summary["ownership_changes"] == 0
    paths = tools.write_review(tmp_path / "review", rows, grouped, summary)
    assert "'=City General Hospital Annex" in paths["queue"].read_text(encoding="utf-8")


# User-supplied places -------------------------------------------------------------------


def test_augment_adds_new_places_and_codes_existing_ones(tmp_path, classifier):
    places = pd.DataFrame([{"canonical_id": "p1", "osm_name": "Old Cafe",
                            "osm_category": "amenity=cafe", "sources": "osm"}])
    supplied_path = tmp_path / "user.csv"
    supplied_path.write_text(
        "source_id,name,lon,lat,canonical_id,explicit_io80_code,employees\n"
        "R1,Old Cafe,121.0,14.6,p1,63,4\n"
        "R2,Corner Bakery,121.1,14.7,,55,9\n",
        encoding="utf-8",
    )
    out, report = augment_places(places, read_supplied(supplied_path), source="my_list")
    assert report == {"source": "user:my_list", "input": 2, "matched": 1, "added": 1,
                      "total": 2}
    out = out.set_index("canonical_id")
    assert out.loc["p1", "sources"] == "osm|user:my_list"
    assert out.loc["user-my_list-R2", "explicit_io80_code"] == "55"
    assert str(out.loc["user-my_list-R2", "aug_my_list__employees"]) == "9"
    with pytest.raises(ValueError, match="already exists"):
        augment_places(out.reset_index(), read_supplied(supplied_path), source="my_list")
    path = tmp_path / "augmented.parquet"
    out.reset_index().to_parquet(path, index=False)
    _, summary = classify_openplaces(path, {"psic": classifier}, tmp_path / "run")
    resolved = pd.read_parquet(summary).set_index("canonical_id")
    assert resolved.loc["user-my_list-R2", "io80_resolved_code"] == "55"
    assert resolved.loc["user-my_list-R2", "io16_resolved_code"] != ""


def test_augment_refuses_conflicts_and_bad_rows(tmp_path):
    places = pd.DataFrame([{"canonical_id": "p1", "explicit_io80_code": "63"}])
    conflict = pd.DataFrame([{"source_id": "R1", "name": "A", "lon": 121, "lat": 14,
                              "canonical_id": "p1", "explicit_io80_code": "55"}])
    with pytest.raises(ValueError, match="nothing will be written"):
        augment_places(places, conflict, source="list")
    bad = tmp_path / "bad.csv"
    bad.write_text("source_id,name,lon,lat\n1,A,121,91\n1,B,121,14\n", encoding="utf-8")
    with pytest.raises(ValueError, match="2 invalid"):
        read_supplied(bad)
    with pytest.raises(ValueError, match="built-in source"):
        augment_places(places, conflict, source="osm")
