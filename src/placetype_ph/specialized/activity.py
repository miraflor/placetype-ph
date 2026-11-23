"""Conservative typed-source activity proposals; never silently rewrite reviewed codes."""
from __future__ import annotations

import csv
import importlib.resources
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .model import Evidence
from .source_evidence import compound_status, from_row, normalized, text

BROAD_CODES = frozenset({"C", "F", "G", "H", "I", "L", "M", "N", "47", "46", "56", "85", "86", "94"})


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
        rule = ActivityRule(*(text(row[k]) for k in ("id", "source", "field", "kind", "expression", "code", "domain", "strength")))
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


def activity_proposals(row: dict[str, object], rules: Iterable[ActivityRule]) -> tuple[tuple[Evidence, ...], tuple[str, ...]]:
    base = from_row(row)
    hits: list[Evidence] = []
    flags: list[str] = []
    for source in ("osm", "overture", "fsq"):
        status = compound_status(source, row.get(f"{source}_category"))
        if status == "MULTIPLE_PRIMARY":
            flags.append(f"COMPOUND_CONFLICT_{source.upper()}")
        if status == "MULTIPLE_CATEGORIES":
            flags.append(f"MULTIPLE_CATEGORIES_{source.upper()}")
    for item in base:
        target = text(item.value).casefold() if item.field != "name" else normalized(item.value)
        for rule in rules:
            if rule.source not in {"*", item.source} or rule.field != item.field:
                continue
            expression = rule.expression.casefold() if item.field != "name" else rule.expression
            match = (target == expression if rule.kind == "exact" else
                     re.search(expression, target, flags=re.IGNORECASE) is not None)
            if match:
                hits.append(Evidence(item.source, item.field, item.value, "ACTIVITY_PROPOSAL",
                                     rule.code, rule.id, rule.strength, item.dependency_group))
    # Each evidence/rule is a single vote; duplicate upstream representations do not amplify it.
    return tuple(dict.fromkeys(hits)), tuple(sorted(set(flags)))


def reconcile_activity(hits: tuple[Evidence, ...], flags: tuple[str, ...], original_code: str = "",
                       original_method: str = "") -> tuple[tuple[str, ...], str, str]:
    precisions = {h.code for h in hits if h.strength == "PRECISE"}
    broader = {h.code for h in hits if h.strength == "BROAD"}
    if not hits:
        return (), ("REVIEW" if any(flag.startswith("COMPOUND_CONFLICT") for flag in flags) else "NO_EVIDENCE"), "no_matching_activity_rule"
    if any(flag.startswith("COMPOUND_CONFLICT") for flag in flags):
        return tuple(sorted(precisions or broader)), "REVIEW", "incompatible_compound_source_tags"
    if not precisions:
        return tuple(sorted(broader)), "BROAD_CANDIDATE", "broad_activity_evidence_only"
    # Two precise subtrees may be compatible (one is a valid ancestor of the other).
    ordered = sorted(precisions, key=lambda value: (-len(value), value))
    descendants = all(code == ordered[0] or (code.isdigit() and ordered[0].startswith(code))
                      for code in ordered)
    if not descendants:
        return tuple(sorted(precisions)), "REVIEW", "incompatible_precise_evidence"
    code = ordered[0]
    if original_code and original_code != code:
        if original_method.casefold() in {"reviewed_crosswalk", "reviewed", "manual", "user_supplied"}:
            return (code,), "REVIEW", "reviewed_classification_requires_review"
        if code.startswith(original_code) and original_code.isdigit():
            return (code,), "REFINEMENT_CANDIDATE", "candidate_is_descendant_of_original"
        return (code,), "REVIEW", "candidate_disagrees_with_original"
    return (code,), "SUPPORTING_CANDIDATE", "precise_matching_activity_evidence"
