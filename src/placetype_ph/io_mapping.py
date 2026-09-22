"""PSIC -> PSA 2018 input-output industry sets (IO16 / IO80 / IO240).

Two packaged data files are involved:

* ``psic_2019_to_io_2018.csv`` relates PSIC 2019 sections, divisions and groups to the
  PSA 2018 I-O industries. PSA defined the 2018 I-O sectors on PSIC 2019.
* ``psic_rev5_to_psic_2019.csv`` relates every PSIC Revision 5 section, division and
  group to the PSIC 2019 code or codes that hold the same activities.

A Revision 5 code maps through its deepest section/division/group ancestor that has a
bridge row whose Revision 5 title still matches the loaded taxonomy. The I-O sets are the
union of the concordance rows of the PSIC 2019 codes listed on that bridge row. Bridge rows
marked ``draft`` expose candidate sets with ``PROVISIONAL_*`` statuses; they are not promoted
to ordinary ``MAPPED_*`` results until their basis is changed to ``reviewed``. A draft row
decides the status only when it changes the answer: if the deepest accepted ancestor row gives
the same I-O sets, that accepted mapping is reported instead.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from .taxonomy import Taxonomy


CONCORDANCE_FILE = "psic_2019_to_io_2018.csv"
BRIDGE_FILE = "psic_rev5_to_psic_2019.csv"
CONCORDANCE_PSIC_VERSION = "2019 update to 2009 PSIC"

RESOLUTIONS = ("io16", "io80", "io240")
_IO_CODE_PATTERN = {
    "io16": re.compile(r"[0-9]{2}"),
    "io80": re.compile(r"[0-9]{2}"),
    "io240": re.compile(r"[0-9]{3}"),
}
_PSIC_CODE_PATTERN = {
    "section": re.compile(r"[A-Z]"),
    "division": re.compile(r"[0-9]{2}"),
    "group": re.compile(r"[0-9]{3}"),
}

# Strongest first. "Not mapped" is valid only for a resolution whose code set is blank.
CONFIDENCE_ORDER = ("High", "High with caveat", "Medium-High", "Medium", "Low")
NOT_MAPPED = "Not mapped"

BRIDGE_RELATIONS = frozenset(
    {"same", "recoded", "renamed", "changed_scope", "new", "no_counterpart"}
)
BRIDGE_BASES = frozenset({"code_title_match", "draft", "reviewed"})

CODE_DELIMITER = "; "
# Names use a different delimiter because some I-O names contain semicolons.
NAME_DELIMITER = " | "


class IOMappingStatus(StrEnum):
    MAPPED_GROUP = "MAPPED_GROUP"
    MAPPED_DIVISION = "MAPPED_DIVISION"
    MAPPED_SECTION = "MAPPED_SECTION"
    PROVISIONAL_GROUP = "PROVISIONAL_GROUP"
    PROVISIONAL_DIVISION = "PROVISIONAL_DIVISION"
    PROVISIONAL_SECTION = "PROVISIONAL_SECTION"
    NO_IO_CORRESPONDENCE = "NO_IO_CORRESPONDENCE"
    PROVISIONAL_NO_IO_CORRESPONDENCE = "PROVISIONAL_NO_IO_CORRESPONDENCE"
    NO_PSIC2019_COUNTERPART = "NO_PSIC2019_COUNTERPART"
    PROVISIONAL_NO_PSIC2019_COUNTERPART = "PROVISIONAL_NO_PSIC2019_COUNTERPART"
    UNMAPPED_NO_PSIC = "UNMAPPED_NO_PSIC"
    UNMAPPED_UNKNOWN_PSIC_CODE = "UNMAPPED_UNKNOWN_PSIC_CODE"
    UNMAPPED_NO_BRIDGE = "UNMAPPED_NO_BRIDGE"


_MAPPED_AT = {
    "group": IOMappingStatus.MAPPED_GROUP,
    "division": IOMappingStatus.MAPPED_DIVISION,
    "section": IOMappingStatus.MAPPED_SECTION,
}
_PROVISIONAL_AT = {
    "group": IOMappingStatus.PROVISIONAL_GROUP,
    "division": IOMappingStatus.PROVISIONAL_DIVISION,
    "section": IOMappingStatus.PROVISIONAL_SECTION,
}

IO_OUTPUT_COLUMNS = (
    "io_mapping_status",
    "io_mapping_psic_level",
    "io_mapping_psic_code",
    "io_mapping_psic2019_codes",
    "io_mapping_bridge_relation",
    "io_mapping_bridge_basis",
    "io16_codes",
    "io16_names",
    "io16_confidence",
    "io80_codes",
    "io80_names",
    "io80_confidence",
    "io240_codes",
    "io240_names",
    "io240_confidence",
)

# The QGIS layer excludes I-O names for the same reason it excludes taxonomy titles.
IO_GIS_COLUMNS = tuple(
    column
    for column in IO_OUTPUT_COLUMNS
    if not column.endswith("_names") and column != "io_mapping_psic2019_codes"
)


class IOCrosswalkError(ValueError):
    pass


def _normalize_title(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("&", " and ")
    return re.sub(r"[^0-9a-z]+", " ", text).strip()


def _split(value: str, delimiter: str) -> list[str]:
    if not value.strip():
        return []
    return [part.strip() for part in value.split(delimiter.strip())]


def _psic_level_of(code: str) -> str | None:
    for level, pattern in _PSIC_CODE_PATTERN.items():
        if pattern.fullmatch(code):
            return level
    return None


def _check_psic_code(level: str, code: str, where: str) -> None:
    pattern = _PSIC_CODE_PATTERN.get(level)
    if pattern is None:
        raise IOCrosswalkError(f"{where}: unsupported PSIC level {level!r}")
    if not pattern.fullmatch(code):
        raise IOCrosswalkError(f"{where}: malformed {level} code {code!r}")


@dataclass(frozen=True, slots=True)
class IOSet:
    codes: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    confidence: str = NOT_MAPPED


@dataclass(frozen=True, slots=True)
class IORow:
    """One PSIC 2019 category and its I-O industry sets at the three resolutions."""

    psic_level: str
    psic_code: str
    psic_name: str
    io16: IOSet = IOSet()
    io80: IOSet = IOSet()
    io240: IOSet = IOSet()

    def resolution(self, name: str) -> IOSet:
        return getattr(self, name)


@dataclass(frozen=True, slots=True)
class BridgeRow:
    """One PSIC Revision 5 category and the PSIC 2019 codes that hold its activities."""

    rev5_level: str
    rev5_code: str
    rev5_title: str
    psic2019_codes: tuple[str, ...]
    relation: str
    basis: str
    note: str = ""


@dataclass(frozen=True, slots=True)
class _Source:
    name: str
    text: str
    sha256: str


def _source_from_bytes(data: bytes, name: str) -> _Source:
    return _Source(
        name=name,
        text=data.decode("utf-8-sig"),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _packaged(filename: str) -> _Source:
    data = resources.files("placetype_ph").joinpath("data", filename).read_bytes()
    return _source_from_bytes(data, filename)


def _from_path(path: str | Path) -> _Source:
    path = Path(path)
    return _source_from_bytes(path.read_bytes(), path.name)


def _records(
    source: _Source, required: set[str], label: str
) -> Iterator[tuple[int, dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(source.text, newline=""))
    missing = required - set(reader.fieldnames or ())
    if missing:
        raise IOCrosswalkError(f"{label} is missing columns {sorted(missing)}")
    for line_no, record in enumerate(reader, start=2):
        clean = {key: str(value or "").strip() for key, value in record.items() if key is not None}
        if any(clean.values()):
            yield line_no, clean


_CONCORDANCE_COLUMNS = {
    "psic_version",
    "psic_level",
    "psic_code",
    "psic_name",
    *(f"{res}_{field}" for res in RESOLUTIONS for field in ("codes", "names", "confidence")),
}
_BRIDGE_COLUMNS = {
    "rev5_level",
    "rev5_code",
    "rev5_title",
    "psic2019_codes",
    "relation",
    "basis",
    "note",
}


def _parse_io_set(record: dict[str, str], resolution: str, where: str) -> IOSet:
    codes = _split(record[f"{resolution}_codes"], CODE_DELIMITER)
    names = _split(record[f"{resolution}_names"], NAME_DELIMITER)
    confidence = record[f"{resolution}_confidence"]
    where = f"{where} {resolution}"
    malformed = [code for code in codes if not _IO_CODE_PATTERN[resolution].fullmatch(code)]
    if malformed:
        raise IOCrosswalkError(f"{where}: malformed codes {malformed}")
    if len(set(codes)) != len(codes):
        raise IOCrosswalkError(f"{where}: repeated codes")
    if len(codes) != len(names):
        raise IOCrosswalkError(f"{where}: {len(codes)} codes but {len(names)} names")
    if codes and confidence not in CONFIDENCE_ORDER:
        raise IOCrosswalkError(f"{where}: unknown confidence {confidence!r}")
    if not codes and confidence not in ("", NOT_MAPPED):
        raise IOCrosswalkError(f"{where}: blank codes require confidence {NOT_MAPPED!r}")
    return IOSet(tuple(codes), tuple(names), confidence if codes else NOT_MAPPED)


def _parse_concordance(source: _Source) -> list[IORow]:
    rows: list[IORow] = []
    for line_no, record in _records(source, _CONCORDANCE_COLUMNS, "I-O concordance"):
        where = f"I-O concordance line {line_no}"
        if record["psic_version"] != CONCORDANCE_PSIC_VERSION:
            raise IOCrosswalkError(
                f"{where}: psic_version {record['psic_version']!r}, "
                f"expected {CONCORDANCE_PSIC_VERSION!r}"
            )
        level = record["psic_level"].casefold()
        _check_psic_code(level, record["psic_code"], where)
        if not record["psic_name"]:
            raise IOCrosswalkError(f"{where}: blank psic_name")
        rows.append(
            IORow(
                level,
                record["psic_code"],
                record["psic_name"],
                *(_parse_io_set(record, res, where) for res in RESOLUTIONS),
            )
        )
    return rows


def _parse_bridge(source: _Source) -> list[BridgeRow]:
    links: list[BridgeRow] = []
    for line_no, record in _records(source, _BRIDGE_COLUMNS, "PSIC bridge"):
        where = f"PSIC bridge line {line_no}"
        level = record["rev5_level"].casefold()
        code = record["rev5_code"]
        _check_psic_code(level, code, where)
        targets = tuple(_split(record["psic2019_codes"], CODE_DELIMITER))
        if len(targets) != len(set(targets)):
            raise IOCrosswalkError(f"{where}: duplicate PSIC 2019 codes {list(targets)!r}")
        relation = record["relation"]
        if relation not in BRIDGE_RELATIONS:
            raise IOCrosswalkError(f"{where}: unknown relation {relation!r}")
        if relation == "no_counterpart":
            if targets:
                raise IOCrosswalkError(f"{where}: no_counterpart must have blank PSIC 2019 codes")
        elif not targets:
            raise IOCrosswalkError(f"{where}: relation {relation!r} requires PSIC 2019 codes")
        malformed = [target for target in targets if _psic_level_of(target) is None]
        if malformed:
            raise IOCrosswalkError(f"{where}: malformed PSIC 2019 codes {malformed}")
        if record["basis"] not in BRIDGE_BASES:
            raise IOCrosswalkError(f"{where}: unknown basis {record['basis']!r}")
        if relation in {"same", "renamed"} and targets != (code,):
            raise IOCrosswalkError(f"{where}: relation {relation!r} must map to its own code")
        if relation == "recoded" and (len(targets) != 1 or targets == (code,)):
            raise IOCrosswalkError(
                f"{where}: relation 'recoded' must map to exactly one different code"
            )
        if record["basis"] == "code_title_match" and relation != "same":
            raise IOCrosswalkError(f"{where}: a code_title_match row must use relation 'same'")
        if not record["rev5_title"]:
            raise IOCrosswalkError(f"{where}: blank rev5_title")
        links.append(
            BridgeRow(
                level,
                code,
                record["rev5_title"],
                targets,
                relation,
                record["basis"],
                record["note"],
            )
        )
    return links


def _weakest(confidences: Iterable[str]) -> str:
    ranked = [CONFIDENCE_ORDER.index(value) for value in confidences]
    return CONFIDENCE_ORDER[max(ranked)] if ranked else NOT_MAPPED


def _empty(status: IOMappingStatus) -> dict[str, str]:
    row = dict.fromkeys(IO_OUTPUT_COLUMNS, "")
    row["io_mapping_status"] = str(status)
    return row


class IOCrosswalk:
    """PSIC 2019 -> PSA 2018 I-O concordance plus the PSIC Revision 5 -> 2019 bridge.

    The concordance is a relation rather than a lookup function: one PSIC category can
    correspond to several I-O industries. Codes and names are therefore kept as delimited
    sets instead of selecting one industry. A set means "one of these industries".
    """

    def __init__(
        self,
        rows: Iterable[IORow],
        bridge: Iterable[BridgeRow] = (),
        *,
        sources: dict[str, _Source] | None = None,
    ):
        self.rows = list(rows)
        self._index: dict[tuple[str, str], IORow] = {}
        for row in self.rows:
            key = (row.psic_level, row.psic_code)
            if key in self._index:
                raise IOCrosswalkError(f"duplicate I-O concordance row for {key}")
            self._index[key] = row
        for resolution in RESOLUTIONS:
            names: dict[str, str] = {}
            for row in self.rows:
                io_set = row.resolution(resolution)
                for code, name in zip(io_set.codes, io_set.names, strict=True):
                    if names.setdefault(code, name) != name:
                        raise IOCrosswalkError(
                            f"{resolution} code {code} has two names: {names[code]!r}, {name!r}"
                        )
        self.bridge = list(bridge)
        self._bridge: dict[tuple[str, str], BridgeRow] = {}
        for link in self.bridge:
            key = (link.rev5_level, link.rev5_code)
            if key in self._bridge:
                raise IOCrosswalkError(f"duplicate PSIC bridge row for {key}")
            absent = [c for c in link.psic2019_codes if (_psic_level_of(c), c) not in self._index]
            if absent:
                raise IOCrosswalkError(
                    f"PSIC bridge row {key} lists codes absent from the concordance: {absent}"
                )
            self._bridge[key] = link
        self._sources = dict(sources or {})

    @classmethod
    def _from_sources(cls, concordance: _Source, bridge: _Source) -> IOCrosswalk:
        return cls(
            _parse_concordance(concordance),
            _parse_bridge(bridge),
            sources={"concordance": concordance, "bridge": bridge},
        )

    @classmethod
    def load(cls, path: str | Path, bridge_path: str | Path | None = None) -> IOCrosswalk:
        bridge = _packaged(BRIDGE_FILE) if bridge_path is None else _from_path(bridge_path)
        return cls._from_sources(_from_path(path), bridge)

    @classmethod
    def load_default(cls) -> IOCrosswalk:
        return cls._from_sources(_packaged(CONCORDANCE_FILE), _packaged(BRIDGE_FILE))

    def concordance_row(self, code: str) -> IORow | None:
        return self._index.get((_psic_level_of(code) or "", code))

    def bridge_row(self, level: str, code: str) -> BridgeRow | None:
        return self._bridge.get((level, code))

    def provenance(self) -> dict[str, object]:
        """File names, SHA-256 hashes and row counts, for the run manifest."""
        concordance: dict[str, object] = {
            "rows": len(self.rows),
            "psic_version": CONCORDANCE_PSIC_VERSION,
        }
        bridge: dict[str, object] = {
            "rows": len(self.bridge),
            "rows_by_basis": dict(sorted(Counter(link.basis for link in self.bridge).items())),
            "rows_by_relation": dict(
                sorted(Counter(link.relation for link in self.bridge).items())
            ),
        }
        for role, target in (("concordance", concordance), ("bridge", bridge)):
            source = self._sources.get(role)
            if source is not None:
                target["file"] = source.name
                target["sha256"] = source.sha256
        return {"concordance": concordance, "bridge": bridge}

    def map_psic(self, code: str | None, taxonomy: Taxonomy) -> dict[str, str]:
        """Map one PSIC Revision 5 code to I-O sets.

        The deepest section/division/group ancestor with a bridge row decides the result,
        but only when that row's Revision 5 title still matches the loaded taxonomy. A tree
        that differs from the one the bridge was built for therefore uses a coarser ancestor
        or stays unmapped, instead of being mapped by code alone. Rows whose bridge basis is
        ``draft`` retain their candidate I-O sets but receive ``PROVISIONAL_*`` status, unless
        the deepest accepted (non-draft) ancestor row gives the same I-O sets at all three
        resolutions; then the answer does not depend on the draft row, and the accepted
        mapping is reported. A ``no_counterpart`` row is never collapsed this way because
        "no historical bridge" is semantically different from an ancestor with a blank I-O set.
        For the same reason, an accepted ``no_counterpart`` ancestor confirms no answer and
        never replaces a draft row's result.
        """
        if code is None or not str(code).strip():
            return _empty(IOMappingStatus.UNMAPPED_NO_PSIC)
        if taxonomy.scheme != "psic":
            raise ValueError(f"I-O mapping requires a PSIC taxonomy, got {taxonomy.scheme!r}")
        code = str(code).strip()
        if code not in taxonomy.nodes:
            return _empty(IOMappingStatus.UNMAPPED_UNKNOWN_PSIC_CODE)
        chain: list[tuple[str, BridgeRow]] = []
        for ancestor in taxonomy.ancestors(code, include_self=True):
            node = taxonomy.get(ancestor)
            level = str(node.level).casefold()
            link = self._bridge.get((level, str(node.code)))
            if link is None or _normalize_title(link.rev5_title) != _normalize_title(node.title):
                continue
            chain.append((level, link))
        if not chain:
            return _empty(IOMappingStatus.UNMAPPED_NO_BRIDGE)
        candidate = self._compose(*chain[0])
        if chain[0][1].basis != "draft" or chain[0][1].relation == "no_counterpart":
            return candidate
        accepted = next(((lv, lk) for lv, lk in chain[1:] if lk.basis != "draft"), None)
        if accepted is not None and accepted[1].relation != "no_counterpart":
            fallback = self._compose(*accepted)
            if all(fallback[f"{r}_codes"] == candidate[f"{r}_codes"] for r in RESOLUTIONS):
                return fallback
        return candidate

    def _compose(self, level: str, link: BridgeRow) -> dict[str, str]:
        targets = [self._index[(_psic_level_of(c) or "", c)] for c in link.psic2019_codes]
        out = {
            "io_mapping_psic_level": level,
            "io_mapping_psic_code": link.rev5_code,
            "io_mapping_psic2019_codes": CODE_DELIMITER.join(link.psic2019_codes),
            "io_mapping_bridge_relation": link.relation,
            "io_mapping_bridge_basis": link.basis,
        }
        has_io = False
        for resolution in RESOLUTIONS:
            names: dict[str, str] = {}
            confidences: list[str] = []
            for row in targets:
                io_set = row.resolution(resolution)
                names.update(zip(io_set.codes, io_set.names, strict=True))
                if io_set.codes:
                    confidences.append(io_set.confidence)
            codes = sorted(names)
            has_io = has_io or bool(codes)
            out[f"{resolution}_codes"] = CODE_DELIMITER.join(codes)
            out[f"{resolution}_names"] = NAME_DELIMITER.join(names[c] for c in codes)
            out[f"{resolution}_confidence"] = _weakest(confidences)
        if link.relation == "no_counterpart":
            status = (
                IOMappingStatus.PROVISIONAL_NO_PSIC2019_COUNTERPART
                if link.basis == "draft"
                else IOMappingStatus.NO_PSIC2019_COUNTERPART
            )
        elif link.basis == "draft":
            status = (
                _PROVISIONAL_AT[level]
                if has_io
                else IOMappingStatus.PROVISIONAL_NO_IO_CORRESPONDENCE
            )
        else:
            status = _MAPPED_AT[level] if has_io else IOMappingStatus.NO_IO_CORRESPONDENCE
        out["io_mapping_status"] = str(status)
        return {column: out[column] for column in IO_OUTPUT_COLUMNS}


def _clean_code(value: object) -> str | None:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    return str(value).strip() or None


def enrich_summary_with_io(
    summary: pd.DataFrame,
    results: pd.DataFrame,
    taxonomy: Taxonomy,
    crosswalk: IOCrosswalk | None = None,
) -> pd.DataFrame:
    """Add IO16/IO80/IO240 set columns to the classified POI summary.

    Each distinct PSIC code is mapped once and joined back, so the cost grows with the
    number of distinct codes rather than with the number of POIs.
    """
    crosswalk = crosswalk or IOCrosswalk.load_default()
    psic = results.loc[results["scheme"] == "psic", ["canonical_id", "code"]]
    ids = [str(value) for value in psic["canonical_id"]]
    codes = [_clean_code(value) for value in psic["code"]]
    lookup = {code: crosswalk.map_psic(code, taxonomy) for code in dict.fromkeys(codes)}
    mapped = [
        {"canonical_id": cid, **lookup[code]} for cid, code in zip(ids, codes, strict=True)
    ]
    io_frame = pd.DataFrame(mapped, columns=["canonical_id", *IO_OUTPUT_COLUMNS])
    # An explicit string dtype avoids Arrow's null type when a slice has no mapped rows.
    for column in IO_OUTPUT_COLUMNS:
        io_frame[column] = io_frame[column].astype("string")
    enriched = summary.merge(io_frame, on="canonical_id", how="left", validate="one_to_one")
    # A POI without a PSIC result row has no PSIC code to map.
    missing = enriched["io_mapping_status"].isna()
    if missing.any():
        enriched.loc[missing, list(IO_OUTPUT_COLUMNS)] = ""
        enriched.loc[missing, "io_mapping_status"] = str(IOMappingStatus.UNMAPPED_NO_PSIC)
    return enriched


def io_status_counts(summary: pd.DataFrame) -> dict[str, int]:
    """Number of POIs per ``io_mapping_status``, for the run manifest."""
    if "io_mapping_status" not in summary.columns:
        return {}
    counts = summary["io_mapping_status"].value_counts(dropna=False)
    return {str(status): int(count) for status, count in sorted(counts.items(), key=str)}
