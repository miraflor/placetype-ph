from __future__ import annotations

from placetype_ph.models import TaxonomyNode
from placetype_ph.retrieval import TaxonomyRetriever
from placetype_ph.suggest import (
    _auto_proposal_block_reason,
    _has_multiple_source_components,
    category_plan,
    category_query,
    suggest_mapping,
)
from placetype_ph.taxonomy import Taxonomy


def test_category_query_normalizes_openplaces_vocabularies():
    assert category_query("overture", "hardware_store") == "hardware store"
    assert category_query("osm", "shop=car_repair") == "car repair shop"
    assert "Bakery" in category_query("fsq", "[Dining and Drinking > Bakery]")


def test_source_ontology_rewrites_activity_and_constrains_branch():
    assert category_plan("overture", "lodging").branch_roots == ("55",)
    assert "accommodation" in category_plan("overture", "lodging").query_text
    assert category_plan("osm", "shop=hardware").branch_roots == ("4752",)
    assert category_plan("osm", "amenity=school").branch_roots == ("85",)
    assert category_plan("overture", "automotive_repair").branch_roots == ("953",)
    assert category_plan("overture", "gas_station").branch_roots == ("473",)
    assert category_plan("fsq", "[Retail > Pharmacy]").branch_roots == ("4772",)


def test_high_precision_place_category_is_only_a_suggestion(toy_psic):
    result = suggest_mapping(
        toy_psic, TaxonomyRetriever(toy_psic), "osm", "man_made=bridge"
    )
    assert result.suggested_kind == "NOT_ACTIVITY"
    assert result.review_status == "REVIEW_REQUIRED"


def test_ambiguous_retrieval_keeps_candidates_without_forcing_mapping(toy_psic):
    result = suggest_mapping(toy_psic, TaxonomyRetriever(toy_psic), "overture", "bakery")
    assert result.candidate_codes
    assert result.suggested_kind == ""
    assert result.review_status == "REVIEW_CANDIDATES"


def _live_shape_taxonomy() -> Taxonomy:
    return Taxonomy(
        [
            TaxonomyNode("psic", "rev5", "A", "section", "Agriculture"),
            TaxonomyNode("psic", "rev5", "02", "division", "Forestry and Logging", "A"),
            TaxonomyNode("psic", "rev5", "022", "group", "Logging", "02"),
            TaxonomyNode("psic", "rev5", "0220", "class", "Logging", "022"),
            TaxonomyNode("psic", "rev5", "I", "section", "Accommodation and Food Service"),
            TaxonomyNode("psic", "rev5", "55", "division", "Accommodation", "I"),
            TaxonomyNode(
                "psic", "rev5", "551", "group", "Hotels and similar accommodation", "55"
            ),
            TaxonomyNode(
                "psic", "rev5", "5510", "class", "Hotels and similar accommodation", "551"
            ),
            TaxonomyNode("psic", "rev5", "G", "section", "Wholesale and Retail Trade"),
            TaxonomyNode("psic", "rev5", "47", "division", "Retail Trade", "G"),
            TaxonomyNode(
                "psic", "rev5", "475", "group", "Retail sale of household goods", "47"
            ),
            TaxonomyNode(
                "psic",
                "rev5",
                "4752",
                "class",
                "Retail sale of hardware, building materials, paints and glass",
                "475",
            ),
            TaxonomyNode(
                "psic",
                "rev5",
                "47521",
                "subclass",
                "Retail sale of hardware and electrical materials and supplies",
                "4752",
            ),
        ]
    )


def test_lodging_rewrite_does_not_retrieve_logging():
    taxonomy = _live_shape_taxonomy()
    result = suggest_mapping(
        taxonomy, TaxonomyRetriever(taxonomy), "overture", "lodging", min_score=1.0
    )
    assert result.branch_codes == "55"
    assert result.candidate_codes
    assert "022" not in result.candidate_codes
    assert all(code == "55" or code.startswith("55") for code in result.candidate_codes.split("|"))


def test_osm_shop_hardware_stays_inside_retail_hardware_branch():
    taxonomy = _live_shape_taxonomy()
    result = suggest_mapping(
        taxonomy, TaxonomyRetriever(taxonomy), "osm", "shop=hardware", min_score=1.0
    )
    assert result.branch_codes == "4752"
    assert result.candidate_codes
    assert all(
        code == "4752" or code.startswith("4752")
        for code in result.candidate_codes.split("|")
    )


def _retail_shape_taxonomy() -> Taxonomy:
    return Taxonomy(
        [
            TaxonomyNode("psic", "rev5", "G", "section", "Wholesale and Retail Trade"),
            TaxonomyNode("psic", "rev5", "47", "division", "Retail Trade", "G"),
            TaxonomyNode("psic", "rev5", "471", "group", "Non-specialized retail sale", "47"),
            TaxonomyNode(
                "psic",
                "rev5",
                "4711",
                "class",
                "Non-specialized retail sale with food, beverages or tobacco predominating",
                "471",
            ),
            TaxonomyNode(
                "psic",
                "rev5",
                "47114",
                "subclass",
                "Retail selling in convenience stores",
                "4711",
            ),
            TaxonomyNode(
                "psic",
                "rev5",
                "47119",
                "subclass",
                "Non-specialized retail sale with food, beverages or tobacco predominating, n.e.c.",
                "4711",
            ),
        ]
    )


