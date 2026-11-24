"""Regression and failure-path tests for classification completion hardening."""

import json
import os

import pandas as pd
import pytest

from placetype_ph.augment import augment_places


def _supplied(**updates):
    return {"source_id": "r1", "name": "Supplied", "lon": 121.2, "lat": 14.7,
            "canonical_id": "p1", **updates}


def test_duplicate_target_rejected_without_mutation():
    places = pd.DataFrame([{"canonical_id": "p1", "sources": "osm"}])
    before = places.copy(deep=True)
    supplied = pd.DataFrame([_supplied(), _supplied(source_id="r2")])
    with pytest.raises(ValueError, match="duplicate target canonical_id"):
        augment_places(places, supplied, source="registry")
    pd.testing.assert_frame_equal(places, before)


def test_matched_semantic_fields_preserved_and_coordinates_unchanged():
    places = pd.DataFrame([{"canonical_id": "p1", "sources": "osm", "lon": 121.0,
                            "lat": 14.0, "osm_category": "amenity=cafe"}])
    supplied = pd.DataFrame([_supplied(osm_category="amenity=cafe",
                                       overture_category="cafe")])
    out, report = augment_places(places, supplied, source="registry")
    assert report["matched"] == 1
    row = out.iloc[0]
    assert row["osm_category"] == "amenity=cafe"
    assert row["overture_category"] == "cafe"
    assert row["sources"] == "osm|user:registry"
    assert (row["lon"], row["lat"]) == (121.0, 14.0)
    assert row["aug_registry__source_id"] == "r1"
    assert row["aug_registry__name"] == "Supplied"
    assert row["aug_registry__lon"] == 121.2
    assert row["aug_registry__lat"] == 14.7
    assert "overture_category" not in places.columns


def test_conflicting_source_evidence_rejected():
    places = pd.DataFrame([{"canonical_id": "p1", "osm_category": "amenity=cafe"}])
    before = places.copy(deep=True)
    with pytest.raises(ValueError, match="conflicting osm_category"):
        augment_places(places, pd.DataFrame([_supplied(osm_category="amenity=bank")]),
                       source="registry")
    pd.testing.assert_frame_equal(places, before)


@pytest.mark.parametrize("rows, error", [
    ([_supplied(lat=91)], "out of range"),
    ([_supplied(), _supplied(canonical_id="")], "duplicate source_id"),
    ([_supplied(canonical_id="unknown")], "not in the places"),
])
def test_in_memory_validation_is_not_bypassed(rows, error):
    with pytest.raises(ValueError, match=error):
        augment_places(pd.DataFrame([{"canonical_id": "p1"}]), pd.DataFrame(rows),
                       source="registry")


def _reference_config(tmp_path, monkeypatch):
    from placetype_ph.specialized.institutional_pipeline import CONFIG_SCHEMA, ENVIRONMENT_KEY

    beis = tmp_path / "beis.csv"
    beis.write_text("school_id\n123456\n", encoding="utf-8")
    links = tmp_path / "verified.json"
    links.write_text("{}", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"schema": CONFIG_SCHEMA, "references": {
        "beis": str(beis), "verified_links": str(links),
    }}), encoding="utf-8")
    monkeypatch.setenv(ENVIRONMENT_KEY, str(config))
    return config, beis, links


def test_cache_fingerprints_all_reference_contents(tmp_path, monkeypatch):
    from placetype_ph.express import institutional_reference_state, pipeline_state
    from placetype_ph.specialized.institutional_pipeline import ENVIRONMENT_KEY

    monkeypatch.setenv(ENVIRONMENT_KEY, "off")
    assert institutional_reference_state() == {"enabled": False}
    disabled = pipeline_state()
    config, beis, links = _reference_config(tmp_path, monkeypatch)
    initial = pipeline_state()
    assert initial != disabled
    prior = beis.stat()
    beis.write_text("school_id\n654321\n", encoding="utf-8")
    os.utime(beis, ns=(prior.st_atime_ns, prior.st_mtime_ns))
    changed = pipeline_state()
    assert changed != initial
    links.write_text("[]", encoding="utf-8")
    assert pipeline_state() != changed
    previous = pipeline_state()
    config.write_text(config.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert pipeline_state() != previous
    assert pipeline_state("extended") != pipeline_state("strict")


def test_cache_invalid_config_fails_closed(tmp_path, monkeypatch):
    from placetype_ph.express import institutional_reference_state
    from placetype_ph.specialized.institutional_pipeline import ENVIRONMENT_KEY

    monkeypatch.setenv(ENVIRONMENT_KEY, "")
    with pytest.raises(ValueError, match="empty"):
        institutional_reference_state()
    _, beis, _ = _reference_config(tmp_path, monkeypatch)
    beis.unlink()
    with pytest.raises(FileNotFoundError):
        institutional_reference_state()
