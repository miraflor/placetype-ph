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

Two further packaged files support the mapping without widening it:

* ``io_hierarchy_2018.csv`` gives the official IO240 -> IO80 -> IO16 membership of the PSA
  2018 tables. Every concordance row is checked against it when the crosswalk is loaded, so
  an IO240 code whose official parent is missing from the same row's IO80 set stops the run.
* ``psic_rev5_io240_refinement.csv`` narrows the IO240 set of a Revision 5 class or subclass
  inside its group's set. All of its rows are ``draft``; the narrowed sets are therefore
  written to separate ``io*_subgroup_codes`` columns and never replace the candidate sets.

A PSIC result whose evidence admits several separate subtrees (a reviewed UNION, for
example Overture ``lodging``) is mapped as the union of the I-O sets of those subtrees,
not through their common parent, which may hold activities the evidence excludes.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
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

from .io80_resolver import (
    IO80_RESOLUTION_COLUMNS,
    IO80_RESOLVED_STATUSES,
    parse_candidate_codes,
    resolve_io80_ambiguity,
    validate_io80_map_fields,
)

if TYPE_CHECKING:
    from .taxonomy import Taxonomy


CONCORDANCE_FILE = "psic_2019_to_io_2018.csv"
BRIDGE_FILE = "psic_rev5_to_psic_2019.csv"
HIERARCHY_FILE = "io_hierarchy_2018.csv"
REFINEMENT_FILE = "psic_rev5_io240_refinement.csv"
CONCORDANCE_PSIC_VERSION = "2019 update to 2009 PSIC"

RESOLUTIONS = ("io16", "io80", "io240")
PSIC_LEVELS = ("section", "division", "group", "class", "subclass")
IO_MAP_COLUMNS = (
    tuple(
        column
        for resolution in RESOLUTIONS
        for column in (f"{resolution}_map_code", f"{resolution}_map_status")
    )
    + IO80_RESOLUTION_COLUMNS
)
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
    # A PSIC result that admits several separate subtrees, mapped member by member.
    MAPPED_UNION = "MAPPED_UNION"
    PROVISIONAL_UNION = "PROVISIONAL_UNION"
    UNMAPPED_UNION_PARTIAL = "UNMAPPED_UNION_PARTIAL"


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
    "io16_map_code",
    "io16_map_status",
    "io16_names",
    "io16_confidence",
    "io80_codes",
    "io80_map_code",
    "io80_map_status",
    *IO80_RESOLUTION_COLUMNS,
    "io80_names",
    "io80_confidence",
    "io240_codes",
    "io240_map_code",
    "io240_map_status",
    "io240_names",
    "io240_confidence",
)

# Draft class/subclass narrowing. Kept apart from the candidate sets because every row of the
# packaged refinement table is a draft; the original sets stay authoritative.
IO_SUBGROUP_COLUMNS = (
    "io_subgroup_refinement_code",
    "io_subgroup_refinement_basis",
    "io16_subgroup_codes",
    "io80_subgroup_codes",
    "io240_subgroup_codes",
)
REFINEMENT_BASES = frozenset({"draft", "reviewed"})
# Resolutions whose candidate set depends on a draft bridge row ("io16; io80; io240" or a subset).
# A level is listed only when the deepest accepted ancestor row gives a different set at that
# level, so a resolution that the draft row does not change is not marked provisional.
# A provisional row with no dependent levels uses the explicit sentinel "none".
IO_DRAFT_COLUMNS = ("io_mapping_draft_levels",)
_ORDINARY_UNION = {str(IOMappingStatus.MAPPED_UNION)}
_PROVISIONAL_UNION = {str(IOMappingStatus.PROVISIONAL_UNION)}

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


@dataclass(frozen=True, slots=True)
class IOHierarchy:
    """Official PSA 2018 membership: each IO240 industry in one IO80, each IO80 in one IO16."""

    io240_parent: dict[str, str]
    io80_parent: dict[str, str]
    io240_name: dict[str, str]
    parent_basis: dict[str, str]


@dataclass(frozen=True, slots=True)
class RefinementRow:
    """Draft or reviewed IO240 narrowing for one Revision 5 class or subclass."""

    rev5_level: str
    rev5_code: str
    rev5_title: str
    io240_codes: tuple[str, ...]
    basis: str
    note: str = ""


_HIERARCHY_COLUMNS = ("io240_code", "io240_name", "io80_code", "io16_code", "parent_basis", "note")
_REFINEMENT_COLUMNS = {"rev5_level", "rev5_code", "rev5_title", "io240_codes", "basis", "note"}


