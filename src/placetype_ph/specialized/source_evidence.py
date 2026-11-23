"""Preserve source-specific name/category semantics and parsed OSM tags."""

from __future__ import annotations

import ast
import re
import unicodedata
from collections.abc import Mapping

from .model import Evidence

SOURCES = ("osm", "overture", "fsq")
AUXILIARY_OSM_KEYS = frozenset(
    {
        "brand",
        "cuisine",
        "delivery",
        "drive_through",
        "internet_access",
        "opening_hours",
        "operator",
        "outdoor_seating",
        "takeaway",
        "wheelchair",
    }
)
COMPATIBLE_OSM_PAIRS = frozenset(
    {
        frozenset(("amenity=pharmacy", "healthcare=pharmacy")),
        frozenset(("amenity=clinic", "healthcare=clinic")),
        frozenset(("amenity=dentist", "healthcare=dentist")),
        frozenset(("amenity=doctors", "healthcare=doctor")),
        frozenset(("amenity=townhall", "office=government")),
        frozenset(("amenity=cafe", "shop=coffee")),
        frozenset(("amenity=cafe", "shop=tea")),
        frozenset(("amenity=bank", "atm=yes")),
    }
)


def text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value != value:
        return ""
    return str(value).strip()


def normalized(value: object) -> str:
    value = unicodedata.normalize("NFKC", text(value)).casefold()
    return " ".join(re.findall(r"[^\W_]+", value, flags=re.UNICODE))


def split_categories(value: object) -> list[str]:
    """Split canonical pipe-separations; preserve source strings for audit."""
    raw = text(value)
    if raw.startswith("[") and raw.endswith("]"):
        try:
            literal = ast.literal_eval(raw)
            if isinstance(literal, (tuple, list)):
                return [text(part) for part in literal if text(part)]
        except (ValueError, SyntaxError):
            pass
    return list(dict.fromkeys(part.strip() for part in raw.split("|") if part.strip()))


def osm_pairs(value: object) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for part in split_categories(value):
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        key, val = key.strip().casefold(), val.strip().casefold()
        if key and val:
            for item in val.split(";"):
                if item.strip():
                    pairs.append((key, item.strip()))
    return tuple(dict.fromkeys(pairs))


def compound_status(source: str, category: object) -> str:
    parts = split_categories(category)
    if source == "osm":
        primaries = [
            f"{k}={v}"
            for k, v in osm_pairs(category)
            if k not in AUXILIARY_OSM_KEYS and not k.startswith(("contact:", "payment:"))
        ]
        unique = set(primaries)
        if len(unique) <= 1:
            return "SINGLE"
        if len(unique) == 2 and frozenset(unique) in COMPATIBLE_OSM_PAIRS:
            return "COMPATIBLE"
        return "MULTIPLE_PRIMARY"
    if source == "overture":
        # Alternates are independent assertions, not automatically a multi-activity fact.
        return "MULTIPLE_CATEGORIES" if len(parts) > 1 else "SINGLE"
    return "MULTIPLE_CATEGORIES" if len(parts) > 1 else "SINGLE"


def from_row(row: Mapping[str, object]) -> tuple[Evidence, ...]:
    evidence: list[Evidence] = []
    for source in SOURCES:
        name = text(row.get(f"{source}_name"))
        if name:
            evidence.append(Evidence(source, "name", name, "NAME", dependency_group=source))
        category = row.get(f"{source}_category")
        for part in split_categories(category):
            kind = "CATEGORY" if source != "osm" else "TAG"
            evidence.append(Evidence(source, "category", part, kind, dependency_group=source))
        # For an upstream schema that retains Overture's structured taxonomy.
        if source == "overture":
            for field, kind in (
                ("overture_primary_category", "PRIMARY_CATEGORY"),
                ("overture_alternate_category", "ALTERNATE_CATEGORY"),
                ("overture_basic_category", "BROAD_CATEGORY"),
            ):
                for part in split_categories(row.get(field)):
                    evidence.append(Evidence(source, field, part, kind, dependency_group=source))
    if not any(item.field == "name" for item in evidence):
        name = text(row.get("name")) or text(row.get("canonical_name"))
        if name:
            evidence.append(
                Evidence("canonical", "name", name, "NAME", dependency_group="canonical")
            )
    if not evidence:
        for part in split_categories(row.get("category")):
            evidence.append(
                Evidence("canonical", "category", part, "CATEGORY", dependency_group="canonical")
            )
    return tuple(evidence)
