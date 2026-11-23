"""Priority 3b tests: verified ownership only; immutable PSIC/PCPC/PSCC decisions."""

from __future__ import annotations

import hashlib
import json

import pandas as pd
import pytest

from placetype_ph.specialized.institutional_promotion import (
    NEW_FIELDS,
    _assert_outside_git,
    main,
    resolve_institutional_ownership,
)
from placetype_ph.specialized.institutional_registry import (
    Institution,
    InstitutionIndex,
)
from placetype_ph.specialized.institutional_shadow import (
    InstitutionResolver,
    VerifiedLink,
)


def frame(cid="site-1", psic="861", status="SINGLE", owner=""):
    return pd.DataFrame(
        [
            {
                "canonical_id": cid,
                "psic_code": psic,
                "psic_status": status,
                "pcpc_code": "98765",
                "pscc_code": "abc",
                "psic_method": "reviewed",
                "ownership_candidate": owner,
                "custom_metadata": "preserved",
            }
        ]
    )


def places(cid="site-1", name="Cedar Hospital", city="Quezon City"):
    return pd.DataFrame(
        [
            {
                "canonical_id": cid,
                "name": name,
                "canonical_name": name,
                "city": city,
                "osm_category": "amenity=hospital",
                "overture_category": "hospital",
                "nhfr_code": "NHFR-001",
            }
        ]
    )


def hospital(
    *,
    source_id="NHFR-001",
    name="Cedar Hospital",
    city="Quezon City",
    owner="public",
    id_kind="official_code",
    site_scope="site",
):
    return Institution(
        "doh_nhfr",
        source_id,
        name,
        "health",
        owner,
        city=city,
        source_url="https://nhfr.doh.gov.ph/",
        as_of="2026-11-01",
        id_kind=id_kind,
        evidence_type="explicit_sector",
        site_scope=site_scope,
    )


def resolver(*records, links=None):
    return InstitutionResolver(InstitutionIndex(records), links or {})


def verified(*, owner="public", domain="health", source_id="NHFR-001"):
    return VerifiedLink(
        "site-1", domain, owner, "doh_nhfr", source_id, "https://nhfr.doh.gov.ph/", "2026-11-01"
    )


def check_unchanged(original, output):
    assert len(output) == len(original)
    assert output["canonical_id"].tolist() == original["canonical_id"].tolist()
    assert set(output.columns) == set(original.columns) | set(NEW_FIELDS)
    for column in original:
        assert output[column].equals(original[column]), column


def test_verified_external_official_id_promotes_only_owner():
    old = frame()
    new = resolve_institutional_ownership(old, places(), resolver=resolver(hospital()))
    check_unchanged(old, new)
    assert new.at[0, "institution_resolution_status"] == "RESOLVED"
    assert new.at[0, "institution_resolved_ownership"] == "public"
    assert new.at[0, "institution_resolution_basis"] == "official_registry_external_id"


def test_manually_verified_canonical_id_link_promotes():
    old = frame()
    raw = places()
    raw["nhfr_code"] = ""
    new = resolve_institutional_ownership(old, raw, resolver=resolver(links={"site-1": verified()}))
    check_unchanged(old, new)
    assert new.at[0, "institution_resolution_status"] == "RESOLVED"
    assert new.at[0, "institution_resolution_basis"] == "verified_canonical_registry_link"


@pytest.mark.parametrize(
    "status",
    [
        "REVIEW",
        "UNRESOLVED",
        "INVALID",
        "AMBIGUOUS",
        "ERROR",
        "CONFLICT",
        "CANDIDATES_ONLY",
        "NOT_PSIC_ACTIVITY",
        "NOT_ACTIVITY",
        "UNCODEABLE",
        "REVIEW_ENTITY_MATCH",
    ],
)
def test_unresolved_original_activity_blocks_promotion(status):
    out = resolve_institutional_ownership(
        frame(status=status), places(), resolver=resolver(hospital())
    )
    assert out.at[0, "institution_resolution_status"] == "ABSTAIN"
    assert out.at[0, "institution_resolved_ownership"] == ""


def test_unrelated_activity_blocks_promotion():
    out = resolve_institutional_ownership(
        frame(psic="4711"), places(), resolver=resolver(hospital())
    )
    assert out.at[0, "institution_resolved_ownership"] == ""