def _parse_hierarchy(source: _Source) -> IOHierarchy:
    reader = csv.DictReader(io.StringIO(source.text, newline=""))
    if tuple(reader.fieldnames or ()) != _HIERARCHY_COLUMNS:
        raise IOCrosswalkError(f"I-O hierarchy must have exactly {','.join(_HIERARCHY_COLUMNS)}")
    io240_parent: dict[str, str] = {}
    io80_parent: dict[str, str] = {}
    names: dict[str, str] = {}
    basis: dict[str, str] = {}
    for line_no, record in _records(source, set(_HIERARCHY_COLUMNS), "I-O hierarchy"):
        where = f"I-O hierarchy line {line_no}"
        c240, c80, c16 = record["io240_code"], record["io80_code"], record["io16_code"]
        for resolution, code in (("io240", c240), ("io80", c80), ("io16", c16)):
            if not _IO_CODE_PATTERN[resolution].fullmatch(code):
                raise IOCrosswalkError(f"{where}: malformed {resolution} code {code!r}")
        if c240 in io240_parent:
            raise IOCrosswalkError(f"{where}: repeated IO240 code {c240}")
        if io80_parent.setdefault(c80, c16) != c16:
            raise IOCrosswalkError(f"{where}: IO80 {c80} has two IO16 parents")
        io240_parent[c240] = c80
        names[c240] = record["io240_name"]
        basis[c240] = record["parent_basis"]
    if len(io240_parent) != 240 or len(io80_parent) != 80 or len(set(io80_parent.values())) != 16:
        raise IOCrosswalkError(
            "I-O hierarchy must cover 240 IO240, 80 IO80 and 16 IO16 industries; found "
            f"{len(io240_parent)}, {len(io80_parent)} and {len(set(io80_parent.values()))}"
        )
    if (
        set(io240_parent) != {f"{i:03d}" for i in range(1, 241)}
        or set(io80_parent) != {f"{i:02d}" for i in range(1, 81)}
        or set(io80_parent.values()) != {f"{i:02d}" for i in range(1, 17)}
    ):
        raise IOCrosswalkError("I-O hierarchy does not contain the official code universe")
    return IOHierarchy(io240_parent, io80_parent, names, basis)


def _parse_refinement(source: _Source) -> list[RefinementRow]:
    rows: list[RefinementRow] = []
    seen: set[str] = set()
    for line_no, record in _records(source, _REFINEMENT_COLUMNS, "I-O refinement"):
        where = f"I-O refinement line {line_no}"
        level = record["rev5_level"].casefold()
        if level not in {"class", "subclass"}:
            raise IOCrosswalkError(f"{where}: refinement applies to classes and subclasses only")
        code = record["rev5_code"]
        if not re.fullmatch(r"[0-9]{4}" if level == "class" else r"[0-9]{5}", code):
            raise IOCrosswalkError(f"{where}: malformed {level} code {code!r}")
        if code in seen:
            raise IOCrosswalkError(f"{where}: repeated refinement code {code}")
        seen.add(code)
        codes = tuple(_split(record["io240_codes"], CODE_DELIMITER))
        if not codes or any(not _IO_CODE_PATTERN["io240"].fullmatch(c) for c in codes):
            raise IOCrosswalkError(f"{where}: malformed IO240 codes {list(codes)!r}")
        if len(set(codes)) != len(codes):
            raise IOCrosswalkError(f"{where}: repeated IO240 codes")
        if record["basis"] not in REFINEMENT_BASES:
            raise IOCrosswalkError(f"{where}: unknown basis {record['basis']!r}")
        if not record["rev5_title"]:
            raise IOCrosswalkError(f"{where}: blank rev5_title")
        rows.append(
            RefinementRow(level, code, record["rev5_title"], codes, record["basis"], record["note"])
        )
    return rows


def _weakest(confidences: Iterable[str]) -> str:
    ranked = [CONFIDENCE_ORDER.index(value) for value in confidences]
    return CONFIDENCE_ORDER[max(ranked)] if ranked else NOT_MAPPED


