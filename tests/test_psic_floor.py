from __future__ import annotations

from placetype_ph.models import TaxonomyNode
from placetype_ph.retrieval import TaxonomyRetriever
from placetype_ph.suggest import category_plan, prepare_suggestion, suggest_mapping
from placetype_ph.taxonomy import Taxonomy


def _floor_taxonomy() -> Taxonomy:
    return Taxonomy(
        [
            TaxonomyNode("psic", "rev5", "G", "section", "Wholesale and Retail Trade"),
            TaxonomyNode("psic", "rev5", "47", "division", "Retail Trade", "G"),
            TaxonomyNode("psic", "rev5", "471", "group", "Non-specialized retail sale", "47"),
            TaxonomyNode("psic", "rev5", "K", "section", "Financial and Insurance Activities"),
            TaxonomyNode(
                "psic",
                "rev5",
                "64",
                "division",
                "Financial Service Activities, except Insurance and Pension Funding",
                "K",
            ),
            TaxonomyNode(
                "psic",
                "rev5",
                "R",
                "section",
                "Arts, Entertainment and Recreation",
            ),
            TaxonomyNode("psic", "rev5", "93", "division", "Sports activities", "R"),
            TaxonomyNode("psic", "rev5", "931", "group", "Sports activities", "93"),
            TaxonomyNode(
                "psic",
                "rev5",
                "9311",
                "class",
                "Operation of sports facilities",
                "931",
            ),
            TaxonomyNode(
                "psic",
                "rev5",
                "93112",
                "subclass",
                "Operation of courts for sports",
                "9311",
            ),
            TaxonomyNode("psic", "rev5", "Q", "section", "Human Health Activities"),
            TaxonomyNode("psic", "rev5", "86", "division", "Human Health Activities", "Q"),
            TaxonomyNode(
                "psic",
                "rev5",
                "862",
                "group",
                "Medical and dental practice activities",
                "86",
            ),
            TaxonomyNode(
                "psic",
                "rev5",
                "8622",
                "class",
                "Dental practice activities",
                "862",
            ),
            TaxonomyNode(
                "psic",
                "rev5",
                "86222",
                "subclass",
                "Private dental and laboratory services",
                "8622",
            ),
            TaxonomyNode("psic", "rev5", "I", "section", "Accommodation and Food Service"),
            TaxonomyNode(
                "psic",
                "rev5",
                "56",
                "division",
                "Food and beverage service activities",
                "I",
            ),
            TaxonomyNode(
                "psic",
                "rev5",
                "561",
                "group",
                "Restaurants and mobile food service activities",
                "56",
            ),
            TaxonomyNode(
                "psic",
                "rev5",
                "5610",
                "class",
                "Restaurants and mobile food service activities",
                "561",
            ),
        ]
    )


class _Hit:
    def __init__(self, code: str, score: float):
        self.code = code
        self.score = score


class _ScriptedRetriever:
    def __init__(self, ranking: list[tuple[str, float]]):
        self.ranking = ranking

    def search_hierarchical(self, query, *, top_n=5, branch_roots=()):
        return [_Hit(code, score) for code, score in self.ranking[:top_n]]


def test_dental_categories_keep_862_when_retrieval_cannot_refine():
    taxonomy = _floor_taxonomy()
    retriever = TaxonomyRetriever(taxonomy)

    for source, value in (
        ("fsq", "[Health and Medicine > Dentist]"),
        ("overture", "dental_clinic"),
    ):
        result = suggest_mapping(
            taxonomy,
            retriever,
            source,
            value,
            min_score=1.1,
            min_margin=1.1,
        )
        assert result.suggested_codes == "862"
        assert result.suggested_kind == "SUBTREE"
        assert result.review_status == "REVIEW_MAPPING"
        assert "floor:trusted_source_ontology" in result.suggestion_source


