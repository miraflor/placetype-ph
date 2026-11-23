"""Optional, identity-gated institutional facts; names alone never verify identity."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .source_evidence import normalized, text

REQUIRED_COLUMNS = ("source", "official_id", "name", "city", "domain", "ownership", "source_url")
VALID_DOMAINS = {"health", "education"}
VALID_OWNERSHIP = {"public", "private", "unknown"}


@dataclass(frozen=True)
class RegistryRecord:
    source: str
    official_id: str
    name: str
    city: str
    domain: str
    ownership: str
    source_url: str


@dataclass(frozen=True)
class RegistryDecision:
    status: str = "NO_REGISTRY_EVIDENCE"
    side: str = ""
    reference: str = ""
    basis: str = ""


def load_registry(path: Path | None) -> tuple[RegistryRecord, ...]:
    if path is None:
        return ()
    path = Path(path)
    records: list[RegistryRecord] = []
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not set(REQUIRED_COLUMNS) <= set(reader.fieldnames or ()):
            raise ValueError("institutional registry has missing required columns")
        keys: set[tuple[str, str]] = set()
        for i, row in enumerate(reader, start=2):
            record = RegistryRecord(*(text(row[column]) for column in REQUIRED_COLUMNS))
            if not record.source or not record.official_id or not record.name or not record.source_url:
                raise ValueError(f"registry row {i}: source, ID, name and provenance URL are required")
            if record.domain not in VALID_DOMAINS or record.ownership not in VALID_OWNERSHIP:
                raise ValueError(f"registry row {i}: invalid domain or ownership")
            if not record.source_url.startswith(("https://", "http://")):
                raise ValueError(f"registry row {i}: source_url must be an HTTP(S) URL")
            key = (record.source.casefold(), record.official_id.casefold())
            if key in keys:
                raise ValueError(f"registry row {i}: duplicate source/official ID")
            keys.add(key)
            records.append(record)
    return tuple(records)


def _source_identifiers(row: Mapping[str, object]) -> set[tuple[str, str]]:
    """Identifiers are namespaced; anonymous numbers are not authoritative IDs."""
    found: set[tuple[str, str]] = set()
    explicit_source = text(row.get("official_id_source")).casefold()
    if explicit_source and text(row.get("official_id")):
        found.add((explicit_source, text(row.get("official_id")).casefold()))
    for source, fields in (("doh_nhfr", ("nhfr_code", "doh_facility_code")),
                           ("deped_beis", ("school_id",))):
        for field in fields:
            if text(row.get(field)):
                found.add((source, text(row.get(field)).casefold()))
    # Explicit source-specific OpenStreetMap external IDs are typed separately.
    from .source_evidence import osm_pairs
    for key, val in osm_pairs(row.get("osm_category")):
        if key == "ref:nhfr":
            found.add(("doh_nhfr", val))
        elif key == "ref:deped":
            found.add(("deped_beis", val))
    return found


def match_registry(row: Mapping[str, object], records: Iterable[RegistryRecord],
                   domain: str = "") -> RegistryDecision:
    """External ID + site name + locality must agree for a promotable suggestion.

    An official code is site evidence only if the input itself carries that code.
    An exact normalized name/city match is recorded as a review candidate only.
    """
    records = tuple(record for record in records if not domain or record.domain == domain)
    if not records:
        return RegistryDecision()
    ids = _source_identifiers(row)
    names = {normalized(row.get(k)) for k in ("name", "canonical_name", "osm_name", "overture_name", "fsq_name")
             if normalized(row.get(k))}
    cities = {normalized(row.get(k)) for k in ("city", "municipality", "town", "osm_city", "overture_city")
              if normalized(row.get(k))}
    id_matches = [r for r in records if (r.source.casefold(), r.official_id.casefold()) in ids]
    if id_matches:
        if len(id_matches) != 1:
            return RegistryDecision("IDENTITY_AMBIGUOUS", basis="multiple_matching_registry_ids")
        record = id_matches[0]
        ref = f"{record.source}:{record.official_id}"
        if normalized(record.name) not in names:
            return RegistryDecision("EXTERNAL_ID_NAME_CONFLICT", reference=ref, basis="name_mismatch")
        if not cities or not normalized(record.city) or normalized(record.city) not in cities or len(cities) > 1:
            return RegistryDecision("EXTERNAL_ID_LOCALITY_REVIEW", reference=ref,
                                    basis="site_locality_not_verified")
        if record.ownership == "unknown":
            return RegistryDecision("IDENTITY_MATCH_NO_OWNERSHIP", reference=ref, basis="exact_official_id")
        return RegistryDecision("EXTERNAL_ID_SITE_MATCH", record.ownership, ref, "exact_official_id_name_city")
    named = [r for r in records if normalized(r.name) in names and normalized(r.city) in cities
             and normalized(r.city)]
    if not named:
        return RegistryDecision()
    if len(named) != 1:
        return RegistryDecision("IDENTITY_AMBIGUOUS", basis="multiple_name_city_matches")
    record = named[0]
    return RegistryDecision("NAME_CITY_PROVISIONAL", record.ownership if record.ownership != "unknown" else "",
                            f"{record.source}:{record.official_id}", "name_city_not_site_identity")
