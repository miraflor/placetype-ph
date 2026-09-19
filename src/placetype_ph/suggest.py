from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from .retrieval import TaxonomyRetriever
from .taxonomy import Taxonomy
from .text import clean_literal, normalize_key

SUGGESTION_COLUMNS = (
    "query_text",
    "branch_codes",
    "branch_titles",
    "suggested_kind",
    "suggested_codes",
    "candidate_codes",
    "candidate_titles",
    "candidate_scores",
    "retrieval_score",
    "suggestion_source",
    "review_status",
)

_FSQ_NON_ACTIVITY = {
    "neighborhood",
    "apartment or condo",
    "beach",
    "bridge",
    "forest",
    "housing development",
    "island",
    "lake",
    "mountain",
    "park",
    "river",
    "road",
    "structure",
    "trail",
}
_OVERTURE_NON_ACTIVITY = {
    "beach",
    "bridge",
    "historic_site",
    "landmark",
    "monument",
    "park",
    "road",
}
_OSM_NON_ACTIVITY_PREFIXES = ("boundary=", "highway=", "natural=", "waterway=")
_OSM_NON_ACTIVITY_EXACT = {
    "leisure=park",
    "public_transport=stop_position",
    "leisure=pitch",
    "man_made=bridge",
    "public_transport=platform",
    "tourism=artwork",
}

# Very broad source buckets describe a place/context rather than a defensible principal
# activity.  Keep them explicit and reviewable instead of allowing unconstrained lexical
# retrieval to manufacture a precise-looking PSIC candidate.
_OVERTURE_UNCODEABLE = {
    "arts_and_entertainment",
    "basketball_court",
    "campus_building",
    "community_and_government",
    "community_center",
    "corporate_or_business_office",
    "shopping_mall",
    "social_or_community_service",
    "sports_and_recreation",
    "swimming_pool",
    "travel_and_transportation",
}
_FSQ_UNCODEABLE = {
    "arts and entertainment",
    "business and professional services",
    "business and professional services > event space",
    "business and professional services > factory",
    "business and professional services > convention center > conference room",
    "business and professional services > office",
    "business and professional services > office > coworking space",
    "business and professional services > office > meeting room",
    "business and professional services > office > tech startup",
    "community and government",
    "community and government > organization > non-profit organization",
    "landmarks and outdoors > other great outdoors",
    "retail > shopping mall",
    "sports and recreation > water sports > swimming > swimming pool",
    "travel and transportation",
    "travel and transportation > parking",
}
_OSM_UNCODEABLE_EXACT = {
    "amenity=atm",
    "amenity=bus_station | public_transport=station",
    "amenity=community_centre",
    "amenity=marketplace",
    "amenity=parking",
    "amenity=recycling",
    "amenity=taxi",
    "leisure=sports_hall",
    "man_made=works",
    "amenity=parking_entrance",
    "industrial=depot",
    "leisure=playground",
    "office=company",
    "office=ngo",
    "office=yes",
    "public_transport=station",
    "shop=mall",
    "tourism=attraction",
}

_OVERTURE_COHERENT_COMPOUNDS = frozenset(
    {
        "attorney_or_law_firm",
        "bank_or_credit_union",
        "building_or_construction_service",
        "flowers_and_gifts_store",
        "food_and_drink",
        "freight_and_cargo_service",
        "social_or_community_service",
        "tattoo_and_piercing",
        "bar_and_grill_restaurant",
    }
)

