"""Priority 4: conservative reference-backed I/O public/private candidate narrowing."""

from __future__ import annotations

import inspect

import pandas as pd
import pytest

from placetype_ph.io_mapping import IOCrosswalk
from placetype_ph.specialized.ownership_io import (
    POLICY,
    REFINED_FIELDS,
    _verified_pairs,
    ownership_io_metadata,
    refine_verified_institutional_io,
)


def row(domain: str = "education", side: str = "public") -> dict[str, str]:
    part = "85" if domain == "education" else "86"
    code = "851" if domain == "education" else "861"
    record = IOCrosswalk.load_default().concordance_row(part)
    assert record is not None
    return {
        "canonical_id": domain + ":" + side,
        "psic_code": code,
        "psic_status": "SINGLE",
        "pcpc_code": "99999",
        "pscc_code": "88888",
        "io_mapping_status": "MAPPED_GROUP",
        "io_mapping_psic_level": "group",
        "io_mapping_psic_code": code,
        "io_mapping_bridge_basis": "code_title_match",
        "io16_codes": "; ".join(record.io16.codes),
        "io80_codes": "; ".join(record.io80.codes),
        "io240_codes": "; ".join(record.io240.codes),
        "io80_map_code": "",
        "io240_map_code": "",
        "io80_map_status": "AMBIGUOUS_SET",
        "io240_map_status": "AMBIGUOUS_SET",
        "institution_resolution_status": "RESOLVED",
        "institution_resolved_ownership": side,
        "institution_resolution_basis": "official_registry_external_id",
        "institution_resolution_reference": "official:institution:00001",
        "institution_resolution_reason": "verified_identity",
        "institution_candidate_ownership": side,
        "institution_evidence_status": "EXACT_EXTERNAL_ID",
    }


def test_no_op_when_institutional_stage_disabled() -> None:
    source = pd.DataFrame([row()]).drop(columns=[c for c in row() if c.startswith("institution_")])
    assert refine_verified_institutional_io(source) is source
    assert ownership_io_metadata(source) is None


@pytest.mark.parametrize(
    ("domain", "side", "io16", "io80", "io240"),
    [
        ("education", "public", "14", "74", "229"),
        ("education", "private", "14", "75", "230"),
        ("health", "public", "15", "76", "231"),
        ("health", "private", "15", "77", "232"),
    ],
)
def test_verified_ownership_refines_exact_pairs_only(
    domain: str, side: str, io16: str, io80: str, io240: str
) -> None:
    original = pd.DataFrame([row(domain, side)])
    out = refine_verified_institutional_io(original)
    assert out.at[0, "io_ownership_refinement_status"] == "REFINED"
    assert out.at[0, "io16_refined_codes"] == io16
    assert out.at[0, "io80_refined_codes"] == io80
    assert out.at[0, "io240_refined_codes"] == io240
    assert out.at[0, "io80_refined_map_code"] == io80
    assert out.at[0, "io240_refined_map_code"] == io240
    assert out.at[0, "io_ownership_refinement_reference"] == "official:institution:00001"
    for col in original.columns:
        assert original[col].equals(out[col]), col
    assert len(out) == len(original)
    assert set(out) - set(original) == set(REFINED_FIELDS)
    manifest = ownership_io_metadata(out)
    assert manifest and manifest["refined_rows"] == 1 and manifest["policy"] == POLICY


