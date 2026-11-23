"""Activity eligibility versus uncodeable places; preserves context without inventing codes."""

from __future__ import annotations

from collections.abc import Mapping

from .source_evidence import osm_pairs, split_categories

OVERTURE_NON_ACTIVITY = frozenset(
    {"beach", "bridge", "historic_site", "landmark", "monument", "park", "road"}
)
OVERTURE_UNCODEABLE = frozenset(
    {
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
)
OSM_NON_ACTIVITY = frozenset(
    {
        "leisure=park",
        "leisure=pitch",
        "man_made=bridge",
        "man_made=mast",
        "man_made=street_cabinet",
        "man_made=surveillance",
        "man_made=tower",
        "public_transport=platform",
        "public_transport=stop_position",
        "tourism=artwork",
    }
)
OSM_UNCODEABLE = frozenset(
    {
        "amenity=atm",
        "amenity=community_centre",
        "amenity=marketplace",
        "amenity=parking",
        "amenity=parking_entrance",
        "amenity=recycling",
        "amenity=taxi",
        "industrial=depot",
        "leisure=playground",
        "leisure=sports_hall",
        "man_made=works",
        "office=company",
        "office=ngo",
        "office=yes",
        "public_transport=station",
        "shop=mall",
        "tourism=attraction",
    }
)
OSM_NON_ACTIVITY_PREFIXES = ("boundary=", "highway=", "natural=", "waterway=")


def eligibility(row: Mapping[str, object]) -> tuple[str, str]:
    """Evaluate every source's terminal activity eligibility, including OSM pairs."""
    decisions: set[str] = set()
    for key, value in osm_pairs(row.get("osm_category")):
        cat = f"{key}={value}"
        if cat in OSM_NON_ACTIVITY or cat.startswith(OSM_NON_ACTIVITY_PREFIXES):
            decisions.add("NOT_PSIC_ACTIVITY")
        elif cat in OSM_UNCODEABLE:
            decisions.add("UNCODEABLE")
    for field in (
        "overture_category",
        "overture_primary_category",
        "overture_alternate_category",
        "overture_basic_category",
    ):
        for category in split_categories(row.get(field)):
            raw = category.casefold()
            if raw in OVERTURE_NON_ACTIVITY:
                decisions.add("NOT_PSIC_ACTIVITY")
            elif raw in OVERTURE_UNCODEABLE:
                decisions.add("UNCODEABLE")
    if len(decisions) > 1:
        return "REVIEW", "nonactivity_vs_uncodeable_evidence"
    if decisions:
        return next(iter(decisions)), "source_category_policy"
    return "", ""
