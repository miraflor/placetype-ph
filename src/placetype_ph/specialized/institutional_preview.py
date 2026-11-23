"""Read-only institutional evidence CLI; publishes complete reports or none."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path

import pandas as pd

from .institutional_registry import (
    InstitutionIndex,
    load_beis,
    load_hospitals,
    load_unifast,
    sha256,
)
from .institutional_shadow import InstitutionResolver, load_verified_links


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only institution identity evidence preview")
    parser.add_argument("--places", type=Path, required=True, help="CSV/Parquet with canonical_id")
    parser.add_argument("--beis", type=Path)
    parser.add_argument("--unifast", type=Path)
    parser.add_argument("--hospitals", type=Path)
    parser.add_argument("--verified-links", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not any((args.beis, args.unifast, args.hospitals, args.verified_links)):
        parser.error("at least one explicit registry/reference must be supplied")
    if args.places.suffix.lower() == ".parquet":
        frame = pd.read_parquet(args.places)
    elif args.places.suffix.lower() == ".csv":
        frame = pd.read_csv(args.places, dtype=str, keep_default_na=False)
    else:
        raise ValueError("places must be CSV or Parquet")
    if "canonical_id" not in frame or not frame["canonical_id"].is_unique:
        raise ValueError("input must have unique canonical_id")
    if (
        frame["canonical_id"].isna().any()
        or not frame["canonical_id"].astype(str).str.strip().all()
    ):
        raise ValueError("empty canonical_id in places")
    paths = [p for p in (args.beis, args.unifast, args.hospitals, args.verified_links) if p]
    references = {str(p.resolve()): sha256(p) for p in paths}
    entries = (
        (load_beis(args.beis) if args.beis else ())
        + (load_unifast(args.unifast) if args.unifast else ())
        + (load_hospitals(args.hospitals) if args.hospitals else ())
    )
    resolver = InstitutionResolver(
        InstitutionIndex(entries), load_verified_links(args.verified_links)
    )
    decisions = [resolver.assess(row) for row in frame.to_dict("records")]
    reports = []
    for cid, decision in zip(frame["canonical_id"], decisions, strict=True):
        reports.append(
            {
                "canonical_id": str(cid),
                "institution_status": decision.status,
                "institution_domain": decision.domain,
                "institution_ownership_candidate": decision.ownership_candidate,
                "institution_basis": decision.basis,
                "institution_references": "|".join(decision.source_references),
                "institution_reason": decision.reason,
                "institution_promotion_eligible": str(decision.promotion_eligible).lower(),
            }
        )
    stats = {
        "policy": "institution-identity-shadow-v1",
        "mode": "advisory_only",
        "authoritative_tag_changes": 0,
        "rows": len(frame),
        "references": references,
        "status_counts": dict(sorted(Counter(d.status for d in decisions).items())),
        "promotion_eligible_not_applied": sum(d.promotion_eligible for d in decisions),
    }
    if args.dry_run:
        print(json.dumps(stats, sort_keys=True, indent=2))
        return 0
    target = args.output_dir.resolve()
    if target.exists():
        raise FileExistsError(f"output directory exists: {target}")
    if not target.parent.is_dir():
        raise FileNotFoundError(f"output parent directory missing: {target.parent}")
    # Never put generated evidence files into a Git checkout.
    for parent in (Path.cwd().resolve(), *Path.cwd().resolve().parents):
        if (parent / ".git").exists():
            if target.is_relative_to(parent):
                raise ValueError("output directory must be outside the repository")
            break
    staging = Path(tempfile.mkdtemp(prefix=".institution-", dir=target.parent))
    try:
        pd.DataFrame(reports).to_csv(staging / "institutional_review.csv", index=False)
        (staging / "summary.json").write_text(
            json.dumps(stats, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        if os.environ.get("PLACETYPE_P3_INJECT_PUBLISH_FAILURE") == "1":
            raise RuntimeError("injected report failure")
        staging.rename(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print(f"PASS: {len(reports)} institutional assertions audited; no authoritative tags changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
