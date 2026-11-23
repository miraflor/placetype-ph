"""Conservative typed-source activity proposals; never silently rewrite reviewed codes."""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .fusion import AncestorLookup, fuse_activity
from .model import Evidence
from .source_evidence import compound_status, from_row, normalized, text

BROAD_CODES = frozenset(
    {"C", "F", "G", "H", "I", "L", "M", "N", "47", "46", "56", "85", "86", "94"}
)


@dataclass(frozen=True)
class ActivityRule:
    id: str
    source: str
    field: str
    kind: str
    expression: str
    code: str
    domain: str
    strength: str


def load_rules(path: Path | None = None) -> tuple[ActivityRule, ...]:
    if path is None:
        from importlib import resources

        root = resources.files("placetype_ph.specialized")
        contents = root.joinpath("activity_rules.csv").read_text(encoding="utf-8")
    else:
        contents = Path(path).read_text(encoding="utf-8")
    import io

    reader = csv.DictReader(io.StringIO(contents))
    required = {"id", "source", "field", "kind", "expression", "code", "domain", "strength"}
    if not required <= set(reader.fieldnames or []):
        raise ValueError("activity rules have an invalid schema")
    rules: list[ActivityRule] = []
    ids: set[str] = set()
    for row in reader:
        rule = ActivityRule(
            *(
                text(row[k])
                for k in (
                    "id",
                    "source",
                    "field",
                    "kind",
                    "expression",
                    "code",
                    "domain",
                    "strength",
                )
            )
        )
        if not all((rule.id, rule.source, rule.field, rule.expression, rule.code)):
            raise ValueError("activity rule contains empty required fields")
        if rule.id in ids:
            raise ValueError(f"duplicate activity rule id {rule.id}")
        if rule.kind not in {"exact", "regex"} or rule.strength not in {"PRECISE", "BROAD"}:
            raise ValueError(f"invalid matching policy: {rule.id}")
        if rule.kind == "regex":
            re.compile(rule.expression, re.IGNORECASE)
        ids.add(rule.id)
        rules.append(rule)
    return tuple(rules)


def activity_proposals(
    row: dict[str, object], rules: Iterable[ActivityRule]
) -> tuple[tuple[Evidence, ...], tuple[str, ...]]:
    """Generate source-aware activity proposals; preserve Overture role provenance."""
    base = from_row(row)
    hits: list[Evidence] = []
    flags: set[str] = set()
    for source in ("osm", "overture", "fsq"):
        status = compound_status(source, row.get(f"{source}_category"))
        if status == "MULTIPLE_PRIMARY":
            flags.add(f"COMPOUND_CONFLICT_{source.upper()}")
        elif status == "MULTIPLE_CATEGORIES":
            flags.add(f"MULTIPLE_CATEGORIES_{source.upper()}")
    # A structured primary/alternate category is still category evidence, not
    # an independent observation; duplicates from the combined field count once.
    seen: set[tuple[str, str, str]] = set()
    precedence = {
        "overture_primary_category": 0,
        "category": 1,
        "overture_alternate_category": 2,
        "overture_basic_category": 3,
        "name": 4,
    }
    for item in sorted(base, key=lambda entry: precedence.get(entry.field, 5)):
        category_field = item.field in {
            "category",
            "overture_primary_category",
            "overture_alternate_category",
            "overture_basic_category",
        }
        rule_field = "category" if category_field else item.field
        target = normalized(item.value) if item.field == "name" else text(item.value).casefold()
        for rule in rules:
            if rule.source not in {"*", item.source} or rule.field != rule_field:
                continue
            expression = rule.expression if item.field == "name" else rule.expression.casefold()
            matched = (
                target == expression
                if rule.kind == "exact"
                else re.search(expression, target, flags=re.IGNORECASE) is not None
            )
            if not matched:
                continue
            identity = (item.source, target, rule.id)
            if identity in seen:
                continue
            seen.add(identity)
            # Basic Overture categories may be broad coarse classifications;
            # never allow them to become precise activity evidence by accident.
            strength = "BROAD" if item.kind == "BROAD_CATEGORY" else rule.strength
            hits.append(
                Evidence(
                    item.source,
                    item.field,
                    item.value,
                    "ACTIVITY_PROPOSAL",
                    rule.code,
                    rule.id,
                    strength,
                    item.dependency_group,
                )
            )
    return tuple(hits), tuple(sorted(flags))


def reconcile_activity(
    hits: tuple[Evidence, ...],
    flags: tuple[str, ...],
    original_code: str = "",
    original_method: str = "",
    *,
    ancestors: AncestorLookup | None = None,
) -> tuple[tuple[str, ...], str, str]:
    """Backward-compatible public reconciliation interface (without mutation)."""
    outcome = fuse_activity(
        hits, original_code=original_code, original_method=original_method, ancestors=ancestors
    )
    if any(flag.startswith("COMPOUND_CONFLICT") for flag in flags):
        return outcome.candidate_codes, "REVIEW", "incompatible_compound_source_tags"
    return outcome.candidate_codes, outcome.status, outcome.reason