def test_multiple_entities_preserve_order_and_all_columns() -> None:
    values = [row("health", "private"), row("education", "public"), row("health", "public")]
    original = pd.DataFrame(values, index=[18, 4, 97])
    out = refine_verified_institutional_io(original)
    assert out.index.tolist() == [18, 4, 97]
    assert out["canonical_id"].tolist() == original["canonical_id"].tolist()
    assert out["io240_refined_codes"].tolist() == ["232", "229", "231"]
    for c in original:
        assert original[c].equals(out[c]), c
    assert original["io240_codes"].tolist() == ["231; 232", "229; 230", "231; 232"]


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("institution_resolution_status", "PROVISIONAL", "no_verified_ownership"),
        (
            "institution_evidence_status",
            "EXPLICIT_SOURCE_CLAIM",
            "institution_evidence_not_verifiable",
        ),
        ("institution_resolution_reference", "", "institution_provenance_incomplete"),
        ("institution_resolution_basis", "", "institution_provenance_incomplete"),
        ("psic_status", "REVIEW", "psic_not_single"),
        ("psic_status", "SUBTREE", "psic_not_single"),
        ("io_mapping_status", "PROVISIONAL_GROUP", "mapping_not_confirmed_at_specific_level"),
        ("io_mapping_status", "MAPPED_SECTION", "mapping_not_confirmed_at_specific_level"),
        ("io_mapping_bridge_basis", "draft", "bridge_not_reviewed"),
        ("io_mapping_psic_code", "47", "unsupported_psic_domain"),
        ("io_mapping_psic_level", "section", "unsupported_psic_domain"),
        ("psic_code", "471", "unsupported_psic_domain"),
        ("io240_codes", "229; 230; 231", "candidate_set_not_exact_pair"),
        ("io80_codes", "74; 75; 76", "candidate_set_not_exact_pair"),
        ("io16_codes", "14; 15", "candidate_set_not_exact_pair"),
        ("io80_map_code", "75", "preexisting_io_resolution"),
        ("io240_map_code", "229", "preexisting_io_resolution"),
        ("io80_map_status", "NAME_MATCH_RESOLVED", "io_candidate_status_not_ambiguous"),
    ],
)
def test_ineligible_does_not_change_any_candidates(field: str, value: str, reason: str) -> None:
    data = row()
    data[field] = value
    if field == "institution_resolution_status":
        data["institution_resolved_ownership"] = ""
    original = pd.DataFrame([data])
    out = refine_verified_institutional_io(original)
    assert out.at[0, "io_ownership_refinement_status"] == "ABSTAIN"
    assert out.at[0, "io_ownership_refinement_reason"] == reason
    assert out.at[0, "io80_refined_codes"] == data["io80_codes"]
    assert out.at[0, "io240_refined_codes"] == data["io240_codes"]
    for col in original.columns:
        assert original[col].equals(out[col]), col


def test_unresolved_with_nonempty_verified_value_rejected() -> None:
    r = row()
    r["institution_resolution_status"] = "REVIEW"
    with pytest.raises(ValueError, match="unresolved institution"):
        refine_verified_institutional_io(pd.DataFrame([r]))


@pytest.mark.parametrize("owner", ["", "corporate", "PUBLIC-PRIVATE"])
def test_invalid_resolved_owner_fails(owner: str) -> None:
    r = row()
    r["institution_resolved_ownership"] = owner
    with pytest.raises(ValueError, match="invalid ownership"):
        refine_verified_institutional_io(pd.DataFrame([r]))


@pytest.mark.parametrize("bad", ["io240_refined_codes", "io_ownership_refinement_status"])
def test_does_not_overwrite_existing_refinement(bad: str) -> None:
    r = row()
    r[bad] = "OLD"
    with pytest.raises(ValueError, match="already exist"):
        refine_verified_institutional_io(pd.DataFrame([r]))


def test_partial_institutional_fields_fail() -> None:
    r = row()
    r.pop("institution_resolution_basis")
    with pytest.raises(ValueError, match="partial institutional"):
        refine_verified_institutional_io(pd.DataFrame([r]))


def test_missing_mapping_input_fails() -> None:
    r = row()
    r.pop("io_mapping_status")
    with pytest.raises(ValueError, match="crosswalk fields"):
        refine_verified_institutional_io(pd.DataFrame([r]))


def test_duplicate_canonical_ids_fail() -> None:
    with pytest.raises(ValueError, match="canonical_id"):
        refine_verified_institutional_io(pd.DataFrame([row(), row()]))


def test_nonunique_dataframe_index_fails() -> None:
    with pytest.raises(ValueError, match="index"):
        refine_verified_institutional_io(pd.DataFrame([row(), row("health")], index=[0, 0]))


@pytest.mark.parametrize("bad", ["74; 74", "74;; 75", "74;"])
def test_malformed_candidate_set_fails(bad: str) -> None:
    r = row()
    r["io80_codes"] = bad
    with pytest.raises(ValueError, match="invalid or duplicate"):
        refine_verified_institutional_io(pd.DataFrame([r]))


def test_bundled_reference_pairs_verified_at_runtime() -> None:
    pairs = _verified_pairs()
    assert pairs["education"]["sides"]["public"] == ("74", "229")
    assert pairs["education"]["sides"]["private"] == ("75", "230")
    assert pairs["health"]["sides"]["public"] == ("76", "231")
    assert pairs["health"]["sides"]["private"] == ("77", "232")


def test_pipeline_hook_is_after_ownership_before_export() -> None:
    import placetype_ph.pipeline as pipeline

    source = inspect.getsource(pipeline.classify_openplaces)
    assert (
        source.index("summary = maybe_resolve_institutions(summary, frame)")
        < source.index("summary = refine_verified_institutional_io(summary)")
        < source.index('summary_path = out_dir / "classified_pois.parquet"')
    )
