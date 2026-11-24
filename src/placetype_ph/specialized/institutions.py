"""Separate administrative activity and institution ownership assertions."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from .source_evidence import text

# Precise administrative functions, not merely public ownership or accreditation.
# Administrative patterns: regional DOH and DepEd offices (with or
# without punctuation after the agency name), schools division offices, and city,
# municipal or provincial health departments.
_ADMIN_PATTERNS = (
    re.compile(
        r"^(?:doh|department\s+of\s+health)\s*[-\u2013,:]?\s*"
        r"(?:ncr\s+|national\s+capital\s+region\s+)?regional\s+office\b",
        re.I,
    ),
    re.compile(
        r"^(?:deped|department\s+of\s+education)\s*[-\u2013,:]?\s*"
        r"(?:ncr\s+|national\s+capital\s+region\s+)?regional\s+office\b",
        re.I,
    ),
    re.compile(
        r"^(?:deped|department\s+of\s+education)\b.{0,60}\b"
        r"(?:schools?\s+division|division)\s+office\b",
        re.I,
    ),
    re.compile(r"^(?:department\s+of\s+education\s+)?schools?\s+division\s+office\b", re.I),
    re.compile(
        r"\b(?:city|municipal|municipality|provincial|province)\s+health\s+department"
        r"(?:\s+(?:office|headquarters))?\s*$",
        re.I,
    ),
)
# Frontline services and co-located teaching or clinical units are never reassigned.
_FRONTLINE = re.compile(
    r"\b(?:hospital|clinics?|medical\s+cent(?:er|re)|health\s+cent(?:er|re)|"
    r"eye\s+cent(?:er|re)|animal\s+bite|dental|dentists?|laborator(?:y|ies)|"
    r"elementary\s+school|high\s+school|national\s+high\s+school|"
    r"university|college|academy|tutorial|training\s+cent(?:er|re))\b",
    re.I,
)
# An agency or office phrase that is not specific enough to change the activity.
# The bare words "DepEd" and "DOH" are not enough (a teachers' union, a bank ATM or a
# "DOH accredited" clinic contain them).
_POTENTIAL_ADMIN = re.compile(
    r"\b(?:department\s+of\s+(?:health|education)|health\s+department|"
    r"schools?\s+division\s+office|health\s+office|"
    r"(?:city|municipal|municipality|provincial|regional|government)\s+education\s+department|"
    r"doh\s+chd)\b",
    re.I,
)

_PUBLIC_EDUCATION = re.compile(
    r"\b(?:national\s+(?:\w+\s+){0,2}high school|state university|"
    r"public (?:elementary |high )?school|city college|municipal college|"
    r"university of the philippines|central elementary school|"
    r"child development cent(?:er|re)|barangay day\s*care cent(?:er|re))\b",
    re.I,
)
_PRIVATE_EDUCATION = re.compile(
    r"\b(?:private (?:school|college|university)|montessori|"
    r"international school|review cent(?:er|re)|tutorial cent(?:er|re))\b",
    re.I,
)
_PUBLIC_HEALTH = re.compile(
    r"\b(?:rural health unit|barangay health (?:station|cent(?:er|re)|unit)|"
    r"provincial hospital|district hospital|regional medical cent(?:er|re)|"
    r"philippine general hospital)\b",
    re.I,
)
_PRIVATE_HEALTH = re.compile(
    r"\b(?:private hospital|private clinic|doctors'? hospital|"
    r"dental clinic|diagnostic cent(?:er|re)|polyclinic)\b",
    re.I,
)


# Extended ownership name patterns. They are added to the patterns above; a name matching
# either list is a provisional hint, never verified ownership.
_EXTENDED_PATTERNS = {
    ("education", "public"): (
        r"\b(?:government|public)\s+(?:elementary\s+|high\s+|primary\s+)?school\b",
        r"\bpublic\s+(?:university|college|academy)\b",
        r"\bnational\s+(?:[a-z]+\s+){0,2}high\s+school\b",
        r"\b(?:central|integrated)\s+(?:elementary\s+)?school\b",
        r"\bstate\s+(?:university|college)\b",
        r"\buniversity\s+of\s+the\s+philippines\b",
        r"\b(?:polytechnic|technological)\s+university\s+of\s+the\s+philippines\b",
        r"\bphilippine\s+normal\s+university\b",
        r"\bpamantasan\s+ng\s+lungsod\b",
        r"\b(?:city|municipal)\s+college\b",
        r"\bchild\s+development\s+cent(?:er|re)\b",
        r"\bday\s*care\s+cent(?:er|re)\b",
        r"\btesda\b",
        r"\bphilippine\s+(?:military|national\s+police|merchant\s+marine|public\s+safety)\s+"
        r"(?:academy|college)\b",
    ),
    ("education", "private"): (
        r"\bprivate\s+(?:elementary\s+|high\s+|primary\s+)?school\b",
        r"\bprivate\s+(?:university|college|academy|learning\s+cent(?:er|re))\b",
        r"\bmontessori\b",
        r"\bcolegio\b",
        r"\blearning\s+(?:cent(?:er|re)|school|institute)\b",
        r"\b(?:christian|catholic|baptist|adventist|methodist|lutheran|evangelical|islamic)\s+"
        r"(?:school|academy|college|learning)\b",
        r"\b(?:inc|incorporated)\b",
        r"\bdon\s+bosco\b",
        r"\bla\s+salle\b",
        r"\bateneo\b",
        r"\bmaryknoll\b",
        r"\bama\s+computer\b",
        r"\bsti\s+(?:college|education)\b",
        r"\binternational\s+school\b",
        r"\breview\s+cent(?:er|re)\b",
        r"\btutorial\b",
        r"\bkiddie\b",
    ),
    ("health", "public"): (
        r"\b(?:government|public)\s+(?:medical\s+|dental\s+|health\s+)?"
        r"(?:clinic|hospital|cent(?:er|re))\b",
        r"\brural\s+health\s+(?:unit|cent(?:er|re)|office)\b",
        r"\brhu\b",
        r"\bbarangay\s+health\s+(?:station|cent(?:er|re)|unit|office|post)\b",
        r"\bbhs\b",
        r"\b(?:city|municipal|provincial)\s+health\s+(?:cent(?:er|re)|unit)\b",
        r"\b(?:city|municipal|provincial)\s+health\s+office\s+clinic\b",
        r"\bsuper\s+health\s+cent(?:er|re)\b",
        r"\b(?:provincial|district|regional|municipal|city)\s+(?:general\s+)?hospital\b",
        r"\bmedicare\s+(?:community\s+)?hospital\b",
        r"\bregional\s+(?:medical|training\s+and\s+teaching)\s+cent(?:er|re)\b",
        r"\bregional\s+training\s+and\s+teaching\s+hospital\b",
        r"\bphilippine\s+general\s+hospital\b",
    ),
    ("health", "private"): (
        r"\bprivate\s+(?:medical\s+|dental\s+|health\s+)?(?:clinic|hospital|cent(?:er|re))\b",
        r"\b(?:inc|incorporated|corp|corporation)\b",
        r"\bdoctors?'?\s+(?:hospital|clinic)\b",
        r"\bdental\b",
        r"\bdentist",
        r"\bdiagnostic",
        r"\bpolyclinic\b",
        r"\blaborator(?:y|ies)\b",
        r"\blying[\s-]?in\b",
        r"\beye\s+(?:cent(?:er|re)|clinic)\b",
        r"\bderma(?:tology)?\b",
        r"\bortho(?:dontic|dontics)?\b",
    ),
}
_COMPILED_EXTENDED = {
    key: tuple(re.compile(pattern, re.I) for pattern in patterns)
    for key, patterns in _EXTENDED_PATTERNS.items()
}
# Private-practice categories in the Philippine setting (extended ownership policy).
_HEALTH_PRIVATE_CATEGORIES = frozenset(
    {
        "overture:dental_clinic",
        "overture:general_dentistry",
        "overture:doctors_office",
        "overture:family_practice",
        "overture:pediatric_clinic",
        "overture:obstetrics_and_gynecology",
        "overture:dialysis_clinic",
        "overture:vision_or_eye_care_clinic",
        "osm:amenity=dentist",
        "osm:healthcare=dentist",
    }
)
_CATEGORY_FIELDS = (
    ("osm", "osm_category"),
    ("overture", "overture_category"),
    ("overture", "overture_primary_category"),
    ("overture", "overture_alternate_category"),
)


def _categories(row: Mapping[str, object]) -> set[str]:
    return {
        f"{source}:{part.strip().casefold()}"
        for source, field in _CATEGORY_FIELDS
        for part in re.split(r"[|;]", text(row.get(field)))
        if part.strip()
    }


def _extended_hint(names: tuple[str, ...], domain: str, side: str) -> bool:
    return any(p.search(name) for p in _COMPILED_EXTENDED[(domain, side)] for name in names)


@dataclass(frozen=True)
class AdministrativeDecision:
    status: str = ""
    code: str = ""
    reason: str = ""


@dataclass(frozen=True)
class OwnershipDecision:
    status: str = ""
    side: str = ""
    reason: str = ""


def _names(row: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            text(row.get(key))
            for key in ("name", "canonical_name", "osm_name", "overture_name", "fsq_name")
            if text(row.get(key))
        )
    )


def administrative(row: Mapping[str, object]) -> AdministrativeDecision:
    names = _names(row)
    if not names:
        return AdministrativeDecision()
    canonical = names[0]
    strong = any(pattern.search(canonical) for pattern in _ADMIN_PATTERNS)
    frontline = any(_FRONTLINE.search(name) for name in names)
    if strong:
        if frontline:
            return AdministrativeDecision("REVIEW", "", "administration_and_frontline_conflict")
        if any(
            name.casefold() != canonical.casefold()
            and not any(p.search(name) for p in _ADMIN_PATTERNS)
            for name in names[1:]
        ):
            return AdministrativeDecision("REVIEW", "", "conflicting_source_names")
        return AdministrativeDecision("PROPOSED", "84120", "specific_administrative_function")
    if _POTENTIAL_ADMIN.search(canonical) and not frontline:
        return AdministrativeDecision("REVIEW", "", "insufficient_administrative_function_evidence")
    return AdministrativeDecision()


def ownership(row: Mapping[str, object], activity_codes: tuple[str, ...] = ()) -> OwnershipDecision:
    names = _names(row)
    if not names:
        return OwnershipDecision()
    joined = " | ".join(names)
    education = any(c.startswith("85") for c in activity_codes if c.isdigit())
    health = any(c.startswith("86") for c in activity_codes if c.isdigit())
    if not (education or health):
        return OwnershipDecision()
    hints: set[str] = set()
    categories = _categories(row)
    if education:
        if _PUBLIC_EDUCATION.search(joined) or _extended_hint(names, "education", "public"):
            hints.add("public")
        if _PRIVATE_EDUCATION.search(joined) or _extended_hint(names, "education", "private"):
            hints.add("private")
        if "overture:public_school" in categories:
            hints.add("public")
        if "overture:private_school" in categories:
            hints.add("private")
    if health:
        if _PUBLIC_HEALTH.search(joined) or _extended_hint(names, "health", "public"):
            hints.add("public")
        if _PRIVATE_HEALTH.search(joined) or _extended_hint(names, "health", "private"):
            hints.add("private")
        if categories & _HEALTH_PRIVATE_CATEGORIES:
            hints.add("private")
    if len(hints) != 1:
        return OwnershipDecision(
            "REVIEW" if hints else "NO_EVIDENCE",
            "",
            "conflicting_name_clues" if hints else "no_ownership_evidence",
        )
    side = next(iter(hints))
    if re.search(r"\bprivate\b", joined, re.I) and side == "public":
        return OwnershipDecision("REVIEW", "", "explicit_private_name_conflict")
    if re.search(r"\b(?:public|government)\b", joined, re.I) and side == "private":
        return OwnershipDecision("REVIEW", "", "explicit_public_name_conflict")
    return OwnershipDecision("PROVISIONAL", side, "name_or_category_heuristic_not_verified")