# Query rewrites deliberately describe economic activity rather than merely replacing
# underscores. Branch roots are coarse constraints, not accepted labels.
_OVERTURE_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "restaurant": ("restaurants and mobile food service activities", ("561",)),
    "fast_food_restaurant": ("fast-food restaurant operations", ("561",)),
    "filipino_restaurant": ("restaurants and mobile food service activities", ("561",)),
    "seafood_restaurant": ("restaurants and mobile food service activities", ("561",)),
    "barbecue_restaurant": ("restaurants and mobile food service activities", ("561",)),
    "bakery": ("bakery products manufacture retail sale", ("1071", "47214")),
    "coffee_shop": ("operation of cafes or coffee shops", ("563",)),
    "cafe": ("operation of cafes or coffee shops", ("563",)),
    "bar": ("beverage serving activities", ("563",)),
    "hotel": ("hotels and similar accommodation activities", ("551",)),
    "lodging": ("accommodation activities", ("55",)),
    "real_estate_service": ("real estate activities", ("68",)),
    "real_estate_agent": ("real estate activities on a fee or contract basis", ("68",)),
    "government_office": ("general public administration activities", ("84",)),
    "school": ("education activities primary secondary tertiary education", ("85",)),
    "elementary_school": ("primary education", ("852",)),
    "college_university": ("tertiary education", ("854",)),
    "education": ("education activities", ("85",)),
    "pharmacy": ("retail sale pharmaceutical medical goods", ("4772",)),
    "dental_clinic": ("medical and dental practice activities", ("862",)),
    "hospital": ("hospital activities", ("861",)),
    "health_care": ("human health activities", ("86",)),
    "bank_or_credit_union": ("banking and credit cooperative activities", ("64",)),
    "christian_place_of_worship": ("activities of religious organizations", ("9491",)),
    "roman_catholic_place_of_worship": (
        "activities of religious organizations",
        ("9491",),
    ),
    "religious_organization": ("activities of religious organizations", ("9491",)),
    "beauty_salon": ("hairdressing and beauty treatment activities", ("962",)),
    "spa": ("day spa sauna and steam bath activities", ("962",)),
    "barber": ("hairdressing and barber activities", ("962",)),
    "laundromat": ("washing and cleaning of textile and fur products", ("961",)),
    "hardware_store": ("retail sale hardware building materials", ("4752",)),
    "clothing_store": ("retail sale garments clothing apparel", ("4771",)),
    "convenience_store": ("retail selling in convenience stores", ("4711",)),
    "grocery_store": ("retail selling in groceries", ("4711",)),
    "auto_dealer": ("retail sale of motor vehicles", ("4781",)),
    "auto_parts_store": ("retail sale motor vehicle parts accessories", ("4782",)),
    "gas_station": ("retail sale of automotive fuel", ("473",)),
    "automotive_repair": ("repair and maintenance of motor vehicles", ("953",)),
    "motorcycle_repair": ("repair and maintenance of motorcycles", ("9532",)),
    "auto_detailing": ("motor vehicle and motorcycle washing and detailing", ("9534",)),
    "printing_service": ("printing and service activities related to printing", ("181",)),
    "party_and_event_planning": (
        "event planning and organization services",
        ("96901", "823", "5621"),
    ),
    "social_or_community_service": (
        "social work activities without accommodation",
        ("88",),
    ),
    "food_and_drink": ("food and beverage service activities", ("56",)),
    "employment_agency": ("employment activities", ("78",)),
    "manufacturer": ("manufacturing activities", ("C",)),
    "industrial_equipment_manufacturer": ("manufacturing activities", ("C",)),
    "warehouse_club_store": ("retail trade activities", ("47",)),
    "flowers_and_gifts_store": ("retail sale flowers gifts and novelty goods", ("47",)),
    "shoe_store": ("retail sale clothing footwear and leather articles", ("4771",)),
    "furniture_store": ("retail sale household equipment and furniture", ("475",)),
    "beauty_supply_store": ("retail sale beauty and personal care goods", ("47",)),
    "freight_and_cargo_service": ("transportation and storage activities", ("H",)),
    "financial_service": ("financial and insurance activities", ("L",)),
    "pet_store": ("retail sale pet and pet supplies", ("4776",)),
    "food_beverage_distributor": ("wholesale and retail trade activities", ("G",)),
    "food_delivery_service": (
        "food delivery food service courier activities",
        ("56", "532"),
    ),
    "nail_salon": ("beauty treatment activities", ("962",)),
    "jewelry_store": ("retail sale jewelry watches and clocks", ("47734",)),
    "motorcycle_dealer": ("retail sale motorcycles and related parts", ("4783",)),
    "mobile_phone_store": ("retail sale mobile phones and communication equipment", ("474",)),
    "computer_store": ("retail sale computers and peripheral equipment", ("474",)),
    "food_truck_stand": ("mobile food service activities", ("561",)),
    "hair_salon": ("hairdressing and beauty treatment activities", ("962",)),
    "advertising_agency": ("advertising activities", ("731",)),
    "eyewear_store": ("retail sale eyewear and related supplies", ("47736",)),
    "home_improvement_store": ("retail sale household and building supplies", ("475",)),
    "hvac_service": ("construction installation activities", ("F",)),
    "software_development": ("computer programming and related activities", ("62",)),
    "womens_clothing_store": ("retail sale clothing footwear and leather articles", ("4771",)),
    "attorney_or_law_firm": ("legal activities", ("691",)),
    "bike_store": ("retail trade activities", ("47",)),
    "internet_cafe": ("provision of internet access in facilities open to the public", ("61202",)),
    "contractor": ("construction activities", ("F",)),
    "tattoo_and_piercing": ("personal service activities", ("96",)),
    "bottled_water_company": (
        "bottled water manufacturing wholesale or retail activities",
        ("11", "46", "47"),
    ),
    "dessert_shop": ("dessert retail or food service activities", ("47", "56")),
    "cupcake_shop": (
        "bakery manufacture retail or food service activities",
        ("10", "47", "56"),
    ),
    "ice_cream_shop": ("ice cream retail or food service activities", ("47", "56")),
    "it_service_and_computer_repair": (
        "information technology service and computer repair activities",
        ("62", "951"),
    ),
    "bank": ("banking activities", ("64",)),
    "bubble_tea_shop": ("beverage serving activities", ("563",)),
    "tea_room": ("beverage serving activities", ("563",)),
    "smoothie_juice_bar": ("beverage serving activities", ("563",)),
    "doctors_office": ("medical and dental practice activities", ("862",)),
    "family_practice": ("medical and dental practice activities", ("862",)),
    "caterer": ("event catering activities", ("5621",)),
    "veterinarian": ("veterinary activities", ("75",)),
    "preschool": ("education activities", ("85",)),
    "high_school": ("education activities", ("85",)),
    "car_wash": (
        "motor vehicle and motorcycle washing and detailing activities",
        ("9534",),
    ),
    "accountant": (
        "accounting bookkeeping auditing and tax consultancy activities",
        ("692",),
    ),
    "funeral_service": ("funeral and related activities", ("963",)),
    "bookstore": (
        "retail sale books newspapers stationery and school supplies",
        ("4761",),
    ),
    "resort": ("accommodation activities", ("55",)),
    "holiday_rental_home": ("accommodation activities", ("55",)),
    "laboratory_testing": ("technical testing and analysis", ("712",)),
    "car_rental_service": ("rental and leasing of motor vehicles", ("771",)),
    "b2b_advertising_and_marketing_service": ("advertising activities", ("731",)),
    "pawn_shop": ("pawnshop operations", ("6496",)),
    "installment_loans": ("other credit granting activities", ("6495",)),
    "shipping_center": ("postal and courier activities", ("53",)),
    "event_photography_service": ("photographic activities", ("742",)),
    "information_technology_company": (
        "computer programming consultancy and related activities",
        ("62",),
    ),
    "metal_fabricator": (
        "manufacture of fabricated metal products except machinery and equipment",
        ("25",),
    ),
    "animal_or_pet_service": ("pet care services", ("96902",)),
    "meat_wholesaler": ("wholesale trade activities", ("46",)),
    "electronics_store": (
        "retail sale of information communication equipment consumer electronics",
        ("474",),
    ),
    "travel_service": ("travel agency and other travel related activities", ("79",)),
    "gym": ("operation of sports facilities fitness centers", ("931",)),
    "building_or_construction_service": ("construction activities", ("F",)),
    "professional_service": ("professional scientific and technical activities", ("N",)),
    "shopping": ("retail trade activities", ("47",)),
}

