from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

import pandas as pd

from .openplaces import Row, SOURCES, source_evidence
from .text import normalize_key, normalize_match_key


class UnitType(StrEnum):
    ESTABLISHMENT = "ESTABLISHMENT"
    INSTITUTIONAL_SITE = "INSTITUTIONAL_SITE"
    RESIDENTIAL = "RESIDENTIAL"
    CONTAINER = "CONTAINER"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    AMENITY = "AMENITY"
    OTHER = "OTHER"
    UNCERTAIN = "UNCERTAIN"


class EstablishmentStatus(StrEnum):
    YES = "YES"
    NO = "NO"
    UNCERTAIN = "UNCERTAIN"


class IORole(StrEnum):
    PRODUCER = "PRODUCER"
    INTERMEDIATE_DEMAND = "INTERMEDIATE_DEMAND"
    HOUSEHOLD_FINAL_DEMAND_PROXY = "HOUSEHOLD_FINAL_DEMAND_PROXY"
    GOVERNMENT_FINAL_DEMAND_PROXY = "GOVERNMENT_FINAL_DEMAND_PROXY"
    NPISH_FINAL_DEMAND_PROXY = "NPISH_FINAL_DEMAND_PROXY"
    CAPITAL_ASSET = "CAPITAL_ASSET"
    CONTAINER = "CONTAINER"


ROLE_REVIEW_COLUMNS = (
    "unit_type",
    "establishment_status",
    "io_roles",
    "role_confidence",
    "role_notes",
)

ROLE_OUTPUT_COLUMNS = (
    "canonical_id",
    "unit_type",
    "establishment_status",
    "io_roles",
    "role_confidence",
    "role_status",
    "role_flags",
)

_VALID_MATCH_TYPES = {"exact", "contains", "regex"}


class RoleCrosswalkError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RoleRule:
    source: str
    source_value: str
    source_field: str = "category"
    match_type: str = "exact"
    unit_type: UnitType | None = None
    establishment_status: EstablishmentStatus | None = None
    io_roles: tuple[IORole, ...] = ()
    confidence: float | None = None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class TaxonomyClassification:
    """One scheme's place-level result as evidence for the role layer.

    The economic-role API receives every available scheme. Scheme-specific semantic rules
    decide what a result can imply; the API itself does not privilege PSIC, PCPC, or PSCC.
    """

    code: str | None = None
    status: str = ""


@dataclass(slots=True)
class EconomicProfile:
    canonical_id: str
    unit_type: str
    establishment_status: str
    io_roles: list[str] = field(default_factory=list)
    role_confidence: float | None = None
    role_status: str = "UNRESOLVED"
    role_flags: list[str] = field(default_factory=list)

    def as_record(self) -> dict[str, object]:
        return {
            "canonical_id": self.canonical_id,
            "unit_type": self.unit_type,
            "establishment_status": self.establishment_status,
            "io_roles": json.dumps(self.io_roles, separators=(",", ":")),
            "role_confidence": self.role_confidence,
            "role_status": self.role_status,
            "role_flags": json.dumps(self.role_flags, separators=(",", ":")),
        }


def _role_key(rule: RoleRule) -> tuple[str, str, str]:
    # The joint semantic key excludes scheme/version and matching policy. Scheme copies of
    # one source value must agree on the role decision *and* on how the role rule matches.
    return rule.source, rule.source_field, rule.source_value.strip()