def _empty(status: IOMappingStatus) -> dict[str, str]:
    row = dict.fromkeys(IO_OUTPUT_COLUMNS, "")
    row["io_mapping_status"] = str(status)
    for resolution in RESOLUTIONS:
        row[f"{resolution}_map_status"] = "UNMAPPED"
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
        hierarchy: IOHierarchy | None = None,
        refinement: Iterable[RefinementRow] = (),
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
        self.hierarchy = hierarchy
        if hierarchy is not None:
            self._check_hierarchy(hierarchy)
        self.refinement = list(refinement)
        if self.refinement and hierarchy is None:
            raise IOCrosswalkError("an IO240 refinement table requires the I-O hierarchy")
        self._refine: dict[str, RefinementRow] = {}
        for row in self.refinement:
            unknown = [c for c in row.io240_codes if c not in hierarchy.io240_parent]  # type: ignore[union-attr]
            if unknown:
                raise IOCrosswalkError(f"refinement {row.rev5_code} lists unknown IO240 {unknown}")
            self._refine[row.rev5_code] = row
        self._names = {
            resolution: {
                code: name
                for row in self.rows
                for code, name in zip(
                    row.resolution(resolution).codes, row.resolution(resolution).names, strict=True
                )
            }
            for resolution in RESOLUTIONS
        }

    def _check_hierarchy(self, hierarchy: IOHierarchy) -> None:
        """Every row must nest: IO240 parents inside its IO80 set, IO80 parents inside IO16.

        A row that lists an IO240 code without that code's official IO80 parent would give
        places IO80 and IO240 codes that contradict each other in the PSA 2018 tables.
        """
        problems: list[str] = []
        for row in self.rows:
            label = f"{row.psic_level} {row.psic_code}"
            io80 = set(row.io80.codes)
            io16 = set(row.io16.codes)
            unknown240 = [c for c in row.io240.codes if c not in hierarchy.io240_parent]
            unknown80 = [c for c in row.io80.codes if c not in hierarchy.io80_parent]
            if unknown240 or unknown80:
                problems.append(
                    f"{label}: codes outside the official hierarchy {unknown240 + unknown80}"
                )
                continue
            orphan240 = [c for c in row.io240.codes if hierarchy.io240_parent[c] not in io80]
            orphan80 = [c for c in row.io80.codes if hierarchy.io80_parent[c] not in io16]
            if orphan240:
                problems.append(
                    f"{label}: IO240 {orphan240} belong to IO80 "
                    f"{sorted({hierarchy.io240_parent[c] for c in orphan240})}, "
                    f"not in {sorted(io80)}"
                )
            if orphan80:
                problems.append(
                    f"{label}: IO80 {orphan80} belong to IO16 "
                    f"{sorted({hierarchy.io80_parent[c] for c in orphan80})}, not in {sorted(io16)}"
                )
        if problems:
            raise IOCrosswalkError(
                "I-O concordance contradicts the official PSA 2018 hierarchy:\n  "
                + "\n  ".join(problems)
            )

    @classmethod
    def _from_sources(
        cls,
        concordance: _Source,
        bridge: _Source,
        hierarchy: _Source | None = None,
        refinement: _Source | None = None,
    ) -> IOCrosswalk:
        sources = {"concordance": concordance, "bridge": bridge}
        if hierarchy is not None:
            sources["hierarchy"] = hierarchy
        if refinement is not None:
            sources["refinement"] = refinement
        return cls(
            _parse_concordance(concordance),
            _parse_bridge(bridge),
            sources=sources,
            hierarchy=_parse_hierarchy(hierarchy) if hierarchy is not None else None,
            refinement=_parse_refinement(refinement) if refinement is not None else (),
        )

    @classmethod
    def load(
        cls,
        path: str | Path,
        bridge_path: str | Path | None = None,
        *,
        check_hierarchy: bool = True,
    ) -> IOCrosswalk:
        """Load a concordance file; by default it must nest in the official hierarchy."""
        bridge = _packaged(BRIDGE_FILE) if bridge_path is None else _from_path(bridge_path)
        hierarchy = _packaged(HIERARCHY_FILE) if check_hierarchy else None
        refinement = _packaged(REFINEMENT_FILE) if check_hierarchy else None
        return cls._from_sources(_from_path(path), bridge, hierarchy, refinement)

    @classmethod
    def load_default(cls) -> IOCrosswalk:
        return cls._from_sources(
            _packaged(CONCORDANCE_FILE),
            _packaged(BRIDGE_FILE),
            _packaged(HIERARCHY_FILE),
            _packaged(REFINEMENT_FILE),
        )

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
        out: dict[str, object] = {"concordance": concordance, "bridge": bridge}
        if self.hierarchy is not None:
            out["hierarchy"] = {
                "io240_rows": len(self.hierarchy.io240_parent),
                "parent_basis": dict(sorted(Counter(self.hierarchy.parent_basis.values()).items())),
                "concordance_nesting_checked": True,
            }
        if self._sources.get("refinement") is not None:
            out["subgroup_refinement"] = {
                "rows": len(self.refinement),
                "rows_by_basis": dict(sorted(Counter(r.basis for r in self.refinement).items())),
                "applied_to": "io*_subgroup_codes only; candidate sets unchanged",
            }
        for role, target in out.items():
            source = self._sources.get(role if role != "subgroup_refinement" else "refinement")
            if source is not None and isinstance(target, dict):
                target["file"] = source.name
                target["sha256"] = source.sha256
        return out

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

    def accepted_sets(self, code: str, taxonomy: Taxonomy) -> dict[str, frozenset[str]] | None:
        """I-O sets from the deepest accepted (non-draft) bridge row above ``code``, if any.

        This is the answer that does not depend on any draft row. ``None`` means no accepted
        ancestor row exists (or it records no counterpart), so every level depends on a draft.
        """
        if code not in taxonomy.nodes:
            return None
        for ancestor in taxonomy.ancestors(code, include_self=True):
            node = taxonomy.get(ancestor)
            level = str(node.level).casefold()
            link = self._bridge.get((level, str(node.code)))
            if link is None or _normalize_title(link.rev5_title) != _normalize_title(node.title):
                continue
            if link.basis == "draft":
                continue
            if link.relation == "no_counterpart":
                return None
            composed = self._compose(level, link)
            return {
                r: frozenset(_split(composed[f"{r}_codes"], CODE_DELIMITER)) for r in RESOLUTIONS
            }
        return None

    def draft_levels(self, members: Iterable[str], taxonomy: Taxonomy, base: dict[str, str]) -> str:
        """Levels of ``base`` whose candidate set depends on a draft bridge row.

        ``members`` is one PSIC code, or the admissible roots of a union. For each member the
        accepted set is its own set when its mapping is not provisional, otherwise the set of
        its deepest accepted ancestor row. A level depends on a draft row when the union of
        the members' accepted sets differs from the base set at that level.
        """
        if not str(base.get("io_mapping_status", "")).startswith("PROVISIONAL"):
            return ""
        accepted: dict[str, set[str]] = {r: set() for r in RESOLUTIONS}
        unknown = False
        for code in members:
            own = self.map_psic(code, taxonomy)
            if not own["io_mapping_status"].startswith("PROVISIONAL"):
                sets = {
                    r: frozenset(_split(own[f"{r}_codes"], CODE_DELIMITER)) for r in RESOLUTIONS
                }
            else:
                found = self.accepted_sets(code, taxonomy)
                if found is None:
                    unknown = True
                    continue
                sets = found
            for r in RESOLUTIONS:
                accepted[r] |= sets[r]
        levels = [
            r
            for r in RESOLUTIONS
            if base.get(f"{r}_codes")
            and (unknown or accepted[r] != set(_split(base[f"{r}_codes"], CODE_DELIMITER)))
        ]
        return CODE_DELIMITER.join(levels) if levels else "none"

    def _compose(self, level: str, link: BridgeRow) -> dict[str, str]:
        targets = [self._index[(_psic_level_of(c) or "", c)] for c in link.psic2019_codes]
        out = dict.fromkeys(IO_OUTPUT_COLUMNS, "")
        out.update(
            {
                "io_mapping_psic_level": level,
                "io_mapping_psic_code": link.rev5_code,
                "io_mapping_psic2019_codes": CODE_DELIMITER.join(link.psic2019_codes),
                "io_mapping_bridge_relation": link.relation,
                "io_mapping_bridge_basis": link.basis,
            }
        )
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
            if len(codes) == 1:
                out[f"{resolution}_map_code"] = codes[0]
                out[f"{resolution}_map_status"] = "SINGLETON_CANDIDATE"
            elif codes:
                out[f"{resolution}_map_code"] = ""
                out[f"{resolution}_map_status"] = "AMBIGUOUS_SET"
            else:
                out[f"{resolution}_map_code"] = ""
                out[f"{resolution}_map_status"] = "UNMAPPED"
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

    def map_union(self, codes: Iterable[str], taxonomy: Taxonomy) -> dict[str, str]:
        """Map a PSIC result that admits several separate subtrees, member by member.

        The I-O sets are the union of the members' sets, so they are never wider than the
        members justify. The common parent is not used: in Revision 5 a division can hold an
        activity that maps to another I-O industry (division 55 holds booking intermediation,
        division 47 holds motor vehicle retail). If any member has no I-O correspondence the
        union is incomplete and nothing is mapped.
        """
        members = sorted(set(str(c).strip() for c in codes if str(c).strip()))
        if not members or any(code not in taxonomy.nodes for code in members):
            return _empty(IOMappingStatus.UNMAPPED_UNKNOWN_PSIC_CODE)
        if len(members) == 1:
            return self.map_psic(members[0], taxonomy)
        parts = [self.map_psic(code, taxonomy) for code in members]
        if any(not part[f"{res}_codes"] for part in parts for res in RESOLUTIONS):
            row = _empty(IOMappingStatus.UNMAPPED_UNION_PARTIAL)
            row["io_mapping_psic_level"] = "union"
            row["io_mapping_psic_code"] = CODE_DELIMITER.join(members)
            return row
        provisional = any(part["io_mapping_status"].startswith("PROVISIONAL") for part in parts)
        out = dict.fromkeys(IO_OUTPUT_COLUMNS, "")
        out.update(
            {
                "io_mapping_status": str(
                    IOMappingStatus.PROVISIONAL_UNION
                    if provisional
                    else IOMappingStatus.MAPPED_UNION
                ),
                "io_mapping_psic_level": "union",
                "io_mapping_psic_code": CODE_DELIMITER.join(members),
                "io_mapping_psic2019_codes": CODE_DELIMITER.join(
                    dict.fromkeys(
                        c
                        for part in parts
                        for c in _split(part["io_mapping_psic2019_codes"], CODE_DELIMITER)
                    )
                ),
                "io_mapping_bridge_relation": CODE_DELIMITER.join(
                    sorted({part["io_mapping_bridge_relation"] for part in parts})
                ),
                "io_mapping_bridge_basis": CODE_DELIMITER.join(
                    sorted({part["io_mapping_bridge_basis"] for part in parts})
                ),
            }
        )
        for resolution in RESOLUTIONS:
            codes_ = sorted(
                {c for part in parts for c in _split(part[f"{resolution}_codes"], CODE_DELIMITER)}
            )
            out[f"{resolution}_codes"] = CODE_DELIMITER.join(codes_)
            out[f"{resolution}_names"] = NAME_DELIMITER.join(
                self._names[resolution].get(c, "") for c in codes_
            )
            out[f"{resolution}_confidence"] = _weakest(
                part[f"{resolution}_confidence"]
                for part in parts
                if part[f"{resolution}_confidence"] not in ("", NOT_MAPPED)
            )
            out[f"{resolution}_map_code"] = codes_[0] if len(codes_) == 1 else ""
            out[f"{resolution}_map_status"] = (
                "SINGLETON_CANDIDATE"
                if len(codes_) == 1
                else "AMBIGUOUS_SET"
                if codes_
                else "UNMAPPED"
            )
        return {column: out[column] for column in IO_OUTPUT_COLUMNS}

    def subgroup(
        self, code: str | None, taxonomy: Taxonomy, base: dict[str, str]
    ) -> dict[str, str]:
        """Draft IO240 narrowing for a Revision 5 class or subclass, in separate columns.

        The nearest class/subclass ancestor with a refinement row decides, but only when the
        row's title matches the loaded taxonomy. The narrowed IO240 set must lie inside the
        base set, and its IO80/IO16 parents inside the base IO80/IO16 sets; otherwise the row
        is reported as ``not_applied``. Without a row the base sets are repeated unchanged.
        """
        out = {
            "io_subgroup_refinement_code": "",
            "io_subgroup_refinement_basis": "",
            **{f"{r}_subgroup_codes": base.get(f"{r}_codes", "") for r in RESOLUTIONS},
        }
        if not code or self.hierarchy is None or code not in taxonomy.nodes:
            return out
        row = None
        for ancestor in taxonomy.ancestors(code, include_self=True):
            candidate = self._refine.get(str(ancestor))
            if candidate is not None and _normalize_title(candidate.rev5_title) == _normalize_title(
                taxonomy.get(ancestor).title
            ):
                row = candidate
                break
        base240 = set(_split(base.get("io240_codes", ""), CODE_DELIMITER))
        if row is None or not base240:
            return out
        base80 = set(_split(base.get("io80_codes", ""), CODE_DELIMITER))
        base16 = set(_split(base.get("io16_codes", ""), CODE_DELIMITER))
        refined240 = sorted(row.io240_codes)
        parents80 = sorted({self.hierarchy.io240_parent[c] for c in refined240})
        parents16 = sorted({self.hierarchy.io80_parent[c] for c in parents80})
        out["io_subgroup_refinement_code"] = row.rev5_code
        nested = set(refined240) <= base240 and set(parents80) <= base80
        if not (nested and set(parents16) <= base16):
            out["io_subgroup_refinement_basis"] = "not_applied"
            return out
        out["io_subgroup_refinement_basis"] = row.basis
        out["io240_subgroup_codes"] = CODE_DELIMITER.join(refined240)
        out["io80_subgroup_codes"] = CODE_DELIMITER.join(parents80)
        out["io16_subgroup_codes"] = CODE_DELIMITER.join(parents16)
        return out

    def subgroup_union(
        self, codes: Iterable[str], taxonomy: Taxonomy, base: dict[str, str]
    ) -> dict[str, str]:
        """Union of the members' draft narrowing; never wider than the union's own sets."""
        members = [str(c).strip() for c in codes if str(c).strip() in taxonomy.nodes]
        parts = [self.subgroup(c, taxonomy, self.map_psic(c, taxonomy)) for c in members]
        out = {
            "io_subgroup_refinement_code": CODE_DELIMITER.join(
                p["io_subgroup_refinement_code"] for p in parts if p["io_subgroup_refinement_code"]
            ),
            "io_subgroup_refinement_basis": CODE_DELIMITER.join(
                sorted(
                    {
                        p["io_subgroup_refinement_basis"]
                        for p in parts
                        if p["io_subgroup_refinement_basis"]
                    }
                )
            ),
        }
        for resolution in RESOLUTIONS:
            if not base.get(f"{resolution}_codes"):
                out[f"{resolution}_subgroup_codes"] = ""
                continue
            out[f"{resolution}_subgroup_codes"] = CODE_DELIMITER.join(
                sorted(
                    {
                        c
                        for p in parts
                        for c in _split(p[f"{resolution}_subgroup_codes"], CODE_DELIMITER)
                    }
                )
            )
        return out


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
    columns = ["canonical_id", "code"] + [
        col for col in ("status", "audit", "admissible_roots") if col in results.columns
    ]
    psic = results.loc[results["scheme"] == "psic", columns]
    ids = [str(value) for value in psic["canonical_id"]]
    keys: list[tuple[str, object]] = []
    for record in psic.to_dict("records"):
        code = _clean_code(record["code"])
        roots = _admissible_roots(record, taxonomy, code)
        keys.append(("union", roots) if len(roots) > 1 else ("code", code))
    lookup: dict[tuple[str, object], dict[str, str]] = {}
    for key in dict.fromkeys(keys):
        kind, value = key
        if kind == "union":
            base = crosswalk.map_union(value, taxonomy)  # type: ignore[arg-type]
            extra = crosswalk.subgroup_union(value, taxonomy, base)  # type: ignore[arg-type]
            members = tuple(value)  # type: ignore[arg-type]
        else:
            base = crosswalk.map_psic(value, taxonomy)  # type: ignore[arg-type]
            extra = crosswalk.subgroup(value, taxonomy, base)  # type: ignore[arg-type]
            members = (value,) if value else ()
        draft = {"io_mapping_draft_levels": crosswalk.draft_levels(members, taxonomy, base)}
        lookup[key] = {**base, **extra, **draft}
    out_columns = (*IO_OUTPUT_COLUMNS, *IO_SUBGROUP_COLUMNS, *IO_DRAFT_COLUMNS)
    mapped = [{"canonical_id": cid, **lookup[key]} for cid, key in zip(ids, keys, strict=True)]
    io_frame = pd.DataFrame(mapped, columns=["canonical_id", *out_columns])
    # An explicit string dtype avoids Arrow's null type when a slice has no mapped rows.
    for column in out_columns:
        io_frame[column] = io_frame[column].astype("string")
    enriched = summary.merge(io_frame, on="canonical_id", how="left", validate="one_to_one")
    # A POI without a PSIC result row has no PSIC code to map.
    missing = enriched["io_mapping_status"].isna()
    if missing.any():
        enriched.loc[missing, list(out_columns)] = ""
        enriched.loc[missing, "io_mapping_status"] = str(IOMappingStatus.UNMAPPED_NO_PSIC)
        for resolution in RESOLUTIONS:
            enriched.loc[missing, f"{resolution}_map_status"] = "UNMAPPED"
    return resolve_io80_ambiguity(enriched)