_OSM_AMENITY_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "restaurant": ("restaurants and mobile food service activities", ("561",)),
    "fast_food": ("fast-food restaurant operations", ("561",)),
    "cafe": ("operation of cafes or coffee shops", ("563",)),
    "bar": ("beverage serving activities", ("563",)),
    "pub": ("beverage serving activities", ("563",)),
    "school": ("education activities primary secondary education", ("85",)),
    "college": ("tertiary education", ("854",)),
    "university": ("tertiary education", ("854",)),
    "bank": ("banking activities", ("64",)),
    "fuel": ("retail sale of automotive fuel", ("473",)),
    "pharmacy": ("retail sale pharmaceutical medical goods", ("4772",)),
    "place_of_worship": ("activities of religious organizations", ("9491",)),
    "hospital": ("hospital activities", ("861",)),
    "clinic": ("medical and dental practice activities", ("862",)),
    "dentist": ("medical and dental practice activities", ("862",)),
    "doctors": ("medical and dental practice activities", ("862",)),
    "townhall": ("general public administration activities", ("84",)),
    "police": ("public administration and public order activities", ("84",)),
    "fire_station": ("public administration and public safety activities", ("84",)),
    "car_wash": ("motor vehicle and motorcycle washing and detailing", ("9534",)),
    "post_office": ("postal activities", ("531",)),
    "veterinary": ("veterinary activities", ("75",)),
    "social_facility": ("social work activities", ("87", "88")),
    "internet_cafe": (
        "provision of internet access in facilities open to the public",
        ("61202",),
    ),
    "kindergarten": ("education activities", ("85",)),
    "library": ("library activities", ("9111",)),
}

_OSM_SHOP_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "car_repair": ("repair and maintenance of motor vehicles", ("953",)),
    "car": ("retail sale of motor vehicles", ("4781",)),
    "car_parts": ("retail sale motor vehicle parts accessories", ("4782",)),
    "convenience": ("retail selling in convenience stores", ("4711",)),
    "supermarket": ("retail selling in supermarkets and hypermarkets", ("4711",)),
    "hardware": ("retail sale hardware building materials", ("4752",)),
    "clothes": ("retail sale garments clothing apparel", ("4771",)),
    "pharmacy": ("retail sale pharmaceutical medical goods", ("4772",)),
    "bakery": ("retail sale bakery products", ("47",)),
    "pawnbroker": ("pawnshop operations", ("6496",)),
    "motorcycle": ("retail sale motorcycles and related parts", ("4783",)),
    "laundry": ("washing and cleaning of textile and fur products", ("961",)),
    "bicycle": ("retail trade activities", ("47",)),
    "hairdresser": ("hairdressing and beauty treatment activities", ("962",)),
    "beauty": ("retail sale beauty and personal care goods", ("47",)),
    "computer": ("retail sale computers and peripheral equipment", ("474",)),
    "electronics": ("retail sale information and communication equipment", ("474",)),
    "optician": ("retail sale eyewear and related supplies", ("47736",)),
    "water": ("retail trade activities", ("47",)),
    "gas": ("retail trade activities", ("47",)),
    "furniture": ("retail sale household equipment and furniture", ("475",)),
    "general": ("retail trade activities", ("47",)),
    "variety_store": ("retail trade activities", ("47",)),
    "yes": ("retail trade activities", ("47",)),
    "books": ("retail trade activities", ("47",)),
    "shoes": ("retail sale clothing footwear and leather articles", ("4771",)),
    "copyshop": ("printing and service activities related to printing", ("181",)),
    "funeral_directors": ("funeral and related activities", ("963",)),
    "jewelry": ("retail sale jewelry watches and clocks", ("47734",)),
    "pastry": ("retail sale bakery products", ("47",)),
    "pet": ("retail sale pet and pet supplies", ("4776",)),
    "butcher": ("retail sale meat meat products and poultry", ("47213",)),
    "tyres": ("retail sale motor vehicle parts and accessories", ("4782",)),
    "printing": ("printing and service activities related to printing", ("181",)),
    "medical_supply": ("retail sale pharmaceutical and medical goods", ("4772",)),
    "travel_agency": ("travel agency activities", ("7911",)),
    "mobile_phone": (
        "retail sale mobile phones and communication equipment",
        ("474",),
    ),
    "tailor": ("custom tailoring and dressmaking", ("144",)),
    "rice": ("retail sale food", ("472",)),
    "appliance": ("retail sale household equipment", ("475",)),
    "photo": ("retail sale photographic equipment and supplies", ("47737",)),
}

_OSM_OFFICE_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "courier": ("courier activities", ("532",)),
    "association": ("membership organization activities", ("94",)),
    "educational_institution": ("education activities", ("85",)),
}

_OSM_HEALTHCARE_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "pharmacy": ("retail sale pharmaceutical medical goods", ("4772",)),
    "clinic": ("medical and dental practice activities", ("862",)),
    "dentist": ("medical and dental practice activities", ("862",)),
    "laboratory": ("medical and diagnostic laboratory service activities", ("869",)),
}

_FSQ_RETAIL_LEAF_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "retail": ("retail trade activities", ("47",)),
    "miscellaneous store": ("retail trade activities", ("47",)),
    "cosmetics store": ("retail sale cosmetics and toilet articles", ("4772",)),
    "furniture and home store": ("retail sale household equipment and furniture", ("475",)),
    "shoe store": ("retail sale clothing footwear and leather articles", ("4771",)),
    "boutique": ("retail trade activities", ("47",)),
    "jewelry store": ("retail sale jewelry watches and clocks", ("47734",)),
    "food and beverage retail": ("retail sale food and beverages", ("472",)),
    "sporting goods retail": ("retail sale sporting equipment", ("4762",)),
    "arts and crafts store": ("retail trade activities", ("47",)),
    "eyecare store": ("retail sale eyewear and related supplies", ("47736",)),
    "grocery store": ("retail selling in groceries", ("4711",)),
    "pet supplies store": ("retail sale pet supplies", ("47762",)),
    "motorcycle dealership": ("retail sale motorcycles and related parts", ("4783",)),
    "bookstore": (
        "retail sale books newspapers stationery and school supplies",
        ("4761",),
    ),
    "gift store": ("retail sale gift and novelty goods", ("47192",)),
    "toy store": ("retail trade activities", ("47",)),
    "supermarket": ("retail selling in supermarkets and hypermarkets", ("4711",)),
    "market": ("retail trade activities", ("47",)),
    "flower store": ("retail sale fresh and artificial flowers and plants", ("47735",)),
    "pawn shop": ("pawnshop operations", ("6496",)),
    "car dealership": ("retail sale of motor vehicles", ("4781",)),
    "construction supplies store": (
        "retail sale hardware building materials paints and glass",
        ("4752",),
    ),
    "fashion accessories store": ("retail trade activities", ("47",)),
    "men's store": ("retail sale clothing footwear and leather articles", ("4771",)),
    "bridal store": ("retail sale clothing footwear and leather articles", ("4771",)),
    "smoke shop": ("retail trade activities", ("47",)),
    "hobby store": ("retail trade activities", ("47",)),
    "women's store": ("retail sale clothing footwear and leather articles", ("4771",)),
    "bicycle store": ("retail trade activities", ("47",)),
    "office supply store": ("retail trade activities", ("47",)),
}

