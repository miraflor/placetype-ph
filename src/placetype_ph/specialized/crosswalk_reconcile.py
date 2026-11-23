"""Non-destructive reconciliation of candidate PSIC source rules and local crosswalks.

No authority is inferred from filenames or origins. This tool never edits source
crosswalks or promotes imported proposals to reviewed classification decisions.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

KINDS = frozenset({"EXACT", "SUBTREE", "UNION", "NOT_ACTIVITY", "UNCODEABLE"})
CODED = frozenset({"EXACT", "SUBTREE", "UNION"})
MATCH_TYPES = frozenset({"exact", "regex"})
REVIEW_STATES = frozenset({"REVIEWED", "APPROVED", "ACCEPTED", "UNREVIEWED", "PENDING", "REJECTED"})
RECONCILIATION_FIELDS = (
    "source",
    "source_field",
    "source_value",
    "match_type",
    "incoming_mapping_kind",
    "incoming_codes",
    "local_mapping_kind",
    "local_codes",
    "local_review_state",
    "comparison",
    "relationship",
    "validation",
    "manual_review_required",
    "specialized_rule_ids",
    "specialized_codes",
    "specialized_comparison",
    "incoming_line",
    "local_file",
    "local_lines",
    "incoming_notes",
)
WORKLIST_FIELDS = (
    "source",
    "source_field",
    "source_value",
    "match_type",
    "scheme",
    "version",
    "mapping_kind",
    "codes",
    "proposed_mapping_kind",
    "proposed_codes",
    "review_status",
    "reason",
    "existing_mapping_kind",
    "existing_codes",
    "existing_review_state",
    "taxonomy_validation",
    "specialized_comparison",
    "incoming_line",
    "local_file",
    "origin",
    "source_sha256",
)


@dataclass(frozen=True)
class Rule:
    source: str
    field: str
    value: str
    match_type: str
    kind: str
    codes: tuple[str, ...]
    review_state: str
    notes: str
    filename: str
    line: int
    scheme: str
    version: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        normalized = self.value.casefold() if self.match_type == "exact" else self.value
        normalized = (
            re.sub(r"\s+", " ", normalized.strip()) if self.match_type == "exact" else normalized
        )
        return (self.source, self.field, self.match_type, normalized)

    @property
    def semantic_key(self) -> tuple[str, tuple[str, ...]]:
        return self.kind, self.codes


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_codes(raw: str) -> tuple[str, ...]:
    value = raw.strip()
    if not value:
        return ()
    if value.startswith("["):
        parsed = json.loads(value)
        if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
            raise ValueError("codes JSON must be a list of strings")
        values = parsed
    else:
        if "," in value:
            raise ValueError("comma-delimited codes are ambiguous; use semicolons or JSON")
        values = re.split(r"[;|]", value)
    result = [code.strip() for code in values]
    if not result or any(not re.fullmatch(r"[A-Z0-9]+", code) for code in result):
        raise ValueError("codes must be nonempty uppercase alphanumeric identifiers")
    if len(result) != len(set(result)):
        raise ValueError("duplicate code in one mapping")
    return tuple(sorted(result))


def _version(value: str) -> str:
    normalized = re.sub(r"[\s_.-]+", "", value).casefold()
    if normalized in {"rev5", "revision5", "5"}:
        return "rev5"
    return normalized


def _read_csv(path: Path) -> tuple[list[dict[str, str]], set[str]]:
    if not path.is_file():
        raise FileNotFoundError(f"required CSV does not exist: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        fields = reader.fieldnames or []
        if not fields or len(fields) != len(set(fields)) or any(not field for field in fields):
            raise ValueError(f"invalid or duplicate CSV headers: {path}")
        rows = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"ragged CSV at line {reader.line_num}: {path}")
            if any("\x00" in value for value in row.values()):
                raise ValueError(f"NUL character in CSV line {reader.line_num}: {path}")
            row["__line"] = str(reader.line_num)
            rows.append(row)
    return rows, set(fields)


def load_rules(path: Path, *, incoming: bool) -> tuple[Rule, ...]:
    rows, fields = _read_csv(path)
    required = {"source", "source_value", "mapping_kind", "codes"}
    if not required <= fields:
        raise ValueError(f"missing required columns in {path}: {sorted(required - fields)}")
    result = []
    for row in rows:
        line = int(row["__line"])
        # Joint crosswalks may contain other taxonomies with different kinds.
        # Filter them before interpreting PSIC-specific source mappings.
        scheme = row["scheme"].strip().casefold() if "scheme" in fields else "psic"
        version = _version(row["version"]) if "version" in fields else "rev5"
        if scheme != "psic" or version != "rev5":
            if incoming:
                raise ValueError(f"incoming mapping is not PSIC Rev. 5: {path}:{line}")
            continue
        source = row["source"].strip().casefold()
        field = (row.get("source_field") or "category").strip().casefold()
        value = row["source_value"].strip()
        match_type = (row.get("match_type") or "exact").strip().casefold()
        kind = row["mapping_kind"].strip().upper()
        if not source or not field or not value:
            raise ValueError(f"missing source/field/value in {path} at line {line}")
        if field not in {"category", "name"}:
            raise ValueError(f"unsupported source_field={field!r} in {path} at line {line}")
        if match_type not in MATCH_TYPES:
            raise ValueError(
                f"unsupported match_type={match_type!r} in {path} at line {line}; "
                f"supported types: {sorted(MATCH_TYPES)}"
            )
        # The local master crosswalk includes rows not yet adjudicated. Preserve
        # them as placeholders, not authoritative or absent mappings. Incoming
        # catalogue records must always have an explicit valid mapping kind.
        local_placeholder = not incoming and not kind and not row["codes"].strip()
        if kind not in KINDS and not local_placeholder:
            raise ValueError(
                f"unsupported mapping_kind={kind!r} with codes={row['codes']!r} "
                f"in {path} at line {line}; expected an approved kind or "
                "a completely blank local mapping"
            )
        if match_type == "regex":
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"invalid regex in {path} at line {line}: {exc}") from exc
        try:
            codes = parse_codes(row["codes"])
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid mapping codes in {path} at line {line}: {exc}") from exc
        if not local_placeholder and (kind in CODED) != bool(codes):
            raise ValueError(f"mapping-kind/code mismatch in {path} at line {line}")
        state = (row.get("review_status") or row.get("reviewed_status") or "").strip().upper()
        if local_placeholder:
            if state in {"REVIEWED", "APPROVED", "ACCEPTED"}:
                raise ValueError(
                    f"local mapping at {path}:{line} is marked {state!r} "
                    "but has no mapping_kind or codes"
                )
            state = "UNREVIEWED"
        elif state not in REVIEW_STATES:
            state = "UNKNOWN"
        if incoming:
            # External origins are never independently authoritative in this repo.
            state = "UNREVIEWED"
        result.append(
            Rule(
                source,
                field,
                value,
                match_type,
                kind,
                codes,
                state,
                (row.get("notes") or "").strip(),
                str(path),
                line,
                scheme,
                version,
            )
        )
    if incoming and not result:
        raise ValueError("incoming catalogue has no PSIC Rev. 5 mappings")
    return tuple(result)


@dataclass(frozen=True)
class Taxonomy:
    parents: dict[str, str]

    @classmethod
    def load(cls, path: Path) -> Taxonomy:
        rows, fields = _read_csv(path)
        if not {"code", "parent_code"} <= fields:
            raise ValueError("taxonomy CSV requires code,parent_code")
        parents: dict[str, str] = {}
        for row in rows:
            code = row["code"].strip()
            parent = row["parent_code"].strip()
            if not re.fullmatch(r"[A-Z0-9]+", code) or (
                parent and not re.fullmatch(r"[A-Z0-9]+", parent)
            ):
                raise ValueError(f"invalid taxonomy identifier on line {row['__line']}")
            if code in parents:
                raise ValueError(f"duplicate taxonomy node: {code}")
            parents[code] = parent
        if not parents:
            raise ValueError("empty taxonomy")
        for code, parent in parents.items():
            if parent and parent not in parents:
                raise ValueError(f"missing taxonomy parent {parent!r} for {code}")
            seen = {code}
            cursor = parent
            while cursor:
                if cursor in seen:
                    raise ValueError(f"taxonomy cycle involving {code}")
                seen.add(cursor)
                cursor = parents[cursor]
        return cls(parents)

    def ancestor_of(self, ancestor: str, descendant: str) -> bool:
        parent = self.parents[descendant]
        while parent:
            if parent == ancestor:
                return True
            parent = self.parents[parent]
        return False


def relationship(incoming: Rule, local: Rule, taxonomy: Taxonomy | None) -> str:
    if incoming.semantic_key == local.semantic_key:
        return "IDENTICAL"
    if incoming.kind != local.kind:
        return "DIFFERENT_MAPPING_KIND"
    if not incoming.codes or not local.codes:
        return "DIFFERENT_TERMINAL_MAPPING"
    if taxonomy is None or any(c not in taxonomy.parents for c in (*incoming.codes, *local.codes)):
        return "CODE_SETS_DIFFER_UNVERIFIED"

    def confined(children: tuple[str, ...], parents: tuple[str, ...]) -> bool:
        return all(
            any(child == parent or taxonomy.ancestor_of(parent, child) for parent in parents)
            for child in children
        )

    incoming_within_local = confined(incoming.codes, local.codes)
    local_within_incoming = confined(local.codes, incoming.codes)
    if incoming_within_local and not local_within_incoming:
        return "INCOMING_NARROWER"
    if local_within_incoming and not incoming_within_local:
        return "INCOMING_BROADER"
    return "INCOMPATIBLE_OR_OVERLAPPING_SETS"


def _source_groups(
    existing: tuple[Rule, ...],
) -> tuple[
    dict[tuple[str, str, str, str], list[Rule]],
    dict[tuple[str, str, str], list[Rule]],
]:
    exact: dict[tuple[str, str, str, str], list[Rule]] = defaultdict(list)
    values: dict[tuple[str, str, str], list[Rule]] = defaultdict(list)
    for rule in existing:
        exact[rule.key].append(rule)
        values[(rule.source, rule.field, rule.key[3])].append(rule)
    return exact, values


def _specialized_index() -> dict[tuple[str, str, str], list[tuple[str, str]]]:
    """Read packaged activity proposals as separate, non-authoritative evidence."""
    from .activity import load_rules

    index: dict[tuple[str, str, str], list[tuple[str, str]]] = defaultdict(list)
    for entry in load_rules():
        if entry.kind == "exact":
            key = (entry.source, entry.field, entry.expression.strip().casefold())
            index[key].append((entry.id, entry.code))
    return index


def _specialized_match(
    rule: Rule,
    index: dict[tuple[str, str, str], list[tuple[str, str]]],
    taxonomy: Taxonomy | None = None,
) -> tuple[str, str, str]:
    """Compare candidate roots against advisory rule roots, not their label strings.

    Codes can be equal without mapping scopes being equivalent: specialized rules
    have evidence strength, but do not specify EXACT/SUBTREE/UNION semantics.
    This comparison is diagnostic and can never authorize a crosswalk mapping.
    """
    if rule.match_type != "exact":
        return "", "", "SPECIALIZED_NOT_COMPARABLE_REGEX"
    key = (rule.source, rule.field, rule.value.casefold())
    universal = ("*", rule.field, rule.value.casefold())
    # Never mutate the cached index; both source-specific and wildcard rules count.
    records = [*index.get(key, ()), *index.get(universal, ())]
    if not records:
        return "", "", "SPECIALIZED_ABSENT"
    ids = ";".join(sorted({item[0] for item in records}))
    codes = tuple(sorted({item[1] for item in records}))
    text_codes = ";".join(codes)
    if rule.kind in {"NOT_ACTIVITY", "UNCODEABLE"}:
        return ids, text_codes, "SPECIALIZED_TERMINAL_CODE_CONFLICT"
    if codes == rule.codes:
        # Same code identifiers, *not* equivalent mapping/evidence semantics.
        return ids, text_codes, "SPECIALIZED_SAME_CODE"
    if taxonomy is None:
        return ids, text_codes, "SPECIALIZED_HIERARCHY_UNVERIFIED"
    if any(value not in taxonomy.parents for value in (*codes, *rule.codes)):
        return ids, text_codes, "SPECIALIZED_CODE_NOT_IN_TAXONOMY"

    def within(children: tuple[str, ...], roots: tuple[str, ...]) -> bool:
        return all(
            any(child == root or taxonomy.ancestor_of(root, child) for root in roots)
            for child in children
        )

    incoming_within = within(rule.codes, codes)
    specialized_within = within(codes, rule.codes)
    if incoming_within and specialized_within:
        status = "SPECIALIZED_OVERLAPPING_ROOTS"
    elif incoming_within:
        status = "SPECIALIZED_INCOMING_WITHIN_ROOT"
    elif specialized_within:
        status = "SPECIALIZED_ROOT_WITHIN_INCOMING"
    elif any(
        incoming == specialist
        or taxonomy.ancestor_of(incoming, specialist)
        or taxonomy.ancestor_of(specialist, incoming)
        for incoming in rule.codes
        for specialist in codes
    ):
        status = "SPECIALIZED_PARTIAL_HIERARCHY_OVERLAP"
    else:
        status = "SPECIALIZED_DIFFERENT_BRANCHES"
    return ids, text_codes, status


def _validate_incoming(rule: Rule, taxonomy: Taxonomy | None) -> str:
    if not rule.codes:
        return "NOT_APPLICABLE_TERMINAL"
    if taxonomy is None:
        return "TAXONOMY_NOT_SUPPLIED"
    missing = [code for code in rule.codes if code not in taxonomy.parents]
    return "INVALID_CODES:" + ";".join(missing) if missing else "VALID_PSIC_NODES"


def reconcile(
    incoming: tuple[Rule, ...],
    existing: tuple[Rule, ...],
    taxonomy: Taxonomy | None = None,
    *,
    source_sha256: str = "",
    specialized_index: dict[tuple[str, str, str], list[tuple[str, str]]] | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, Any]]:
    exact, alternate = _source_groups(existing)
    if len({r.key for r in incoming}) != len(incoming):
        raise ValueError(
            "duplicate incoming source/field/match-type/value keys; no output produced"
        )
    reports, worklist = [], []
    for proposal in incoming:
        matches = exact.get(proposal.key, [])
        validation = _validate_incoming(proposal, taxonomy)
        local = matches[0] if len(matches) == 1 else None
        if len(matches) > 1:
            comparison, relation = "MULTIPLE_LOCAL_MATCHES", "NOT_RESOLVED"
        elif local is not None:
            if local.kind == "" and not local.codes:
                comparison, relation = "LOCAL_UNREVIEWED_MAPPING", "LOCAL_UNMAPPED_PLACEHOLDER"
            else:
                relation = relationship(proposal, local, taxonomy)
                if relation == "IDENTICAL":
                    comparison = {
                        "REVIEWED": "MATCH_REVIEWED",
                        "APPROVED": "MATCH_REVIEWED",
                        "ACCEPTED": "MATCH_ACCEPTED",
                    }.get(local.review_state, "MATCH_LOCAL_UNVERIFIED")
                else:
                    comparison = (
                        "CONFLICT_WITH_REVIEWED"
                        if local.review_state in {"REVIEWED", "APPROVED"}
                        else "CONFLICT_WITH_ACCEPTED"
                        if local.review_state == "ACCEPTED"
                        else "CONFLICT_WITH_LOCAL_UNVERIFIED"
                    )
        else:
            semantics = alternate.get((proposal.source, proposal.field, proposal.key[3]), [])
            comparison = "MATCH_TYPE_DISAGREEMENT" if semantics else "ABSENT_LOCAL_MAPPING"
            relation = "DIFFERENT_MATCH_TYPE" if semantics else "NOT_PRESENT"
        manual = comparison != "MATCH_REVIEWED" or validation not in {
            "VALID_PSIC_NODES",
            "NOT_APPLICABLE_TERMINAL",
        }
        specialized_ids, specialized_codes, specialized_comparison = _specialized_match(
            proposal, specialized_index or {}, taxonomy
        )
        source_local = local or (matches[0] if matches else None)
        report = {
            "source": proposal.source,
            "source_field": proposal.field,
            "source_value": proposal.value,
            "match_type": proposal.match_type,
            "incoming_mapping_kind": proposal.kind,
            "incoming_codes": ";".join(proposal.codes),
            "local_mapping_kind": local.kind if local else "",
            "local_codes": ";".join(local.codes) if local else "",
            "local_review_state": local.review_state if local else "",
            "comparison": comparison,
            "relationship": relation,
            "validation": validation,
            "manual_review_required": "YES" if manual else "NO",
            "specialized_rule_ids": specialized_ids,
            "specialized_codes": specialized_codes,
            "specialized_comparison": specialized_comparison,
            "incoming_line": str(proposal.line),
            "local_file": source_local.filename if source_local else "",
            "local_lines": ";".join(str(m.line) for m in matches),
            "incoming_notes": proposal.notes,
        }
        reports.append(report)
        if manual:
            worklist.append(
                {
                    "source": proposal.source,
                    "source_field": proposal.field,
                    "source_value": proposal.value,
                    "match_type": proposal.match_type,
                    "scheme": "psic",
                    "version": "rev5",
                    "mapping_kind": "",
                    "codes": "",
                    "proposed_mapping_kind": proposal.kind,
                    "proposed_codes": ";".join(proposal.codes),
                    "review_status": "REVIEW_REQUIRED",
                    "reason": comparison,
                    "existing_mapping_kind": report["local_mapping_kind"],
                    "existing_codes": report["local_codes"],
                    "existing_review_state": report["local_review_state"],
                    "taxonomy_validation": validation,
                    "specialized_comparison": specialized_comparison,
                    "incoming_line": str(proposal.line),
                    "local_file": report["local_file"],
                    "origin": "external_catalog_candidate_only",
                    "source_sha256": source_sha256,
                }
            )
    stats = {
        "policy": "crosswalk-reconcile-v2-taxonomy-aware-advisory",
        "incoming_rows": len(incoming),
        "local_psic_rev5_rows": len(existing),
        "review_required_rows": len(worklist),
        "comparison_counts": dict(sorted(Counter(r["comparison"] for r in reports).items())),
        "validation_counts": dict(sorted(Counter(r["validation"] for r in reports).items())),
        "specialized_comparison_counts": dict(
            sorted(Counter(r["specialized_comparison"] for r in reports).items())
        ),
        "taxonomy_supplied": taxonomy is not None,
        "source_sha256": source_sha256,
        "automatic_promotion": False,
    }
    return reports, worklist, stats


def _write_table(path: Path, columns: tuple[str, ...], records: list[dict[str, str]]) -> None:
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=columns, extrasaction="raise", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(records)


def publish(
    out_dir: Path,
    reports: list[dict[str, str]],
    worklist: list[dict[str, str]],
    stats: dict[str, Any],
) -> None:
    """Publish three read-only reports together using a non-overwriting directory rename."""
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise FileExistsError(f"report destination already exists: {out_dir}")
    if not out_dir.parent.is_dir():
        raise FileNotFoundError(f"report destination parent does not exist: {out_dir.parent}")
    pending = out_dir.with_name(out_dir.name + ".pending-" + uuid.uuid4().hex)
    pending.mkdir(exist_ok=False)
    committed = False
    try:
        _write_table(pending / "reconciliation.csv", RECONCILIATION_FIELDS, reports)
        _write_table(pending / "review_worklist.csv", WORKLIST_FIELDS, worklist)
        (pending / "summary.json").write_text(
            json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        if json.loads((pending / "summary.json").read_text(encoding="utf-8")) != stats:
            raise RuntimeError("report summary round-trip failed")
        for name, expected in (
            ("reconciliation.csv", len(reports)),
            ("review_worklist.csv", len(worklist)),
        ):
            rows, _ = _read_csv(pending / name)
            if len(rows) != expected:
                raise RuntimeError(f"report CSV round-trip failed: {name}")
        if out_dir.exists():
            raise FileExistsError(f"report destination appeared before publication: {out_dir}")
        os.rename(pending, out_dir)
        committed = True
    finally:
        if not committed and pending.exists():
            for path in pending.iterdir():
                if path.is_file():
                    path.unlink()
            pending.rmdir()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only PSIC Rev. 5 crosswalk reconciliation")
    parser.add_argument(
        "--incoming", type=Path, required=True, help="candidate source catalogue CSV"
    )
    parser.add_argument(
        "--existing", type=Path, required=True, nargs="+", help="local crosswalk CSV file(s)"
    )
    parser.add_argument(
        "--taxonomy-nodes", type=Path, help="verified PSIC Rev. 5 code,parent_code CSV"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="new directory, outside repository recommended",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="validate and summarize without writing outputs"
    )
    args = parser.parse_args(argv)
    incoming = args.incoming.resolve()
    existing_paths = [p.resolve() for p in args.existing]
    taxonomy_path = args.taxonomy_nodes.resolve() if args.taxonomy_nodes else None
    if len(set(existing_paths)) != len(existing_paths):
        raise ValueError("duplicate existing input files")
    if incoming in existing_paths or (
        taxonomy_path and taxonomy_path in {*existing_paths, incoming}
    ):
        raise ValueError("candidate, local, and taxonomy inputs must be different files")
    if not args.dry_run:
        destination = args.output_dir.resolve()
        for source_path in [incoming, *existing_paths, *([taxonomy_path] if taxonomy_path else [])]:
            if destination == source_path or destination in source_path.parents:
                raise ValueError("report destination cannot contain an input source")
        if destination.exists():
            raise FileExistsError("report directory must not exist")
    candidate_rules = load_rules(incoming, incoming=True)
    local_rules = tuple(
        rule for path in existing_paths for rule in load_rules(path, incoming=False)
    )
    taxonomy = Taxonomy.load(taxonomy_path) if taxonomy_path else None
    digest = sha(incoming)
    reports, worklist, stats = reconcile(
        candidate_rules,
        local_rules,
        taxonomy,
        source_sha256=digest,
        specialized_index=_specialized_index(),
    )
    stats["local_source_sha256"] = {str(path): sha(path) for path in existing_paths}
    stats["taxonomy_sha256"] = sha(taxonomy_path) if taxonomy_path else None
    if args.dry_run:
        print(json.dumps(stats, indent=2, sort_keys=True))
    else:
        publish(args.output_dir, reports, worklist, stats)
        print(
            f"PASS: {len(reports)} rows audited; "
            f"{len(worklist)} require review; results: {args.output_dir}"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
