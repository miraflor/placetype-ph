"""Opt-in verified institutional ownership resolution after independent taxonomy tagging.

This is a native PlaceType PH post-classification stage, not a new PSIC classifier.
It never changes or infers PSIC, PCPC, PSCC, or IO fields. Provisional matches,
name-only candidates, category claims, and conflicting assertions cannot be promoted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from .institutional_registry import (
    InstitutionIndex,
    clean,
    load_beis,
    load_hospitals,
    load_unifast,
)
from .institutional_shadow import InstitutionResolver, load_verified_links

POLICY = "institution-verified-ownership-promotion-v2"
NEW_FIELDS = (
    "institution_resolution_status",
    "institution_resolved_ownership",
    "institution_resolution_basis",
    "institution_resolution_reference",
    "institution_resolution_reason",
    "institution_candidate_ownership",
    "institution_evidence_status",
)
PROMOTABLE = frozenset({"VERIFIED_LINK", "EXACT_EXTERNAL_ID"})
INELIGIBLE_ACTIVITY = frozenset(
    {
        "REVIEW",
        "UNRESOLVED",
        "INVALID",
        "AMBIGUOUS",
        "ERROR",
        "CONFLICT",
        "CANDIDATES_ONLY",
        "NOT_PSIC_ACTIVITY",
        "NOT_ACTIVITY",
        "UNCODEABLE",
        "REVIEW_ENTITY_MATCH",
    }
)


def _identities(frame: pd.DataFrame, name: str) -> list[str]:
    if "canonical_id" not in frame.columns:
        raise ValueError(f"{name}: missing canonical_id")
    if frame["canonical_id"].isna().any():
        raise ValueError(f"{name}: null canonical_id")
    ids = [str(x).strip() for x in frame["canonical_id"]]
    if not all(ids) or len(set(ids)) != len(ids):
        raise ValueError(f"{name}: blank or duplicate canonical_id")
    return ids


def resolve_institutional_ownership(
    classified: pd.DataFrame,
    places: pd.DataFrame,
    *,
    resolver: InstitutionResolver,
) -> pd.DataFrame:
    """Append verified institutional ownership results, preserving all original columns.

    `classified` is authoritative for PSIC and existing tagging; `places` supplies
    original names/tags and external identity assertions. Exact join is required.
    An existing ownership hint never becomes a verified resolution by copying it.
    """
    left_ids = _identities(classified, "classified")
    right_ids = _identities(places, "places")
    if set(left_ids) != set(right_ids):
        raise ValueError("classified and places must have identical canonical_id sets")
    if "psic_code" not in classified.columns or "psic_status" not in classified.columns:
        raise ValueError("authoritative classified input requires psic_code and psic_status")
    if any(f in classified.columns for f in NEW_FIELDS):
        raise ValueError("institutional resolution columns already present; refusing overwrite")
    if not classified.index.is_unique:
        raise ValueError("classified row index must be unique")
    by_id = dict(zip(right_ids, places.to_dict("records"), strict=True))
    decisions: list[dict[str, str]] = []
    for cid, record in zip(left_ids, classified.to_dict("records"), strict=True):
        full: dict[str, Any] = dict(by_id[cid])
        if not clean(full.get("name")) and clean(record.get("name")):
            full["name"] = record["name"]
        full["canonical_id"] = cid
        # The independent classified result, not an older raw POI column, is authoritative.
        full["psic_code"] = record["psic_code"]
        full["psic_status"] = record["psic_status"]
        if (
            not clean(record["psic_status"])
            or clean(record["psic_status"]).upper() in INELIGIBLE_ACTIVITY
        ):
            status, reason, owner, basis, refs = (
                "ABSTAIN",
                "authoritative_psic_unresolved",
                "",
                "",
                "",
            )
            candidate, evidence = "", "ABSTAIN"
        else:
            assessment = resolver.assess(full)
            candidate = assessment.ownership_candidate
            evidence = assessment.status
            owner, basis, refs = "", assessment.basis, "|".join(assessment.source_references)
            status, reason = assessment.status, assessment.reason
            if assessment.status in PROMOTABLE and assessment.promotion_eligible:
                if assessment.domain not in {"education", "health"} or candidate not in {
                    "public",
                    "private",
                }:
                    status, reason = "REVIEW", "invalid_verified_ownership_assertion"
                else:
                    # Explicit or untyped prior ownership claims still require review.
                    # A known provisional hint is weaker than an independently verified link.
                    baseline_fields = ["institution_ownership", "ownership_side"]
                    provisional_hint = clean(record.get("ownership_status")) == "PROVISIONAL"
                    if not provisional_hint:
                        baseline_fields.append("ownership_candidate")
                    baseline = {
                        clean(record.get(field)).casefold()
                        for field in baseline_fields
                        if clean(record.get(field)).casefold() in {"public", "private"}
                    }
                    if baseline and baseline != {candidate}:
                        status, reason = "REVIEW", "existing_ownership_evidence_conflict"
                    else:
                        status, reason = "RESOLVED", assessment.reason
                        owner = candidate
                        hint = clean(record.get("ownership_candidate")).casefold()
                        if provisional_hint and hint in {"public", "private"} and hint != candidate:
                            reason = "verified_ownership_overrides_provisional_hint"
            elif assessment.status in PROMOTABLE:
                status, reason = "REVIEW", "strong_status_without_promotion_eligibility"
        decisions.append(
            {
                "institution_resolution_status": status,
                "institution_resolved_ownership": owner,
                "institution_resolution_basis": basis,
                "institution_resolution_reference": refs,
                "institution_resolution_reason": reason,
                "institution_candidate_ownership": candidate,
                "institution_evidence_status": evidence,
            }
        )
    # A registered site reference cannot be promoted onto multiple distinct POIs.
    # This is a cross-entity safeguard; individual lookup checks are insufficient.
    use_counts = Counter(
        reference
        for row in decisions
        if row["institution_resolution_status"] == "RESOLVED"
        for reference in row["institution_resolution_reference"].split("|")
        if reference
    )
    for row in decisions:
        if row["institution_resolution_status"] == "RESOLVED" and any(
            use_counts[ref] > 1 for ref in row["institution_resolution_reference"].split("|") if ref
        ):
            row["institution_resolution_status"] = "REVIEW"
            row["institution_resolved_ownership"] = ""
            row["institution_resolution_reason"] = "same_registry_site_matches_multiple_entities"
    out = classified.copy(deep=True)
    for field in NEW_FIELDS:
        out[field] = [row[field] for row in decisions]
    if len(out) != len(classified):
        raise RuntimeError("institutional resolution changed row count")
    if out["canonical_id"].tolist() != classified["canonical_id"].tolist():
        raise RuntimeError("institutional resolution reordered canonical_id")
    for field in classified.columns:
        if not out[field].equals(classified[field]):
            raise RuntimeError(f"institutional resolution changed existing column {field}")
    return out


def _read_places(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    raise ValueError("inputs must be CSV or Parquet")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_outside_git(target: Path) -> None:
    # Walk from the output destination, not just the current directory.
    for ancestor in (target, *target.parents):
        if (ancestor / ".git").exists():
            raise ValueError("generated ownership outputs must be outside Git repositories")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve verified institution ownership, preserving original tags"
    )
    parser.add_argument("--places", type=Path, required=True)
    parser.add_argument("--classified", type=Path, required=True)
    parser.add_argument("--beis", type=Path)
    parser.add_argument("--unifast", type=Path)
    parser.add_argument("--hospitals", type=Path)
    parser.add_argument("--verified-links", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    refs = tuple(p for p in (args.beis, args.unifast, args.hospitals, args.verified_links) if p)
    if not refs:
        parser.error("supply at least one registry or verified-links reference")
    if args.classified.resolve() == args.places.resolve():
        raise ValueError("classified and places inputs must be distinct files")
    for p in (*refs, args.classified, args.places):
        if not p.is_file():
            raise FileNotFoundError(f"required input file does not exist: {p}")
    records = (
        (load_beis(args.beis) if args.beis else ())
        + (load_unifast(args.unifast) if args.unifast else ())
        + (load_hospitals(args.hospitals) if args.hospitals else ())
    )
    resolver = InstitutionResolver(
        InstitutionIndex(records), load_verified_links(args.verified_links)
    )
    classified = _read_places(args.classified)
    places = _read_places(args.places)
    promoted = resolve_institutional_ownership(classified, places, resolver=resolver)
    stats = {
        "policy": POLICY,
        "rows": len(promoted),
        "input_sha256": {
            str(p.resolve()): _sha256(p) for p in (*refs, args.classified, args.places)
        },
        "status_counts": dict(sorted(Counter(promoted["institution_resolution_status"]).items())),
        "resolved_verified": int((promoted["institution_resolution_status"] == "RESOLVED").sum()),
        "original_classification_columns_changed": 0,
        "original_classification_rows_changed": 0,
        "name_only_matches_promoted": 0,
    }
    if args.dry_run:
        print(json.dumps(stats, indent=2, sort_keys=True))
        return 0
    target = args.output_dir.resolve()
    if target.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {target}")
    if not target.parent.is_dir():
        raise FileNotFoundError(f"output parent directory does not exist: {target.parent}")
    _assert_outside_git(target)
    staging = Path(tempfile.mkdtemp(prefix=".institution-promotion-", dir=target.parent))
    try:
        ext = args.classified.suffix.lower()
        output = staging / ("classified_with_institutions" + ext)
        if ext == ".parquet":
            promoted.to_parquet(output, index=False)
        else:
            promoted.to_csv(output, index=False)
        (staging / "institution_resolution_summary.json").write_text(
            json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        if os.environ.get("PLACETYPE_P3B_INJECT_PUBLISH_FAILURE") == "1":
            raise RuntimeError("injected publish failure")
        if target.exists():
            raise FileExistsError(f"output directory appeared during publication: {target}")
        staging.rename(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print(
        "PASS: resolved verified institution ownership; "
        "PSIC/PCPC/PSCC and existing columns unchanged"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
