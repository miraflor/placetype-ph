"""Institutional identity and ownership evidence, separated from final classification.

No source-name guess or unverified roster can change PSIC/PCPC/PSCC. The
promotion_eligible property expresses a guarded *capability*, not a mutation.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .institutional_registry import (
    InstitutionIndex,
    clean,
    https_host,
    key,
    region_code,
)

POLICY = "institution-identity-shadow-v1"
BLOCKED_PSIC = frozenset(
    {"NOT_PSIC_ACTIVITY", "UNCODEABLE", "REVIEW_ENTITY_MATCH", "CANDIDATES_ONLY", "CONFLICT"}
)
SUBUNIT = re.compile(
    r"\b(?:annex|ward|icu|chapel|auditorium|department|dept\.?|dialysis\s+unit|"
    r"division\s+of|emergency\s+(?:ward|room)|medical\s+arts|"
    r"outpatient|doctors?\s+offices?|registrar|faculty)\b|\s@\s",
    re.I,
)
NOT_INSTITUTION = (
    "public_transport=platform",
    "highway=bus_stop",
    "railway=station",
    "restaurant",
    "bus_stop",
    "transit_stop",
    "train_station",
    "pharmacy",
)
SCHOOL = ("amenity=school", "amenity=university", "public_school", "private_school")
HOSPITAL = ("amenity=hospital", "healthcare=hospital", "hospital")
OWNER_KEYS = frozenset({"ownership", "owner:type", "owner_type", "ownership:type"})
PUBLIC = frozenset({"public", "government", "state", "national_government", "local_government"})
PRIVATE = frozenset({"private", "privately_owned"})
VERIFIED_SCHEMA = "placetype-ph-verified-institution-links-v1"


def _names(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            clean(row.get(k))
            for k in ("name", "canonical_name", "osm_name", "overture_name", "fsq_name")
            if clean(row.get(k))
        )
    )


def _parts(row: Mapping[str, Any], *, include_basic: bool = True) -> tuple[str, ...]:
    sources = (
        "osm_category",
        "overture_category",
        "overture_primary_category",
        "overture_alternate_category",
    )
    if include_basic:
        sources += ("overture_basic_category",)
    return tuple(
        p.strip().casefold()
        for src in sources
        for p in re.split(r"[|;]", clean(row.get(src)))
        if p.strip()
    )


def domain_of(row: Mapping[str, Any]) -> str:
    code = clean(row.get("psic_code"))
    if code.isdigit() and code.startswith("85"):
        return "education"
    if code.isdigit() and code.startswith("86"):
        return "health"
    return ""


def _gate(row: Mapping[str, Any], domain: str) -> tuple[str, str]:
    if clean(row.get("psic_status")).upper() in BLOCKED_PSIC:
        return "ABSTAIN", "unresolved_activity"
    if not domain:
        return "ABSTAIN", "no_supported_psic_institution_domain"
    names = _names(row)
    if not names:
        return "ABSTAIN", "no_entity_name"
    if any(SUBUNIT.search(n) for n in names):
        return "REVIEW", "possible_institution_subunit"
    cats = set(_parts(row))
    if any(x in cats for x in NOT_INSTITUTION):
        return "ABSTAIN", "noninstitution_source_category"
    if domain == "education" and cats.intersection(HOSPITAL) and not cats.intersection(SCHOOL):
        return "REVIEW", "source_health_conflicts_with_education_psic"
    if domain == "health" and cats.intersection(SCHOOL) and not cats.intersection(HOSPITAL):
        return "REVIEW", "source_education_conflicts_with_health_psic"
    return "", ""


def _source_identities(row: Mapping[str, Any]) -> set[tuple[str, str]]:
    ids = set()
    for field, source in (
        ("nhfr_code", "doh_nhfr"),
        ("doh_facility_code", "doh_nhfr"),
        ("deped_school_id", "deped_beis"),
        ("beis_school_id", "deped_beis"),
        ("school_id", "deped_beis"),
    ):
        if clean(row.get(field)):
            ids.add((source, clean(row[field]).casefold()))
    for part in _parts(row):
        if part.startswith("ref:nhfr="):
            ids.add(("doh_nhfr", part.partition("=")[2].strip()))
        if part.startswith("ref:deped=") or part.startswith("ref:deped:school="):
            ids.add(("deped_beis", part.partition("=")[2].strip()))
    if clean(row.get("official_id_source")) and clean(row.get("official_id")):
        ids.add((clean(row["official_id_source"]).casefold(), clean(row["official_id"]).casefold()))
    return ids


def _explicit_sides(row: Mapping[str, Any], domain: str) -> set[str]:
    sides: set[str] = set()
    # A basic category is a coarse class, not an explicit ownership assertion.
    for part in _parts(row, include_basic=False):
        if domain == "education" and part in {"public_school", "private_school"}:
            sides.add("public" if part == "public_school" else "private")
        if "=" in part:
            k, val = part.split("=", 1)
            if k.strip() in OWNER_KEYS:
                if val.strip() in PUBLIC:
                    sides.add("public")
                if val.strip() in PRIVATE:
                    sides.add("private")
    for field in ("osm_ownership", "overture_ownership", "osm_owner_type", "overture_owner_type"):
        value = key(row.get(field)).replace(" ", "_")
        if value in PUBLIC:
            sides.add("public")
        if value in PRIVATE:
            sides.add("private")
    return sides


@dataclass(frozen=True)
class VerifiedLink:
    canonical_id: str
    domain: str
    side: str
    source: str
    source_id: str
    source_url: str
    verified_as_of: str


def load_verified_links(path: Path | None) -> dict[str, VerifiedLink]:
    if path is None:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != VERIFIED_SCHEMA or not isinstance(payload.get("links"), list):
        raise ValueError("unsupported verified institution link schema")
    result: dict[str, VerifiedLink] = {}
    for i, item in enumerate(payload["links"], 1):
        if not isinstance(item, dict) or item.get("verified") is not True:
            raise ValueError(f"verified link {i}: independently verified=true is required")
        cid = clean(item.get("canonical_id"))
        source_url = clean(item.get("source_url"))
        link = VerifiedLink(
            cid,
            clean(item.get("domain")),
            clean(item.get("ownership")),
            clean(item.get("source")),
            clean(item.get("source_id")),
            source_url,
            clean(item.get("verified_as_of")),
        )
        if (
            not cid
            or not link.source
            or not link.source_id
            or link.domain not in {"education", "health"}
            or link.side not in {"public", "private"}
            or not https_host(source_url)
        ):
            raise ValueError(f"verified link {i}: incomplete identity/provenance")
        try:
            date.fromisoformat(link.verified_as_of)
        except ValueError as exc:
            raise ValueError(f"verified link {i}: invalid verification date") from exc
        if cid in result:
            raise ValueError(f"duplicate verified link for canonical_id={cid!r}")
        result[cid] = link
    return result


@dataclass(frozen=True)
class InstitutionDecision:
    status: str = "NO_EVIDENCE"
    domain: str = ""
    ownership_candidate: str = ""
    basis: str = ""
    source_references: tuple[str, ...] = ()
    reason: str = ""
    promotion_eligible: bool = False
    audit: tuple[str, ...] = ()

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


class InstitutionResolver:
    """Source-aware identity evidence. No classification or ownership mutation."""

    def __init__(
        self,
        index: InstitutionIndex | None = None,
        verified_links: Mapping[str, VerifiedLink] | None = None,
    ) -> None:
        self.index = index or InstitutionIndex(())
        self.links = dict(verified_links or {})

    def assess(self, row: Mapping[str, Any]) -> InstitutionDecision:
        domain = domain_of(row)
        gate, reason = _gate(row, domain)
        if gate:
            return InstitutionDecision(gate, domain, reason=reason)
        cid = clean(row.get("canonical_id"))
        names = _names(row)
        canonical = key(names[0])
        explicit = _explicit_sides(row, domain)
        if len(explicit) > 1:
            return InstitutionDecision("REVIEW", domain, reason="contradictory_explicit_ownership")
        ids = _source_identities(row)
        exact = self.index.by_identifiers(ids)
        if exact:
            matches = tuple(x for x in exact if x.domain == domain)
            if len(matches) != len(exact):
                return InstitutionDecision("REVIEW", domain, reason="registry_domain_conflict")
        else:
            matches = self.index.by_names(names, domain)
            if ids and matches:
                return InstitutionDecision(
                    "REVIEW", domain, reason="supplied_external_id_not_in_registry"
                )
        if cid in self.links:
            link = self.links[cid]
            if link.domain != domain:
                return InstitutionDecision("REVIEW", domain, reason="verified_link_domain_conflict")
            # A verified canonical link does not license a conflicting external ID.
            known_for_link = {value for source, value in ids if source == link.source}
            if known_for_link and link.source_id.casefold() not in known_for_link:
                return InstitutionDecision(
                    "REVIEW", domain, reason="verified_link_external_id_conflict"
                )
            if explicit and link.side not in explicit:
                return InstitutionDecision("REVIEW", domain, reason="verified_link_source_conflict")
            if matches and any(r.ownership and r.ownership != link.side for r in matches):
                return InstitutionDecision(
                    "REVIEW", domain, reason="verified_link_registry_conflict"
                )
            return InstitutionDecision(
                "VERIFIED_LINK",
                domain,
                link.side,
                "verified_canonical_registry_link",
                (f"{link.source}:{link.source_id}",),
                "independently_verified_entity_link",
                True,
            )
        if matches:
            sides = {r.ownership for r in matches if r.ownership}
            if len(sides) > 1:
                return InstitutionDecision("REVIEW", domain, reason="registry_ownership_conflict")
            if len(matches) > 1:
                return InstitutionDecision(
                    "REVIEW",
                    domain,
                    source_references=tuple(r.reference for r in matches),
                    reason="multiple_registered_sites_or_sources",
                )
            match = matches[0]
            refs = (match.reference,)
            registered = {key(n) for n in (match.name, *match.aliases)}
            if canonical not in registered:
                return InstitutionDecision(
                    "REVIEW",
                    domain,
                    source_references=refs,
                    reason="canonical_name_not_registered_site",
                )
            record_names = {key(n) for n in names if key(n)}
            if not record_names & registered:
                return InstitutionDecision("REVIEW", domain, reason="registry_name_conflict")
            # An official ID is not permission to identify a different local branch.
            city_values = {
                key(row.get(k))
                for k in ("city", "municipality", "osm_city", "overture_city")
                if key(row.get(k))
            }
            if len(city_values) > 1:
                return InstitutionDecision("REVIEW", domain, reason="conflicting_place_localities")
            town_values = {
                clean(row.get(k))
                for k in ("town_psgc", "municipality_psgc", "city_psgc", "area_psgc")
                if clean(row.get(k))
            }
            if len(town_values) > 1:
                return InstitutionDecision("REVIEW", domain, reason="conflicting_place_psgc")
            if match.town_psgc and town_values and match.town_psgc not in town_values:
                return InstitutionDecision("REVIEW", domain, reason="school_psgc_mismatch")
            if match.city and city_values and key(match.city) not in city_values:
                return InstitutionDecision(
                    "REVIEW", domain, reason="registered_site_locality_mismatch"
                )
            # Site-level addresses are identity constraints, not arbitrary aliases.
            addresses = {
                key(row.get(k))
                for k in ("address", "osm_address", "overture_address")
                if key(row.get(k))
            }
            if len(addresses) > 1:
                return InstitutionDecision("REVIEW", domain, reason="conflicting_place_addresses")
            if match.address and addresses and key(match.address) not in addresses:
                return InstitutionDecision(
                    "REVIEW", domain, reason="registered_site_address_mismatch"
                )
            regions = {
                region_code(row.get(k))
                for k in ("region", "region_code", "admin_region")
                if region_code(row.get(k))
            }
            if len(regions) > 1:
                return InstitutionDecision("REVIEW", domain, reason="conflicting_place_regions")
            if match.region and regions and match.region not in regions:
                return InstitutionDecision("REVIEW", domain, reason="hei_region_mismatch")
            if explicit and match.ownership and match.ownership not in explicit:
                return InstitutionDecision(
                    "REVIEW", domain, reason="source_registry_ownership_conflict"
                )
            # Only genuine official codes can establish site-level identity.
            matched_id = (match.source, match.source_id.casefold()) in ids
            if (
                matched_id
                and match.id_kind == "official_code"
                and match.site_scope == "site"
                and match.city
                and city_values
                and match.ownership
                and match.source_url
                and match.as_of
            ):
                return InstitutionDecision(
                    "EXACT_EXTERNAL_ID",
                    domain,
                    match.ownership,
                    "official_registry_external_id",
                    refs,
                    "official_id_name_city_agree",
                    True,
                )
            side = match.ownership
            basis = (
                "unverified_beis_school_identity"
                if match.source == "deped_beis"
                else "unifast_participating_hei"
                if match.source == "unifast_ra10931"
                else "registry_name_city_provisional"
            )
            return InstitutionDecision(
                "PROVISIONAL",
                domain,
                side,
                basis,
                refs,
                "registry_candidate_not_independently_verified",
                False,
            )
        if explicit:
            side = next(iter(explicit))
            return InstitutionDecision(
                "EXPLICIT_SOURCE_CLAIM",
                domain,
                side,
                "explicit_source_attribute",
                (),
                "source_ownership_claim_not_official_registry_verification",
                False,
            )
        # Operator/owner organization name does not prove legal ownership.
        return InstitutionDecision(
            "NO_EVIDENCE", domain, reason="no_qualified_institution_evidence"
        )