def _coalesce_role_copies(
    copies: list[tuple[str, int, RoleRule]],
    problems: list[str],
) -> RoleRule | None:
    """Collapse the PSIC/PCPC/PSCC copies of one joint role annotation.

    Blank copies never reach this function. Nonblank copies may repeat the same semantic
    decision, but may not disagree merely because they sit on different scheme rows.
    """

    _, _, first = copies[0]
    labels = ", ".join(f"{path}:{line_no}" for path, line_no, _ in copies)

    match_types = {rule.match_type for _, _, rule in copies}
    if len(match_types) > 1:
        problems.append(
            f"{labels} disagree on match_type for {first.source}:{first.source_value!r}"
        )
        return None

    unit_values = {rule.unit_type for _, _, rule in copies if rule.unit_type is not None}
    if len(unit_values) > 1:
        problems.append(f"{labels} disagree on unit_type for {first.source}:{first.source_value!r}")
        return None

    establishment_values = {
        rule.establishment_status
        for _, _, rule in copies
        if rule.establishment_status is not None
    }
    if len(establishment_values) > 1:
        problems.append(
            f"{labels} disagree on establishment_status for "
            f"{first.source}:{first.source_value!r}"
        )
        return None

    role_sets = {
        frozenset(rule.io_roles) for _, _, rule in copies if rule.io_roles
    }
    if len(role_sets) > 1:
        problems.append(f"{labels} disagree on io_roles for {first.source}:{first.source_value!r}")
        return None

    confidence_values = {
        rule.confidence for _, _, rule in copies if rule.confidence is not None
    }
    if len(confidence_values) > 1:
        problems.append(
            f"{labels} disagree on role_confidence for {first.source}:{first.source_value!r}"
        )
        return None

    notes = "; ".join(
        dict.fromkeys(rule.notes.strip() for _, _, rule in copies if rule.notes.strip())
    )
    roles = next(iter(role_sets), frozenset())
    return replace(
        first,
        unit_type=next(iter(unit_values), None),
        establishment_status=next(iter(establishment_values), None),
        io_roles=tuple(role for role in IORole if role in roles),
        confidence=next(iter(confidence_values), None),
        notes=notes,
    )


