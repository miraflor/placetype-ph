"""Typed, auditable evidence models; independent of classification scheme."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Evidence:
    source: str
    field: str
    value: str
    kind: str
    code: str = ""
    rule: str = ""
    strength: str = "CANDIDATE"
    dependency_group: str = ""

    def as_record(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class Assessment:
    candidate_codes: tuple[str, ...] = ()
    status: str = "NO_EVIDENCE"
    reason: str = ""
    evidence: tuple[Evidence, ...] = ()
    flags: tuple[str, ...] = ()
    administrative_status: str = ""
    administrative_code: str = ""
    administrative_reason: str = ""
    ownership_candidate: str = ""
    ownership_status: str = ""
    ownership_reason: str = ""
    registry_status: str = ""
    registry_reference: str = ""
    registry_basis: str = ""
    audit: dict[str, Any] = field(default_factory=dict)
