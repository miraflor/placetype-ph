from __future__ import annotations

import pytest

from placetype_ph.models import TaxonomyNode
from placetype_ph.suggest import (
    _has_multiple_source_components,
    category_plan,
    prepare_suggestion,
)
from placetype_ph.taxonomy import Taxonomy

_CODES = {
    "10",
    "11",
    "181",
    "46",
    "47",
    "4711",
    "472",
    "474",
    "475",
    "4762",
    "477",
    "4771",
    "4772",
    "47734",
    "47736",
    "4776",
    "47762",
    "4783",
    "531",
    "532",
    "56",
    "561",
    "591",
    "61202",
    "62",
    "64",
    "6496",
    "68",
    "691",
    "731",
    "75",
    "78",
    "84",
    "86",
    "862",
    "869",
    "87",
    "88",
    "90",
    "910",
    "93293",
    "94",
    "951",
    "9532",
    "9534",
    "96",
    "961",
    "962",
    "963",
    "C",
    "F",
    "G",
    "H",
    "L",
    "N",
    "R",
}


def _taxonomy() -> Taxonomy:
    return Taxonomy(
        [
            TaxonomyNode("psic", "rev5", code, "section", f"Node {code}")
            for code in sorted(_CODES)
        ]
    )


@pytest.mark.parametrize(
    ("source", "value", "root"),
    [
        ("fsq", "[Health and Medicine > Medical Center]", "86"),
        ("fsq", "[Health and Medicine > Medical Lab]", "869"),
        (
            "fsq",
            "[Business and Professional Services > Health and Beauty Service > Nail Salon]",
            "962",
        ),
        ("fsq", "[Business and Professional Services > Laundry Service]", "961"),
        (
            "fsq",
            "[Business and Professional Services > Automotive Service > Car Wash and Detail]",
            "9534",
        ),
        ("fsq", "[Business and Professional Services > Employment Agency]", "78"),
        ("fsq", "[Business and Professional Services > Advertising Agency]", "731"),
        ("fsq", "[Business and Professional Services > Design Studio]", "N"),
        ("fsq", "[Retail]", "47"),
        ("fsq", "[Retail > Miscellaneous Store]", "47"),
        ("fsq", "[Retail > Cosmetics Store]", "4772"),
        ("fsq", "[Retail > Furniture and Home Store]", "475"),
        ("fsq", "[Retail > Fashion Retail > Shoe Store]", "4771"),
        ("fsq", "[Retail > Fashion Retail > Jewelry Store]", "47734"),
        ("fsq", "[Retail > Food and Beverage Retail]", "472"),
        ("fsq", "[Retail > Sporting Goods Retail]", "4762"),
        ("fsq", "[Retail > Pet Supplies Store]", "47762"),
        ("fsq", "[Retail > Automotive Retail > Motorcycle Dealership]", "4783"),
        ("fsq", "[Arts and Entertainment > Internet Cafe]", "61202"),
        ("fsq", "[Arts and Entertainment > Movie Theater]", "591"),
        ("fsq", "[Arts and Entertainment > Arcade]", "93293"),
        ("osm", "shop=pawnbroker", "6496"),
        ("osm", "shop=motorcycle", "4783"),
        ("osm", "shop=laundry", "961"),
        ("osm", "shop=hairdresser", "962"),
        ("osm", "shop=computer", "474"),
        ("osm", "shop=electronics", "474"),
        ("osm", "shop=optician", "47736"),
        ("osm", "amenity=car_wash", "9534"),
        ("osm", "amenity=post_office", "531"),
        ("osm", "amenity=veterinary", "75"),
        ("osm", "healthcare=laboratory", "869"),
        ("osm", "shop=funeral_directors", "963"),
        ("osm", "shop=jewelry", "47734"),
        ("overture", "motorcycle_repair", "9532"),
        ("overture", "employment_agency", "78"),
        ("overture", "manufacturer", "C"),
        ("overture", "bank_or_credit_union", "64"),
        ("overture", "freight_and_cargo_service", "H"),
        ("overture", "financial_service", "L"),
        ("overture", "nail_salon", "962"),
        ("overture", "motorcycle_dealer", "4783"),
        ("overture", "mobile_phone_store", "474"),
        ("overture", "real_estate_agent", "68"),
        ("overture", "computer_store", "474"),
        ("overture", "food_truck_stand", "561"),
        ("overture", "auto_detailing", "9534"),
        ("overture", "hair_salon", "962"),
        ("overture", "advertising_agency", "731"),
        ("overture", "eyewear_store", "47736"),
        ("overture", "software_development", "62"),
        ("overture", "attorney_or_law_firm", "691"),
        ("overture", "internet_cafe", "61202"),
        ("overture", "contractor", "F"),
    ],
)
def test_clear_qc_categories_receive_a_trusted_floor(source: str, value: str, root: str):
    prepared = prepare_suggestion(_taxonomy(), source, value)
    assert prepared.branch_codes == (root,)
    assert prepared.floor_code == root
    assert prepared.block_reason is None