def test_different_facility_name_blocks():
    out = resolve_institutional_ownership(
        frame(), places(name="Elm Hospital"), resolver=resolver(hospital())
    )
    assert out.at[0, "institution_resolved_ownership"] == ""


def test_different_registered_city_blocks():
    out = resolve_institutional_ownership(
        frame(), places(city="Makati City"), resolver=resolver(hospital())
    )
    assert out.at[0, "institution_resolved_ownership"] == ""


def test_existing_opposite_owner_blocks_strong_link():
    out = resolve_institutional_ownership(
        frame(owner="private"), places(), resolver=resolver(hospital())
    )
    assert out.at[0, "institution_resolution_status"] == "REVIEW"
    assert out.at[0, "institution_resolution_reason"] == "existing_ownership_evidence_conflict"
    assert out.at[0, "institution_resolved_ownership"] == ""


def test_existing_matching_owner_does_not_block():
    out = resolve_institutional_ownership(
        frame(owner="public"), places(), resolver=resolver(hospital())
    )
    assert out.at[0, "institution_resolved_ownership"] == "public"


def test_name_only_registry_match_cannot_promote():
    raw = places()
    raw["nhfr_code"] = ""
    out = resolve_institutional_ownership(frame(), raw, resolver=resolver(hospital()))
    assert out.at[0, "institution_resolution_status"] == "PROVISIONAL"
    assert out.at[0, "institution_candidate_ownership"] == "public"
    assert out.at[0, "institution_resolved_ownership"] == ""


def test_publication_row_cannot_promote():
    raw = places()
    raw["nhfr_code"] = ""
    rec = Institution(
        "philhealth_hospitals",
        "row-1",
        "Cedar Hospital",
        "health",
        "public",
        city="Quezon City",
        id_kind="publication_row",
        site_scope="site",
    )
    out = resolve_institutional_ownership(frame(), raw, resolver=resolver(rec))
    assert out.at[0, "institution_resolved_ownership"] == ""


def test_explicit_source_attribute_is_only_candidate():
    raw = places()
    raw["nhfr_code"] = ""
    raw["osm_category"] = "amenity=hospital|ownership=public"
    out = resolve_institutional_ownership(frame(), raw, resolver=resolver())
    assert out.at[0, "institution_resolution_status"] == "EXPLICIT_SOURCE_CLAIM"
    assert out.at[0, "institution_candidate_ownership"] == "public"
    assert out.at[0, "institution_resolved_ownership"] == ""


def test_institution_subunit_blocks_even_correct_official_id():
    out = resolve_institutional_ownership(
        frame(), places(name="Cedar Hospital ICU"), resolver=resolver(hospital())
    )
    assert out.at[0, "institution_resolved_ownership"] == ""


def test_mismatched_verified_link_and_explicit_source_owner_blocks():
    raw = places()
    raw["osm_category"] = "amenity=hospital|ownership=private"
    out = resolve_institutional_ownership(
        frame(), raw, resolver=resolver(links={"site-1": verified()})
    )
    assert out.at[0, "institution_resolved_ownership"] == ""


def test_existing_output_columns_refuse_overwrite():
    old = frame()
    old["institution_resolved_ownership"] = "PRIVATE"
    with pytest.raises(ValueError, match="already present"):
        resolve_institutional_ownership(old, places(), resolver=resolver(hospital()))


