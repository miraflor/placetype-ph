from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .models import CrosswalkEntry, MappingKind, SourceField
from .openplaces import SOURCES
from .taxonomy import SCHEMES
from .text import normalize_key, normalize_match_key


class CrosswalkError(ValueError):
    pass


_VALID_MATCH_TYPES = {"exact", "contains", "regex"}
_REQUIRED_COLUMNS = {"source", "source_value", "scheme", "version", "mapping_kind"}
_SOURCES = frozenset(SOURCES)


@dataclass(frozen=True, slots=True)
class CrosswalkMatches:
    """Result of looking one OpenPlaces record up in the crosswalk.

    `ambiguities` describes fields where several scanned rules matched with different
    decisions. That field contributes no mapping, but the run continues: an ambiguity is
    a review problem for one category value, not a reason to lose a whole build.
    """

    entries: tuple[CrosswalkEntry, ...] = ()
    ambiguities: tuple[str, ...] = field(default=())

    def __bool__(self) -> bool:
        return bool(self.entries)


class Crosswalk:
    """Indexed crosswalk.

    Every `source_value` is normalized once at load and every regex is compiled once.
    Exact matching is a dict lookup; only `contains` and `regex` rows are scanned.
    """

    def __init__(self, entries: list[CrosswalkEntry], strict_ambiguity: bool = False):
        self.entries = list(entries)
        self.strict_ambiguity = strict_ambiguity
        self._exact: dict[tuple[str, str, str, str, str], CrosswalkEntry] = {}
        Scanned = list[tuple[CrosswalkEntry, re.Pattern | None, str]]
        self._scanned: dict[tuple[str, str, str, str], Scanned]
        self._scanned = defaultdict(list)
        self._scope: dict[tuple[str, str], int] = defaultdict(int)

        for entry in self.entries:
            source = entry.source.casefold().strip()
            scheme = entry.scheme.casefold().strip()
            source_value = entry.source_value.strip()
            if source not in _SOURCES:
                raise CrosswalkError(f"unknown OpenPlaces source {entry.source!r}")
            if not source_value:
                raise CrosswalkError(f"blank source_value for {source} crosswalk row")
            if scheme not in SCHEMES:
                raise CrosswalkError(f"unknown classification scheme {entry.scheme!r}")
            if not str(entry.version).strip():
                raise CrosswalkError(f"blank version for {source}:{entry.source_value!r}")
            field_name = str(entry.source_field).casefold().strip()
            if field_name not in {"category", "name"}:
                raise CrosswalkError(
                    f"unknown source_field {entry.source_field!r} for "
                    f"{source}:{entry.source_value!r}"
                )
            if entry.confidence is not None and not 0.0 <= float(entry.confidence) <= 1.0:
                raise CrosswalkError(
                    f"confidence must be between 0 and 1 for "
                    f"{source}:{entry.source_value!r}, got {entry.confidence}"
                )
            match_type = entry.match_type.casefold()
            if match_type not in _VALID_MATCH_TYPES:
                raise CrosswalkError(
                    f"unknown match_type {entry.match_type!r} for "
                    f"{entry.source}:{entry.source_value!r}; expected exact, contains, or regex"
                )
            match_key = normalize_match_key(source_value)
            if match_type != "regex" and not match_key:
                # A value made only of separators indexes under the empty key: the exact
                # rule would then match any record whose normalized value is also empty,
                # and the contains rule could never match at all. Neither is reviewable.
                raise CrosswalkError(
                    f"source_value {entry.source_value!r} for {source} normalizes to an "
                    f"empty match key ({scheme} {entry.version}, {field_name}); it carries "
                    "no matchable characters, so use a regex rule if this is intended"
                )
            if match_type == "exact" and not normalize_key(source_value):
                # The canonical observed value is removed by POI null semantics, while
                # punctuated or extended variants may normalize differently. Exact matching
                # on such a reviewed value is therefore inconsistent and not reviewable.
                raise CrosswalkError(
                    f"source_value {entry.source_value!r} for {source} is a generic null label "
                    f"({scheme} {entry.version}, {field_name}); its canonical value is missing, "
                    "so exact matching is not well-defined. Use contains for larger values, "
                    "regex for deliberate raw matching, or change the null vocabulary in text.py."
                )
            self._scope[(scheme, entry.version)] += 1
            bucket = (source, scheme, entry.version, field_name)
            if match_type == "exact":
                key = (*bucket, match_key)
                previous = self._exact.get(key)
                if previous is not None and self._signature(previous) != self._signature(entry):
                    raise CrosswalkError(
                        "conflicting duplicate exact crosswalk rows for "
                        f"{source}:{entry.source_value!r} "
                        f"({scheme} {entry.version}, {field_name})"
                    )
                self._exact.setdefault(key, entry)
            elif match_type == "regex":
                try:
                    pattern = re.compile(source_value, flags=re.I)
                except re.error as exc:
                    raise CrosswalkError(
                        "invalid regex in crosswalk row "
                        f"{entry.source}:{entry.source_value!r}: {exc}"
                    ) from exc
                self._scanned[bucket].append((entry, pattern, ""))
            else:
                self._scanned[bucket].append((entry, None, match_key))

    @staticmethod
    def _signature(entry: CrosswalkEntry) -> tuple[MappingKind, tuple[str, ...]]:
        """Fields that can change the classification decision.

        Notes/confidence are review metadata. Code order is not semantic for a UNION,
        so two reviewed rules that name the same alternatives in a different order must
        not become a false ambiguity.
        """
        codes = (
            tuple(sorted(entry.codes))
            if entry.mapping_kind == MappingKind.UNION
            else entry.codes
        )
        return entry.mapping_kind, codes

    def count_for(self, scheme: str, version: str) -> int:
        """How many loaded rows can ever apply to this scheme/version."""
        return self._scope.get((scheme.casefold(), version), 0)

    @classmethod
    def load(
        cls, paths: list[str | Path], strict_ambiguity: bool = False
    ) -> Crosswalk:
        entries: list[CrosswalkEntry] = []
        problems: list[str] = []
        for path in paths:
            frame = pd.read_csv(path, dtype=str).fillna("")
            missing = _REQUIRED_COLUMNS - set(frame.columns)
            if missing:
                problems.append(f"{path}: missing columns {sorted(missing)}")
                continue
            for line_no, row in enumerate(frame.to_dict("records"), start=2):
                raw_kind = str(row.get("mapping_kind") or "").strip()
                if not raw_kind:
                    continue  # permits partially reviewed worklist CSVs

                source = str(row.get("source") or "").strip().casefold()
                source_value = str(row.get("source_value") or "").strip()
                scheme = str(row.get("scheme") or "").strip().casefold()
                version = str(row.get("version") or "").strip()
                if source not in _SOURCES:
                    problems.append(f"{path}:{line_no} unknown source {source!r}")
                    continue
                if not source_value:
                    problems.append(f"{path}:{line_no} source_value must not be blank")
                    continue
                if scheme not in SCHEMES:
                    problems.append(f"{path}:{line_no} unknown scheme {scheme!r}")
                    continue
                if not version:
                    problems.append(f"{path}:{line_no} version must not be blank")
                    continue

                kind_name = raw_kind.upper()
                if kind_name == "CLASS":  # original PSIC-pipeline terminology
                    kind_name = "EXACT"
                try:
                    kind = MappingKind(kind_name)
                except ValueError:
                    problems.append(f"{path}:{line_no} unknown mapping_kind {raw_kind!r}")
                    continue
                raw_field = str(row.get("source_field") or "category").strip().casefold()
                try:
                    source_field = SourceField(raw_field)
                except ValueError:
                    problems.append(f"{path}:{line_no} unknown source_field {raw_field!r}")
                    continue
                raw_codes = str(row.get("codes") or "")
                codes = tuple(c.strip() for c in raw_codes.split("|") if c.strip())
                confidence = row.get("confidence")
                match_type = str(row.get("match_type") or "exact").strip().casefold()
                if match_type not in _VALID_MATCH_TYPES:
                    problems.append(f"{path}:{line_no} unknown match_type {match_type!r}")
                    continue
                confidence_value: float | None = None
                if confidence not in (None, ""):
                    try:
                        confidence_value = float(confidence)
                    except ValueError:
                        problems.append(f"{path}:{line_no} invalid confidence {confidence!r}")
                        continue
                    if not 0.0 <= confidence_value <= 1.0:
                        problems.append(
                            f"{path}:{line_no} confidence must be between 0 and 1, got "
                            f"{confidence_value}"
                        )
                        continue
                entries.append(
                    CrosswalkEntry(
                        source=source,
                        source_value=source_value,
                        scheme=scheme,
                        version=version,
                        mapping_kind=kind,
                        codes=codes,
                        match_type=match_type,
                        confidence=confidence_value,
                        notes=str(row.get("notes") or ""),
                        source_field=source_field,
                    )
                )
        if problems:
            raise CrosswalkError("crosswalk problems:\n  " + "\n  ".join(problems))
        return cls(entries, strict_ambiguity=strict_ambiguity)

    def _match_field(
        self, source: str, scheme: str, version: str, field_name: SourceField, value: str
    ) -> tuple[CrosswalkEntry | None, str | None]:
        bucket = (source, scheme, version, str(field_name))
        # Observed POI text keeps POI null semantics. Reviewed rule targets are
        # normalized separately at load so authoritative values do not collide.
        norm = normalize_key(value)
        hit = self._exact.get((*bucket, norm))
        if hit is not None:
            return hit, None
        scanned_hits: list[CrosswalkEntry] = []
        for entry, pattern, target in self._scanned.get(bucket, ()):
            if pattern is not None:
                matched = bool(pattern.search(value))
            else:
                matched = bool(target and target in norm)
            if matched:
                scanned_hits.append(entry)

        if not scanned_hits:
            return None, None
        if len({self._signature(entry) for entry in scanned_hits}) > 1:
            rules = ", ".join(
                f"{entry.match_type}:{entry.source_value!r}"
                f"->{entry.mapping_kind.value}:{'|'.join(entry.codes)}"
                for entry in sorted(scanned_hits, key=lambda e: e.source_value)
            )
            detail = (
                f"ambiguous crosswalk rules for {source}:{value!r} "
                f"({scheme} {version}, {field_name}): {rules}"
            )
            if self.strict_ambiguity:
                raise CrosswalkError(detail)
            return None, detail
        # Same decision under several rules is harmless. Prefer the most specific
        # textual rule for stable audit metadata rather than depending on CSV order.
        return max(scanned_hits, key=lambda entry: len(entry.source_value)), None

    def matches(
        self,
        source: str,
        scheme: str,
        version: str,
        category: str | None = None,
        name: str | None = None,
    ) -> CrosswalkMatches:
        """Return both category- and name-keyed evidence when both apply.

        The two matches are not independent annotators; the classifier keeps them in the
        same source dependency group. This lets a reviewed chain/name rule refine a broad
        category rule without counting the source twice.
        """
        source = source.casefold()
        scheme = scheme.casefold()
        found: list[CrosswalkEntry] = []
        ambiguities: list[str] = []
        for field_name, value in (
            (SourceField.CATEGORY, category),
            (SourceField.NAME, name),
        ):
            if not value:
                continue
            hit, detail = self._match_field(source, scheme, version, field_name, value)
            if detail is not None:
                ambiguities.append(detail)
            if hit is not None and hit not in found:
                found.append(hit)
        return CrosswalkMatches(tuple(found), tuple(ambiguities))

    def match(
        self,
        source: str,
        scheme: str,
        version: str,
        category: str | None = None,
        name: str | None = None,
    ) -> CrosswalkEntry | None:
        """First applicable entry, category-keyed rules first. Thin wrapper on `matches`."""
        found = self.matches(source, scheme, version, category=category, name=name)
        return found.entries[0] if found.entries else None