_FSQ_HEALTH_LEAF_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "medical center": ("human health activities", ("86",)),
    "medical lab": ("other human health activities", ("869",)),
    "veterinarian": ("veterinary activities", ("75",)),
}

_FSQ_BUSINESS_LEAF_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "nail salon": ("hairdressing and beauty treatment activities", ("962",)),
    "laundry service": ("washing and cleaning of textile and fur products", ("961",)),
    "car wash and detail": ("motor vehicle and motorcycle washing and detailing", ("9534",)),
    "health and beauty service": ("health and personal service activities", ("R", "96")),
    "shipping, freight, and material transportation service": (
        "transportation and storage activities",
        ("H",),
    ),
    "employment agency": ("employment activities", ("78",)),
    "advertising agency": ("advertising activities", ("731",)),
    "design studio": ("specialized design activities", ("N",)),
    "funeral home": ("funeral and related activities", ("963",)),
    "motorcycle repair shop": ("repair and maintenance of motorcycles", ("9532",)),
    "tailor": ("custom tailoring and dressmaking", ("144",)),
    "photography lab": ("photographic activities", ("742",)),
    "commercial real estate developer": ("real estate activities", ("68",)),
    "pet service": ("pet care services", ("96902",)),
    "tattoo parlor": ("personal service activities", ("96",)),
}

_FSQ_ARTS_LEAF_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "internet cafe": (
        "provision of internet access in facilities open to the public",
        ("61202",),
    ),
    "movie theater": ("motion picture projection activities", ("591",)),
    "arcade": ("operation of arcade and amusement games", ("93293",)),
}


# A floor is stronger than a retrieval branch: it is a coarse PSIC classification that
# the source ontology itself supports well enough to retain when semantic retrieval cannot
# justify a refinement. Keep this allowlist deliberately narrower than _OVERTURE_RULES.
# Explicit broad place/context buckets are handled as UNCODEABLE, while genuinely ambiguous
# categories such as bakery and party_and_event_planning remain retrieval-only.
_TRUSTED_OVERTURE_FLOOR_CATEGORIES = frozenset(
    {
        "restaurant",
        "fast_food_restaurant",
        "filipino_restaurant",
        "seafood_restaurant",
        "barbecue_restaurant",
        "coffee_shop",
        "cafe",
        "bar",
        "hotel",
        "lodging",
        "real_estate_service",
        "government_office",
        "school",
        "elementary_school",
        "college_university",
        "education",
        "pharmacy",
        "dental_clinic",
        "hospital",
        "health_care",
        "bank_or_credit_union",
        "christian_place_of_worship",
        "roman_catholic_place_of_worship",
        "religious_organization",
        "beauty_salon",
        "spa",
        "barber",
        "laundromat",
        "hardware_store",
        "clothing_store",
        "convenience_store",
        "grocery_store",
        "auto_dealer",
        "auto_parts_store",
        "gas_station",
        "automotive_repair",
        "printing_service",
        "motorcycle_repair",
        "auto_detailing",
        "food_and_drink",
        "employment_agency",
        "manufacturer",
        "industrial_equipment_manufacturer",
        "warehouse_club_store",
        "flowers_and_gifts_store",
        "shoe_store",
        "furniture_store",
        "beauty_supply_store",
        "freight_and_cargo_service",
        "financial_service",
        "pet_store",
        "food_beverage_distributor",
        "nail_salon",
        "jewelry_store",
        "motorcycle_dealer",
        "mobile_phone_store",
        "real_estate_agent",
        "computer_store",
        "food_truck_stand",
        "hair_salon",
        "advertising_agency",
        "eyewear_store",
        "home_improvement_store",
        "hvac_service",
        "software_development",
        "womens_clothing_store",
        "attorney_or_law_firm",
        "bike_store",
        "internet_cafe",
        "contractor",
        "tattoo_and_piercing",
        "bank",
        "bubble_tea_shop",
        "tea_room",
        "smoothie_juice_bar",
        "doctors_office",
        "family_practice",
        "caterer",
        "veterinarian",
        "preschool",
        "high_school",
        "car_wash",
        "accountant",
        "funeral_service",
        "bookstore",
        "resort",
        "holiday_rental_home",
        "laboratory_testing",
        "car_rental_service",
        "b2b_advertising_and_marketing_service",
        "pawn_shop",
        "installment_loans",
        "shipping_center",
        "event_photography_service",
        "information_technology_company",
        "metal_fabricator",
        "animal_or_pet_service",
        "meat_wholesaler",
        "electronics_store",
        "travel_service",
        "gym",
        "professional_service",
        "building_or_construction_service",
    }
)

_TRUSTED_FSQ_FLOOR_RULES = frozenset(
    {
        "fsq:retail_pharmacy",
        "fsq:retail_convenience",
        "fsq:retail_hardware",
        "fsq:retail_electronics",
        "fsq:retail_clothing",
        "fsq:restaurant",
        "fsq:beverage",
        "fsq:pharmacy",
        "fsq:hospital",
        "fsq:medical_practice",
        "fsq:education",
        "fsq:religion",
        "fsq:government",
        "fsq:fitness",
        "fsq:bank",
        "fsq:beauty",
        "fsq:auto_repair",
        "fsq:lodging",
        "fsq:fuel",
        "fsq:retail_explicit",
        "fsq:health_explicit",
        "fsq:business_explicit",
        "fsq:arts_explicit",
        "fsq:travel_explicit",
    }
)


