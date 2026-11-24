"""National directory adapters retain conservative identity policy."""

import csv
import json

import pytest

from placetype_ph.specialized.institutional_pipeline import load_reference_config
from placetype_ph.specialized.institutional_registry import (
    HEIDA_FIELDS,
    HEIDA_URL,
    HOSPITAL_FIELDS,
    InstitutionIndex,
    load_heida,
    load_hospitals,
    load_level1_hospitals,
    load_level2_hospitals,
)
from placetype_ph.specialized.institutional_shadow import InstitutionResolver
from placetype_ph.specialized.institutional_tools import hospital_rows, level3_hospital_rows


def write(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def hei(**changes):
    return dict(
        source_id="00123 A", name="Example University",
        hei_type="Local Government College/University", ownership="public",
        address="Example City", source_url=HEIDA_URL, as_of="2026-11-04",
    ) | changes


def test_heida_code_kept_and_not_site_verified(tmp_path):
    records = load_heida(write(tmp_path / "hei.csv", HEIDA_FIELDS, [hei()]))
    assert records[0].source_id == "00123 A"
    resolver = InstitutionResolver(InstitutionIndex(records))
    result = resolver.assess(dict(
        name="Example University", psic_code="853", psic_status="RESOLVED",
        ched_hei_code="00123 A", city="Example City",
    ))
    assert result.status == "PROVISIONAL"
    assert not result.promotion_eligible


@pytest.mark.parametrize("changes", [{"source_id": "07XXX1"}, {"source_id": "NEW"},
                                     {"ownership": "private"},
                                     {"source_url": "https://example.org"}])
def test_invalid_heida(tmp_path, changes):
    with pytest.raises(ValueError):
        load_heida(write(tmp_path / "hei.csv", HEIDA_FIELDS, [hei(**changes)]))


def test_duplicate_hei_ids(tmp_path):
    with pytest.raises(ValueError):
        load_heida(write(tmp_path / "hei.csv", HEIDA_FIELDS, [hei(), hei(source_id="00123 a")]))


@pytest.mark.parametrize("level,loader", [(1, load_level1_hospitals), (2, load_level2_hospitals)])
def test_hospital_levels(tmp_path, level, loader):
    rows = [{"No": "123", "NAME OF HEALTH FACILITY": "Example Hospital", "CAT": f"LEVEL {level}",
             "SEC": "G", "MUNICIPALITY": "Example City", "STREET": "Main Road"}]
    args = dict(source="philhealth_hospitals", url="https://www.philhealth.gov.ph/hospitals.pdf",
                as_of="2026-09-30")
    converted, stats = hospital_rows(rows, level=level, **args)
    assert stats["included"] == 1
    path = write(tmp_path / "hosp.csv", HOSPITAL_FIELDS, converted)
    assert loader(path)[0].id_kind == "publication_row"
    other = load_level2_hospitals if level == 1 else load_level1_hospitals
    with pytest.raises(ValueError):
        other(path)
    assert hospital_rows([dict(rows[0], CAT="INFIRMARY")], level=level, **args)[0] == []
    assert len(load_hospitals(path)) == 1
    assert level3_hospital_rows(rows, **args)[0] == []
    rows[0]["SEC"] = ""
    with pytest.raises(ValueError):
        hospital_rows(rows, level=level, **args)


def test_config_accepts_new_keys(tmp_path):
    path = tmp_path / "heida.csv"
    path.touch()
    hospital = tmp_path / "level2.csv"
    hospital.touch()
    primary = tmp_path / "level1.csv"
    primary.touch()
    config = tmp_path / "institution_references.json"
    config.write_text(json.dumps({
        "schema": "placetype-ph-institution-reference-config-v1",
        "references": {
            "heida": str(path), "level1_hospitals": str(primary),
            "level2_hospitals": str(hospital),
        },
    }))
    assert load_reference_config(config) == {
        "heida": path, "level1_hospitals": primary, "level2_hospitals": hospital,
    }



def test_bundled_default_reference_selection(monkeypatch):
    from placetype_ph.specialized import institutional_tools as tools
    from placetype_ph.specialized.institutional_pipeline import (
        ENVIRONMENT_KEY,
        selected_reference_config,
    )

    monkeypatch.delenv(ENVIRONMENT_KEY, raising=False)
    manifest, paths = selected_reference_config()
    assert manifest == tools.REGISTRY_DATA / "manifest.json"
    counts = {
        "heida": 2424, "hospitals": 1338, "unifast": 1955, "beis": 30364,
    }
    assert set(paths) == set(counts)
    entries = []
    for label, path in paths.items():
        records = tools.LOADERS[label](path)
        assert len(records) == counts[label]
        entries.extend(records)
    assert len(InstitutionIndex(entries).entries) == 36081
    report = tools.verify_bundled()
    assert set(report) == {path.name for path in paths.values()}
    assert report["philhealth_hospitals_20260930.csv"]["rows"] == 1338
    with paths["hospitals"].open(encoding="utf-8", newline="") as handle:
        hospital_rows_actual = list(csv.DictReader(handle))
    from collections import Counter

    assert Counter((r["facility_level"], r["ownership"]) for r in hospital_rows_actual) == {
        ("Level 1", "public"): 326, ("Level 1", "private"): 455,
        ("Level 2", "public"): 79, ("Level 2", "private"): 353,
        ("Level 3", "public"): 63, ("Level 3", "private"): 62,
    }


def test_bundled_default_and_explicit_off(monkeypatch):
    from placetype_ph.specialized import institutional_pipeline as pipeline

    monkeypatch.delenv(pipeline.ENVIRONMENT_KEY, raising=False)
    seen = {}

    def resolve(classified, places, *, references):
        seen.update(references)
        return "enriched"

    monkeypatch.setattr(pipeline, "resolve_from_references", resolve)
    original = object()
    assert pipeline.maybe_resolve_institutions(original, object()) == "enriched"
    assert set(seen) == pipeline.BUNDLED_KEYS
    monkeypatch.setenv(pipeline.ENVIRONMENT_KEY, "off")
    assert pipeline.maybe_resolve_institutions(original, object()) is original


def test_bundled_install_defaults_to_all_sources(tmp_path):
    from placetype_ph.specialized import institutional_tools as tools
    from placetype_ph.specialized.institutional_pipeline import BUNDLED_KEYS

    config = tools.install_references(tmp_path / "refs")
    assert set(load_reference_config(config)) == BUNDLED_KEYS


def test_bundled_default_rejects_corruption(monkeypatch, tmp_path):
    import shutil

    from placetype_ph.specialized import institutional_tools as tools
    from placetype_ph.specialized.institutional_pipeline import (
        ENVIRONMENT_KEY,
        selected_reference_config,
    )

    monkeypatch.delenv(ENVIRONMENT_KEY, raising=False)
    destination = tmp_path / "registry"
    shutil.copytree(tools.REGISTRY_DATA, destination)
    monkeypatch.setattr(tools, "REGISTRY_DATA", destination)
    path = destination / "philhealth_hospitals_20260930.csv"
    path.write_bytes(path.read_bytes() + b"corrupt")
    with pytest.raises(ValueError, match="manifest hash"):
        selected_reference_config()
