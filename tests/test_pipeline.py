from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("pyarrow")

from placetype_ph.classifier import EntityClassifier
from placetype_ph.crosswalk import Crosswalk
from placetype_ph.pipeline import classify_openplaces


def _bakery_rows(count: int) -> list[dict]:
    return [
        {
            "canonical_id": f"fsq:{i}",
            "canonical_name": f"ABC Bakery {i}",
            "lon": 122.0,
            "lat": 11.0,
            "fsq_name": f"ABC Bakery {i}",
            "fsq_category": "Bakery",
            "osm_name": f"ABC Bakery {i}",
            "osm_category": "shop=bakery",
            "overture_has_foursquare_provenance": False,
        }
        for i in range(count)
    ]


def _bakery_crosswalk(path: Path) -> Crosswalk:
    pd.DataFrame(
        [
            {
                "source": "fsq",
                "source_value": "Bakery",
                "scheme": "psic",
                "version": "rev5",
                "mapping_kind": "SUBTREE",
                "codes": "1011",
                "match_type": "exact",
            },
            {
                "source": "osm",
                "source_value": "shop=bakery",
                "scheme": "psic",
                "version": "rev5",
                "mapping_kind": "SUBTREE",
                "codes": "1011",
                "match_type": "exact",
            },
        ]
    ).to_csv(path, index=False)
    return Crosswalk.load([path])


def test_end_to_end_deterministic(tmp_path: Path, toy_psic):
    input_path = tmp_path / "canonical_pois.parquet"
    pd.DataFrame(_bakery_rows(1)).to_parquet(input_path, index=False)
    classifier = EntityClassifier(toy_psic, _bakery_crosswalk(tmp_path / "crosswalk.csv"))

    classifications, summary = classify_openplaces(
        input_path, {"psic": classifier}, tmp_path / "out"
    )
    c = pd.read_parquet(classifications)
    s = pd.read_parquet(summary)
    assert c.loc[0, "code"] == "1011"
    assert s.loc[0, "psic_code"] == "1011"


def test_parallel_deterministic_matches_serial(tmp_path: Path, toy_psic):
    input_path = tmp_path / "canonical_pois.parquet"
    pd.DataFrame(_bakery_rows(12)).to_parquet(input_path, index=False)
    crosswalk = _bakery_crosswalk(tmp_path / "crosswalk.csv")
    serial = EntityClassifier(toy_psic, crosswalk)
    parallel = EntityClassifier(toy_psic, crosswalk)

    serial_classifications, serial_summary = classify_openplaces(
        input_path, {"psic": serial}, tmp_path / "serial", workers=1
    )
    parallel_classifications, parallel_summary = classify_openplaces(
        input_path, {"psic": parallel}, tmp_path / "parallel", workers=2
    )

    pd.testing.assert_frame_equal(
        pd.read_parquet(serial_classifications),
        pd.read_parquet(parallel_classifications),
    )
    pd.testing.assert_frame_equal(
        pd.read_parquet(serial_summary),
        pd.read_parquet(parallel_summary),
    )
    manifest = json.loads((tmp_path / "parallel" / "run.json").read_text(encoding="utf-8"))
    assert manifest["workers"] == 2


def test_parallel_merges_crosswalk_ambiguity_diagnostics(tmp_path: Path, toy_psic):
    input_path = tmp_path / "canonical_pois.parquet"
    rows = _bakery_rows(4)
    for row in rows:
        row["fsq_category"] = "ABC Bakery"
        row["osm_category"] = None
    pd.DataFrame(rows).to_parquet(input_path, index=False)

    cw_path = tmp_path / "ambiguous.csv"
    pd.DataFrame(
        [
            {
                "source": "fsq",
                "source_value": "Bakery",
                "scheme": "psic",
                "version": "rev5",
                "mapping_kind": "SUBTREE",
                "codes": "1011",
                "match_type": "contains",
            },
            {
                "source": "fsq",
                "source_value": "ABC",
                "scheme": "psic",
                "version": "rev5",
                "mapping_kind": "SUBTREE",
                "codes": "2011",
                "match_type": "contains",
            },
        ]
    ).to_csv(cw_path, index=False)
    classifier = EntityClassifier(toy_psic, Crosswalk.load([cw_path]))

    classify_openplaces(input_path, {"psic": classifier}, tmp_path / "parallel", workers=2)

    assert classifier.ambiguity_rows == 4
    assert sum(classifier.ambiguities.values()) == 4
