from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("pyarrow")

from placetype_ph.classifier import EntityClassifier
from placetype_ph.crosswalk import Crosswalk
from placetype_ph.pipeline import classify_openplaces


def test_end_to_end_deterministic(tmp_path: Path, toy_psic):
    input_path = tmp_path / "canonical_pois.parquet"
    pd.DataFrame(
        [
            {
                "canonical_id": "fsq:1",
                "canonical_name": "ABC Bakery",
                "lon": 122.0,
                "lat": 11.0,
                "fsq_name": "ABC Bakery",
                "fsq_category": "Bakery",
                "osm_name": "ABC Bakery",
                "osm_category": "shop=bakery",
                "overture_has_foursquare_provenance": False,
            }
        ]
    ).to_parquet(input_path, index=False)

    cw_path = tmp_path / "crosswalk.csv"
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
    ).to_csv(cw_path, index=False)
    classifier = EntityClassifier(toy_psic, Crosswalk.load([cw_path]))

    classifications, summary = classify_openplaces(
        input_path, {"psic": classifier}, tmp_path / "out"
    )
    c = pd.read_parquet(classifications)
    s = pd.read_parquet(summary)
    assert c.loc[0, "code"] == "1011"
    assert s.loc[0, "psic_code"] == "1011"
