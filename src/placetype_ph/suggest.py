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
    "leisure=pitch",
    "man_made=bridge",
    "public_transport=platform",
    "tourism=artwork",
}

# Very broad source buckets describe a place/context rather than a defensible principal
# activity.  Keep them explicit and reviewable instead of allowing unconstrained lexical
# retrieval to manufacture a precise-looking PSIC candidate.
_OVERTURE_UNCODEABLE = {
    "community_and_government",
}
_FSQ_UNCODEABLE = {
    "business and professional services > office",
    "community and government",
    "travel and transportation",
}
_OSM_UNCODEABLE_EXACT = {
    "office=company",
}

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
    "printing_service": ("printing and service activities related to printing", ("181",)),
    "party_and_event_planning": (
        "event planning and organization services",
        ("96901", "823", "5621"),
    ),
    "social_or_community_service": (
        "social work activities without accommodation",
        ("88",),
    ),
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
}


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
        # OpenPlaces joins multiple OSM category tags with this separator.
        return " | " in raw

    if source == "overture":
        # Explicit conjunction/disjunction marks a semantically compound label,
        # e.g. bank_or_credit_union or flowers_and_gifts_store.
        folded = raw.casefold()
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
    return bool(parts & {"shop=pet", "shop=pet_grooming"})


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

    if folded[0] == "business and professional services":
        if "bank" in joined or "banking" in joined:
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
        if tags.get("office") == "government":
            return QueryPlan("general public administration activities", ("84",), "osm:government")
        if "office" in tags:
            return QueryPlan(f"{tags['office'].replace('_', ' ')} professional service activities")
        if tags.get("tourism") == "hotel":
            return QueryPlan("hotels and similar accommodation activities", ("551",), "osm:hotel")
        if tags.get("tourism") in {"guest_house", "hostel", "chalet"}:
            return QueryPlan("short term accommodation activities", ("55",), "osm:lodging")

    if source == "fsq":
        plan = _fsq_plan(value)
        if plan is not None:
            return plan

    return QueryPlan(category_query(source, value))


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

    All schemes are evaluated in the same joint pass, but evidence admissibility follows the
    semantics of the classification. PSIC may use its curated activity-oriented query rewrites
    and branch constraints. PCPC uses the normalized OpenPlaces category directly and may
    propose a potential product/service family when retrieval is strong and separated. PSCC
    also receives normalized category retrieval, but place-category text alone is not accepted
    as commodity evidence: it remains candidate-only until a reviewer confirms a mapping or a
    later workflow supplies explicit product/commodity evidence.

    No first-pass suggestion becomes a reviewed crosswalk mapping without review. Every reason
    a hit was kept candidate-only is recorded in ``suggestion_source`` as ``guard:<reason>``.
    """
    if taxonomy.scheme == "psic":
        plan = category_plan(source, source_value)
    else:
        # Construct this explicitly rather than relying on QueryPlan defaults. PCPC and PSCC
        # cannot reuse PSIC branch roots because codes are not portable across taxonomies.
        plan = QueryPlan(
            query_text=category_query(source, source_value),
            rule="raw_category",
        )
    compound_source = _has_multiple_source_components(source, source_value)
    query = plan.query_text
    branches = _valid_branch_roots(taxonomy, plan.branch_roots)
    branch_titles = [taxonomy.get(code).title for code in branches]

    if (
        taxonomy.scheme == "psic"
        and not compound_source
        and _is_non_activity(source, source_value)
    ):
        return Suggestion(
            query_text=query,
            suggested_kind="NOT_ACTIVITY",
            suggestion_source="rule:high_precision_non_activity",
            review_status="REVIEW_REQUIRED",
        )

    if (
        taxonomy.scheme == "psic"
        and not compound_source
        and _is_broad_uncodeable(source, source_value)
    ):
        return Suggestion(
            query_text=query,
            suggested_kind="UNCODEABLE",
            suggestion_source="rule:broad_ontology_bucket",
            review_status="REVIEW_REQUIRED",
        )

    hits = retriever.search_hierarchical(query, top_n=top_n, branch_roots=branches)
    if not hits:
        return Suggestion(
            query_text=query,
            branch_codes="|".join(branches),
            branch_titles=" || ".join(branch_titles),
            suggestion_source=f"semantic:{plan.rule}",
            review_status="NO_CANDIDATES",
        )

    codes = [hit.code for hit in hits]
    titles = [taxonomy.get(hit.code).title for hit in hits]
    scores = [hit.score for hit in hits]
    top = scores[0]
    margin = top - scores[1] if len(scores) > 1 else top

    suggested_kind = ""
    suggested_codes = ""
    source_name = f"semantic:{plan.rule};retrieval:candidates"
    status = "REVIEW_CANDIDATES"
    query_tokens = [token for token in normalize_key(query).split() if len(token) > 2]
    block_reason = _auto_proposal_block_reason(source, source_value, plan)

    # PSCC is present in the joint pass, produces candidates, receives peer-context rechecks,
    # and may contribute reviewed codes as peer evidence. What it must not do is turn a POI
    # category alone into an accepted commodity suggestion.
    if block_reason is None and taxonomy.scheme == "pscc":
        block_reason = "commodity_evidence_required"
    elif block_reason is None and len(query_tokens) < 2:
        block_reason = "short_query"

    if block_reason is not None:
        source_name = f"semantic:{plan.rule};retrieval:candidates;guard:{block_reason}"
    if top >= min_score and margin >= min_margin and block_reason is None:
        suggested_codes = codes[0]
        suggested_kind = "SUBTREE" if taxonomy.has_children(suggested_codes) else "EXACT"
        source_name = f"semantic:{plan.rule};retrieval:strong_separated_hit"
        status = "REVIEW_REQUIRED"

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
