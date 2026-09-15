from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class MappingKind(StrEnum):
    EXACT = "EXACT"
    SUBTREE = "SUBTREE"
    UNION = "UNION"
    NOT_ACTIVITY = "NOT_ACTIVITY"
    UNCODEABLE = "UNCODEABLE"


class SourceField(StrEnum):
    """Which OpenPlaces field a crosswalk row is keyed on."""

    CATEGORY = "category"
    NAME = "name"


class FusionStatus(StrEnum):
    EMPTY = "EMPTY"
    SINGLE = "SINGLE"
    NESTED = "NESTED"
    INTERSECT = "INTERSECT"
    UNION = "UNION"
    CONFLICT = "CONFLICT"


class Eligibility(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    NON_ECONOMIC_POI = "NON_ECONOMIC_POI"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True, slots=True)
class TaxonomyNode:
    scheme: str
    version: str
    code: str
    level: str
    title: str
    parent_code: str | None = None
    description: str = ""
    includes: str = ""
    excludes: str = ""
    source_url: str = ""

    @property
    def retrieval_text(self) -> str:
        # Exclusions are intentionally not indexed as positive retrieval text.
        return "\n".join(x for x in (self.title, self.description, self.includes) if x).strip()


@dataclass(frozen=True, slots=True)
class CrosswalkEntry:
    source: str
    source_value: str
    scheme: str
    version: str
    mapping_kind: MappingKind
    codes: tuple[str, ...] = ()
    match_type: str = "exact"
    confidence: float | None = None
    notes: str = ""
    source_field: SourceField = SourceField.CATEGORY


@dataclass(frozen=True, slots=True)
class SourceEvidence:
    source: str
    category: str | None
    name: str | None
    dependency_group: str
    mapping: CrosswalkEntry | None = None


@dataclass(slots=True)
class FusionResult:
    scheme: str
    version: str
    status: FusionStatus
    code: str | None
    candidate_codes: list[str] = field(default_factory=list)
    evidence_sources: list[str] = field(default_factory=list)
    independent_groups: int = 0
    flags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TraversalResult:
    code: str | None
    path: list[str]
    agreement: float
    reason: str = ""
    decisions: list[dict[str, Any]] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ClassificationResult:
    canonical_id: str
    scheme: str
    version: str
    code: str | None
    level: str | None
    status: str
    deciding_component: str
    candidate_codes: list[str] = field(default_factory=list)
    evidence_sources: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    traversal_agreement: float | None = None
    model: str | None = None
    audit: dict[str, Any] = field(default_factory=dict)
    classification_depth: int | None = None
    max_depth: int | None = None
    branch_max_depth: int | None = None