@pytest.mark.parametrize(
    ("source", "value"),
    [
        ("fsq", "[Business and Professional Services > Office > Meeting Room]"),
        ("fsq", "[Business and Professional Services > Office > Coworking Space]"),
        ("fsq", "[Business and Professional Services > Event Space]"),
        ("fsq", "[Business and Professional Services > Factory]"),
        ("fsq", "[Business and Professional Services > Office > Tech Startup]"),
        ("fsq", "[Arts and Entertainment]"),
        ("fsq", "[Retail > Shopping Mall]"),
        ("fsq", "[Travel and Transportation > Parking]"),
        (
            "fsq",
            "[Business and Professional Services > Convention Center > Conference Room]",
        ),
        ("overture", "community_and_government"),
        ("overture", "corporate_or_business_office"),
        ("overture", "shopping_mall"),
        ("overture", "social_or_community_service"),
        ("overture", "travel_and_transportation"),
        ("osm", "amenity=recycling"),
        ("osm", "amenity=parking"),
        ("osm", "amenity=atm"),
        ("osm", "amenity=taxi"),
        ("osm", "amenity=community_centre"),
        ("osm", "man_made=works"),
        ("osm", "shop=mall"),
        ("osm", "amenity=marketplace"),
        ("osm", "leisure=sports_hall"),
        ("osm", "public_transport=station"),
        ("osm", "amenity=bus_station | public_transport=station"),
        ("osm", "office=ngo"),
    ],
)
def test_place_or_context_buckets_are_explicitly_uncodeable(source: str, value: str):
    prepared = prepare_suggestion(_taxonomy(), source, value)
    assert prepared.terminal is not None
    assert prepared.terminal.suggested_kind == "UNCODEABLE"


@pytest.mark.parametrize(
    ("source", "value"),
    [
        ("fsq", "[Landmarks and Outdoors > States and Municipalities > Neighborhood]"),
        ("osm", "public_transport=stop_position"),
    ],
)
def test_clear_non_activity_place_types_stay_out_of_psic(source: str, value: str):
    prepared = prepare_suggestion(_taxonomy(), source, value)
    assert prepared.terminal is not None
    assert prepared.terminal.suggested_kind == "NOT_ACTIVITY"


@pytest.mark.parametrize(
    ("value", "floor"),
    [
        ("amenity=pharmacy | healthcare=pharmacy", "4772"),
        ("amenity=clinic | healthcare=clinic", "862"),
        ("amenity=dentist | healthcare=dentist", "862"),
        ("amenity=doctors | healthcare=doctor", "862"),
        ("amenity=townhall | office=government", "84"),
    ],
)
def test_redundant_osm_tags_are_corroboration_not_compound_conflict(
    value: str,
    floor: str,
):
    assert not _has_multiple_source_components("osm", value)
    prepared = prepare_suggestion(_taxonomy(), "osm", value)
    assert prepared.floor_code == floor
    assert prepared.block_reason is None


def test_distinct_osm_tags_remain_compound():
    assert _has_multiple_source_components("osm", "amenity=bus_station | shop=convenience")
    assert _has_multiple_source_components("osm", "building=yes | shop=yes")


@pytest.mark.parametrize(
    "value",
    [
        "bank_or_credit_union",
        "building_or_construction_service",
        "flowers_and_gifts_store",
        "food_and_drink",
        "freight_and_cargo_service",
        "social_or_community_service",
        "attorney_or_law_firm",
        "tattoo_and_piercing",
    ],
)
def test_curated_overture_compounds_are_treated_as_coherent(value: str):
    assert not _has_multiple_source_components("overture", value)


@pytest.mark.parametrize(
    ("value", "roots"),
    [
        ("bakery", ("1071", "47214")),
        ("party_and_event_planning", ("96901", "823", "5621")),
        ("food_delivery_service", ("56", "532")),
        ("it_service_and_computer_repair", ("62", "951")),
        ("bottled_water_company", ("11", "46", "47")),
        ("dessert_shop", ("47", "56")),
        ("cupcake_shop", ("10", "47", "56")),
        ("ice_cream_shop", ("47", "56")),
    ],
)
def test_genuinely_ambiguous_overture_categories_remain_multi_branch(
    value: str,
    roots: tuple[str, ...],
):
    assert category_plan("overture", value).branch_roots == roots


def test_pet_shop_is_retail_but_pet_grooming_remains_guarded():
    taxonomy = _taxonomy()
    shop = prepare_suggestion(taxonomy, "osm", "shop=pet")
    assert shop.branch_codes == ("4776",)
    assert shop.floor_code == "4776"
    assert shop.block_reason is None

    grooming = prepare_suggestion(taxonomy, "osm", "shop=pet_grooming")
    assert grooming.floor_code is None
    assert grooming.block_reason == "ambiguous_pet_shop"


def test_broad_health_beauty_and_social_facility_stay_explicitly_ambiguous():
    taxonomy = _taxonomy()
    health_beauty = prepare_suggestion(
        taxonomy,
        "fsq",
        "[Business and Professional Services > Health and Beauty Service]",
    )
    assert health_beauty.branch_codes == ("R", "96")
    assert health_beauty.floor_code is None
    assert health_beauty.block_reason == "ambiguous_branch_roots"

    social = prepare_suggestion(taxonomy, "osm", "amenity=social_facility")
    assert social.branch_codes == ("87", "88")
    assert social.floor_code is None
    assert social.block_reason == "ambiguous_branch_roots"
