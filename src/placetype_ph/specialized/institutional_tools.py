"""Tools around the institution registry: import, build, install, convert and review.

None of these tools changes a classification. They prepare reference files that the
institution stage reads (``PLACETYPE_PH_INSTITUTION_REFERENCES``), convert
verified-link files, and write review queues. Outputs are new files; an existing file is
never overwritten.

- ``import_official_export``: DOH NHFR or DepEd masterlist exports (CSV or Excel) with an
  explicit public/private sector, to official-record rows. A row without an explicit
  sector is left out, never guessed. NHFR rows are kept only for hospitals.
- ``level3_hospital_rows``: an official Level 3 hospital table (the PhilHealth accredited
  hospital list transcribed with its row numbers, or an NHFR export) to the Level 3
  hospital reference read by ``load_hospitals``.
- ``install_references``: copy the bundled reference snapshots, after checking their
  hashes, into a folder and write the reference configuration that points to them.
- ``convert_registry_links``: convert a verified-link file in the registry layout
  (``registry_id`` instead of ``source`` and ``source_id``) to the documented format.
- ``review_institutions``: a review queue for places matched to registry records, with
  relationship hypotheses and evidence checks. It never verifies identity or promotes
  ownership.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from .institutional_registry import (
    HOSPITAL_FIELDS,
    OFFICIAL_FIELDS,
    OFFICIAL_SOURCES,
    Institution,
    https_host,
    key,
    load_beis,
    load_heida,
    load_hospitals,
    load_level1_hospitals,
    load_level2_hospitals,
    load_official_records,
    load_unifast,
)
from .institutional_shadow import VERIFIED_SCHEMA, load_verified_links

POLICY = "institution-registry-tools-v1"
REGISTRY_DATA = Path(__file__).resolve().parent.parent / "data" / "registry"
MANIFEST_SCHEMA = "placetype-ph-registry-data-v1"
CONFIG_SCHEMA = "placetype-ph-institution-reference-config-v1"
LOADERS: dict[str, Callable[[Path], tuple[Institution, ...]]] = {
    "beis": load_beis,
    "unifast": load_unifast,
    "hospitals": load_hospitals,
    "heida": load_heida,
    "level1_hospitals": load_level1_hospitals,
    "level2_hospitals": load_level2_hospitals,
    "official_records": load_official_records,
}
# Source family of a verified link, taken from the official host of its source URL.
LINK_SOURCE_BY_HOST = {
    "nhfr.doh.gov.ph": "doh_nhfr",
    "hfsrb.doh.gov.ph": "doh_nhfr",
    "deped.gov.ph": "deped_beis",
    "www.deped.gov.ph": "deped_beis",
    "depedncr.com.ph": "deped_beis",
    "www.depedncr.com.ph": "deped_beis",
    "philhealth.gov.ph": "philhealth_hospitals",
    "www.philhealth.gov.ph": "philhealth_hospitals",
    "unifast.gov.ph": "unifast_ra10931",
    "heida.ched.gov.ph": "ched_hei",
    "ched.gov.ph": "ched_cmo",
    "www.ched.gov.ph": "ched_cmo",
    "quezoncity.gov.ph": "qc_government",
}
REGISTRY_LINK_FIELDS = frozenset(
    {"canonical_id", "verified", "domain", "ownership", "source_url", "registry_id",
     "verified_as_of"}
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.casefold() in {"nan", "none", "nat", "<na>"} else text


def _field(row: Mapping[str, Any], *aliases: str) -> str:
    """Value of the first alias present, ignoring case, spaces and punctuation."""
    indexed = {re.sub(r"[^a-z0-9]", "", str(k).casefold()): _text(v) for k, v in row.items()}
    for alias in aliases:
        value = indexed.get(re.sub(r"[^a-z0-9]", "", alias.casefold()), "")
        if value:
            return value
    return ""


def read_table(path: Path) -> list[dict[str, str]]:
    """Rows of an official export: CSV or Excel. A PDF must first be transcribed to CSV."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=str).fillna("").to_dict("records")
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    raise ValueError("official export must be CSV or Excel (.xlsx or .xls)")


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _iso_date(value: str, label: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO date (YYYY-MM-DD)") from exc


# Official exports ----------------------------------------------------------------------


def import_official_export(
    path: Path, source: str, *, as_of: str, source_url: str, city: str = ""
) -> list[dict[str, str]]:
    """Official-record rows from a DOH NHFR or DepEd masterlist export.

    Supported columns (case, spaces and punctuation are ignored):

    - DOH NHFR: Health Facility Code, Health Facility Name, Ownership Major
      Classification, Health Facility Type, City/Municipality, Street Address.
    - DepEd: School ID, School Name, Sector (or School Type, Public/Private
      Classification, Ownership), City/Municipality (or Division), Address.

    ``city`` keeps only rows whose city matches it. Without it all cities are kept.
    """
    if source not in {"doh_nhfr", "deped_masterlist"}:
        raise ValueError("source must be doh_nhfr or deped_masterlist")
    hosts = OFFICIAL_SOURCES[source][0]
    if https_host(source_url) not in hosts:
        raise ValueError(f"source_url must be an official HTTPS address of {source}")
    _iso_date(as_of, "as_of")
    wanted_city = key(city)
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for record in read_table(path):
        if source == "doh_nhfr":
            identifier = _field(record, "Health Facility Code", "Facility Code", "NHFR Code")
            name = _field(record, "Health Facility Name", "Facility Name",
                          "Name of Health Facility")
            label = key(_field(record, "Ownership Major Classification", "Ownership Major",
                               "Ownership"))
            facility_type = key(_field(record, "Health Facility Type",
                                       "Type of Health Facility", "Facility Type"))
            place_city = _field(record, "City/Municipality", "City Municipality",
                                "Municipality", "City")
            address = _field(record, "Street Address", "Address", "Street")
            domain = "health"
            if not re.search(r"\bhospital\b", facility_type):
                continue
            ownership = (
                "public" if label in {"government", "public", "government owned"}
                else "private" if label in {"private", "privately owned"} else ""
            )
        else:
            identifier = _field(record, "School ID", "SchoolID")
            name = _field(record, "School Name", "School")
            label = key(_field(record, "Sector", "School Type",
                               "Public/Private Classification", "Ownership", "Classification"))
            place_city = _field(record, "City/Municipality", "City", "Division",
                                "Schools Division Office")
            address = _field(record, "Address", "School Address")
            domain = "education"
            ownership = "public" if label == "public" else "private" if label == "private" else ""
        if not identifier or not name or not ownership or not place_city:
            continue
        if wanted_city and key(place_city) != wanted_city:
            continue
        if identifier in seen:
            raise ValueError(f"duplicate registry identifier in {Path(path).name}: {identifier}")
        seen.add(identifier)
        rows.append(
            {
                "source": source,
                "source_id": identifier,
                "name": name,
                "domain": domain,
                "ownership": ownership,
                "city": place_city,
                "address": address,
                "aliases": "",
                "source_url": source_url,
                "as_of": as_of,
                "id_kind": "official_code",
                "evidence_type": "explicit_sector",
            }
        )
    return rows


# Level 3 hospital reference ------------------------------------------------------------


def _level3(value: str) -> bool:
    text = key(value)
    return bool(re.fullmatch(r"(?:level\s*)?(?:3|iii|three)", text))


def level3_hospital_rows(
    records: Iterable[Mapping[str, Any]], *, source: str, url: str, as_of: str
) -> tuple[list[dict[str, str]], Counter[str]]:
    return hospital_rows(records, source=source, url=url, as_of=as_of, level=3)


def hospital_rows(
    records: Iterable[Mapping[str, Any]], *, source: str, url: str, as_of: str, level: int
) -> tuple[list[dict[str, str]], Counter[str]]:
    """Explicit Level 1, 2 or 3 hospital reference rows from an official table.

    A PhilHealth row number is a publication row, never a facility identifier, and is
    stored as ``<as_of>:row-<number>``. An NHFR code is an official facility code.
    """
    if source not in {"philhealth_hospitals", "doh_nhfr"}:
        raise ValueError("source must be philhealth_hospitals or doh_nhfr")
    if level not in {1, 2, 3}:
        raise ValueError("hospital level must be 1, 2 or 3")
    _iso_date(as_of, "as_of")
    output: list[dict[str, str]] = []
    stats: Counter[str] = Counter()
    for n, record in enumerate(records, start=2):
        stats["input"] += 1
        observed_level = _field(
            record, "CAT", "Hospital Level", "Service Capability Level", "Level",
            "Hospital Classification",
        )
        matches = _level3(observed_level) if level == 3 else bool(
            re.fullmatch(
                {1: r"(?:level\s*)?(?:1|i|one)", 2: r"(?:level\s*)?(?:2|ii|two)"}[level],
                key(observed_level),
            )
        )
        if not matches:
            stats["other_hospital_level"] += 1
            continue
        name = _field(record, "NAME OF HEALTH FACILITY", "Health Facility Name",
                      "Facility Name", "Name")
        place_city = _field(record, "MUNICIPALITY", "City/Municipality", "City Municipality",
                            "City")
        address = _field(record, "STREET", "Address", "Street Address")
        owner = _field(record, "SEC", "Ownership Major Classification", "Ownership",
                       "Ownership Major").casefold()
        side = (
            "public" if owner in {"g", "government", "public", "government owned"}
            else "private" if owner in {"p", "private", "privately owned"} else ""
        )
        if source == "doh_nhfr":
            identifier = _field(record, "Health Facility Code", "Facility Code", "NHFR Code")
            kind = "official_code"
        else:
            identifier = _field(record, "Row No", "Publication Row", "No", "Number", "#")
            if identifier:
                identifier = f"{as_of}:row-{identifier}"
            kind = "publication_row"
        if not identifier or not name or not side:
            raise ValueError(
                f"Level {level} hospital input row {n}: missing row number or official code, "
                "name, or explicit G/P ownership"
            )
        output.append(
            {
                "source": source,
                "source_id": identifier,
                "name": name,
                "domain": "health",
                "ownership": side,
                "city": place_city,
                "province": _field(record, "Province"),
                "address": address,
                "aliases": "",
                "source_url": url,
                "as_of": as_of,
                "id_kind": kind,
                "evidence_type": "explicit_sector",
                "facility_level": f"Level {level}",
            }
        )
        stats["included"] += 1
    return output, stats


def write_reference(
    path: Path,
    rows: list[dict[str, str]],
    fields: tuple[str, ...],
    loader: Callable[[Path], tuple[Institution, ...]],
) -> int:
    """Write rows to a new CSV, check it with ``loader``, then publish it."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing reference: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    if pending.exists():
        raise FileExistsError(f"a pending file exists: {pending}")
    try:
        with pending.open("x", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        loaded = loader(pending)
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)
    return len(loaded)


# Bundled snapshots and installation -----------------------------------------------------


def registry_manifest() -> dict[str, Any]:
    document = json.loads((REGISTRY_DATA / "manifest.json").read_text(encoding="utf-8"))
    if document.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("unknown registry data manifest schema")
    return document


def verify_bundled() -> dict[str, dict[str, Any]]:
    """Check every bundled snapshot against its manifest hash and row count."""
    report: dict[str, dict[str, Any]] = {}
    for name, entry in registry_manifest()["files"].items():
        path = REGISTRY_DATA / name
        digest = _sha256(path)
        if digest != entry["sha256"]:
            raise ValueError(f"bundled registry file {name} does not match its manifest hash")
        if entry.get("reference_key") in LOADERS:
            rows = len(LOADERS[entry["reference_key"]](path))
        else:
            rows = len(read_table(path))
        if rows != entry["rows"]:
            raise ValueError(
                f"bundled registry file {name}: {rows} rows, manifest says {entry['rows']}"
            )
        report[name] = {"sha256": digest, "rows": rows}
    return report


def install_references(
    target: Path,
    *,
    include: Iterable[str] | None = None,
    verified_links: Path | None = None,
) -> Path:
    """Copy bundled snapshots into ``target`` and write ``institution_references.json``.

    ``include`` names the reference types to install. Each bundled file is checked
    against the manifest before it is copied. ``verified_links``, when given, is
    validated and copied too. Point ``PLACETYPE_PH_INSTITUTION_REFERENCES`` at the
    returned file to use the references in a run.
    """
    target = Path(target).expanduser().resolve()
    wanted = list(dict.fromkeys(LOADERS if include is None else include))
    unknown = set(wanted) - LOADERS.keys()
    if unknown:
        raise ValueError(f"unknown reference types: {sorted(unknown)}")
    config_path = target / "institution_references.json"
    if config_path.exists():
        raise FileExistsError(f"refusing to overwrite existing configuration: {config_path}")
    verify_bundled()
    files = registry_manifest()["files"]
    by_key = {entry["reference_key"]: name for name, entry in files.items()
              if entry.get("reference_key") in LOADERS}
    plan: dict[str, tuple[Path, Path]] = {}
    for label in wanted:
        name = by_key[label]
        plan[label] = (REGISTRY_DATA / name, target / name)
    if verified_links is not None:
        load_verified_links(Path(verified_links))
        plan["verified_links"] = (Path(verified_links), target / "verified_links.json")
    for _, destination in plan.values():
        if destination.exists():
            raise FileExistsError(f"refusing to overwrite existing file: {destination}")
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    try:
        for source, destination in plan.values():
            shutil.copyfile(source, destination)
            written.append(destination)
        config = {
            "schema": CONFIG_SCHEMA,
            "references": {label: str(dest) for label, (_, dest) in plan.items()},
        }
        with config_path.open("x", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
        written.append(config_path)
        from .institutional_pipeline import load_reference_config

        load_reference_config(config_path)
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return config_path


# Verified links in the registry layout --------------------------------------------------


def convert_registry_links(payload: Mapping[str, Any], *, schema: str) -> dict[str, Any]:
    """Convert a registry-layout verified-link file to the documented format.

    ``schema`` is the schema identifier written in the input file; it must match, so that
    an unrelated JSON file is not converted by mistake. Each link must carry
    ``canonical_id``, ``verified``, ``domain``, ``ownership``, ``source_url``,
    ``registry_id`` and ``verified_as_of``. The source family is taken from the official
    host of ``source_url``; an unknown host is refused. The converted file must then pass
    the normal verified-link checks.
    """
    if not schema or payload.get("schema") != schema:
        raise ValueError("the input file does not declare the given schema identifier")
    links = payload.get("links")
    if not isinstance(links, list):
        raise ValueError("the input file has no list of links")
    converted = []
    for i, item in enumerate(links, 1):
        if not isinstance(item, dict) or not REGISTRY_LINK_FIELDS <= set(item):
            raise ValueError(f"link {i}: registry-layout fields are missing")
        host = https_host(_text(item.get("source_url")))
        if host not in LINK_SOURCE_BY_HOST:
            raise ValueError(f"link {i}: source_url is not an official registry host")
        converted.append(
            {
                "canonical_id": item.get("canonical_id"),
                "verified": item.get("verified"),
                "domain": item.get("domain"),
                "ownership": item.get("ownership"),
                "source": LINK_SOURCE_BY_HOST[host],
                "source_id": item.get("registry_id"),
                "source_url": item.get("source_url"),
                "verified_as_of": item.get("verified_as_of"),
            }
        )
    return {"schema": VERIFIED_SCHEMA, "links": converted}


def write_converted_links(source: Path, output: Path, *, schema: str) -> int:
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing file: {output}")
    payload = json.loads(Path(source).read_text(encoding="utf-8-sig"))
    converted = convert_registry_links(payload, schema=schema)
    pending = output.with_name(output.name + ".pending")
    try:
        pending.write_text(json.dumps(converted, indent=2) + "\n", encoding="utf-8")
        count = len(load_verified_links(pending))
        os.replace(pending, output)
    finally:
        pending.unlink(missing_ok=True)
    return count


# Review queue ---------------------------------------------------------------------------

SIDE_BY_IO80 = {"74": "public", "75": "private", "76": "public", "77": "private"}
SUBUNIT = re.compile(
    r"\b(?:\d+(?:st|nd|rd|th)\s+floor|wing|annex|ward|radiology|diagnostic\s+imaging|"
    r"outpatient|department|emergency\s+room|branch|campus|extension\s+campus|laboratory)\b",
    re.I,
)
PUBLIC_VALUES = {"public", "government", "state", "local_government", "national_government"}
PRIVATE_VALUES = {"private", "privately_owned"}
CITY_FIELDS = ("city", "municipality", "osm_city", "overture_city")
REVIEW_FIELDS = (
    "canonical_id",
    "name",
    "psic_code",
    "psic_status",
    "io80_codes",
    "io80_resolved_code",
    "institution_evidence_status",
    "institution_resolution_status",
    "institution_resolution_reason",
    "institution_candidate_ownership",
    "registry_reference",
    "registry_name",
    "registry_ownership",
    "registered_city",
    "registered_address",
    "registered_source_id",
    "registered_id_kind",
    "registered_as_of",
    "relationship_hypothesis",
    "locality_basis",
    "current_ownership_side",
    "ownership_opportunity",
    "verification_required",
    "place_source_names",
    "source_name_relation",
    "source_external_id_values",
    "official_id_relation",
    "source_ownership_evidence",
    "ownership_evidence_relation",
    "place_city_values",
    "locality_comparison",
    "place_longitude",
    "place_latitude",
    "coordinate_basis",
    "subunit_or_branch_signal",
    "candidate_review_reasons",
    "evidence_priority",
    "identity_verified",
    "promotion_allowed",
)
GROUP_FIELDS = (
    "registry_reference",
    "registry_name",
    "registry_ownership",
    "candidate_observations",
    "unassigned_io80_observations",
    "canonical_ids",
    "candidate_relationships",
    "official_id_evidence",
    "independently_verified",
)


def _norm(value: Any) -> str:
    return key(value)


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(v for v in (_text(x) for x in values) if v))


def _combine(values: Iterable[str]) -> str:
    return " | ".join(_unique(values))


def _tags(record: Mapping[str, Any]) -> dict[str, list[str]]:
    tags: dict[str, list[str]] = defaultdict(list)
    for column in ("osm_category", "overture_category"):
        for segment in _text(record.get(column)).split("|"):
            if "=" in segment:
                name, value = segment.split("=", 1)
                if name.strip() and value.strip():
                    tags[name.strip().casefold()].append(value.strip())
    return tags


def _locality_class(registered_city: str, address: str, place_city: str) -> tuple[str, str]:
    """Location gives a triage hint, never identity proof."""
    city, place, text = _norm(registered_city), _norm(place_city), _norm(address)
    if not place:
        return "INSTITUTIONAL_AFFILIATION_CANDIDATE", "place_locality_not_supplied"
    if city:
        if city == place:
            return "SAME_LOCALITY_SITE_CANDIDATE", "registered_city_equals_place_city"
        return ("CROSS_LOCALITY_BRANCH_OR_AFFILIATE_CANDIDATE",
                "registered_city_differs_from_place_city")
    if place in text:
        return "SAME_LOCALITY_SITE_CANDIDATE", "address_mentions_place_city_not_official_psgc"
    return ("INSTITUTIONAL_AFFILIATION_CANDIDATE",
            "registered_city_missing_no_inference_from_address")


def _official_ids(place: Mapping[str, Any], tags: dict[str, list[str]],
                  record: Institution | None) -> tuple[str, str]:
    """Compare only real official-code namespaces; never compare publication rows."""
    if record is None:
        return "", "NO_UNIQUE_REGISTRY_RECORD"
    if record.id_kind != "official_code":
        return "", "NOT_COMPARABLE_PUBLICATION_ROW"
    if record.source == "doh_nhfr":
        observed = _unique([*(_text(place.get(k)) for k in ("nhfr_code", "doh_facility_code")),
                            *tags.get("ref:nhfr", []), *tags.get("nhfr:code", [])])
    elif record.source == "ched_hei":
        observed = _unique([_text(place.get("ched_hei_code")), *tags.get("ref:ched", [])])
    elif record.source.startswith("deped"):
        observed = _unique([*(_text(place.get(k)) for k in ("deped_school_id", "beis_school_id",
                                                            "school_id")),
                            *tags.get("ref:deped", [])])
    else:
        return "", "NO_COMPARABLE_OFFICIAL_CODE_NAMESPACE"
    if not observed:
        return "", "NO_COMPARABLE_ID"
    if len({x.casefold() for x in observed}) > 1:
        return _combine(observed), "SOURCE_OFFICIAL_IDS_DISAGREE"
    if observed[0].casefold() == record.source_id.casefold():
        return observed[0], "OFFICIAL_CODE_EQUALS_REGISTRY_REVIEW"
    return observed[0], "OFFICIAL_CODE_CONFLICT_REVIEW"


def _ownership_relation(place: Mapping[str, Any], tags: dict[str, list[str]],
                        registered: str) -> tuple[str, str]:
    values = _unique([
        *(_text(place.get(k)) for k in ("osm_ownership", "overture_ownership",
                                         "osm_owner_type", "overture_owner_type")),
        *tags.get("ownership", []), *tags.get("owner:type", []), *tags.get("owner_type", []),
        *tags.get("ownership:type", []),
    ])
    sides = set()
    for value in values:
        normal = _norm(value).replace(" ", "_")
        if normal in PUBLIC_VALUES:
            sides.add("public")
        elif normal in PRIVATE_VALUES:
            sides.add("private")
    if len(sides) > 1:
        return _combine(values), "SOURCE_OWNERSHIP_CONFLICT_REVIEW"
    if not sides:
        return _combine(values), "NO_EXPLICIT_OWNERSHIP"
    relation = ("EXPLICIT_OWNERSHIP_AGREES_WITH_REGISTRY" if registered in sides
                else "EXPLICIT_OWNERSHIP_CONFLICTS_WITH_REGISTRY")
    return _combine(values), relation


def _name_relation(place: Mapping[str, Any], official: str) -> tuple[str, str]:
    names = _unique([_text(place.get("osm_name")), _text(place.get("overture_name"))])
    if len({_norm(v) for v in names}) > 1:
        return _combine(names), "RAW_SOURCE_NAMES_DISAGREE_REVIEW"
    if not names:
        return "", "NO_RAW_SOURCE_NAMES"
    same = _norm(names[0]) == _norm(official)
    return _combine(names), ("RAW_NAME_EQUALS_OFFICIAL" if same
                             else "RAW_NAME_DIFFERS_FROM_OFFICIAL_REVIEW")


def _locality(place: Mapping[str, Any], registered_city: str) -> tuple[str, str]:
    observed = _unique([_text(place.get(k)) for k in CITY_FIELDS])
    values = {_norm(x) for x in observed}
    registered = _norm(registered_city)
    if not registered:
        return _combine(observed), "OFFICIAL_CITY_MISSING"
    if not values:
        return "", "PLACE_CITY_MISSING"
    if len(values) > 1:
        return _combine(observed), "PLACE_LOCALITY_DISAGREEMENT_REVIEW"
    if registered in values:
        return _combine(observed), "LOCALITY_TEXT_AGREES_NOT_IDENTITY_PROOF"
    return _combine(observed), "CROSS_LOCALITY_BRANCH_OR_SITE_REVIEW_NOT_REJECTION"


def _coordinates(place: Mapping[str, Any]) -> tuple[str, str, str]:
    for lat_key, lon_key in (("latitude", "longitude"), ("lat", "lon"), ("lat", "lng"),
                             ("osm_lat", "osm_lon"), ("overture_lat", "overture_lon")):
        lat_text, lon_text = _text(place.get(lat_key)), _text(place.get(lon_key))
        if not lat_text or not lon_text:
            continue
        try:
            lat, lon = float(lat_text), float(lon_text)
        except ValueError:
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return f"{lon:.8f}", f"{lat:.8f}", f"explicit_WGS84:{lat_key}/{lon_key}"
    return "", "", "NO_USABLE_COORDINATE"


def review_institutions(
    classified: pd.DataFrame,
    places: pd.DataFrame,
    records: Iterable[Institution],
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, Any]]:
    """Review rows, grouped registry rows and counts for places matched to registry records.

    ``classified`` is a ``classified_pois.parquet`` written with the institution stage
    on; ``places`` is the input of that run; ``records`` are the references it used.
    """
    if "institution_resolution_reference" not in classified.columns:
        raise ValueError("classified places have no institution columns; run with references")
    for label, frame in (("classified", classified), ("places", places)):
        ids = frame["canonical_id"].astype(str).str.strip()
        if ids.eq("").any() or ids.duplicated().any():
            raise ValueError(f"{label}: blank or duplicate canonical_id")
    by_ref = {f"{r.source}:{r.source_id}": r for r in records}
    by_id = {str(r["canonical_id"]).strip(): r for r in places.to_dict("records")}
    rows: list[dict[str, str]] = []
    counts: Counter[str] = Counter()
    for item in classified.to_dict("records"):
        references = [x for x in _text(item.get("institution_resolution_reference")).split("|")
                      if x]
        if not references:
            continue
        cid = _text(item.get("canonical_id"))
        place = by_id.get(cid)
        evidence = _text(item.get("institution_evidence_status"))
        record = by_ref.get(references[0]) if len(references) == 1 else None
        current = SIDE_BY_IO80.get(_text(item.get("io80_resolved_code")), "")
        suggested = record.ownership if record else ""
        conflict = evidence == "REVIEW" or record is None
        place_city = _combine(_text((place or {}).get(k)) for k in CITY_FIELDS)
        if conflict:
            hypothesis = "IDENTITY_CONFLICT_REVIEW"
            locality_basis = "official_identity_not_uniquely_resolved"
        else:
            hypothesis, locality_basis = _locality_class(record.city, record.address,
                                                          place_city.split(" | ")[0])
        if conflict or not suggested:
            opportunity = "IDENTITY_REVIEW_FIRST"
        elif not current:
            opportunity = "CURRENT_IO80_UNASSIGNED"
        elif current == suggested:
            opportunity = "CURRENT_IO80_ALREADY_AGREES"
        else:
            opportunity = "CURRENT_IO80_DISAGREES_REVIEW"
        if hypothesis == "IDENTITY_CONFLICT_REVIEW":
            requirement = "Resolve conflicting names or identifiers; verify the entity and activity"
        elif hypothesis == "CROSS_LOCALITY_BRANCH_OR_AFFILIATE_CANDIDATE":
            requirement = ("Independent official branch or affiliate link, or site ID; "
                           "verify the legal owner and activity")
        else:
            requirement = "Independent official site ID or an independently verified link"
        row = {
            "canonical_id": cid,
            "name": _text(item.get("name")) or _text((place or {}).get("canonical_name")),
            "psic_code": _text(item.get("psic_code")),
            "psic_status": _text(item.get("psic_status")),
            "io80_codes": _text(item.get("io80_codes")),
            "io80_resolved_code": _text(item.get("io80_resolved_code")),
            "institution_evidence_status": evidence,
            "institution_resolution_status": _text(item.get("institution_resolution_status")),
            "institution_resolution_reason": _text(item.get("institution_resolution_reason")),
            "institution_candidate_ownership": _text(item.get("institution_candidate_ownership")),
            "registry_reference": "|".join(references),
            "registry_name": record.name if record else "",
            "registry_ownership": suggested,
            "registered_city": record.city if record else "",
            "registered_address": record.address if record else "",
            "registered_source_id": record.source_id if record else "",
            "registered_id_kind": record.id_kind if record else "",
            "registered_as_of": record.as_of if record else "",
            "relationship_hypothesis": hypothesis,
            "locality_basis": locality_basis,
            "current_ownership_side": current,
            "ownership_opportunity": opportunity,
            "verification_required": requirement,
            "identity_verified": "false",
            "promotion_allowed": "false",
        }
        if place is None:
            row.update(candidate_review_reasons="candidate_not_found_in_places",
                       evidence_priority="BLOCKED_MISSING_PLACE")
        else:
            tags = _tags(place)
            names, name_relation = _name_relation(place, row["registry_name"])
            observed, id_relation = _official_ids(place, tags, record)
            owners, owner_relation = _ownership_relation(place, tags, suggested)
            cities, locality = _locality(place, row["registered_city"])
            lon, lat, basis = _coordinates(place)
            subunits = _combine(v for v in (row["name"], _text(place.get("osm_name")),
                                            _text(place.get("overture_name")))
                                if v and SUBUNIT.search(v))
            issues = []
            if evidence not in {"PROVISIONAL", "EXACT_EXTERNAL_ID", "VERIFIED_LINK"}:
                issues.append("registry_status_requires_review")
            if name_relation in {"RAW_SOURCE_NAMES_DISAGREE_REVIEW",
                                 "RAW_NAME_DIFFERS_FROM_OFFICIAL_REVIEW"}:
                issues.append("raw_name_evidence_requires_review")
            if "CONFLICT" in id_relation or id_relation == "SOURCE_OFFICIAL_IDS_DISAGREE":
                issues.append("official_code_conflict")
            if owner_relation in {"SOURCE_OWNERSHIP_CONFLICT_REVIEW",
                                  "EXPLICIT_OWNERSHIP_CONFLICTS_WITH_REGISTRY"}:
                issues.append("direct_ownership_conflict")
            if locality in {"PLACE_LOCALITY_DISAGREEMENT_REVIEW",
                            "CROSS_LOCALITY_BRANCH_OR_SITE_REVIEW_NOT_REJECTION"}:
                issues.append("site_locality_review_not_institution_rejection")
            if subunits:
                issues.append("possible_subunit_or_branch")
            if id_relation == "OFFICIAL_CODE_EQUALS_REGISTRY_REVIEW":
                issues.append("official_code_present_requires_independent_verification")
            if not issues:
                issues.append("independent_entity_or_branch_verification_required")
            priority = ("HIGH_CONFLICT" if any("conflict" in x for x in issues)
                        else "HIGH_UNASSIGNED" if opportunity == "CURRENT_IO80_UNASSIGNED"
                        else "REVIEW")
            row.update(
                place_source_names=names,
                source_name_relation=name_relation,
                source_external_id_values=observed,
                official_id_relation=id_relation,
                source_ownership_evidence=owners,
                ownership_evidence_relation=owner_relation,
                place_city_values=cities,
                locality_comparison=locality,
                place_longitude=lon,
                place_latitude=lat,
                coordinate_basis=basis,
                subunit_or_branch_signal=subunits,
                candidate_review_reasons=";".join(issues),
                evidence_priority=priority,
            )
        rows.append({k: row.get(k, "") for k in REVIEW_FIELDS})
        counts["candidates"] += 1
        counts[f"relationship:{hypothesis}"] += 1
        counts[f"opportunity:{opportunity}"] += 1
        counts[f"priority:{row['evidence_priority']}"] += 1
    order = {"HIGH_CONFLICT": 0, "HIGH_UNASSIGNED": 1, "BLOCKED_MISSING_PLACE": 2, "REVIEW": 3}
    rows.sort(key=lambda r: (order.get(r["evidence_priority"], 4), r["canonical_id"]))
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row["registry_reference"]].append(row)
    grouped = [
        {
            "registry_reference": reference,
            "registry_name": items[0]["registry_name"],
            "registry_ownership": items[0]["registry_ownership"],
            "candidate_observations": str(len(items)),
            "unassigned_io80_observations": str(
                sum(r["ownership_opportunity"] == "CURRENT_IO80_UNASSIGNED" for r in items)),
            "canonical_ids": " | ".join(sorted(r["canonical_id"] for r in items)),
            "candidate_relationships": _combine(r["relationship_hypothesis"] for r in items),
            "official_id_evidence": _combine(r["official_id_relation"] for r in items),
            "independently_verified": "false",
        }
        for reference, items in sorted(groups.items())
    ]
    summary = {
        "policy": POLICY,
        "counts": dict(sorted(counts.items())),
        "verified_matches_created": 0,
        "ownership_changes": 0,
    }
    return rows, grouped, summary


def safe_csv_cell(value: Any) -> str:
    """Stop spreadsheet programs from reading untrusted source text as a formula."""
    text = _text(value)
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        return text
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def write_review(out_dir: Path, rows: list[dict[str, str]], grouped: list[dict[str, str]],
                 summary: dict[str, Any]) -> dict[str, Path]:
    """Write the queue, the grouped references and the summary; refuse to overwrite."""
    out_dir = Path(out_dir)
    paths = {
        "queue": out_dir / "institution_review_queue.csv",
        "grouped": out_dir / "institution_review_by_reference.csv",
        "summary": out_dir / "institution_review_summary.json",
    }
    existing = [p for p in paths.values() if p.exists()]
    if existing:
        raise FileExistsError(f"refusing to overwrite existing review output: {existing[0]}")
    out_dir.mkdir(parents=True, exist_ok=True)
    for label, fields, data in (("queue", REVIEW_FIELDS, rows),
                                ("grouped", GROUP_FIELDS, grouped)):
        with paths[label].open("x", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            for row in data:
                writer.writerow({k: safe_csv_cell(row.get(k, "")) for k in fields})
    with paths["summary"].open("x", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return paths


def load_references(config_path: Path) -> tuple[Institution, ...]:
    """All registry records named in a reference configuration (not the verified links)."""
    from .institutional_pipeline import load_reference_config

    references = load_reference_config(Path(config_path))
    records: list[Institution] = []
    for label, path in references.items():
        if label in LOADERS:
            records.extend(LOADERS[label](path))
    return tuple(records)


__all__ = [
    "HOSPITAL_FIELDS",
    "OFFICIAL_FIELDS",
    "convert_registry_links",
    "import_official_export",
    "install_references",
    "level3_hospital_rows",
    "load_references",
    "read_table",
    "review_institutions",
    "verify_bundled",
    "write_converted_links",
    "write_reference",
    "write_review",
]
