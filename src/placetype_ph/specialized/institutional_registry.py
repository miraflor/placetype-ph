"""Optional institutional registry adapters; offline, source-typed, no automatic tagging.

Loaders preserve source identity semantics: BEIS-format extracts are unverified,
UniFAST local digests are not official IDs, PhilHealth publication rows are not
facility identifiers, and NHFR site codes can qualify only with site evidence.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

POLICY = "institution-registry-v1"
BEIS_FIELDS = ("school_id", "school_name", "town_psgc")
UNIFAST_FIELDS = (
    "region",
    "hei_name",
    "hei_type",
    "ownership",
    "source_url",
    "accessed_utc",
    "record_key",
)
HOSPITAL_FIELDS = (
    "source",
    "source_id",
    "name",
    "domain",
    "ownership",
    "city",
    "province",
    "address",
    "aliases",
    "source_url",
    "as_of",
    "id_kind",
    "evidence_type",
    "facility_level",
)
UNIFAST_URL = "https://unifast.gov.ph/hei-list.html"
UNIFAST_TYPES = {
    "Private HEI": "private",
    "SUC": "public",
    "LUC": "public",
    "CHED Supervised Inst.": "",
}
HOSPITAL_HOSTS = {
    "doh_nhfr": frozenset({"nhfr.doh.gov.ph", "hfsrb.doh.gov.ph"}),
    "philhealth_hospitals": frozenset({"philhealth.gov.ph", "www.philhealth.gov.ph"}),
}
REGION_RE = re.compile(r"^(\d{2}[A-Z]?)\s*-\s*(.+)$", re.I)
# Official records: named sites with an explicit public/private sector from an official
# publication, a directory or an official export (see ``load_official_records``).
OFFICIAL_FIELDS = (
    "source",
    "source_id",
    "name",
    "domain",
    "ownership",
    "city",
    "address",
    "aliases",
    "source_url",
    "as_of",
    "id_kind",
    "evidence_type",
)
DEPED_HOSTS = frozenset(
    {"deped.gov.ph", "www.deped.gov.ph", "depedncr.com.ph", "www.depedncr.com.ph"}
)
# For each source: allowed official hosts, required domain and side ("" = either),
# allowed identifier kinds and allowed evidence types.
OFFICIAL_SOURCES: dict[str, tuple[frozenset[str], str, str, frozenset[str], frozenset[str]]] = {
    "doh_nhfr": (
        frozenset({"nhfr.doh.gov.ph", "hfsrb.doh.gov.ph"}),
        "health", "", frozenset({"official_code"}), frozenset({"explicit_sector"}),
    ),
    "deped_masterlist": (
        DEPED_HOSTS, "education", "", frozenset({"official_code"}),
        frozenset({"explicit_sector"}),
    ),
    "deped_public": (
        frozenset({"depedncr.com.ph", "www.depedncr.com.ph"}), "education", "public",
        frozenset({"official_code"}), frozenset({"official_public_directory"}),
    ),
    "ched_cmo": (
        frozenset({"ched.gov.ph", "www.ched.gov.ph", "legacy.ched.gov.ph"}), "education",
        "private", frozenset({"publication_row"}), frozenset({"official_private_list"}),
    ),
    "philhealth_hospitals": (
        frozenset({"philhealth.gov.ph", "www.philhealth.gov.ph"}), "health", "",
        frozenset({"publication_row"}), frozenset({"explicit_sector"}),
    ),
    "qc_government": (
        frozenset({"quezoncity.gov.ph"}), "", "public", frozenset({"publication_row"}),
        frozenset({"official_public_directory"}),
    ),
}
# A publication date, a year, a month, or an access date written as accessed-YYYY-MM-DD.
AS_OF_RE = re.compile(r"(?:accessed-)?\d{4}(?:-\d{2}(?:-\d{2})?)?")


def clean(value: Any) -> str:
    if value is None:
        return ""
    value = str(value).strip()
    return "" if value.casefold() in {"nan", "none", "nat", "<na>"} else value


def key(value: Any) -> str:
    s = unicodedata.normalize("NFKD", clean(value).casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("&", " and ").replace("’", "").replace("'", "")
    s = re.sub(r"[^\w]+", " ", s)
    s = re.sub(r"\b(?:es)\b$", "elementary school", s)
    s = re.sub(r"\b(?:nhs)\b$", "national high school", s)
    s = re.sub(r"\b(?:hs)\b$", "high school", s)
    s = re.sub(r"\b(?:st)\b(?=\s+\w)", "saint", s)
    s = re.sub(r"\b(?:inc|incorporated|corporation|corp)\b$", "", s)
    return re.sub(r"\s+", " ", s).strip()


def region_code(value: Any) -> str:
    value = clean(value).upper()
    found = REGION_RE.match(value)
    if found:
        return found.group(1)
    return value if re.fullmatch(r"\d{2}[A-Z]?", value) else ""


def https_host(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return ""
    return (parsed.hostname or "").lower()


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_rows(path: Path, fields: tuple[str, ...]) -> list[dict[str, str]]:
    path = Path(path)
    opener = gzip.open if path.suffix.lower() == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != fields:
            raise ValueError(f"{path.name}: expected exact header {','.join(fields)}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"{path.name}: empty registry")
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"{path.name}: malformed CSV row")
    return rows


def _date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid reference date: {value!r}") from exc


@dataclass(frozen=True)
class Institution:
    source: str
    source_id: str
    name: str
    domain: str
    ownership: str
    city: str = ""
    town_psgc: str = ""
    region: str = ""
    address: str = ""
    aliases: tuple[str, ...] = ()
    source_url: str = ""
    as_of: str = ""
    id_kind: str = "unverified_source_id"
    evidence_type: str = "provisional"
    site_scope: str = "site"

    @property
    def reference(self) -> str:
        return f"{self.source}:{self.source_id}"


def load_beis(path: Path) -> tuple[Institution, ...]:
    """Read user-provided BEIS format; six-digit school prefix is a *hint*."""
    rows = _read_rows(path, BEIS_FIELDS)
    seen: set[str] = set()
    output: list[Institution] = []
    for n, row in enumerate(rows, 2):
        sid, name, town = (clean(row[x]) for x in BEIS_FIELDS)
        if not re.fullmatch(r"\d{5,7}", sid) or not name:
            raise ValueError(f"BEIS line {n}: invalid school ID/name")
        if not re.fullmatch(r"\d{10}", town) or not town.endswith("000"):
            raise ValueError(f"BEIS line {n}: invalid town PSGC")
        if sid in seen:
            raise ValueError(f"BEIS line {n}: duplicate school ID {sid}")
        seen.add(sid)
        side = ""
        if len(sid) == 6:
            side = "private" if sid[0] == "4" else "public" if sid[0] in "12356" else ""
        output.append(
            Institution(
                "deped_beis",
                sid,
                name,
                "education",
                side,
                town_psgc=town,
                evidence_type="unverified_beis_prefix_hint",
                id_kind="unverified_source_id",
            )
        )
    return tuple(output)


def load_unifast(path: Path) -> tuple[Institution, ...]:
    """UniFAST participating HEIs: type/region evidence, never site identity."""
    rows = _read_rows(path, UNIFAST_FIELDS)
    seen: set[tuple[str, str]] = set()
    result: list[Institution] = []
    for n, row in enumerate(rows, 2):
        # Preserve the original capture normalizer before verifying local digests.
        vals = {col: re.sub(r"\s+", " ", clean(row[col])) for col in UNIFAST_FIELDS}
        region, name, typ = vals["region"], vals["hei_name"], vals["hei_type"]
        digest = hashlib.sha256(f"{region.casefold()}\0{name.casefold()}".encode()).hexdigest()[:20]
        if not REGION_RE.match(region) or not name or typ not in UNIFAST_TYPES:
            raise ValueError(f"UniFAST line {n}: invalid HEI name/type/region")
        if vals["ownership"] != UNIFAST_TYPES[typ]:
            raise ValueError(f"UniFAST line {n}: type/ownership disagreement")
        if vals["source_url"] != UNIFAST_URL or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", vals["accessed_utc"]
        ):
            raise ValueError(f"UniFAST line {n}: provenance mismatch")
        _date(vals["accessed_utc"][:10])
        if vals["record_key"] != digest:
            raise ValueError(f"UniFAST line {n}: invalid local digest")
        identity = (region.casefold(), name.casefold())
        if identity in seen:
            raise ValueError(f"UniFAST line {n}: duplicate name/region")
        seen.add(identity)
        result.append(
            Institution(
                "unifast_ra10931",
                digest,
                name,
                "education",
                vals["ownership"],
                region=region_code(region),
                source_url=UNIFAST_URL,
                as_of=vals["accessed_utc"][:10],
                id_kind="local_digest",
                evidence_type=f"participating_hei:{typ}",
                site_scope="organization",
            )
        )
    return tuple(result)


HEIDA_FIELDS = ("source_id", "name", "hei_type", "ownership", "address", "source_url", "as_of")
HEIDA_URL = "https://heida.ched.gov.ph/hei-directory"
HEIDA_PUBLIC_TYPES = frozenset({
    "Chartered State College/University (Main)",
    "Chartered State College/University (Satellite Campus)",
    "Local Government College/University",
})
HEIDA_PRIVATE_TYPES = frozenset({
    "Private Non-Sectarian Non-Stock", "Private Non-Sectarian Stock",
    "Private Sectarian Non-Stock", "Private Sectarian Stock",
    "Private Non-Sectarian Foundation", "Private Sectarian Foundation",
    "Private Non-Stock Non-Profit Corporation",
})


def load_heida(path: Path) -> tuple[Institution, ...]:
    """CHED directory codes identify institutions, not independently verified sites."""
    result: list[Institution] = []
    seen: set[str] = set()
    for n, row in enumerate(_read_rows(path, HEIDA_FIELDS), 2):
        v = {col: clean(row[col]) for col in HEIDA_FIELDS}
        code, typ = v["source_id"], v["hei_type"]
        side = (
            "public" if typ in HEIDA_PUBLIC_TYPES
            else "private" if typ in HEIDA_PRIVATE_TYPES else ""
        )
        if (not re.fullmatch(r"(?:[0-9]+(?: ?[A-Za-z][A-Za-z0-9]*)?|N_[0-9]+)", code, re.I)
                or "xxx" in code.casefold() or not v["name"]):
            raise ValueError(f"HEIDA line {n}: invalid HEI code/name")
        if not side or v["ownership"] != side:
            raise ValueError(f"HEIDA line {n}: unreviewed or conflicting HEI ownership")
        if v["source_url"] != HEIDA_URL:
            raise ValueError(f"HEIDA line {n}: invalid source URL")
        _date(v["as_of"])
        if code.casefold() in seen:
            raise ValueError(f"HEIDA line {n}: duplicate HEI code")
        seen.add(code.casefold())
        result.append(Institution(
            "ched_hei", code, v["name"], "education", side,
            address=v["address"], source_url=HEIDA_URL, as_of=v["as_of"],
            id_kind="official_code", evidence_type=f"ched_hei_type:{typ}",
            site_scope="organization",
        ))
    return tuple(result)


def load_hospitals(path: Path) -> tuple[Institution, ...]:
    """Unified Level 1–3 hospital registry; publication rows are not facility IDs."""
    return _load_hospitals(path, None)


def load_level1_hospitals(path: Path) -> tuple[Institution, ...]:
    """Typed Level 1 local registry, separate from higher-level snapshots."""
    return _load_hospitals(path, 1)


def load_level2_hospitals(path: Path) -> tuple[Institution, ...]:
    """Typed Level 2 local registry, separate from the Level 3 snapshot."""
    return _load_hospitals(path, 2)


def _load_hospitals(path: Path, level: int | None) -> tuple[Institution, ...]:
    rows = _read_rows(path, HOSPITAL_FIELDS)
    seen: set[tuple[str, str]] = set()
    result: list[Institution] = []
    for n, row in enumerate(rows, 2):
        v = {col: clean(row[col]) for col in HOSPITAL_FIELDS}
        src, url = v["source"], v["source_url"]
        if src not in HOSPITAL_HOSTS or https_host(url) not in HOSPITAL_HOSTS[src]:
            raise ValueError(f"hospital line {n}: invalid source/official HTTPS URL")
        if not v["source_id"] or not v["name"]:
            raise ValueError(f"hospital line {n}: missing facility identity")
        if v["ownership"] not in {"public", "private"} or v["domain"] != "health":
            raise ValueError(f"hospital line {n}: invalid sector/domain")
        levels = {
            1: {"level 1", "level i", "level one"},
            2: {"level 2", "level ii", "level two"},
            3: {"level 3", "level iii", "level three"},
        }
        allowed = levels[level] if level is not None else set().union(*levels.values())
        if key(v["facility_level"]) not in allowed:
            raise ValueError(f"hospital line {n}: unsupported explicit hospital level")
        if v["evidence_type"] != "explicit_sector":
            raise ValueError(f"hospital line {n}: ownership requires explicit-sector evidence")
        expected_kind = "official_code" if src == "doh_nhfr" else "publication_row"
        if v["id_kind"] != expected_kind:
            raise ValueError(f"hospital line {n}: identifier kind/source mismatch")
        _date(v["as_of"])
        ident = (src, v["source_id"].casefold())
        if ident in seen:
            raise ValueError(f"hospital line {n}: duplicate source identifier")
        seen.add(ident)
        result.append(
            Institution(
                src,
                v["source_id"],
                v["name"],
                "health",
                v["ownership"],
                city=v["city"],
                address=v["address"],
                aliases=tuple(x.strip() for x in v["aliases"].split("|") if x.strip()),
                source_url=url,
                as_of=v["as_of"],
                id_kind=v["id_kind"],
                evidence_type="explicit_sector",
                site_scope="site",
            )
        )
    return tuple(result)


def _as_of(value: str, line: int) -> str:
    if not AS_OF_RE.fullmatch(value):
        raise ValueError(f"official record line {line}: invalid as_of {value!r}")
    digits = value.removeprefix("accessed-")
    if len(digits) == 10:
        _date(digits)
    return value


def load_official_records(path: Path) -> tuple[Institution, ...]:
    """Official sites with an explicit sector; names never verify identity on their own.

    Each row names its source, and the source decides which official HTTPS hosts, domain,
    side, identifier kind and evidence type are allowed (``OFFICIAL_SOURCES``). DOH NHFR
    and DepEd codes are ``official_code`` identifiers that can match a place's own
    external ID; directory and publication rows (``publication_row``) cannot. A row
    without a city is refused, because a name-and-city match needs the city.
    """
    rows = _read_rows(path, OFFICIAL_FIELDS)
    seen: set[tuple[str, str]] = set()
    result: list[Institution] = []
    for n, row in enumerate(rows, 2):
        v = {col: clean(row[col]) for col in OFFICIAL_FIELDS}
        src = v["source"]
        if src not in OFFICIAL_SOURCES:
            raise ValueError(f"official record line {n}: unknown source {src!r}")
        hosts, domain, side, kinds, evidence = OFFICIAL_SOURCES[src]
        if https_host(v["source_url"]) not in hosts:
            raise ValueError(f"official record line {n}: URL is not an official {src} host")
        if v["domain"] not in {"education", "health"} or (domain and v["domain"] != domain):
            raise ValueError(f"official record line {n}: invalid domain for {src}")
        if v["ownership"] not in {"public", "private"} or (side and v["ownership"] != side):
            raise ValueError(f"official record line {n}: invalid ownership for {src}")
        if v["id_kind"] not in kinds or v["evidence_type"] not in evidence:
            raise ValueError(f"official record line {n}: identifier or evidence type mismatch")
        if not v["source_id"] or not v["name"] or not v["city"]:
            raise ValueError(f"official record line {n}: source_id, name and city are required")
        _as_of(v["as_of"], n)
        ident = (src, v["source_id"].casefold())
        if ident in seen:
            raise ValueError(f"official record line {n}: duplicate source identifier")
        seen.add(ident)
        result.append(
            Institution(
                src,
                v["source_id"],
                v["name"],
                v["domain"],
                v["ownership"],
                city=v["city"],
                address=v["address"],
                aliases=tuple(x.strip() for x in v["aliases"].split("|") if x.strip()),
                source_url=v["source_url"],
                as_of=v["as_of"],
                id_kind=v["id_kind"],
                evidence_type=v["evidence_type"],
                site_scope="site",
            )
        )
    return tuple(result)


class InstitutionIndex:
    """Use true external IDs before exact names, preserving all ambiguity."""

    def __init__(self, entries: Iterable[Institution]):
        self.entries = tuple(entries)
        self.by_name: dict[str, list[Institution]] = defaultdict(list)
        self.by_id: dict[tuple[str, str], Institution] = {}
        for item in self.entries:
            ident = (item.source, item.source_id.casefold())
            if ident in self.by_id:
                raise ValueError(f"duplicate institution reference {ident}")
            self.by_id[ident] = item
            for value in dict.fromkeys((item.name, *item.aliases)):
                if key(value):
                    self.by_name[key(value)].append(item)

    def by_identifiers(self, observed: Iterable[tuple[str, str]]) -> tuple[Institution, ...]:
        values = {self.by_id[x] for x in observed if x in self.by_id}
        return tuple(sorted(values, key=lambda r: r.reference))

    def by_names(self, names: Iterable[str], domain: str) -> tuple[Institution, ...]:
        values = {
            r for name in names for r in self.by_name.get(key(name), ()) if r.domain == domain
        }
        return tuple(sorted(values, key=lambda r: r.reference))
