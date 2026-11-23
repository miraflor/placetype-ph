from __future__ import annotations

import csv
from pathlib import Path

import pytest

from placetype_ph.specialized.projection import IndustryReferences
from placetype_ph.specialized.registry import load_registry
from placetype_ph.specialized import assess


def _csv(path: Path, fields: list[str], rows: list[tuple[str, ...]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(fields)
        writer.writerows(rows)
    return path


def _catalog(tmp_path: Path, refined: str = "001") -> IndustryReferences:
    bridge = _csv(tmp_path / "bridge.csv", ["rev5_code", "psic2019_codes", "basis"],
                  [("011", "011", "code_title_match")])
    concordance = _csv(tmp_path / "concordance.csv", ["psic_code", "io16_codes", "io80_codes", "io240_codes"],
                       [("011", "01", "01", "001; 002")])
    refine = _csv(tmp_path / "refinement.csv", ["rev5_code", "rev5_title", "io240_codes", "basis"],
                  [("01121", "Growing of rice", refined, "draft")])
    parents = _csv(tmp_path / "parents.csv", ["io240_code", "io80_code"],
                   [(f"{i:03d}", "01" if i < 3 else "02") for i in range(1, 241)])
    return IndustryReferences(bridge, concordance, refine, parents)


def test_projection_only_narrows_candidate_set(tmp_path):
    result = _catalog(tmp_path).project("01121", psic_title="Growing of rice")
    assert result.io240 == ("001",)
    assert result.io80 == ("01",)
    assert result.provisional
    assert result.status == "CANDIDATES"


def test_projection_rejects_widening(tmp_path):
    result = _catalog(tmp_path, "003").project("01121")
    assert result.status == "REFINEMENT_WOULD_WIDEN"
    assert result.io240 == ("001", "002")


def test_projection_rejects_wrong_title(tmp_path):
    result = _catalog(tmp_path).project("01121", psic_title="Growing of corn")
    assert result.status == "REFINEMENT_TITLE_CONFLICT"
    assert result.io240 == ("001", "002")


def test_nonactivity_and_not_codeable_remain_distinct():
    assert assess({"overture_category": "bridge"}).status == "NOT_PSIC_ACTIVITY"
    assert assess({"overture_category": "shopping_mall"}).status == "UNCODEABLE"


def test_registry_duplicate_key_is_error(tmp_path):
    path = tmp_path / "r.csv"
    path.write_text("source,official_id,name,city,domain,ownership,source_url\n"
                    "health_index,1,One,City,health,public,https://example.org/1\n"
                    "health_index,1,Another,City,health,private,https://example.org/2\n")
    with pytest.raises(ValueError, match="duplicate source/official ID"):
        load_registry(path)


def test_external_id_requires_source_namespace(tmp_path):
    from placetype_ph.specialized.registry import RegistryRecord, match_registry
    item = RegistryRecord("health_index", "17", "Central Hospital", "City", "health", "public", "https://example.org")
    anonymous = match_registry({"name": "Central Hospital", "city": "City", "official_id": "17"}, (item,), "health")
    assert anonymous.status == "NAME_CITY_PROVISIONAL"
    explicit = match_registry({"name": "Central Hospital", "city": "City", "official_id": "17", "official_id_source": "health_index"}, (item,), "health")
    assert explicit.status == "EXTERNAL_ID_SITE_MATCH"
