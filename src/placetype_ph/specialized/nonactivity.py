"""Activity eligibility versus uncodeable places; preserves context without inventing codes."""

from __future__ import annotations

from collections.abc import Mapping

from .source_evidence import split_categories

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
    decisions: set[str] = set()
    for source in ("osm", "overture"):
        for category in split_categories(row.get(f"{source}_category")):
            raw = category.casefold()
            if source == "osm":
                if raw in OSM_NON_ACTIVITY or raw.startswith(OSM_NON_ACTIVITY_PREFIXES):
                    decisions.add("NOT_PSIC_ACTIVITY")
                elif raw in OSM_UNCODEABLE:
                    decisions.add("UNCODEABLE")
            elif raw in OVERTURE_NON_ACTIVITY:
                decisions.add("NOT_PSIC_ACTIVITY")
            elif raw in OVERTURE_UNCODEABLE:
                decisions.add("UNCODEABLE")
    if len(decisions) > 1:
        return "REVIEW", "nonactivity_vs_uncodeable_evidence"
    if decisions:
        status = next(iter(decisions))
        return status, "source_category_policy"
    return "", ""