class RoleCrosswalk:
    """Indexed role annotations stored beside the normal joint crosswalk.

    Role fields belong to the joint OpenPlaces source value, not to PSIC, PCPC, or PSCC.
    Repeated scheme rows therefore collapse to one role rule. Conflicting nonblank copies
    are rejected instead of being counted as independent semantic evidence.
    """

    def __init__(self, rules: list[RoleRule]):
        self.rules: list[RoleRule] = []
        self._exact: dict[tuple[str, str, str], list[RoleRule]] = defaultdict(list)
        self._scanned: dict[
            tuple[str, str], list[tuple[RoleRule, re.Pattern[str] | None, str]]
        ] = defaultdict(list)

        for given in rules:
            source = given.source.casefold().strip()
            field_name = given.source_field.casefold().strip()
            match_type = given.match_type.casefold().strip()
            if source not in SOURCES:
                raise RoleCrosswalkError(f"unknown OpenPlaces source {given.source!r}")
            if field_name not in {"category", "name"}:
                raise RoleCrosswalkError(
                    f"unknown source_field {given.source_field!r} for "
                    f"{source}:{given.source_value!r}"
                )
            if match_type not in _VALID_MATCH_TYPES:
                raise RoleCrosswalkError(
                    f"unknown match_type {given.match_type!r} for "
                    f"{source}:{given.source_value!r}"
                )
            rule = replace(
                given,
                source=source,
                source_field=field_name,
                match_type=match_type,
            )
            self.rules.append(rule)

            target = normalize_match_key(rule.source_value)
            if match_type != "regex" and not target:
                raise RoleCrosswalkError(
                    f"role source_value {rule.source_value!r} normalizes to an empty key"
                )
            if match_type == "exact" and not normalize_key(rule.source_value):
                raise RoleCrosswalkError(
                    f"role source_value {rule.source_value!r} is a generic null label; "
                    "exact matching is not well-defined"
                )
            if match_type == "exact":
                self._exact[(source, field_name, target)].append(rule)
            elif match_type == "regex":
                try:
                    pattern = re.compile(rule.source_value, flags=re.I)
                except re.error as exc:
                    raise RoleCrosswalkError(
                        f"invalid role regex {rule.source_value!r}: {exc}"
                    ) from exc
                self._scanned[(source, field_name)].append((rule, pattern, ""))
            else:
                self._scanned[(source, field_name)].append((rule, None, target))

    @classmethod
    def load(cls, paths: list[str | Path]) -> RoleCrosswalk:
        grouped: dict[tuple[str, str, str], list[tuple[str, int, RoleRule]]] = defaultdict(list)
        problems: list[str] = []
        for path in paths:
            frame = pd.read_csv(path, dtype=str).fillna("")
            for line_no, row in enumerate(frame.to_dict("records"), start=2):
                raw_unit = str(row.get("unit_type") or "").strip().upper()
                raw_est = str(row.get("establishment_status") or "").strip().upper()
                raw_roles = str(row.get("io_roles") or "").strip()
                raw_confidence = str(row.get("role_confidence") or "").strip()
                raw_notes = str(row.get("role_notes") or "").strip()
                if not (raw_unit or raw_est or raw_roles):
                    continue

                source = str(row.get("source") or "").strip().casefold()
                source_value = str(row.get("source_value") or "").strip()
                source_field = str(row.get("source_field") or "").strip().casefold() or "category"
                match_type = str(row.get("match_type") or "").strip().casefold() or "exact"
                if source not in SOURCES:
                    problems.append(f"{path}:{line_no} unknown source {source!r}")
                    continue
                if not source_value:
                    problems.append(f"{path}:{line_no} source_value must not be blank")
                    continue
                if source_field not in {"category", "name"}:
                    problems.append(f"{path}:{line_no} unknown source_field {source_field!r}")
                    continue
                if match_type not in _VALID_MATCH_TYPES:
                    problems.append(f"{path}:{line_no} unknown match_type {match_type!r}")
                    continue

                try:
                    unit_type = UnitType(raw_unit) if raw_unit else None
                except ValueError:
                    problems.append(f"{path}:{line_no} unknown unit_type {raw_unit!r}")
                    continue
                try:
                    establishment = EstablishmentStatus(raw_est) if raw_est else None
                except ValueError:
                    problems.append(
                        f"{path}:{line_no} unknown establishment_status {raw_est!r}"
                    )
                    continue

                parsed_roles: list[IORole] = []
                bad_role = False
                for raw_role in (part.strip().upper() for part in raw_roles.split("|")):
                    if not raw_role:
                        continue
                    try:
                        parsed_roles.append(IORole(raw_role))
                    except ValueError:
                        problems.append(f"{path}:{line_no} unknown io_role {raw_role!r}")
                        bad_role = True
                        break
                if bad_role:
                    continue

                confidence: float | None = None
                if raw_confidence:
                    try:
                        confidence = float(raw_confidence)
                    except ValueError:
                        problems.append(
                            f"{path}:{line_no} invalid role_confidence {raw_confidence!r}"
                        )
                        continue
                    if not 0.0 <= confidence <= 1.0:
                        problems.append(
                            f"{path}:{line_no} role_confidence must be between 0 and 1"
                        )
                        continue

                rule = RoleRule(
                    source=source,
                    source_value=source_value,
                    source_field=source_field,
                    match_type=match_type,
                    unit_type=unit_type,
                    establishment_status=establishment,
                    io_roles=tuple(dict.fromkeys(parsed_roles)),
                    confidence=confidence,
                    notes=raw_notes,
                )
                grouped[_role_key(rule)].append((str(path), line_no, rule))

        rules: list[RoleRule] = []
        for copies in grouped.values():
            merged = _coalesce_role_copies(copies, problems)
            if merged is not None:
                rules.append(merged)
        if problems:
            raise RoleCrosswalkError("role crosswalk problems:\n  " + "\n  ".join(problems))
        return cls(rules)

    def _match_field(self, source: str, field_name: str, value: str) -> list[RoleRule]:
        source = source.casefold()
        norm = normalize_key(value)
        exact = self._exact.get((source, field_name, norm))
        if exact:
            return list(exact)

        found: list[RoleRule] = []
        for rule, pattern, target in self._scanned.get((source, field_name), ()):
            matched = bool(pattern.search(value)) if pattern is not None else bool(
                target and target in norm
            )
            if matched:
                found.append(rule)
        return found

    def matches(
        self,
        source: str,
        *,
        category: str | None = None,
        name: str | None = None,
    ) -> list[RoleRule]:
        found: list[RoleRule] = []
        if category:
            found.extend(self._match_field(source, "category", category))
        if name:
            found.extend(self._match_field(source, "name", name))
        return found