@dataclass(frozen=True, slots=True)
class QueryPlan:
    query_text: str
    branch_roots: tuple[str, ...] = ()
    rule: str = "raw_category"


@dataclass(frozen=True, slots=True)
class Suggestion:
    query_text: str
    branch_codes: str = ""
    branch_titles: str = ""
    suggested_kind: str = ""
    suggested_codes: str = ""
    candidate_codes: str = ""
    candidate_titles: str = ""
    candidate_scores: str = ""
    retrieval_score: str = ""
    suggestion_source: str = ""
    review_status: str = "REVIEW_CANDIDATES"

    def as_dict(self) -> dict[str, str]:
        return {name: str(getattr(self, name)) for name in SUGGESTION_COLUMNS}


def _unwrap_category(value: str) -> str:
    text = clean_literal(value) or ""
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            parsed = None
        if isinstance(parsed, (list, tuple)) and parsed:
            text = " | ".join(str(x) for x in parsed)
        else:
            text = text[1:-1]
    return text.replace("\\'", "'").strip(" '[]\"")


_FSQ_COMPONENT_ROOTS = (
    "Arts and Entertainment",
    "Business and Professional Services",
    "Community and Government",
    "Dining and Drinking",
    "Health and Medicine",
    "Landmarks and Outdoors",
    "Retail",
    "Sports and Recreation",
    "Travel and Transportation",
)
_FSQ_COMPONENT_RE = re.compile(
    r"(?:^|,\s*)['\"]?(?:"
    + "|".join(re.escape(root) for root in _FSQ_COMPONENT_ROOTS)
    + r")(?: >|$)",
    re.IGNORECASE,
)


def _has_multiple_source_components(source: str, value: str) -> bool:
    """True when one source_value represents multiple independently meaningful labels."""
    source = source.casefold().strip()
    raw = _unwrap_category(value).strip()

    if source == "fsq":
        # Internal commas in labels such as "Cafe, Coffee, and Tea House"
        # are not separators.  A new component is recognized only when a
        # comma is followed by another known Foursquare root.
        text = raw.strip(" []")
        return len(_FSQ_COMPONENT_RE.findall(text)) > 1

    if source == "osm":
        # Explicit broad buckets may themselves contain several corroborating OSM tags.
        if raw.casefold() in _OSM_UNCODEABLE_EXACT:
            return False
        # Only observed amenity+healthcare duplicates are treated as corroboration.
        items = [item.strip() for item in raw.split(" | ") if item.strip()]
        if len(items) <= 1:
            return False
        tags: dict[str, str] = {}
        for item in items:
            if "=" not in item:
                return True
            key, val = item.split("=", 1)
            tags[key.strip().casefold()] = val.strip().casefold()
        pairs = frozenset(tags.items())
        coherent = {
            frozenset({("amenity", "pharmacy"), ("healthcare", "pharmacy")}),
            frozenset({("amenity", "clinic"), ("healthcare", "clinic")}),
            frozenset({("amenity", "dentist"), ("healthcare", "dentist")}),
            frozenset({("amenity", "doctors"), ("healthcare", "doctor")}),
            frozenset({("amenity", "townhall"), ("office", "government")}),
        }
        return pairs not in coherent

    if source == "overture":
        # Explicit conjunction/disjunction marks a semantically compound label,
        # e.g. bank_or_credit_union or flowers_and_gifts_store.
        folded = raw.casefold()
        if folded in _OVERTURE_UNCODEABLE:
            return False
        if folded in _OVERTURE_COHERENT_COMPOUNDS:
            return False
        return "_and_" in folded or "_or_" in folded

    return False


def _is_ambiguous_pet_shop(source: str, value: str) -> bool:
    """OSM pet-shop labels do not distinguish pets, supplies, and grooming."""
    if source.casefold().strip() != "osm":
        return False
    parts = {
        part.strip().casefold()
        for part in _unwrap_category(value).split(" | ")
        if part.strip()
    }
    return "shop=pet_grooming" in parts


def _auto_proposal_block_reason(
    source: str,
    source_value: str,
    plan: QueryPlan,
) -> str | None:
    """Return evidence-quality reasons semantic retrieval must remain candidate-only.

    These guards describe the source evidence itself. ``raw_category`` is not one of them:
    normalized category text is legitimate retrieval evidence. Scheme-specific admissibility
    is handled separately in ``suggest_mapping``; in particular, PSCC requires commodity
    evidence before a category-derived candidate may be promoted.
    """
    if _has_multiple_source_components(source, source_value):
        return "compound_source"
    if (
        source.casefold().strip() == "overture"
        and _unwrap_category(source_value).casefold().strip()
        == "flowers_and_gifts_store"
    ):
        # This Overture label explicitly spans two retail families.  Its trusted
        # PSIC 47 floor is useful, but lexical retrieval must not choose one side
        # (for example gift/novelty retail) and manufacture false precision.
        return "mixed_category_ceiling"
    if len(plan.branch_roots) > 1:
        return "ambiguous_branch_roots"
    if plan.rule == "overture:store_suffix":
        # This is a lexical heuristic, not a curated ontology rule. The audit
        # showed systematic over-specialization for compound and pet stores.
        return "heuristic_store_suffix"
    if _is_ambiguous_pet_shop(source, source_value):
        return "ambiguous_pet_shop"
    return None


def category_query(source: str, value: str) -> str:
    text = _unwrap_category(value)
    if source.casefold() == "overture":
        text = text.replace("_", " ")
    elif source.casefold() == "osm":
        parts: list[str] = []
        for item in text.split("|"):
            item = item.strip()
            if "=" in item:
                key, val = item.split("=", 1)
                parts.extend((val.replace("_", " "), key.replace("_", " ")))
            else:
                parts.append(item.replace("_", " "))
        text = " ".join(parts)
    else:
        text = text.replace(">", " ")
    return re.sub(r"\s+", " ", text).strip()