def _admissible_roots(
    record: dict, taxonomy: Taxonomy, code: str | None
) -> tuple[str, ...]:
    """Read accepted roots; never widen a malformed or missing known UNION."""
    if "admissible_roots" in record:
        raw = record["admissible_roots"]
        if isinstance(raw, str):
            raw = json.loads(raw)
    else:
        audit = record.get("audit")
        if isinstance(audit, str) and audit.strip():
            audit = json.loads(audit)
        raw = audit.get("admissible_roots", []) if isinstance(audit, dict) else []
    if not isinstance(raw, (list, tuple)) or any(
        not isinstance(root, str) or root not in taxonomy.nodes for root in raw
    ):
        raise ValueError("invalid admissible_roots in PSIC classification")
    roots = sorted(set(raw))
    if roots and code is not None and any(
        root != code and code not in taxonomy.ancestors(root) for root in roots
    ):
        raise ValueError("admissible_roots outside representative PSIC subtree")
    minimal = tuple(
        root for root in roots if not any(
            other != root and other in taxonomy.ancestors(root) for other in roots
        )
    )
    if record.get("status") == "UNION" and len(minimal) < 2:
        raise ValueError("UNION classification has missing or redundant admissible_roots")
    return minimal


def io_status_counts(summary: pd.DataFrame) -> dict[str, int]:
    """Number of POIs per ``io_mapping_status``, for the run manifest."""
    if "io_mapping_status" not in summary.columns:
        return {}
    counts = summary["io_mapping_status"].value_counts(dropna=False)
    return {str(status): int(count) for status, count in sorted(counts.items(), key=str)}