@pytest.mark.parametrize("which", ["classified", "places"])
def test_duplicate_identifiers_abort(which):
    old, raw = frame(), places()
    if which == "classified":
        old = pd.concat([old, old], ignore_index=True)
    else:
        raw = pd.concat([raw, raw], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        resolve_institutional_ownership(old, raw, resolver=resolver(hospital()))


def test_mismatched_id_sets_abort():
    with pytest.raises(ValueError, match="identical canonical_id"):
        resolve_institutional_ownership(frame(), places(cid="other"), resolver=resolver(hospital()))


def test_classified_source_remains_authoritative_for_psic():
    raw = places()
    raw["psic_code"] = "4711"
    raw["psic_status"] = "REVIEW"
    out = resolve_institutional_ownership(frame(), raw, resolver=resolver(hospital()))
    assert out.at[0, "institution_resolved_ownership"] == "public"


def test_out_of_order_raw_places_still_preserve_classified_order():
    a = frame()
    b = frame(cid="site-2")
    old = pd.concat([a, b], ignore_index=True)
    raw = pd.concat([places(cid="site-2"), places()], ignore_index=True)
    out = resolve_institutional_ownership(old, raw, resolver=resolver(hospital()))
    check_unchanged(old, out)


def test_output_directory_inside_git_checkout_forbidden(tmp_path):
    (tmp_path / ".git").mkdir()
    with pytest.raises(ValueError, match="outside Git"):
        _assert_outside_git(tmp_path / "reports" / "v1")


def test_cli_publishes_atomically_and_dry_run_changes_nothing(tmp_path, capsys):
    csv_class = tmp_path / "classified.csv"
    csv_raw = tmp_path / "places.csv"
    verified_json = tmp_path / "links.json"
    frame().to_csv(csv_class, index=False)
    raw = places()
    raw["nhfr_code"] = ""
    raw.to_csv(csv_raw, index=False)
    verified_json.write_text(
        json.dumps(
            {
                "schema": "placetype-ph-verified-institution-links-v1",
                "links": [
                    {
                        "canonical_id": "site-1",
                        "verified": True,
                        "domain": "health",
                        "ownership": "public",
                        "source": "doh_nhfr",
                        "source_id": "NHFR-001",
                        "source_url": "https://nhfr.doh.gov.ph/",
                        "verified_as_of": "2026-11-01",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    outdir = tmp_path / "published"
    arguments = [
        "--classified",
        str(csv_class),
        "--places",
        str(csv_raw),
        "--verified-links",
        str(verified_json),
        "--output-dir",
        str(outdir),
    ]
    assert main(arguments + ["--dry-run"]) == 0
    assert not outdir.exists()
    assert main(arguments) == 0
    report = json.loads((outdir / "institution_resolution_summary.json").read_text())
    out = pd.read_csv(outdir / "classified_with_institutions.csv", dtype=str, keep_default_na=False)
    assert report["resolved_verified"] == 1
    assert report["original_classification_columns_changed"] == 0
    assert out.at[0, "institution_resolved_ownership"] == "public"
    assert (
        report["input_sha256"][str(csv_class.resolve())]
        == hashlib.sha256(csv_class.read_bytes()).hexdigest()
    )
    with pytest.raises(FileExistsError):
        main(arguments)
    assert "PASS" in capsys.readouterr().out


def test_cli_injected_publication_failure_rolls_back(tmp_path, monkeypatch):
    classified = tmp_path / "classified.csv"
    raw = tmp_path / "raw.csv"
    link = tmp_path / "verified.json"
    frame().to_csv(classified, index=False)
    places().to_csv(raw, index=False)
    link.write_text(
        json.dumps({"schema": "placetype-ph-verified-institution-links-v1", "links": []})
    )
    output = tmp_path / "result"
    monkeypatch.setenv("PLACETYPE_P3B_INJECT_PUBLISH_FAILURE", "1")
    with pytest.raises(RuntimeError, match="injected"):
        main(
            [
                "--classified",
                str(classified),
                "--places",
                str(raw),
                "--verified-links",
                str(link),
                "--output-dir",
                str(output),
            ]
        )
    assert not output.exists()
    assert not list(tmp_path.glob(".institution-promotion-*"))


def test_duplicate_official_site_cannot_promote_to_multiple_entities():
    old = pd.concat([frame(), frame(cid="site-2")], ignore_index=True)
    raw = pd.concat([places(), places(cid="site-2")], ignore_index=True)
    out = resolve_institutional_ownership(old, raw, resolver=resolver(hospital()))
    check_unchanged(old, out)
    assert set(out["institution_resolution_status"]) == {"REVIEW"}
    assert set(out["institution_resolution_reason"]) == {
        "same_registry_site_matches_multiple_entities"
    }
    assert set(out["institution_resolved_ownership"]) == {""}


def test_missing_psic_status_cannot_promote():
    out = resolve_institutional_ownership(frame(status=""), places(), resolver=resolver(hospital()))
    assert out.at[0, "institution_resolution_status"] == "ABSTAIN"
    assert out.at[0, "institution_resolved_ownership"] == ""
