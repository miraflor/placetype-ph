from __future__ import annotations

import pytest

from placetype_ph.suggest import category_plan


@pytest.mark.parametrize(
    ("source", "value", "root"),
    [
        ("overture", "bank", "64"),
        ("overture", "bubble_tea_shop", "563"),
        ("overture", "tea_room", "563"),
        ("overture", "smoothie_juice_bar", "563"),
        ("overture", "doctors_office", "862"),
        ("overture", "family_practice", "862"),
        ("overture", "caterer", "5621"),
        ("overture", "veterinarian", "75"),
        ("overture", "preschool", "85"),
        ("overture", "high_school", "85"),
        ("overture", "car_wash", "9534"),
        ("overture", "accountant", "692"),
        ("overture", "funeral_service", "963"),
        ("overture", "bookstore", "4761"),
        ("overture", "resort", "55"),
        ("overture", "laboratory_testing", "712"),
        ("overture", "car_rental_service", "771"),
        ("overture", "pawn_shop", "6496"),
        ("overture", "installment_loans", "6495"),
        ("overture", "shipping_center", "53"),
        ("overture", "event_photography_service", "742"),
        ("overture", "information_technology_company", "62"),
        ("overture", "metal_fabricator", "25"),
        ("overture", "animal_or_pet_service", "96902"),
        ("overture", "meat_wholesaler", "46"),
        ("fsq", "[Retail > Gift Store]", "47192"),
        ("fsq", "[Retail > Supermarket]", "4711"),
        ("fsq", "[Retail > Pawn Shop]", "6496"),
        ("fsq", "[Retail > Automotive Retail > Car Dealership]", "4781"),
        ("fsq", "[Business and Professional Services > Tailor]", "144"),
        (
            "fsq",
            "[Business and Professional Services > Photography Service > Photography Lab]",
            "742",
        ),
        ("fsq", "[Travel and Transportation > Travel Agency]", "7911"),
        ("fsq", "[Health and Medicine > Veterinarian]", "75"),
        ("osm", "shop=butcher", "47213"),
        ("osm", "shop=tyres", "4782"),
        ("osm", "shop=printing", "181"),
        ("osm", "shop=medical_supply", "4772"),
        ("osm", "shop=travel_agency", "7911"),
        ("osm", "shop=mobile_phone", "474"),
        ("osm", "shop=tailor", "144"),
        ("osm", "amenity=kindergarten", "85"),
        ("osm", "amenity=library", "9111"),
        ("osm", "office=educational_institution", "85"),
    ],
)
def test_cleanup_rules_have_explicit_branches(source: str, value: str, root: str):
    assert category_plan(source, value).branch_roots == (root,)


def test_bar_and_grill_restaurant_uses_restaurant_branch():
    plan = category_plan("overture", "bar_and_grill_restaurant")
    assert plan.branch_roots == ("561",)
    assert plan.rule == "overture:restaurant_suffix"