def test_remaining_live_rewrites_are_branch_constrained():
    assert category_plan("overture", "bakery").branch_roots == ("1071", "47214")
    assert category_plan("overture", "party_and_event_planning").branch_roots == (
        "96901",
        "823",
        "5621",
    )
    assert category_plan("overture", "social_or_community_service").branch_roots == (
        "88",
    )
    assert category_plan(
        "fsq", "[Retail > Computers and Electronics Retail > Electronics Store]"
    ).branch_roots == ("474",)


def test_broad_ontology_buckets_are_explicitly_uncodeable(toy_psic):
    retriever = TaxonomyRetriever(toy_psic)
    for source, value in (
        ("overture", "community_and_government"),
        ("fsq", "[Business and Professional Services > Office]"),
        ("fsq", "[Travel and Transportation]"),
    ):
        result = suggest_mapping(toy_psic, retriever, source, value)
        assert result.suggested_kind == "UNCODEABLE"
        assert result.candidate_codes == ""
        assert result.review_status == "REVIEW_REQUIRED"


def test_strong_leaf_hit_is_exact_not_subtree():
    taxonomy = _retail_shape_taxonomy()
    result = suggest_mapping(
        taxonomy,
        TaxonomyRetriever(taxonomy),
        "overture",
        "convenience_store",
        min_score=0.0,
        min_margin=-1.0,
    )
    assert result.suggested_codes == "47114"
    assert result.suggested_kind == "EXACT"


def test_strong_internal_hit_remains_subtree():
    taxonomy = _retail_shape_taxonomy()
    result = suggest_mapping(
        taxonomy,
        TaxonomyRetriever(taxonomy),
        "overture",
        "shopping",
        min_score=0.0,
        min_margin=-1.0,
    )
    assert result.suggested_codes == "47"
    assert result.suggested_kind == "SUBTREE"


def test_fsq_space_does_not_match_spa():
    spa = category_plan(
        "fsq",
        "[Business and Professional Services > Health and Beauty Service > Spa]",
    )
    cowork = category_plan(
        "fsq",
        "[Business and Professional Services > Office > Coworking Space]",
    )
    event = category_plan(
        "fsq",
        "[Business and Professional Services > Event Space]",
    )
    assert spa.rule == "fsq:beauty"
    assert cowork.rule != "fsq:beauty"
    assert event.rule != "fsq:beauty"


def test_fsq_sports_categories_are_branch_constrained():
    court = category_plan(
        "fsq",
        "[Sports and Recreation > Basketball > Basketball Court]",
    )
    gym = category_plan(
        "fsq",
        "[Sports and Recreation > Gym and Studio > Gym]",
    )
    broad = category_plan("fsq", "[Sports and Recreation]")

    assert court.branch_roots == ("93112",)
    assert court.rule == "fsq:sports_court"
    assert gym.branch_roots == ("93111",)
    assert gym.rule == "fsq:fitness"
    assert broad.branch_roots == ()
    assert broad.rule == "raw_category"


def test_fsq_spa_in_multi_category_value_still_matches_beauty():
    plan = category_plan(
        "fsq",
        "[Business and Professional Services > Health and Beauty Service > Spa, "
        "Business and Professional Services > Health and Beauty Service > Massage Clinic]",
    )
    assert plan.rule == "fsq:beauty"
    assert plan.branch_roots == ("962",)


def test_v5_compound_source_detection():
    assert _has_multiple_source_components(
        "fsq",
        "[Retail > Convenience Store, Retail > Market]",
    )
    assert _has_multiple_source_components(
        "fsq",
        "['Retail > Fashion Retail > Children\\'s Clothing Store', "
        "Retail > Fashion Retail > Shoe Store]",
    )
    assert not _has_multiple_source_components(
        "fsq",
        "[Dining and Drinking > Cafe, Coffee, and Tea House > Coffee Shop]",
    )
    assert _has_multiple_source_components(
        "osm",
        "shop=trade | craft=roofer",
    )
    assert _has_multiple_source_components(
        "overture",
        "bank_or_credit_union",
    )


def test_v5_candidate_only_guards():
    raw = category_plan("overture", "insurance_agency")
    assert _auto_proposal_block_reason(
        "overture",
        "insurance_agency",
        raw,
    ) == "raw_category"

    compound = category_plan(
        "fsq",
        "[Retail > Convenience Store, Retail > Market]",
    )
    assert _auto_proposal_block_reason(
        "fsq",
        "[Retail > Convenience Store, Retail > Market]",
        compound,
    ) == "compound_source"

    store = category_plan("overture", "pet_store")
    assert _auto_proposal_block_reason(
        "overture",
        "pet_store",
        store,
    ) == "heuristic_store_suffix"

    pet = category_plan("osm", "shop=pet")
    assert _auto_proposal_block_reason(
        "osm",
        "shop=pet",
        pet,
    ) == "ambiguous_pet_shop"

    safe = category_plan("overture", "convenience_store")
    assert _auto_proposal_block_reason(
        "overture",
        "convenience_store",
        safe,
    ) is None


def test_v5_mixed_activity_and_non_activity_is_not_forced_non_activity(toy_psic):
    result = suggest_mapping(
        toy_psic,
        TaxonomyRetriever(toy_psic),
        "fsq",
        "[Landmarks and Outdoors > Structure, Retail > Hardware Store]",
    )
    assert result.suggested_kind != "NOT_ACTIVITY"