def test_strong_retrieval_can_refine_below_the_floor():
    taxonomy = _floor_taxonomy()
    result = suggest_mapping(
        taxonomy,
        _ScriptedRetriever([("86222", 0.95), ("862", 0.20)]),
        "overture",
        "dental_clinic",
    )
    assert result.suggested_codes == "86222"
    assert result.suggested_kind == "EXACT"
    assert "strong_separated_hit" in result.suggestion_source


def test_restaurant_suffix_keeps_coarse_floor_when_refinement_is_weak():
    taxonomy = _floor_taxonomy()
    result = suggest_mapping(
        taxonomy,
        TaxonomyRetriever(taxonomy),
        "overture",
        "thai_restaurant",
        min_score=1.1,
        min_margin=1.1,
    )
    assert result.suggested_codes == "561"
    assert result.suggested_kind == "SUBTREE"


def test_explicit_pet_store_rule_replaces_the_generic_store_suffix_heuristic():
    plan = category_plan("overture", "pet_store")
    assert plan.branch_roots == ("4776",)
    assert plan.rule == "overture:pet_store"


def test_generic_fsq_retail_bucket_keeps_only_the_coarse_retail_floor():
    taxonomy = _floor_taxonomy()
    prepared = prepare_suggestion(
        taxonomy,
        "fsq",
        "[Retail > Miscellaneous Store]",
    )
    assert prepared.branch_codes == ("47",)
    assert prepared.floor_code == "47"


def test_fsq_bank_rule_does_not_match_bankruptcy_text():
    taxonomy = _floor_taxonomy()
    bankruptcy = prepare_suggestion(
        taxonomy,
        "fsq",
        "[Business and Professional Services > Financial Service > "
        "Credit Counseling and Bankruptcy Services]",
    )
    bank = prepare_suggestion(
        taxonomy,
        "fsq",
        "[Business and Professional Services > Financial Service > "
        "Banking and Finance > Bank]",
    )
    assert bankruptcy.branch_codes == ()
    assert bankruptcy.floor_code is None
    assert bank.branch_codes == ("64",)
    assert bank.floor_code == "64"


def test_fsq_sports_court_remains_retrieval_only():
    taxonomy = _floor_taxonomy()
    prepared = prepare_suggestion(
        taxonomy,
        "fsq",
        "[Sports and Recreation > Basketball > Basketball Court]",
    )
    assert prepared.branch_codes == ("93112",)
    assert prepared.floor_code is None


def test_generic_clinic_suffix_does_not_become_a_human_health_floor():
    taxonomy = _floor_taxonomy()
    prepared = prepare_suggestion(
        taxonomy,
        "overture",
        "veterinary_clinic",
    )
    assert prepared.branch_codes == ("86",)
    assert prepared.floor_code is None


def test_broad_overture_health_bucket_keeps_only_the_human_health_floor():
    taxonomy = _floor_taxonomy()
    prepared = prepare_suggestion(taxonomy, "overture", "health_care")
    assert prepared.branch_codes == ("86",)
    assert prepared.floor_code == "86"


def test_multi_branch_rules_do_not_manufacture_a_floor():
    taxonomy = _floor_taxonomy()
    for value in ("bakery", "party_and_event_planning"):
        plan = category_plan("overture", value)
        prepared = prepare_suggestion(taxonomy, "overture", value)
        assert len(plan.branch_roots) != 1
        assert prepared.floor_code is None


def test_compound_fsq_and_osm_evidence_does_not_get_a_floor():
    taxonomy = _floor_taxonomy()
    fsq = prepare_suggestion(
        taxonomy,
        "fsq",
        "[Retail > Convenience Store, Retail > Market]",
    )
    osm = prepare_suggestion(
        taxonomy,
        "osm",
        "amenity=dentist | shop=convenience",
    )
    assert fsq.floor_code is None
    assert osm.floor_code is None


def test_explicit_osm_hairdresser_rule_replaces_the_generic_shop_branch():
    plan = category_plan("osm", "shop=hairdresser")
    assert plan.branch_roots == ("962",)
    assert plan.rule == "osm:shop=hairdresser"