def _psic_supports_io_roles(classification: TaxonomyClassification | None) -> bool:
    # ClassificationResult.code is the assigned taxonomy code; unresolved alternatives
    # live in candidate_codes. Therefore any assigned PSIC code is already evidence that
    # the place has an economic activity, even when the exact activity is a UNION.
    return classification is not None and bool(classification.code)


def infer_economic_profile(
    row: Row,
    role_crosswalk: RoleCrosswalk | None,
    *,
    classifications: Mapping[str, TaxonomyClassification] | None = None,
) -> EconomicProfile:
    """Infer place semantics and IO roles from joint role rules plus taxonomy results.

    All available schemes enter through one mapping. At present only a resolved PSIC
    activity has a direct IO-role implication: it supports PRODUCER and INTERMEDIATE_DEMAND.
    PCPC and PSCC remain available to the role layer but do not imply those roles merely by
    having a code, because they classify products/services and traded commodities rather
    than the activity of the producing unit.
    """

    flags: list[str] = []
    selected: list[RoleRule] = []

    if role_crosswalk is not None:
        for evidence in source_evidence(row):
            matches = role_crosswalk.matches(
                evidence.source,
                category=evidence.category,
                name=evidence.name,
            )
            name_rules = [rule for rule in matches if rule.source_field == "name"]
            category_rules = [rule for rule in matches if rule.source_field == "category"]
            if name_rules:
                if category_rules:
                    flags.append(f"ROLE_NAME_RULE_PRECEDENCE:{evidence.source}")
                selected.extend(name_rules)
            else:
                selected.extend(category_rules)

    unit_values = {rule.unit_type for rule in selected if rule.unit_type is not None}
    establishment_values = {
        rule.establishment_status
        for rule in selected
        if rule.establishment_status is not None
    }

    if len(unit_values) == 1:
        unit_type = next(iter(unit_values)).value
    else:
        unit_type = UnitType.UNCERTAIN.value
        if len(unit_values) > 1:
            flags.append("ROLE_UNIT_TYPE_CONFLICT")

    if len(establishment_values) == 1:
        establishment_status = next(iter(establishment_values)).value
    else:
        establishment_status = EstablishmentStatus.UNCERTAIN.value
        if len(establishment_values) > 1:
            flags.append("ROLE_ESTABLISHMENT_STATUS_CONFLICT")

    roles = {role for rule in selected for role in rule.io_roles}
    taxonomy_role_evidence = False
    by_scheme = {str(k).casefold(): v for k, v in (classifications or {}).items()}
    if _psic_supports_io_roles(by_scheme.get("psic")):
        roles.update({IORole.PRODUCER, IORole.INTERMEDIATE_DEMAND})
        flags.append("IO_ROLE_FROM_PSIC_ACTIVITY")
        taxonomy_role_evidence = True

    confidences = [rule.confidence for rule in selected if rule.confidence is not None]
    role_confidence = min(confidences) if confidences else None

    has_conflict = any(flag.endswith("_CONFLICT") for flag in flags)
    if has_conflict:
        role_status = "REVIEW"
    elif selected:
        role_status = "REVIEWED"
    elif taxonomy_role_evidence:
        role_status = "INFERRED_FROM_TAXONOMY"
    else:
        role_status = "UNRESOLVED"

    ordered_roles = [role.value for role in IORole if role in roles]
    return EconomicProfile(
        canonical_id=str(row["canonical_id"]),
        unit_type=unit_type,
        establishment_status=establishment_status,
        io_roles=ordered_roles,
        role_confidence=role_confidence,
        role_status=role_status,
        role_flags=sorted(set(flags)),
    )
