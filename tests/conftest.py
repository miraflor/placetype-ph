from __future__ import annotations

import pytest

from placetype_ph.models import TaxonomyNode
from placetype_ph.taxonomy import Taxonomy


@pytest.fixture
def toy_psic() -> Taxonomy:
    rows = [
        TaxonomyNode("psic", "rev5", "A", "section", "Section A"),
        TaxonomyNode("psic", "rev5", "10", "division", "Food", "A"),
        TaxonomyNode("psic", "rev5", "101", "group", "Processing", "10"),
        TaxonomyNode("psic", "rev5", "1011", "class", "Bakery manufacture", "101"),
        TaxonomyNode("psic", "rev5", "10111", "subclass", "Bread manufacture", "1011"),
        TaxonomyNode("psic", "rev5", "10112", "subclass", "Cake manufacture", "1011"),
        TaxonomyNode("psic", "rev5", "102", "group", "Other food", "10"),
        TaxonomyNode("psic", "rev5", "1021", "class", "Other food activity", "102"),
        TaxonomyNode("psic", "rev5", "10210", "subclass", "Other food subclass", "1021"),
        TaxonomyNode("psic", "rev5", "B", "section", "Section B"),
        TaxonomyNode("psic", "rev5", "20", "division", "Trade", "B"),
        TaxonomyNode("psic", "rev5", "201", "group", "Retail", "20"),
        TaxonomyNode("psic", "rev5", "2011", "class", "Retail bakery", "201"),
        TaxonomyNode("psic", "rev5", "20110", "subclass", "Retail bakery subclass", "2011"),
    ]
    return Taxonomy(rows)
