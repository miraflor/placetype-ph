"""Offline institutional identity fixtures; reject unsafe evidence promotion."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from placetype_ph.specialized.institutional_preview import main
from placetype_ph.specialized.institutional_registry import (
    HOSPITAL_FIELDS,
    UNIFAST_FIELDS,
    Institution,
    InstitutionIndex,
    load_beis,
    load_hospitals,
    load_unifast,
)
from placetype_ph.specialized.institutional_shadow import (
    InstitutionResolver,
    load_verified_links,
)


def _csv(path: Path, fields: tuple[str, ...], rows: list[tuple[str, ...]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(fields)
        writer.writerows(rows)
    return path


def _beis(tmp_path: Path) -> Path:
    return _csv(
        tmp_path / "beis.csv",
        ("school_id", "school_name", "town_psgc"),
        [("123456", "Example National High School", "1374040000")],
    )


def _unifast(tmp_path: Path, *, typ: str = "SUC", own: str = "public") -> Path:
    region, name = "13 - NCR", "Example University"
    digest = hashlib.sha256(f"{region.casefold()}\0{name.casefold()}".encode()).hexdigest()[:20]
    return _csv(
        tmp_path / "unifast.csv",
        UNIFAST_FIELDS,
        [
            (
                region,
                name,
                typ,
                own,
                "https://unifast.gov.ph/hei-list.html",
                "2026-10-20T10:00:00Z",
                digest,
            )
        ],
    )


def _hospital(tmp_path: Path, *, src: str = "doh_nhfr", ownership: str = "public") -> Path:
    url = (
        "https://nhfr.doh.gov.ph/index"
        if src == "doh_nhfr"
        else "https://www.philhealth.gov.ph/list"
    )
    kind = "official_code" if src == "doh_nhfr" else "publication_row"
    return _csv(
        tmp_path / "hospital.csv",
        HOSPITAL_FIELDS,
        [
            (
                src,
                "H100" if src == "doh_nhfr" else "37",
                "Example Hospital",
                "health",
                ownership,
                "Quezon City",
                "Metro Manila",
                "Sample St",
                "Example General Hospital",
                url,
                "2026-10-20",
                kind,
                "explicit_sector",
                "Level 3",
            )
        ],
    )


def _resolver(*entries: Institution) -> InstitutionResolver:
    return InstitutionResolver(InstitutionIndex(entries))


def test_beis_id_ownership_stays_provisional(tmp_path: Path):
    entries = load_beis(_beis(tmp_path))
    assert entries[0].ownership == "public"
    assert entries[0].id_kind == "unverified_source_id"
    result = _resolver(*entries).assess(
        {
            "canonical_id": "p1",
            "name": "Example National High School",
            "psic_code": "8521",
            "deped_school_id": "123456",
            "town_psgc": "1374040000",
        }
    )
    assert result.status == "PROVISIONAL"
    assert result.promotion_eligible is False


def test_beis_wrong_psgc_never_promotes(tmp_path: Path):
    entries = load_beis(_beis(tmp_path))
    result = _resolver(*entries).assess(
        {
            "name": "Example National High School",
            "psic_code": "8521",
            "deped_school_id": "123456",
            "town_psgc": "1374050000",
        }
    )
    assert result.status == "REVIEW"
    assert result.reason == "school_psgc_mismatch"


def test_beis_duplicate_id_rejected(tmp_path: Path):
    path = _csv(
        tmp_path / "bad.csv",
        ("school_id", "school_name", "town_psgc"),
        [
            ("123456", "A", "1374040000"),
            ("123456", "B", "1374040000"),
        ],
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_beis(path)


def test_beis_prefix_is_not_official_ownership(tmp_path: Path):
    p = _csv(
        tmp_path / "long.csv",
        ("school_id", "school_name", "town_psgc"),
        [
            ("4123456", "Example School", "1374040000"),
        ],
    )
    assert load_beis(p)[0].ownership == ""


def test_unifast_name_region_provisional(tmp_path: Path):
    records = load_unifast(_unifast(tmp_path))
    result = _resolver(*records).assess(
        {
            "name": "Example University",
            "psic_code": "854",
            "region": "13 - NCR",
        }
    )
    assert result.status == "PROVISIONAL"
    assert result.promotion_eligible is False
    assert records[0].id_kind == "local_digest"


def test_unifast_type_owner_mismatch_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="type/ownership"):
        load_unifast(_unifast(tmp_path, typ="SUC", own="private"))


def test_unifast_wrong_region_review(tmp_path: Path):
    records = load_unifast(_unifast(tmp_path))
    result = _resolver(*records).assess(
        {
            "name": "Example University",
            "psic_code": "854",
            "region_code": "07",
        }
    )
    assert result.status == "REVIEW"
    assert result.reason == "hei_region_mismatch"


def test_hospital_official_code_site_name_city(tmp_path: Path):
    records = load_hospitals(_hospital(tmp_path))
    result = _resolver(*records).assess(
        {
            "name": "Example Hospital",
            "psic_code": "861",
            "nhfr_code": "H100",
            "city": "Quezon City",
        }
    )
    assert result.status == "EXACT_EXTERNAL_ID"
    assert result.promotion_eligible is True


def test_hospital_name_conflict(tmp_path: Path):
    records = load_hospitals(_hospital(tmp_path))
    result = _resolver(*records).assess(
        {
            "name": "Other Hospital",
            "psic_code": "861",
            "nhfr_code": "H100",
            "city": "Quezon City",
        }
    )
    assert result.status == "REVIEW"
    assert result.reason == "canonical_name_not_registered_site"


def test_hospital_wrong_branch_city(tmp_path: Path):
    records = load_hospitals(_hospital(tmp_path))
    result = _resolver(*records).assess(
        {
            "name": "Example Hospital",
            "psic_code": "861",
            "nhfr_code": "H100",
            "city": "Pasig City",
        }
    )
    assert result.status == "REVIEW"
    assert result.reason == "registered_site_locality_mismatch"


def test_hospital_without_city_unverified(tmp_path: Path):
    records = load_hospitals(_hospital(tmp_path))
    result = _resolver(*records).assess(
        {
            "name": "Example Hospital",
            "psic_code": "861",
            "nhfr_code": "H100",
        }
    )
    assert result.status == "PROVISIONAL"
    assert result.promotion_eligible is False


def test_philhealth_publication_row_not_official_id(tmp_path: Path):
    records = load_hospitals(_hospital(tmp_path, src="philhealth_hospitals"))
    result = _resolver(*records).assess(
        {
            "name": "Example Hospital",
            "psic_code": "861",
            "city": "Quezon City",
            "official_id_source": "philhealth_hospitals",
            "official_id": "37",
        }
    )
    assert result.status == "PROVISIONAL"
    assert result.promotion_eligible is False


def test_reject_hospital_wrong_source_kind(tmp_path: Path):
    path = _hospital(tmp_path)
    values = list(csv.reader(path.open(encoding="utf-8")))
    values[1][11] = "publication_row"
    _csv(path, HOSPITAL_FIELDS, [tuple(values[1])])
    with pytest.raises(ValueError, match="identifier kind"):
        load_hospitals(path)


def test_reject_nonofficial_hospital_url(tmp_path: Path):
    path = _hospital(tmp_path)
    values = list(csv.reader(path.open(encoding="utf-8")))
    values[1][9] = "https://example.com/registry"
    _csv(path, HOSPITAL_FIELDS, [tuple(values[1])])
    with pytest.raises(ValueError, match="official HTTPS"):
        load_hospitals(path)


def test_conflicting_explicit_ownership_review():
    result = _resolver().assess(
        {
            "name": "Example School",
            "psic_code": "8521",
            "osm_category": "ownership=public",
            "overture_category": "private_school",
        }
    )
    assert result.status == "REVIEW"
    assert result.reason == "contradictory_explicit_ownership"


def test_explicit_ownership_claim_is_not_verified_registry():
    result = _resolver().assess(
        {
            "name": "Example School",
            "psic_code": "8521",
            "overture_category": "public_school",
        }
    )
    assert result.status == "EXPLICIT_SOURCE_CLAIM"
    assert result.promotion_eligible is False


def test_operator_name_alone_not_ownership():
    result = _resolver().assess(
        {
            "name": "Example Hospital",
            "psic_code": "861",
            "osm_category": "operator=Department of Health",
        }
    )
    # A government operator is recorded as a proxy, never as ownership evidence.
    assert result.status == "OPERATOR_PROXY"
    assert result.basis == "government_operator_proxy"
    assert result.promotion_eligible is False


def test_religious_name_not_private_evidence():
    result = _resolver().assess({"name": "Saint Luke Hospital", "psic_code": "861"})
    assert result.status == "NO_EVIDENCE"


def test_subunit_blocks_registry_match(tmp_path: Path):
    records = load_hospitals(_hospital(tmp_path))
    result = _resolver(*records).assess(
        {
            "name": "Example Hospital - ICU",
            "psic_code": "861",
            "nhfr_code": "H100",
            "city": "Quezon City",
        }
    )
    assert result.status == "REVIEW"
    assert result.reason == "possible_institution_subunit"


def test_unresolved_psic_blocks_identity_promotion(tmp_path: Path):
    records = load_hospitals(_hospital(tmp_path))
    result = _resolver(*records).assess(
        {
            "name": "Example Hospital",
            "psic_code": "861",
            "psic_status": "CONFLICT",
            "nhfr_code": "H100",
            "city": "Quezon City",
        }
    )
    assert result.status == "ABSTAIN"


def test_school_activity_vs_hospital_category_review():
    result = _resolver().assess(
        {
            "name": "Example Hospital",
            "psic_code": "8521",
            "osm_category": "amenity=hospital",
        }
    )
    assert result.status == "REVIEW"


def test_verified_link_requires_independent_review(tmp_path: Path):
    path = tmp_path / "links.json"
    path.write_text(
        json.dumps(
            {
                "schema": "placetype-ph-verified-institution-links-v1",
                "links": [
                    {
                        "canonical_id": "p1",
                        "verified": False,
                        "domain": "health",
                        "ownership": "public",
                        "source": "doh_nhfr",
                        "source_id": "H100",
                        "source_url": "https://nhfr.doh.gov.ph/",
                        "verified_as_of": "2026-10-20",
                    },
                ],
            }
        )
    )
    with pytest.raises(ValueError, match="independently verified"):
        load_verified_links(path)


def test_verified_link_valid_but_source_conflict_review(tmp_path: Path):
    path = tmp_path / "links.json"
    path.write_text(
        json.dumps(
            {
                "schema": "placetype-ph-verified-institution-links-v1",
                "links": [
                    {
                        "canonical_id": "p1",
                        "verified": True,
                        "domain": "health",
                        "ownership": "public",
                        "source": "doh_nhfr",
                        "source_id": "H100",
                        "source_url": "https://nhfr.doh.gov.ph/",
                        "verified_as_of": "2026-10-20",
                    },
                ],
            }
        )
    )
    result = InstitutionResolver(verified_links=load_verified_links(path)).assess(
        {
            "canonical_id": "p1",
            "name": "Example Hospital",
            "psic_code": "861",
            "osm_category": "ownership=private",
        }
    )
    assert result.status == "REVIEW"
    assert result.reason == "verified_link_source_conflict"


def test_verified_link_positive(tmp_path: Path):
    path = tmp_path / "links.json"
    path.write_text(
        json.dumps(
            {
                "schema": "placetype-ph-verified-institution-links-v1",
                "links": [
                    {
                        "canonical_id": "p1",
                        "verified": True,
                        "domain": "health",
                        "ownership": "public",
                        "source": "doh_nhfr",
                        "source_id": "H100",
                        "source_url": "https://nhfr.doh.gov.ph/",
                        "verified_as_of": "2026-10-20",
                    },
                ],
            }
        )
    )
    result = InstitutionResolver(verified_links=load_verified_links(path)).assess(
        {
            "canonical_id": "p1",
            "name": "Example Hospital",
            "psic_code": "861",
        }
    )
    assert result.status == "VERIFIED_LINK"
    assert result.promotion_eligible


def test_duplicate_names_ambiguous_even_same_owner():
    entries = (
        Institution("deped_beis", "123456", "Example School", "education", "public"),
        Institution("deped_beis", "123457", "Example School", "education", "public"),
    )
    result = _resolver(*entries).assess({"name": "Example School", "psic_code": "8521"})
    assert result.status == "REVIEW"
    assert result.reason == "multiple_registered_sites_or_sources"


def test_conflicting_registered_owner_abstains():
    entries = (
        Institution("deped_beis", "123456", "Example School", "education", "public"),
        Institution("unifast_ra10931", "abc", "Example School", "education", "private"),
    )
    result = _resolver(*entries).assess({"name": "Example School", "psic_code": "8521"})
    assert result.status == "REVIEW"
    assert result.reason == "registry_ownership_conflict"


def test_cli_reports_no_tag_mutation(tmp_path: Path):
    places = _csv(
        tmp_path / "places.csv",
        ("canonical_id", "name", "psic_code"),
        [("p1", "Example National High School", "8521")],
    )
    source = _beis(tmp_path)
    output = tmp_path / "report"
    assert (
        main(
            [
                "--places",
                str(places),
                "--beis",
                str(source),
                "--output-dir",
                str(output),
                "--dry-run",
            ]
        )
        == 0
    )
    assert not output.exists()
    original = places.read_bytes()
    assert main(["--places", str(places), "--beis", str(source), "--output-dir", str(output)]) == 0
    assert places.read_bytes() == original
    assert pd.read_csv(output / "institutional_review.csv").shape[0] == 1
    assert json.loads((output / "summary.json").read_text())["authoritative_tag_changes"] == 0
    with pytest.raises(FileExistsError):
        main(["--places", str(places), "--beis", str(source), "--output-dir", str(output)])


def test_cli_publish_failure_cleans_all_files(tmp_path: Path, monkeypatch):
    places = _csv(
        tmp_path / "places.csv",
        ("canonical_id", "name", "psic_code"),
        [("p1", "Example Hospital", "861")],
    )
    hospital = _hospital(tmp_path)
    output = tmp_path / "report"
    monkeypatch.setenv("PLACETYPE_P3_INJECT_PUBLISH_FAILURE", "1")
    with pytest.raises(RuntimeError, match="injected"):
        main(["--places", str(places), "--hospitals", str(hospital), "--output-dir", str(output)])
    assert not output.exists()
    assert not any(x.name.startswith(".institution-") for x in tmp_path.iterdir())


def test_hospital_wrong_branch_address(tmp_path: Path):
    records = load_hospitals(_hospital(tmp_path))
    result = _resolver(*records).assess(
        {
            "name": "Example Hospital",
            "psic_code": "861",
            "nhfr_code": "H100",
            "city": "Quezon City",
            "address": "Different Street",
        }
    )
    assert result.status == "REVIEW"
    assert result.reason == "registered_site_address_mismatch"


def test_verified_link_conflicting_external_code(tmp_path: Path):
    path = tmp_path / "links.json"
    path.write_text(
        json.dumps(
            {
                "schema": "placetype-ph-verified-institution-links-v1",
                "links": [
                    {
                        "canonical_id": "p1",
                        "verified": True,
                        "domain": "health",
                        "ownership": "public",
                        "source": "doh_nhfr",
                        "source_id": "H100",
                        "source_url": "https://nhfr.doh.gov.ph/",
                        "verified_as_of": "2026-10-20",
                    },
                ],
            }
        )
    )
    result = InstitutionResolver(verified_links=load_verified_links(path)).assess(
        {
            "canonical_id": "p1",
            "name": "Example Hospital",
            "psic_code": "861",
            "nhfr_code": "H999",
        }
    )
    assert result.status == "REVIEW"
    assert result.reason == "verified_link_external_id_conflict"


def test_unknown_explicit_id_cannot_fallback_to_name(tmp_path: Path):
    records = load_hospitals(_hospital(tmp_path))
    result = _resolver(*records).assess(
        {
            "name": "Example Hospital",
            "psic_code": "861",
            "nhfr_code": "UNKNOWN",
            "city": "Quezon City",
        }
    )
    assert result.status == "REVIEW"
    assert result.reason == "supplied_external_id_not_in_registry"


def test_structured_overture_primary_owner_evidence():
    result = _resolver().assess(
        {
            "name": "Example School",
            "psic_code": "8521",
            "overture_primary_category": "public_school",
        }
    )
    assert result.status == "EXPLICIT_SOURCE_CLAIM"
    assert result.ownership_candidate == "public"


def test_hospital_reference_without_city_is_provisional(tmp_path: Path):
    path = _hospital(tmp_path)
    values = list(csv.reader(path.open(encoding="utf-8")))
    values[1][5] = ""
    _csv(path, HOSPITAL_FIELDS, [tuple(values[1])])
    records = load_hospitals(path)
    result = _resolver(*records).assess(
        {
            "name": "Example Hospital",
            "psic_code": "861",
            "nhfr_code": "H100",
            "city": "Quezon City",
        }
    )
    assert result.status == "PROVISIONAL"
    assert result.promotion_eligible is False


def test_overture_basic_category_is_not_explicit_owner():
    result = _resolver().assess(
        {
            "name": "Example School",
            "psic_code": "8521",
            "overture_basic_category": "public_school",
        }
    )
    assert result.status == "NO_EVIDENCE"
    assert result.promotion_eligible is False
