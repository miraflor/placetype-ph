"""Conservative national-registry linkage; no fuzzy matches or invented site IDs."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .institutional_registry import Institution, InstitutionIndex, clean, region_code

SITE_MATCH = "REGISTRY_SITE_MATCH"
ACCEPTED_ACTIVITY = frozenset({"SINGLE", "INTERSECT", "NESTED", "MANUAL"})
STREET = re.compile(
    r"\b(?:street|st|road|rd|avenue|ave|highway|hwy|boulevard|blvd|drive|dr|"
    r"lane|ln|building|bldg)\b"
)


def identity_text(value: Any) -> str:
    """Only case, Unicode composition, punctuation and whitespace normalization."""
    text = unicodedata.normalize("NFC", clean(value)).casefold()
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


def _values(row: Mapping[str, Any], fields: tuple[str, ...]) -> set[str]:
    return {identity_text(row.get(f)) for f in fields if identity_text(row.get(f))}


def _specific_address(address: str, city: str) -> bool:
    # Reject town-only and barangay-only entries. A street/building descriptor
    # and multiple substantive tokens are required; no geocoding or partial match.
    local = re.sub(r"(?:^| )" + re.escape(city) + r"(?: |$)", " ", address)
    return bool(STREET.search(local)) and len(local.split()) >= 3


@dataclass(frozen=True)
class SiteMatch:
    record: Institution
    references: tuple[str, ...]
    basis: str


def national_site_match(
    index: InstitutionIndex,
    row: Mapping[str, Any],
    matches: tuple[Institution, ...],
    ids: set[tuple[str, str]],
    names: tuple[str, ...],
) -> tuple[SiteMatch | None, str]:
    """Return an attributable site match or a reason to review; otherwise abstain.

    PhilHealth: unique exact name, city, sufficiently specific exact address.
    HEIDA: additionally require the source HEI code and city in its address.
    UniFAST is corroboration only, and only with exact name and observed region.
    DepEd prefixes never establish ownership here. Neither route is an
    independently reviewed canonical link. The caller enforces ownership gates.
    """
    if clean(row.get("psic_status")) not in ACCEPTED_ACTIVITY or not names:
        return None, ""
    name = identity_text(names[0])
    if not name or {identity_text(n) for n in names} != {name}:
        return None, ""
    if not _values(row, ("name", "canonical_name")):
        return None, ""
    cities = _values(row, ("city", "municipality", "osm_city", "overture_city", "fsq_city"))
    addresses = _values(row, ("address", "osm_address", "overture_address", "fsq_address"))
    towns = {clean(row.get(f)) for f in (
        "town_psgc", "municipality_psgc", "city_psgc", "area_psgc"
    ) if clean(row.get(f))}
    regions = {region_code(row.get(f)) for f in ("region", "region_code", "admin_region")
               if region_code(row.get(f))}
    if len(cities) != 1 or len(addresses) != 1 or len(towns) > 1 or len(regions) > 1:
        return None, ""
    city = next(iter(cities))
    address = next(iter(addresses))
    if not _specific_address(address, city):
        return None, ""
    code = clean(row.get("psic_code"))
    for record in matches:
        if identity_text(record.name) != name or identity_text(record.address) != address:
            continue
        if record.ownership not in {"public", "private"} or not record.as_of:
            continue
        if (
            record.source == "philhealth_hospitals" and record.domain == "health"
            and record.id_kind == "publication_row" and record.site_scope == "site"
            and record.evidence_type == "explicit_sector" and code.startswith("861")
            and record.source_url.startswith(("https://www.philhealth.gov.ph/",
                                               "https://philhealth.gov.ph/"))
            and identity_text(record.city) == city and not ids
        ):
            # Multiple publication rows, even agreeing ones, may be distinct sites.
            if len(matches) != 1:
                return None, "multiple_registered_sites_or_sources"
            return SiteMatch(record, (record.reference,),
                             "philhealth_exact_name_city_address"), ""
        if (
            record.source == "ched_hei" and record.domain == "education"
            and record.id_kind == "official_code" and code.startswith("854")
            and record.evidence_type.startswith("ched_hei_type:")
            and record.source_url == "https://heida.ched.gov.ph/hei-directory"
            and ids == {("ched_hei", record.source_id.casefold())}
            and f" {city} " in f" {address} "
        ):
            if len(matches) != 1:
                return None, "multiple_registered_sites_or_sources"
            refs = [record.reference]
            for other in index.by_names(names, "education"):
                if other == record or identity_text(other.name) != name:
                    continue
                if other.source == "ched_hei" and identity_text(other.address) == address:
                    return None, "multiple_registered_sites_or_sources"
                if other.source != "unifast_ra10931":
                    continue
                # Do not assume the registry's region from the HEIDA address.
                if regions and other.region not in regions:
                    continue
                if other.ownership and other.ownership != record.ownership:
                    return None, "registry_ownership_conflict"
                if regions and other.region in regions and other.ownership == record.ownership:
                    refs.append(other.reference)
            # Same-name, same-region UniFAST duplicates must not be merged blindly.
            if len(refs) > 2:
                return None, "multiple_registered_sites_or_sources"
            return SiteMatch(record, tuple(sorted(refs)),
                             "heida_exact_id_name_city_address"), ""
    return None, ""


def reconcile_school_records(
    matches: tuple[Institution, ...],
    ids: set[tuple[str, str]],
    row: Mapping[str, Any],
    names: tuple[str, ...],
) -> Institution | None:
    """Accept an official site record + agreeing BEIS record of the same school ID.

    The official site still must pass the caller's existing ID/name/city checks.
    The unverified record can corroborate but cannot promote on its own.
    """
    if len(matches) != 2 or not names:
        return None
    beis = next((r for r in matches if r.source == "deped_beis"), None)
    official = next((r for r in matches if r.source in {"deped_public", "deped_masterlist"}), None)
    if not beis or not official:
        return None
    sid = beis.source_id.casefold()
    if (
        official.source_id.casefold() != sid or official.id_kind != "official_code"
        or official.site_scope != "site" or not official.city or not official.source_url
        or not official.as_of or official.ownership not in {"public", "private"}
        or official.evidence_type not in {"official_public_directory", "explicit_sector"}
        or not ids or any(source not in {"deped_beis", "deped_public", "deped_masterlist"}
                          or value != sid for source, value in ids)
        or (official.source, sid) not in ids
        or identity_text(beis.name) != identity_text(official.name)
        or {identity_text(n) for n in names} != {identity_text(official.name)}
        or beis.ownership not in {"", official.ownership}
    ):
        return None
    towns = {clean(row.get(f)) for f in (
        "town_psgc", "municipality_psgc", "city_psgc", "area_psgc"
    ) if clean(row.get(f))}
    if not beis.town_psgc or towns != {beis.town_psgc}:
        return None
    if official.town_psgc and official.town_psgc != beis.town_psgc:
        return None
    return official
