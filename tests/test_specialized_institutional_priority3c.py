"""Priority 3c: exact, opt-in ownership stage in the normal tagging pipeline."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pandas as pd
import pytest

import placetype_ph.pipeline as pipeline
from placetype_ph.specialized.institutional_pipeline import (
    CONFIG_SCHEMA,
    ENVIRONMENT_KEY,
    load_reference_config,
    maybe_resolve_institutions,
    resolve_from_references,
)
from placetype_ph.specialized.institutional_promotion import NEW_FIELDS


def classified() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "canonical_id": "s1",
                "psic_code": "861",
                "psic_status": "SINGLE",
                "pcpc_code": "12345",
                "pscc_code": "22222",
                "specialized_status": "PRESERVED",
            }
        ]
    )


def places() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "canonical_id": "s1",
                "name": "Example General Hospital",
                "canonical_name": "Example General Hospital",
                "city": "Quezon City",
                "osm_category": "amenity=hospital",
                "nhfr_code": "NHFR-100",
            }
        ]
    )


def config(tmp_path: Path, refs: dict[str, str]) -> Path:
    target = tmp_path / "institution.json"
    target.write_text(json.dumps({"schema": CONFIG_SCHEMA, "references": refs}), encoding="utf-8")
    return target


def hospitals(tmp_path: Path) -> Path:
    target = tmp_path / "hospitals.csv"
    target.write_text(
        "source,source_id,name,domain,ownership,city,province,address,aliases,"
        "source_url,as_of,id_kind,evidence_type,facility_level\n"
        "doh_nhfr,NHFR-100,Example General Hospital,health,public,Quezon City,"
        "Metro Manila,Example Road,,https://nhfr.doh.gov.ph/,2026-11-01,"
        "official_code,explicit_sector,Level 3\n",
        encoding="utf-8",
    )
    return target


def test_disabled_is_exact_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENVIRONMENT_KEY, raising=False)
    old = classified()
    assert maybe_resolve_institutions(old, places()) is old


def test_enabled_adds_fields_and_keeps_existing_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = hospitals(tmp_path)
    settings = config(tmp_path, {"hospitals": str(ref)})
    monkeypatch.setenv(ENVIRONMENT_KEY, str(settings))
    old = classified()
    result = maybe_resolve_institutions(old, places())
    assert set(result.columns) == set(old.columns) | set(NEW_FIELDS)
    for column in old.columns:
        assert result[column].equals(old[column])
    assert result.at[0, "institution_resolution_status"] == "RESOLVED"
    assert result.at[0, "institution_resolved_ownership"] == "public"


def test_source_only_claim_not_verified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ref = hospitals(tmp_path)
    monkeypatch.setenv(ENVIRONMENT_KEY, str(config(tmp_path, {"hospitals": str(ref)})))
    raw = places()
    raw["nhfr_code"] = ""
    raw["osm_ownership"] = "public"
    result = maybe_resolve_institutions(classified(), raw)
    assert result.at[0, "institution_resolved_ownership"] == ""


def test_conflicting_ownership_never_overrides(tmp_path: Path) -> None:
    ref = hospitals(tmp_path)
    old = classified()
    old["ownership_candidate"] = "private"
    result = resolve_from_references(old, places(), references={"hospitals": ref})
    assert result.at[0, "institution_resolution_status"] == "REVIEW"
    assert result.at[0, "institution_resolved_ownership"] == ""


def test_existing_original_resolution_field_refuses_overwrite(tmp_path: Path) -> None:
    ref = hospitals(tmp_path)
    old = classified()
    old["institution_resolution_status"] = "MANUAL"
    with pytest.raises(ValueError, match="already present"):
        resolve_from_references(old, places(), references={"hospitals": ref})


def test_reordered_raw_inputs_preserve_classified_order(tmp_path: Path) -> None:
    ref = hospitals(tmp_path)
    old = classified()
    raw = places()
    new = resolve_from_references(old, raw, references={"hospitals": ref})
    assert new["canonical_id"].tolist() == old["canonical_id"].tolist()


@pytest.mark.parametrize(
    "bad",
    [
        {"schema": "incorrect", "references": {"beis": "C:/none.csv"}},
        {"schema": CONFIG_SCHEMA, "references": {}},
        {"schema": CONFIG_SCHEMA, "references": {"unexpected": "C:/x.csv"}},
        {"schema": CONFIG_SCHEMA, "references": {"hospitals": "relative.csv"}},
        {"schema": CONFIG_SCHEMA, "references": {"hospitals": None}},
    ],
)
def test_invalid_reference_config_is_rejected(tmp_path: Path, bad: dict) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises((ValueError, FileNotFoundError)):
        load_reference_config(path)


def test_empty_opt_in_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENVIRONMENT_KEY, "")
    with pytest.raises(ValueError, match="was set but is empty"):
        maybe_resolve_institutions(classified(), places())


def test_missing_reference_is_error(tmp_path: Path) -> None:
    cfg = config(tmp_path, {"hospitals": str(tmp_path / "missing.csv")})
    with pytest.raises(FileNotFoundError):
        load_reference_config(cfg)


def test_invalid_registry_data_fails_closed(tmp_path: Path) -> None:
    path = hospitals(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace("Level 3", "Level 1"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Level 3"):
        resolve_from_references(classified(), places(), references={"hospitals": path})


def test_pipeline_hook_is_after_enrichment_before_export() -> None:
    """Do not silently move the ownership stage ahead of authoritative tagging."""
    source = inspect.getsource(pipeline.classify_openplaces)
    enrich = source.index("summary = enrich_summary(summary, frame)")
    owner = source.index("summary = maybe_resolve_institutions(summary, frame)")
    export = source.index('summary_path = out_dir / "classified_pois.parquet"')
    assert enrich < owner < export


def test_pipeline_imports_follow_ruff_order() -> None:
    """Regression for Priority 3c installer import-order failure (Ruff I001)."""
    source = inspect.getsource(pipeline)
    expected = (
        "from .specialized.institutional_pipeline import maybe_resolve_institutions\n"
        "from .specialized.integration import enrich_summary\n"
    )
    assert expected in source
