"""Adopt external source rules as *unreviewed proposals*, not reviewed mappings."""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path

ALLOWED_KINDS = {"EXACT", "SUBTREE", "UNION", "NOT_ACTIVITY", "UNCODEABLE"}


def migrate_proposals(source: Path, target: Path, *, existing: Path | None = None) -> int:
    """Fail closed on duplicates and conflicting existing reviewed rows."""
    source, target = Path(source), Path(target)
    if source.resolve() == target.resolve() or target.exists():
        raise ValueError("migration output must be new and different from the input")
    if existing is not None and Path(existing).resolve() == target.resolve():
        raise ValueError("existing reviewed crosswalk must never be the output path")
    previous: dict[tuple[str, str, str], tuple[str, str]] = {}
    if existing:
        with Path(existing).open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                if str(row.get("mapping_kind") or "").strip():
                    key = (str(row.get("source") or "").strip().casefold(),
                           str(row.get("source_field") or "category").strip().casefold(),
                           str(row.get("source_value") or "").strip())
                    found = (str(row.get("mapping_kind") or "").strip(), str(row.get("codes") or "").strip())
                    if key in previous and previous[key] != found:
                        raise ValueError(f"existing crosswalk has conflicting reviewed rows for {key}")
                    previous[key] = found
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    with source.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"source", "source_value", "mapping_kind", "codes"}
        if not required <= set(reader.fieldnames or ()):
            raise ValueError("candidate source missing required crosswalk fields")
        for line, rec in enumerate(reader, start=2):
            src = str(rec.get("source") or "").strip().casefold()
            value = str(rec.get("source_value") or "").strip()
            field = str(rec.get("source_field") or "category").strip().casefold()
            kind = str(rec.get("mapping_kind") or "").strip().upper()
            codes = str(rec.get("codes") or "").strip()
            key = (src, field, value)
            if not src or not value or kind not in ALLOWED_KINDS:
                raise ValueError(f"invalid candidate source mapping at line {line}")
            if (kind in {"EXACT", "SUBTREE", "UNION"}) != bool(codes):
                raise ValueError(f"invalid code/mapping-kind pairing at line {line}")
            if key in seen:
                raise ValueError(f"duplicate source entry at line {line}: {key}")
            seen.add(key)
            prior = previous.get(key)
            disposition = "ALREADY_REVIEWED" if prior == (kind, codes) else (
                "CONFLICT_WITH_REVIEWED" if prior else "REVIEW_REQUIRED")
            rows.append({
                "source": src, "source_field": field, "source_value": value,
                "scheme": "psic", "version": "rev5", "proposed_mapping_kind": kind,
                "proposed_codes": codes, "review_status": disposition,
                "existing_mapping_kind": prior[0] if prior else "",
                "existing_codes": prior[1] if prior else "",
                "evidence_origin": "imported_external_catalog_unreviewed",
                "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            })
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".pending")
    try:
        with tmp.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else [
                "source", "source_field", "source_value", "scheme", "version",
                "proposed_mapping_kind", "proposed_codes", "review_status",
                "existing_mapping_kind", "existing_codes", "evidence_origin", "input_sha256"])
            writer.writeheader()
            writer.writerows(rows)
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)
    return len(rows)


def export_rule_worklist(target: Path) -> int:
    """Package category rules in the joint-worklist schema; none is auto-reviewed."""
    from .activity import load_rules
    target = Path(target)
    if target.exists():
        raise ValueError("rule worklist output already exists")
    fields = ("source", "source_value", "source_field", "scheme", "version",
              "mapping_kind", "codes", "suggested_mapping_kind", "suggested_codes",
              "review_status", "rule_id", "notes")
    records = []
    for r in load_rules():
        if r.kind != "exact" or r.field != "category":
            continue
        records.append({"source": r.source, "source_value": r.expression,
                        "source_field": r.field, "scheme": "psic", "version": "rev5",
                        "mapping_kind": "", "codes": "", "suggested_mapping_kind": "SUBTREE",
                        "suggested_codes": r.code, "review_status": "REVIEW_REQUIRED",
                        "rule_id": r.id,
                        "notes": "Independent candidate; requires taxonomy validation and human review"})
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = target.with_name(target.name + ".pending")
    try:
        with pending.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(records)
        pending.replace(target)
    finally:
        pending.unlink(missing_ok=True)
    return len(records)
