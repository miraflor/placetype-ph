from __future__ import annotations

import pandas as pd

from placetype_ph.classifier import EntityClassifier
from placetype_ph.models import TaxonomyNode
from placetype_ph.taxonomy import Taxonomy


def test_pscc_does_not_infer_from_place_name_alone():
    taxonomy = Taxonomy(
        [
            TaxonomyNode("pscc", "2022", "10", "chapter", "Cereals"),
            TaxonomyNode("pscc", "2022", "1006", "heading", "Rice", "10"),
        ]
    )
    row = pd.Series({"canonical_id": "x", "fsq_name": "Rice Shop", "fsq_category": "Grocery"})
    result = EntityClassifier(taxonomy).classify_row(row)
    assert result.status == "NO_PRODUCT_EVIDENCE"
    assert result.code is None
