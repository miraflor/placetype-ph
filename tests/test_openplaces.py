from __future__ import annotations

import pandas as pd

from placetype_ph.openplaces import source_evidence


def test_overture_fsq_provenance_groups_dependence():
    row = pd.Series(
        {
            "canonical_id": "x",
            "fsq_name": "ABC",
            "fsq_category": "Bakery",
            "overture_name": "ABC Bakery",
            "overture_category": "bakery",
            "osm_name": "ABC",
            "osm_category": "shop=bakery",
            "overture_has_foursquare_provenance": True,
        }
    )
    evidence = source_evidence(row)
    groups = {e.source: e.dependency_group for e in evidence}
    assert groups["fsq"] == groups["overture"] == "fsq-lineage"
    assert groups["osm"] == "osm"
