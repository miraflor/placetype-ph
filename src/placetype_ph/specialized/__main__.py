"""Standalone audit/enrichment CLI, separate from the main taxonomy CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _convert_links(args: argparse.Namespace) -> int:
    from .institutional_tools import write_converted_links

    count = write_converted_links(args.input, args.output, schema=args.schema)
    print(f"Wrote {count} verified links in the documented format to {args.output}")
    return 0


def _import_export(args: argparse.Namespace) -> int:
    from .institutional_registry import OFFICIAL_FIELDS, load_official_records
    from .institutional_tools import import_official_export, write_reference

    rows = import_official_export(
        args.input, args.source, as_of=args.as_of, source_url=args.source_url, city=args.city
    )
    if not rows:
        raise ValueError("no rows with an explicit sector were found")
    count = write_reference(args.output, rows, OFFICIAL_FIELDS, load_official_records)
    print(f"Wrote {count} official records to {args.output}")
    return 0


def _build_hospitals(args: argparse.Namespace) -> int:
    from .institutional_registry import HOSPITAL_FIELDS, load_hospitals
    from .institutional_tools import hospital_rows, read_table, write_reference

    legacy = args.command == "build-level3-hospitals"
    rows, stats = hospital_rows(
        read_table(args.input), source=args.source, url=args.source_url, as_of=args.as_of,
        level=3 if legacy else None,
    )
    if not args.allow_partial:
        if legacy and len(rows) < 100:
            raise ValueError("the table has fewer than 100 Level 3 hospitals; use --allow-partial")
        if not legacy and {r["facility_level"] for r in rows} != {"Level 1", "Level 2", "Level 3"}:
            raise ValueError("all three hospital levels required; use --allow-partial")
    report = {"records": len(rows), "counts": dict(stats), "source_url": args.source_url,
              "as_of": args.as_of}
    if not args.check:
        if args.output is None:
            raise ValueError("--output is required unless --check is given")
        report["written"] = write_reference(args.output, rows, HOSPITAL_FIELDS, load_hospitals)
        report["output"] = str(args.output)
    print(json.dumps(report, indent=2))
    return 0


def _install(args: argparse.Namespace) -> int:
    from .institutional_tools import install_references

    path = install_references(args.target, include=args.include, verified_links=args.verified_links)
    print(f"Wrote {path}")
    print(f"Set PLACETYPE_PH_INSTITUTION_REFERENCES={path} to use these references in a run.")
    return 0


def _verify(args: argparse.Namespace) -> int:
    from .institutional_tools import verify_bundled

    print(json.dumps(verify_bundled(), indent=2))
    return 0


def _review(args: argparse.Namespace) -> int:
    import pandas as pd

    from .institutional_tools import load_references, review_institutions, write_review

    classified = pd.read_parquet(args.classified)
    places = (
        pd.read_csv(args.places, dtype=str, keep_default_na=False)
        if args.places.suffix.lower() == ".csv"
        else pd.read_parquet(args.places)
    )
    rows, grouped, summary = review_institutions(
        classified, places, load_references(args.references)
    )
    paths = write_review(args.output_dir, rows, grouped, summary)
    print(json.dumps({k: str(v) for k, v in paths.items()}, indent=2))
    return 0


def _augment(args: argparse.Namespace) -> int:
    import pandas as pd

    from ..augment import augment_places, read_supplied

    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {args.output}")
    if args.output.resolve() in {args.places.resolve(), args.input.resolve()}:
        raise ValueError("output must be a new file, not an input")
    places = pd.read_parquet(args.places)
    out, report = augment_places(places, read_supplied(args.input), source=args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(args.output, index=False)
    print(json.dumps(report, indent=2))
    return 0


TOOL_COMMANDS = {
    "convert-links": _convert_links,
    "import-registry-export": _import_export,
    "build-level3-hospitals": _build_hospitals,
    "build-hospitals": _build_hospitals,
    "install-references": _install,
    "verify-registry-data": _verify,
    "review-institutions": _review,
    "augment-places": _augment,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m placetype_ph.specialized")
    sub = parser.add_subparsers(dest="command", required=True)
    enrich = sub.add_parser(
        "enrich", help="append auditable evidence columns without overwriting classification"
    )
    enrich.add_argument("--places", type=Path, required=True)
    enrich.add_argument("--classified", type=Path, required=True)
    enrich.add_argument("--output", type=Path, required=True)
    enrich.add_argument("--registry", type=Path)
    enrich.add_argument("--report", type=Path)
    migrate = sub.add_parser(
        "migrate-proposals", help="generate an unreviewed source-mapping worklist"
    )
    migrate.add_argument("--source", type=Path, required=True)
    migrate.add_argument("--output", type=Path, required=True)
    migrate.add_argument("--existing", type=Path)
    worklist = sub.add_parser(
        "export-rule-worklist", help="export built-in PSIC rule candidates for review"
    )
    worklist.add_argument("--output", type=Path, required=True)
    sub.add_parser("validate-rules", help="validate packaged activity rules and show fingerprint")
    convert = sub.add_parser(
        "convert-links", help="convert a registry-layout verified-link file to the documented one"
    )
    convert.add_argument("--input", type=Path, required=True)
    convert.add_argument("--schema", required=True, help="schema identifier written in the input")
    convert.add_argument("--output", type=Path, required=True)
    export = sub.add_parser(
        "import-registry-export", help="official records from a DOH NHFR or DepEd export"
    )
    export.add_argument("--input", type=Path, required=True)
    export.add_argument("--source", choices=("doh_nhfr", "deped_masterlist"), required=True)
    export.add_argument("--source-url", required=True)
    export.add_argument("--as-of", required=True, help="date of the export, YYYY-MM-DD")
    export.add_argument("--city", default="", help="keep only this city (default: all)")
    export.add_argument("--output", type=Path, required=True)
    build = sub.add_parser(
        "build-hospitals", aliases=["build-level3-hospitals"],
        help="Hospital reference from an official table (all levels by default)",
    )
    build.add_argument("--input", type=Path, required=True)
    build.add_argument(
        "--source", choices=("philhealth_hospitals", "doh_nhfr"), default="philhealth_hospitals"
    )
    build.add_argument("--source-url", required=True)
    build.add_argument("--as-of", required=True, help="publication date, YYYY-MM-DD")
    build.add_argument("--output", type=Path)
    build.add_argument("--check", action="store_true", help="validate only; write nothing")
    build.add_argument("--allow-partial", action="store_true", help="allow fewer than 100 rows")
    install = sub.add_parser(
        "install-references", help="copy the bundled registry snapshots and write a configuration"
    )
    install.add_argument("--target", type=Path, required=True)
    install.add_argument(
        "--include",
        nargs="+",
        choices=("hospitals", "heida", "unifast", "beis"),
        default=None,
    )
    install.add_argument("--verified-links", type=Path)
    sub.add_parser("verify-registry-data", help="check bundled registry snapshots")
    review = sub.add_parser(
        "review-institutions", help="review queue for places matched to registry records"
    )
    review.add_argument("--classified", type=Path, required=True)
    review.add_argument("--places", type=Path, required=True)
    review.add_argument("--references", type=Path, required=True, help="reference configuration")
    review.add_argument("--output-dir", type=Path, required=True)
    augment = sub.add_parser("augment-places", help="add user-supplied places to an input file")
    augment.add_argument("--places", type=Path, required=True)
    augment.add_argument("--input", type=Path, required=True, help="user places, CSV or Parquet")
    augment.add_argument("--source", required=True, help="label, for example my_registry")
    augment.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command in TOOL_COMMANDS:
        return TOOL_COMMANDS[args.command](args)
    if args.command == "validate-rules":
        from .activity import load_rules
        from .policy import fingerprint

        print(json.dumps({"rules": len(load_rules()), "fingerprint": fingerprint()}, indent=2))
        return 0
    if args.command == "export-rule-worklist":
        from .migrate import export_rule_worklist

        count = export_rule_worklist(args.output)
        print(f"Wrote {count} source-category rule candidates to {args.output}")
        return 0
    if args.command == "migrate-proposals":
        from .migrate import migrate_proposals

        count = migrate_proposals(args.source, args.output, existing=args.existing)
        print(f"Wrote {count} unreviewed candidate mappings to {args.output}")
        return 0
    if args.output.resolve() in {args.places.resolve(), args.classified.resolve()}:
        raise ValueError("output must be a new file, not an input")
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {args.output}")
    if args.report and args.report.exists():
        raise FileExistsError(f"refusing to overwrite existing report: {args.report}")
    import pandas as pd

    from .integration import enrich_summary, report
    from .registry import load_registry

    registry = load_registry(args.registry)
    places, classified = pd.read_parquet(args.places), pd.read_parquet(args.classified)
    result = enrich_summary(classified, places, registry=registry)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".pending")
    tmp_report = args.report.with_name(args.report.name + ".pending") if args.report else None
    published_output = False
    try:
        result.to_parquet(temporary, index=False)
        # A write must be readable before publication.
        check = pd.read_parquet(temporary)
        if len(check) != len(classified):
            raise RuntimeError("output verification failed: row count changed")
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            assert tmp_report is not None
            tmp_report.write_text(
                json.dumps(report(result), indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            json.loads(tmp_report.read_text(encoding="utf-8"))
        if args.output.exists() or (args.report and args.report.exists()):
            raise FileExistsError("output or report appeared after preflight")
        os.replace(temporary, args.output)
        published_output = True
        if args.report and tmp_report is not None:
            os.replace(tmp_report, args.report)
    except BaseException:
        # All-or-nothing publication in the ordinary failure case.
        if published_output:
            args.output.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
        if tmp_report is not None:
            tmp_report.unlink(missing_ok=True)
    print(f"Wrote enriched classification of {len(result)} places: {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
