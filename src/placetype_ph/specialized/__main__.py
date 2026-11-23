"""Standalone audit/enrichment CLI, separate from the main taxonomy CLI."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m placetype_ph.specialized")
    sub = parser.add_subparsers(dest="command", required=True)
    enrich = sub.add_parser("enrich", help="append auditable evidence columns without overwriting classification")
    enrich.add_argument("--places", type=Path, required=True)
    enrich.add_argument("--classified", type=Path, required=True)
    enrich.add_argument("--output", type=Path, required=True)
    enrich.add_argument("--registry", type=Path)
    enrich.add_argument("--report", type=Path)
    migrate = sub.add_parser("migrate-proposals", help="generate an unreviewed source-mapping worklist")
    migrate.add_argument("--source", type=Path, required=True)
    migrate.add_argument("--output", type=Path, required=True)
    migrate.add_argument("--existing", type=Path)
    worklist = sub.add_parser("export-rule-worklist", help="export built-in PSIC rule candidates for review")
    worklist.add_argument("--output", type=Path, required=True)
    sub.add_parser("validate-rules", help="validate packaged activity rules and show fingerprint")
    args = parser.parse_args(argv)
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
            tmp_report.write_text(json.dumps(report(result), indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
        raise SystemExit(1)