def _is_non_activity(source: str, value: str) -> bool:
    source = source.casefold()
    raw = _unwrap_category(value).casefold().strip()
    if source == "overture":
        return raw in _OVERTURE_NON_ACTIVITY
    if source == "osm":
        return raw in _OSM_NON_ACTIVITY_EXACT or raw.startswith(_OSM_NON_ACTIVITY_PREFIXES)
    if source == "fsq":
        leaf = raw.split(">")[-1].strip(" '\"")
        return leaf in _FSQ_NON_ACTIVITY
    return False


def _is_broad_uncodeable(source: str, value: str) -> bool:
    source = source.casefold().strip()
    raw = _unwrap_category(value).casefold().strip()
    if source == "overture":
        return raw in _OVERTURE_UNCODEABLE
    if source == "osm":
        return raw in _OSM_UNCODEABLE_EXACT
    if source == "fsq":
        normalized = " > ".join(
            part.strip(" '\"").casefold()
            for part in raw.split(">")
            if part.strip()
        )
        return normalized in _FSQ_UNCODEABLE
    return False


def _osm_tags(value: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    for item in _unwrap_category(value).split("|"):
        item = item.strip()
        if "=" not in item:
            continue
        key, val = item.split("=", 1)
        tags[key.strip().casefold()] = val.strip().casefold()
    return tags


def _fsq_plan(value: str) -> QueryPlan | None:
    raw = _unwrap_category(value)
    path = [part.strip(" '\"") for part in raw.split(">") if part.strip()]
    folded = [part.casefold() for part in path]
    text = " ".join(path)
    joined = " > ".join(folded)

    if not folded:
        return None
    if folded[0] == "retail":
        explicit = _FSQ_RETAIL_LEAF_RULES.get(folded[-1])
        if explicit:
            return QueryPlan(explicit[0], explicit[1], "fsq:retail_explicit")
        if "pharmacy" in joined:
            return QueryPlan(
                "retail sale pharmaceutical medical goods",
                ("4772",),
                "fsq:retail_pharmacy",
            )
        if "convenience store" in joined:
            return QueryPlan(
                "retail selling in convenience stores",
                ("4711",),
                "fsq:retail_convenience",
            )
        if "hardware store" in joined:
            return QueryPlan(
                "retail sale hardware building materials",
                ("4752",),
                "fsq:retail_hardware",
            )
        if "electronics store" in joined or "computers and electronics" in joined:
            return QueryPlan(
                "retail sale information communication equipment consumer electronics",
                ("474",),
                "fsq:retail_electronics",
            )
        if "clothing store" in joined:
            return QueryPlan(
                "retail sale garments clothing apparel",
                ("4771",),
                "fsq:retail_clothing",
            )
        return QueryPlan(f"retail sale {path[-1]}", ("47",), "fsq:retail")
    if folded[0] == "dining and drinking":
        if "restaurant" in joined or "joint" in joined or "diner" in joined or "pizzeria" in joined:
            return QueryPlan(
                "restaurants and mobile food service activities",
                ("561",),
                "fsq:restaurant",
            )
        if (
            "cafe" in joined
            or "coffee" in joined
            or "tea house" in joined
            or "bar" in joined
        ):
            return QueryPlan(
                "beverage serving activities cafes coffee shops",
                ("563",),
                "fsq:beverage",
            )
        if "bakery" in joined:
            return QueryPlan(
                "bakery establishment manufacture retail food service bakery products",
                ("10", "47", "56"),
                "fsq:bakery_ambiguous",
            )
        return QueryPlan("food and beverage service activities", ("56",), "fsq:dining")
    if folded[0] == "health and medicine":
        explicit = _FSQ_HEALTH_LEAF_RULES.get(folded[-1])
        if explicit:
            return QueryPlan(explicit[0], explicit[1], "fsq:health_explicit")
        if "pharmacy" in joined:
            return QueryPlan(
                "retail sale pharmaceutical medical goods",
                ("4772",),
                "fsq:pharmacy",
            )
        if "hospital" in joined:
            return QueryPlan("hospital activities", ("861",), "fsq:hospital")
        if "physician" in joined or "dentist" in joined or "doctor" in joined:
            return QueryPlan(
                "medical and dental practice activities",
                ("862",),
                "fsq:medical_practice",
            )
        return QueryPlan("human health activities", ("86",), "fsq:health")
    if folded[0] == "community and government":
        if (
            "education" in joined
            or "school" in joined
            or "college" in joined
            or "university" in joined
        ):
            root = "854" if "college" in joined or "university" in joined else "85"
            return QueryPlan(
                "education activities tertiary primary secondary education",
                (root,),
                "fsq:education",
            )
        if "spiritual center" in joined or "church" in joined:
            return QueryPlan(
                "activities of religious organizations", ("9491",), "fsq:religion"
            )
        if "government building" in joined:
            return QueryPlan(
                "general public administration activities", ("84",), "fsq:government"
            )
        return QueryPlan(text)
    if folded[0] == "sports and recreation":
        if any(
            court in joined
            for court in (
                "basketball court",
                "volleyball court",
                "badminton court",
                "tennis court",
                "pickleball court",
            )
        ):
            return QueryPlan(
                "operation of courts for basketball volleyball badminton tennis and pickleball",
                ("93112",),
                "fsq:sports_court",
            )
        if "gym" in folded:
            return QueryPlan(
                "operation of fitness centers",
                ("93111",),
                "fsq:fitness",
            )
        return QueryPlan(text)

    if folded[0] == "arts and entertainment":
        explicit = _FSQ_ARTS_LEAF_RULES.get(folded[-1])
        if explicit:
            return QueryPlan(explicit[0], explicit[1], "fsq:arts_explicit")
        if folded[-1] == "gaming cafe":
            return QueryPlan(
                "internet access and amusement gaming activities",
                ("61202", "93293"),
                "fsq:arts_ambiguous",
            )
        if folded[-1] == "art gallery":
            return QueryPlan(
                "art gallery cultural exhibition activities",
                ("90", "910"),
                "fsq:arts_ambiguous",
            )
        return QueryPlan(text)

    if folded[0] == "business and professional services":
        explicit = _FSQ_BUSINESS_LEAF_RULES.get(folded[-1])
        if explicit:
            rule = (
                "fsq:health_beauty_ambiguous"
                if folded[-1] == "health and beauty service"
                else "fsq:business_explicit"
            )
            return QueryPlan(explicit[0], explicit[1], rule)
        if folded[-1] in {
            "bank",
            "credit union",
            "banking and finance",
            "banking and finances",
        }:
            return QueryPlan("banking activities", ("64",), "fsq:bank")
        if "hair salon" in joined or "spa" in joined.replace(",", " ").split():
            return QueryPlan(
                "hairdressing beauty treatment day spa activities",
                ("962",),
                "fsq:beauty",
            )
        if "automotive repair" in joined:
            return QueryPlan(
                "repair and maintenance of motor vehicles",
                ("953",),
                "fsq:auto_repair",
            )
        return QueryPlan(text)
    if folded[0] == "travel and transportation":
        if folded[-1] == "travel agency":
            return QueryPlan(
                "travel agency activities",
                ("7911",),
                "fsq:travel_explicit",
            )
        if "lodging" in joined or "hotel" in joined:
            return QueryPlan(
                "accommodation hotels and similar accommodation", ("55",), "fsq:lodging"
            )
        if "fuel station" in joined:
            return QueryPlan("retail sale of automotive fuel", ("473",), "fsq:fuel")
    return None


def category_plan(source: str, value: str) -> QueryPlan:
    source = source.casefold().strip()
    raw = _unwrap_category(value)
    folded = raw.casefold().strip()

    if source == "overture":
        rule = _OVERTURE_RULES.get(folded)
        if rule:
            return QueryPlan(rule[0], rule[1], f"overture:{folded}")
        if folded.endswith("_restaurant"):
            return QueryPlan(
                "restaurants and mobile food service activities",
                ("561",),
                "overture:restaurant_suffix",
            )
        if folded.endswith("_store"):
            return QueryPlan(
                f"retail sale {folded.removesuffix('_store').replace('_', ' ')}",
                ("47",),
                "overture:store_suffix",
            )
        if folded.endswith("_clinic"):
            return QueryPlan(
                "medical and dental practice activities",
                ("86",),
                "overture:clinic_suffix",
            )

    if source == "osm":
        tags = _osm_tags(value)
        if "amenity" in tags:
            val = tags["amenity"]
            rule = _OSM_AMENITY_RULES.get(val)
            if rule:
                return QueryPlan(rule[0], rule[1], f"osm:amenity={val}")
        if "shop" in tags:
            val = tags["shop"]
            rule = _OSM_SHOP_RULES.get(val)
            if rule:
                return QueryPlan(rule[0], rule[1], f"osm:shop={val}")
            return QueryPlan(f"retail sale {val.replace('_', ' ')}", ("47",), "osm:shop")
        if "healthcare" in tags:
            val = tags["healthcare"]
            rule = _OSM_HEALTHCARE_RULES.get(val)
            if rule:
                return QueryPlan(rule[0], rule[1], f"osm:healthcare={val}")
        if tags.get("office") == "government":
            return QueryPlan("general public administration activities", ("84",), "osm:government")
        if "office" in tags:
            val = tags["office"]
            rule = _OSM_OFFICE_RULES.get(val)
            if rule:
                return QueryPlan(rule[0], rule[1], f"osm:office={val}")
            return QueryPlan(f"{tags['office'].replace('_', ' ')} professional service activities")
        if tags.get("tourism") == "hotel":
            return QueryPlan("hotels and similar accommodation activities", ("551",), "osm:hotel")
        if tags.get("tourism") in {"guest_house", "hostel", "chalet", "apartment"}:
            return QueryPlan("short term accommodation activities", ("55",), "osm:lodging")

    if source == "fsq":
        plan = _fsq_plan(value)
        if plan is not None:
            return plan

    return QueryPlan(category_query(source, value))


def _trusted_floor_code(source: str, source_value: str, plan: QueryPlan) -> str | None:
    """Return a conservative coarse PSIC floor established by source ontology evidence.

    `branch_roots` remain search constraints. Only an explicitly trusted rule with one root
    becomes a floor. Compound source values establish no floor unless preprocessing has
    identified them as redundant/corroborating or as an explicitly coherent Overture label.
    """
    if len(plan.branch_roots) != 1:
        return None
    if _has_multiple_source_components(source, source_value):
        return None

    rule = plan.rule
    trusted = False
    if source.casefold().strip() == "overture":
        folded = _unwrap_category(source_value).casefold().strip()
        trusted = (
            folded in _TRUSTED_OVERTURE_FLOOR_CATEGORIES
            or rule == "overture:restaurant_suffix"
        )
    elif source.casefold().strip() == "fsq":
        trusted = rule in _TRUSTED_FSQ_FLOOR_RULES
    elif source.casefold().strip() == "osm":
        # These rule names are produced only after a value matched one of the explicit
        # curated OSM dictionaries above. Generic `shop=*` uses the distinct rule "osm:shop".
        trusted = (
            rule.startswith("osm:amenity=")
            or rule.startswith("osm:shop=")
            or rule.startswith("osm:healthcare=")
            or rule.startswith("osm:office=")
            or rule in {"osm:government", "osm:hotel", "osm:lodging"}
        )

    return plan.branch_roots[0] if trusted else None


def _valid_branch_roots(taxonomy: Taxonomy, roots: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(code for code in roots if code in taxonomy.nodes)


def suggest_mapping(
    taxonomy: Taxonomy,
    retriever: TaxonomyRetriever,
    source: str,
    source_value: str,
    *,
    top_n: int = 5,
    min_score: float = 0.45,
    min_margin: float = 0.12,
) -> Suggestion:
    """Generate one independent first-pass suggestion for one taxonomy.

    This compatibility wrapper keeps the public single-row API while V9's CLI separates
    deterministic preparation from batched retrieval and finalisation.
    """
    prepared = prepare_suggestion(taxonomy, source, source_value)
    if prepared.terminal is not None:
        return prepared.terminal
    hits = retriever.search_hierarchical(
        prepared.query_text,
        top_n=top_n,
        branch_roots=prepared.branch_codes,
    )
    return finalize_suggestion(
        taxonomy,
        prepared,
        hits,
        min_score=min_score,
        min_margin=min_margin,
    )

@dataclass(frozen=True, slots=True)
class PreparedSuggestion:
    """Deterministic first-pass preparation before any taxonomy retrieval is performed."""

    query_text: str
    branch_codes: tuple[str, ...] = ()
    branch_titles: tuple[str, ...] = ()
    rule: str = "raw_category"
    block_reason: str | None = None
    terminal: Suggestion | None = None
    floor_code: str | None = None

def prepare_suggestion(
    taxonomy: Taxonomy,
    source: str,
    source_value: str,
) -> PreparedSuggestion:
    """Prepare source evidence and policy guards without performing retrieval."""
    if taxonomy.scheme == "psic":
        plan = category_plan(source, source_value)
    else:
        plan = QueryPlan(
            query_text=category_query(source, source_value),
            rule="raw_category",
        )

    compound_source = _has_multiple_source_components(source, source_value)
    query = plan.query_text
    branches = _valid_branch_roots(taxonomy, plan.branch_roots)
    branch_titles = tuple(taxonomy.get(code).title for code in branches)
    proposed_floor = (
        _trusted_floor_code(source, source_value, plan)
        if taxonomy.scheme == "psic"
        else None
    )
    floor_code = (
        proposed_floor if proposed_floor is not None and proposed_floor in branches else None
    )

    if (
        taxonomy.scheme == "psic"
        and not compound_source
        and _is_non_activity(source, source_value)
    ):
        return PreparedSuggestion(
            query_text=query,
            rule=plan.rule,
            terminal=Suggestion(
                query_text=query,
                suggested_kind="NOT_ACTIVITY",
                suggestion_source="rule:high_precision_non_activity",
                review_status="REVIEW_DECISION",
            ),
        )

    if (
        taxonomy.scheme == "psic"
        and not compound_source
        and _is_broad_uncodeable(source, source_value)
    ):
        return PreparedSuggestion(
            query_text=query,
            rule=plan.rule,
            terminal=Suggestion(
                query_text=query,
                suggested_kind="UNCODEABLE",
                suggestion_source="rule:broad_ontology_bucket",
                review_status="REVIEW_DECISION",
            ),
        )

    query_tokens = [token for token in normalize_key(query).split() if len(token) > 2]
    block_reason = _auto_proposal_block_reason(source, source_value, plan)
    if block_reason is None and taxonomy.scheme == "pscc":
        block_reason = "commodity_evidence_required"
    elif block_reason is None and len(query_tokens) < 2:
        block_reason = "short_query"

    return PreparedSuggestion(
        query_text=query,
        branch_codes=branches,
        branch_titles=branch_titles,
        rule=plan.rule,
        floor_code=floor_code,
        block_reason=block_reason,
    )


def _floor_source(prepared: PreparedSuggestion, retrieval_state: str) -> str:
    parts = [
        f"rule:{prepared.rule}",
        "floor:trusted_source_ontology",
        f"retrieval:{retrieval_state}",
    ]
    if prepared.block_reason is not None:
        parts.append(f"guard:{prepared.block_reason}")
    return ";".join(parts)


def finalize_suggestion(
    taxonomy: Taxonomy,
    prepared: PreparedSuggestion,
    hits: list,
    *,
    min_score: float = 0.45,
    min_margin: float = 0.12,
) -> Suggestion:
    """Turn precomputed retrieval hits into the same first-pass decision as suggest_mapping."""
    if prepared.terminal is not None:
        return prepared.terminal

    query = prepared.query_text
    branches = prepared.branch_codes
    branch_titles = prepared.branch_titles
    if not hits:
        if prepared.floor_code is not None and prepared.block_reason is None:
            floor = prepared.floor_code
            return Suggestion(
                query_text=query,
                branch_codes="|".join(branches),
                branch_titles=" || ".join(branch_titles),
                suggested_kind="SUBTREE" if taxonomy.has_children(floor) else "EXACT",
                suggested_codes=floor,
                suggestion_source=_floor_source(prepared, "no_candidates"),
                review_status="REVIEW_MAPPING",
            )
        return Suggestion(
            query_text=query,
            branch_codes="|".join(branches),
            branch_titles=" || ".join(branch_titles),
            suggestion_source=f"semantic:{prepared.rule}",
            review_status="NO_CANDIDATES",
        )

    codes = [hit.code for hit in hits]
    titles = [taxonomy.get(hit.code).title for hit in hits]
    scores = [hit.score for hit in hits]
    top = scores[0]
    margin = top - scores[1] if len(scores) > 1 else top

    suggested_kind = ""
    suggested_codes = ""
    source_name = f"semantic:{prepared.rule};retrieval:candidates"
    status = "REVIEW_CANDIDATES"

    if prepared.block_reason is not None:
        source_name = (
            f"semantic:{prepared.rule};retrieval:candidates;guard:{prepared.block_reason}"
        )
    if top >= min_score and margin >= min_margin and prepared.block_reason is None:
        suggested_codes = codes[0]
        suggested_kind = "SUBTREE" if taxonomy.has_children(suggested_codes) else "EXACT"
        source_name = f"semantic:{prepared.rule};retrieval:strong_separated_hit"
        status = "REVIEW_MAPPING"
    elif prepared.floor_code is not None and prepared.block_reason is None:
        suggested_codes = prepared.floor_code
        suggested_kind = "SUBTREE" if taxonomy.has_children(suggested_codes) else "EXACT"
        source_name = _floor_source(prepared, "insufficient_for_refinement")
        status = "REVIEW_MAPPING"

    return Suggestion(
        query_text=query,
        branch_codes="|".join(branches),
        branch_titles=" || ".join(branch_titles),
        suggested_kind=suggested_kind,
        suggested_codes=suggested_codes,
        candidate_codes="|".join(codes),
        candidate_titles=" || ".join(titles),
        candidate_scores="|".join(f"{score:.6f}" for score in scores),
        retrieval_score=f"{top:.6f}",
        suggestion_source=source_name,
        review_status=status,
    )