def _nonblank(series: pd.Series) -> pd.Series:
    """Boolean mask treating nulls and whitespace-only strings as blank."""
    return series.astype("string").fillna("").str.strip().ne("")


def _share(count: int, total: int) -> float:
    return float(count / total) if total else 0.0


def _value_counts(series: pd.Series, mask: pd.Series) -> dict[str, int]:
    values = series[mask].astype("string").fillna("").str.strip()
    counts = values[values.ne("")].value_counts()
    return {
        str(value): int(count)
        for value, count in sorted(counts.items(), key=lambda item: str(item[0]))
    }


def _validate_map_fields(
    summary: pd.DataFrame, resolution: str, candidate_counts: pd.Series
) -> None:
    """Validate map fields against candidate cardinality and resolver provenance."""
    if resolution == "io80":
        validate_io80_map_fields(summary, candidate_counts)
        return

    codes = (
        summary[f"{resolution}_codes"].astype("string").fillna("").str.strip()
    )
    map_codes = (
        summary[f"{resolution}_map_code"].astype("string").fillna("").str.strip()
    )
    map_status = (
        summary[f"{resolution}_map_status"].astype("string").fillna("").str.strip()
    )
    expected_codes = codes.where(candidate_counts.eq(1), "")
    expected_status = pd.Series("UNMAPPED", index=summary.index, dtype="string")
    expected_status.loc[candidate_counts.eq(1)] = "SINGLETON_CANDIDATE"
    expected_status.loc[candidate_counts.gt(1)] = "AMBIGUOUS_SET"
    bad = map_codes.ne(expected_codes) | map_status.ne(expected_status)
    if bad.any():
        raise ValueError(
            f"{resolution} map fields disagree with candidate sets on {int(bad.sum())} row(s)"
        )


