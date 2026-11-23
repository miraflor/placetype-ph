"""Separate administrative activity and institution ownership assertions."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from .source_evidence import normalized, text

# Precise administrative functions, not merely public ownership or accreditation.
_ADMIN_PATTERNS = (
    re.compile(r"^(?:doh|department of health)\s*(?:ncr\s+|national capital region\s+)?regional office\b", re.I),
    re.compile(r"^(?:deped|department of education)\b.{0,60}\b(?:schools? division|division) office\b", re.I),
    re.compile(r"^(?:department of education\s+)?schools? division office\b", re.I),
    re.compile(r"\b(?:city|municipal|provincial) health department(?: office| headquarters)?$", re.I),
)
_FRONTLINE = re.compile(r"\b(?:hospital|clinic|medical cent(?:er|re)|health cent(?:er|re)|dental|dentist|"
                        r"laborator(?:y|ies)|elementary school|high school|university|college|academy|"
                        r"tutorial|training cent(?:er|re))\b", re.I)
_POTENTIAL_ADMIN = re.compile(r"\b(?:department of (?:health|education)|health department|health office|"
                              r"schools? division office|deped|doh)\b", re.I)

_PUBLIC_EDUCATION = re.compile(r"\b(?:national\s+(?:\w+\s+){0,2}high school|state university|"
                               r"public (?:elementary |high )?school|city college|municipal college|"
                               r"university of the philippines|central elementary school|"
                               r"child development cent(?:er|re)|barangay day\s*care cent(?:er|re))\b", re.I)
_PRIVATE_EDUCATION = re.compile(r"\b(?:private (?:school|college|university)|montessori|"
                                r"international school|review cent(?:er|re)|tutorial cent(?:er|re))\b", re.I)
_PUBLIC_HEALTH = re.compile(r"\b(?:rural health unit|barangay health (?:station|cent(?:er|re)|unit)|"
                            r"provincial hospital|district hospital|regional medical cent(?:er|re)|"
                            r"philippine general hospital)\b", re.I)
_PRIVATE_HEALTH = re.compile(r"\b(?:private hospital|private clinic|doctors'? hospital|"
                             r"dental clinic|diagnostic cent(?:er|re)|polyclinic)\b", re.I)


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
    return tuple(dict.fromkeys(text(row.get(key)) for key in
                               ("name", "canonical_name", "osm_name", "overture_name", "fsq_name")
                               if text(row.get(key))))


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
        if any(name.casefold() != canonical.casefold() and
               not any(p.search(name) for p in _ADMIN_PATTERNS) for name in names[1:]):
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
    if education:
        if _PUBLIC_EDUCATION.search(joined):
            hints.add("public")
        if _PRIVATE_EDUCATION.search(joined):
            hints.add("private")
        category = text(row.get("overture_category")).casefold()
        if "public_school" in category:
            hints.add("public")
        if "private_school" in category:
            hints.add("private")
    if health:
        if _PUBLIC_HEALTH.search(joined):
            hints.add("public")
        if _PRIVATE_HEALTH.search(joined):
            hints.add("private")
    if len(hints) != 1:
        return OwnershipDecision("REVIEW" if hints else "NO_EVIDENCE", "",
                                 "conflicting_name_clues" if hints else "no_ownership_evidence")
    side = next(iter(hints))
    if re.search(r"\bprivate\b", joined, re.I) and side == "public":
        return OwnershipDecision("REVIEW", "", "explicit_private_name_conflict")
    if re.search(r"\b(?:public|government)\b", joined, re.I) and side == "private":
        return OwnershipDecision("REVIEW", "", "explicit_public_name_conflict")
    return OwnershipDecision("PROVISIONAL", side, "name_or_category_heuristic_not_verified")