def io_coverage_summary(summary: pd.DataFrame) -> dict[str, object]:
    """Describe candidate availability, map readiness, provenance and confidence.

    This is deliberately not an accuracy metric. A singleton map code means only that the
    derived concordance currently has one candidate at that resolution. Semantic correctness
    still depends on the PSIC classification, the Revision 5 -> 2019 bridge, and the derived
    2019 PSIC -> I-O concordance.
    """
    required = {
        "psic_code",
        "psic_level",
        "psic_status",
        "io_mapping_status",
        "io_mapping_bridge_basis",
        *(f"{resolution}_codes" for resolution in RESOLUTIONS),
        *IO_MAP_COLUMNS,
        *(f"{resolution}_confidence" for resolution in RESOLUTIONS),
    }
    missing = required - set(summary.columns)
    if missing:
        raise ValueError(f"I-O coverage summary is missing columns {sorted(missing)}")

    total = int(len(summary))
    psic_mask = _nonblank(summary["psic_code"])
    psic_coded = int(psic_mask.sum())

    levels = summary["psic_level"].astype("string").fillna("").str.strip()
    level_counts = {level: int(levels.eq(level).sum()) for level in PSIC_LEVELS}
    rank = levels.map({level: index for index, level in enumerate(PSIC_LEVELS)})
    depth: dict[str, dict[str, int | float]] = {}
    for index, level in enumerate(PSIC_LEVELS):
        rows = int(rank.ge(index).sum())
        depth[level] = {
            "rows": rows,
            "share": _share(rows, total),
            "share_of_psic_coded": _share(rows, psic_coded),
        }

    psic_status = summary["psic_status"].astype("string").fillna("").str.strip()
    psic_status_counts = _value_counts(psic_status, psic_status.ne(""))
    psic_status_counts_when_coded = _value_counts(psic_status, psic_mask)

    statuses = summary["io_mapping_status"].astype("string").fillna("").str.strip()
    ordinary_mask = statuses.isin({str(status) for status in _MAPPED_AT.values()} | _ORDINARY_UNION)
    provisional_mask = statuses.isin(
        {str(status) for status in _PROVISIONAL_AT.values()} | _PROVISIONAL_UNION
    )

    basis = summary["io_mapping_bridge_basis"].astype("string").fillna("").str.strip()
    basis_counts = _value_counts(basis, basis.ne(""))

    resolutions: dict[str, dict[str, object]] = {}
    all_rows = pd.Series(True, index=summary.index)
    for resolution in RESOLUTIONS:
        text = summary[f"{resolution}_codes"].astype("string").fillna("").str.strip()
        candidate_counts = text.map(lambda value: len(parse_candidate_codes(value))).astype("int64")
        _validate_map_fields(summary, resolution, candidate_counts)

        available_mask = candidate_counts.gt(0)
        singleton_mask = candidate_counts.eq(1)
        multiple_mask = candidate_counts.gt(1)
        available_rows = int(available_mask.sum())
        singleton_rows = int(singleton_mask.sum())
        multiple_rows = int(multiple_mask.sum())
        mapped_counts = candidate_counts[available_mask]
        confidence = summary[f"{resolution}_confidence"]
        map_status = (
            summary[f"{resolution}_map_status"]
            .astype("string")
            .fillna("")
            .str.strip()
        )
        map_ready_mask = _nonblank(summary[f"{resolution}_map_code"])
        name_resolved_mask = map_status.eq("NAME_MATCH_RESOLVED")
        model_resolved_mask = map_status.eq("MODEL_RESOLVED")
        evidence_resolved_mask = map_status.isin(IO80_RESOLVED_STATUSES)
        unresolved_multiple_mask = multiple_mask & map_status.eq("AMBIGUOUS_SET")
        map_ready_rows = int(map_ready_mask.sum())

        metrics: dict[str, object] = {
            "available_rows": available_rows,
            "available_share": _share(available_rows, total),
            "singleton_candidate_rows": singleton_rows,
            "name_resolved_rows": int(name_resolved_mask.sum()),
            "model_resolved_rows": int(model_resolved_mask.sum()),
            "evidence_resolved_rows": int(evidence_resolved_mask.sum()),
            "map_ready_rows": map_ready_rows,
            "map_ready_share": _share(map_ready_rows, total),
            "map_ready_share_of_available": _share(map_ready_rows, available_rows),
            "multiple_candidate_rows": multiple_rows,
            "multiple_candidate_share": _share(multiple_rows, total),
            "unresolved_multiple_candidate_rows": int(unresolved_multiple_mask.sum()),
            "unresolved_multiple_candidate_share": _share(
                int(unresolved_multiple_mask.sum()), total
            ),
            "mean_candidates_when_available": (
                float(mapped_counts.mean()) if available_rows else 0.0
            ),
            "median_candidates_when_available": (
                float(mapped_counts.median()) if available_rows else 0.0
            ),
            "map_status_counts": _value_counts(map_status, all_rows),
            "bridge_basis_counts_when_available": _value_counts(basis, available_mask),
            "bridge_basis_counts_for_map_ready": _value_counts(basis, map_ready_mask),
            "confidence_counts_when_available": _value_counts(confidence, available_mask),
            "confidence_counts_for_map_ready": _value_counts(confidence, map_ready_mask),
        }
        if resolution == "io80":
            method = (
                summary["io80_resolution_method"]
                .astype("string")
                .fillna("")
                .str.strip()
            )
            metrics["resolution_method_counts"] = _value_counts(method, method.ne(""))
        resolutions[resolution] = metrics

    ordinary_rows = int(ordinary_mask.sum())
    provisional_rows = int(provisional_mask.sum())
    return {
        "total_rows": total,
        "psic_coded_rows": psic_coded,
        "psic_coded_share": _share(psic_coded, total),
        "psic_level_counts": level_counts,
        "psic_depth": depth,
        "psic_status_counts": psic_status_counts,
        "psic_status_counts_when_coded": psic_status_counts_when_coded,
        "ordinary_mapped_rows": ordinary_rows,
        "ordinary_mapped_share": _share(ordinary_rows, total),
        "provisional_mapped_rows": provisional_rows,
        "provisional_mapped_share": _share(provisional_rows, total),
        "bridge_basis_counts": basis_counts,
        "resolutions": resolutions,
    }
