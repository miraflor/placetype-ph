from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .cache import DecisionCache
from .classifier import EntityClassifier
from .crosswalk import Crosswalk
from .evaluate import evaluate_predictions, load_gold
from .openplaces import read_openplaces
from .pipeline import classify_openplaces
from .retrieval import TaxonomyRetriever
from .suggest import SUGGESTION_COLUMNS, suggest_mapping
from .taxonomy import LEVEL_ORDER, StructuralReport, Taxonomy, TaxonomyError
from .taxonomy_import import (
    EXPECTED_STRUCTURE,
    OFFICIAL_FILES,
    ImportReport,
    download_official,
    fetch_pcpc_api,
    import_excel,
    structure_deviations,
)
from .traversal import HierarchicalTraverser

app = typer.Typer(
    no_args_is_help=True, help="Hierarchical economic classification for OpenPlaces PH."
)
taxonomy_app = typer.Typer(no_args_is_help=True, help="Build and validate reference taxonomies.")
app.add_typer(taxonomy_app, name="taxonomy")
console = Console()

DEFAULT_VERSIONS = {"psic": "rev5", "pcpc": "2002", "pscc": "2022"}
_MAX_REPORTED = 20

# Columns a reviewer fills in. Preserved when the worklist is regenerated.
REVIEW_COLUMNS = ("mapping_kind", "codes", "match_type", "confidence", "notes", "source_field")
WORKLIST_COLUMNS = (
    "source",
    "source_value",
    "row_count",
    "row_share",
    "scheme",
    "version",
    *REVIEW_COLUMNS,
)


@app.command("version")
def version_command() -> None:
    """Print the PlaceType PH package version."""
    console.print(__version__)


def _print_lines(header: str, lines: list[str], colour: str) -> None:
    console.print(f"[{colour}]{header}[/{colour}]")
    for line in lines[:_MAX_REPORTED]:
        console.print(f"  {line}")
    if len(lines) > _MAX_REPORTED:
        console.print(f"  ... and {len(lines) - _MAX_REPORTED} more")


def _report_discards(report: ImportReport) -> None:
    """Report true discards separately from benign duplicate merges."""
    messages = report.messages()
    if messages:
        _print_lines(
            f"discarded {report.skipped} of {report.rows_seen} source row(s)",
            messages,
            "yellow",
        )
    merge_messages = report.merge_messages()
    if merge_messages:
        _print_lines("merged repeated taxonomy definitions", merge_messages, "cyan")


def _import_workbook(**kwargs: object) -> tuple[Taxonomy, ImportReport]:
    report = ImportReport()
    with warnings.catch_warnings():
        # The CLI prints the same information in a readable form below.
        warnings.simplefilter("ignore", RuntimeWarning)
        taxonomy = import_excel(report=report, **kwargs)  # type: ignore[arg-type]
    return taxonomy, report


def _warn_structure_deviations(taxonomy: Taxonomy) -> list[str]:
    """Compare the built tree against published counts for a known official source."""
    deviations = structure_deviations(taxonomy)
    if deviations:
        _print_lines(
            "this tree does not match the published structure for "
            f"{taxonomy.scheme} {taxonomy.version}",
            deviations,
            "yellow",
        )
    return deviations


def _require_official_structure(
    taxonomy: Taxonomy, *, allow_structure_deviation: bool
) -> None:
    """Fail an official fetch when known published counts do not match.

    `taxonomy fetch` claims to build a complete official reference taxonomy. A partial
    user-supplied workbook is legitimately advisory-only, but an official fetch that
    silently loses a whole branch should not be saved as the default reference tree.
    """
    deviations = _warn_structure_deviations(taxonomy)
    if deviations and not allow_structure_deviation:
        console.print(
            "[red]Refusing to save an incomplete official taxonomy.[/red] "
            "Pass --allow-structure-deviation only if this is intentional."
        )
        raise typer.Exit(2)


def _warn_level_gaps(report: StructuralReport) -> None:
    if report.level_gaps:
        _print_lines(
            f"{len(report.level_gaps)} missing intermediate level(s); "
            "pass --strict-levels to treat these as errors",
            report.level_gaps,
            "yellow",
        )


def _check_scheme(scheme: str) -> str:
    scheme = scheme.casefold()
    if scheme not in DEFAULT_VERSIONS:
        raise typer.BadParameter("scheme must be psic, pcpc, or pscc")
    return scheme


def _taxonomy_path(scheme: str, version: str) -> Path:
    return Path("reference") / f"{scheme}_{version}" / "nodes.parquet"


def _load_taxonomy(scheme: str, version: str) -> Taxonomy:
    path = _taxonomy_path(scheme, version)
    if not path.exists():
        raise typer.BadParameter(f"missing {path}; run `placetype taxonomy fetch {scheme}` first")
    taxonomy = Taxonomy.load(path)
    # A tree filed under the wrong directory used to be accepted in silence.
    if taxonomy.scheme != scheme or taxonomy.version != version:
        raise typer.BadParameter(
            f"{path} contains {taxonomy.scheme} {taxonomy.version}, "
            f"but {scheme} {version} was requested"
        )
    return taxonomy


def _make_backend(kind: str, model: str | None):
    kind = kind.casefold()
    if kind == "none":
        return None
    try:
        from .llm.openai_compat import OpenAICompatibleBackend
    except ModuleNotFoundError as exc:
        if exc.name == "openai":
            raise typer.BadParameter(
                "LLM backends need the optional client: "
                'pip install "placetype-ph[llm]"'
                ' (or pip install -e ".[llm]" from a source checkout)'
            ) from exc
        raise

    if kind == "hf":
        return OpenAICompatibleBackend.huggingface(model or "Qwen/Qwen3-4B-Instruct-2507")
    if kind == "ollama":
        return OpenAICompatibleBackend.ollama(model or "qwen3:4b")
    raise typer.BadParameter("llm backend must be one of: none, hf, ollama")


@app.command("inspect")
def inspect_openplaces(
    input_path: Annotated[Path, typer.Argument(help="openplaces-ph canonical_pois.parquet")],
    top: Annotated[int, typer.Option(help="Top categories per source")] = 20,
):
    frame = read_openplaces(input_path)
    console.print(f"[bold]{len(frame):,}[/bold] canonical POIs")
    for source in ("fsq", "overture", "osm"):
        col = f"{source}_category"
        if col not in frame.columns:
            continue
        values = frame[col].dropna().astype(str)
        table = Table(
            title=f"{source.upper()} categories "
            f"({values.nunique():,} distinct; {len(values):,} non-null)"
        )
        table.add_column("category")
        table.add_column("rows", justify="right")
        for value, count in values.value_counts().head(top).items():
            table.add_row(value, f"{count:,}")
        console.print(table)


@app.command("crosswalk-init")
def crosswalk_init(
    input_path: Annotated[Path, typer.Argument(help="openplaces-ph canonical_pois.parquet")],
    output: Annotated[Path | None, typer.Option()] = None,
    scheme: Annotated[str, typer.Option()] = "psic",
    version: Annotated[str | None, typer.Option()] = None,
):
    """Create or refresh a distinct-category review worklist; no model calls are made.

    If the output file already exists, reviewed rows are preserved. Regenerating after a
    new OpenPlaces build used to overwrite the file and destroy the review work.
    """
    frame = read_openplaces(input_path)
    scheme = _check_scheme(scheme)
    version = version or DEFAULT_VERSIONS[scheme]
    output = output or Path("reference/crosswalks") / f"{scheme}_{version}.csv"
    total = len(frame)
    rows: list[dict] = []
    for source in ("fsq", "overture", "osm"):
        col = f"{source}_category"
        if col not in frame.columns:
            continue
        counts = frame[col].dropna().astype(str).value_counts()
        for value, count in counts.items():
            rows.append({
                "source": source,
                "source_value": value,
                "row_count": int(count),
                "row_share": float(count / total) if total else 0.0,
                "scheme": scheme,
                "version": version,
                "mapping_kind": "",
                "codes": "",
                "match_type": "exact",
                "confidence": "",
                "notes": "",
                "source_field": "category",
            })
    work = pd.DataFrame(rows, columns=list(WORKLIST_COLUMNS))

    preserved = 0
    carried_over = 0
    if output.exists():
        existing = pd.read_csv(output, dtype=str).fillna("")
        for column in REVIEW_COLUMNS:
            if column not in existing.columns:
                existing[column] = ""
        for column in ("scheme", "version", "source", "source_value"):
            if column not in existing.columns:
                raise typer.BadParameter(f"existing crosswalk {output} is missing {column!r}")

        def review_key(r: dict) -> tuple[str, str, str, str, str]:
            return (
                str(r.get("scheme", "")).casefold(),
                str(r.get("version", "")),
                str(r.get("source", "")).casefold(),
                str(r.get("source_field", "category") or "category").casefold(),
                str(r.get("source_value", "")),
            )

        reviewed_records = [
            r
            for r in existing.to_dict("records")
            if str(r.get("mapping_kind", "")).strip()
        ]
        reviewed = {review_key(r): r for r in reviewed_records}
        merged: list[dict] = []
        for row in work.to_dict("records"):
            key = review_key(row)
            match = reviewed.get(key)
            if match is not None:
                for column in REVIEW_COLUMNS:
                    value = str(match.get(column, "") or "")
                    if value:
                        row[column] = value
                preserved += 1
            merged.append(row)
        # Reviewed rows that are absent from this worklist are carried over unchanged.
        # This includes retired categories, name-keyed rules, and rows for other
        # scheme/version pairs when a reviewer deliberately uses one shared CSV.
        current = {review_key(r) for r in merged}
        for key, match in reviewed.items():
            if key in current:
                continue
            carried = {c: str(match.get(c, "") or "") for c in WORKLIST_COLUMNS}
            carried["row_count"] = 0
            carried["row_share"] = 0.0
            merged.append(carried)
            carried_over += 1
        work = pd.DataFrame(merged, columns=list(WORKLIST_COLUMNS))

    work["row_count"] = pd.to_numeric(work["row_count"], errors="coerce").fillna(0).astype(int)
    work = work.sort_values(["row_count", "source"], ascending=[False, True], kind="stable")
    output.parent.mkdir(parents=True, exist_ok=True)
    work.to_csv(output, index=False)
    console.print(f"Wrote {len(work):,} distinct source categories to {output}")
    if preserved or carried_over:
        console.print(
            f"Preserved [bold]{preserved:,}[/bold] reviewed rows; "
            f"carried over {carried_over:,} other reviewed rows"
        )


@app.command("crosswalk-suggest")
def crosswalk_suggest(
    input_path: Annotated[Path, typer.Argument(help="Crosswalk worklist CSV")],
    output: Annotated[Path | None, typer.Option()] = None,
    taxonomy_path: Annotated[Path | None, typer.Option("--taxonomy")] = None,
    top_n: Annotated[int, typer.Option(help="Retrieval candidates per category")] = 5,
    min_score: Annotated[
        float, typer.Option(help="Minimum score for an automatic SUBTREE suggestion")
    ] = 0.45,
    min_margin: Annotated[
        float, typer.Option(help="Required lead over the second retrieval hit")
    ] = 0.12,
):
    """Add reviewable taxonomy suggestions to a crosswalk worklist.

    Existing mapping_kind/codes are never modified. Suggestions are separate columns
    and become classifier evidence only after a reviewer copies/accepts them.
    """
    frame = pd.read_csv(input_path, dtype=str).fillna("")
    required = {"source", "source_value", "scheme", "version", "mapping_kind", "codes"}
    missing = required - set(frame.columns)
    if missing:
        raise typer.BadParameter(f"worklist is missing columns {sorted(missing)}")
    scopes = {(str(r.scheme).casefold(), str(r.version)) for r in frame.itertuples()}
    if len(scopes) != 1:
        raise typer.BadParameter("crosswalk-suggest requires one scheme/version per worklist")
    scheme, version = next(iter(scopes))
    scheme = _check_scheme(scheme)
    taxonomy_path = taxonomy_path or _taxonomy_path(scheme, version)
    if not taxonomy_path.exists():
        raise typer.BadParameter(f"missing taxonomy {taxonomy_path}")
    taxonomy = Taxonomy.load(taxonomy_path)
    if (taxonomy.scheme, taxonomy.version) != (scheme, version):
        raise typer.BadParameter(
            f"{taxonomy_path} contains {taxonomy.scheme} {taxonomy.version}, "
            f"but the worklist requests {scheme} {version}"
        )
    if top_n < 1:
        raise typer.BadParameter("--top-n must be at least 1")
    retriever = TaxonomyRetriever(taxonomy)

    suggestion_rows: list[dict[str, str]] = []
    counts: dict[str, int] = {}
    weighted = 0
    total_rows = 0
    for row in frame.to_dict("records"):
        reviewed = bool(str(row.get("mapping_kind", "")).strip())
        if reviewed:
            suggestion = {name: "" for name in SUGGESTION_COLUMNS}
            suggestion["review_status"] = "REVIEWED"
        else:
            result = suggest_mapping(
                taxonomy,
                retriever,
                str(row.get("source", "")),
                str(row.get("source_value", "")),
                top_n=top_n,
                min_score=min_score,
                min_margin=min_margin,
            )
            suggestion = result.as_dict()
            if result.suggested_kind:
                try:
                    row_count = int(float(str(row.get("row_count", "0")) or 0))
                except ValueError:
                    row_count = 0
                weighted += row_count
        status = suggestion["review_status"]
        counts[status] = counts.get(status, 0) + 1
        try:
            total_rows += int(float(str(row.get("row_count", "0")) or 0))
        except ValueError:
            pass
        suggestion_rows.append(suggestion)

    for column in SUGGESTION_COLUMNS:
        frame[column] = [row[column] for row in suggestion_rows]
    output = output or input_path.with_name(f"{input_path.stem}_suggested.csv")
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False)
    console.print(f"Wrote suggestions for {len(frame):,} categories to {output}")
    console.print(
        "Suggested mappings cover "
        f"{weighted:,} source-category occurrences before review "
        f"({weighted / total_rows:.1%} of worklist occurrences)" if total_rows else ""
    )
    for status, count in sorted(counts.items()):
        console.print(f"  {status}: {count:,}")


def _print_diagnostic_report(
    taxonomy: Taxonomy,
    report: ImportReport | None = None,
    malformed_api_rows: list[str] | None = None,
) -> list[str]:
    """Print a complete first-contact report and return normal-run refusal reasons."""
    if report is not None:
        if report.sheet_details:
            _print_lines("sheet/parser resolution", report.sheet_details, "cyan")
        _report_discards(report)
        examples: list[str] = []
        examples.extend(f"missing code/title: {x}" for x in report.missing_examples[:10])
        examples.extend(f"invalid code: {x!r}" for x in report.invalid_code_formats[:10])
        examples.extend(
            f"unrecognized code length: {x!r}" for x in report.unrecognized_examples[:10]
        )
        examples.extend(f"short code: {x!r}" for x in report.short_codes[:10])
        examples.extend(f"level mismatch: {x}" for x in report.level_mismatches[:10])
        examples.extend(f"invalid parent: {x}" for x in report.invalid_parent_codes[:10])
        examples.extend(f"duplicate conflict: {x}" for x in report.duplicate_conflicts[:10])
        if examples:
            _print_lines("diagnostic examples", examples, "yellow")
    if malformed_api_rows:
        _print_lines(
            f"{len(malformed_api_rows)} malformed API row(s)", malformed_api_rows, "yellow"
        )

    counts: dict[str, int] = {}
    for node in taxonomy.nodes.values():
        counts[node.level] = counts.get(node.level, 0) + 1
    expected = EXPECTED_STRUCTURE.get((taxonomy.scheme, taxonomy.version), {})
    table = Table(title=f"{taxonomy.scheme.upper()} {taxonomy.version} level histogram")
    table.add_column("level")
    table.add_column("found", justify="right")
    table.add_column("published", justify="right")
    for level in LEVEL_ORDER.get(taxonomy.scheme, tuple(counts)):
        published = expected.get(level)
        table.add_row(
            level,
            f"{counts.get(level, 0):,}",
            f"{published:,}" if published is not None else "-",
        )
    console.print(table)

    structural = taxonomy.structural_report()
    if structural.errors:
        _print_lines(f"{len(structural.errors)} structural error(s)", structural.errors, "red")
    if structural.level_gaps:
        _print_lines(
            f"{len(structural.level_gaps)} intermediate-level gap(s)",
            structural.level_gaps,
            "yellow",
        )
    deviations = _warn_structure_deviations(taxonomy)

    reasons: list[str] = []
    if report is not None:
        if report.short_codes:
            reasons.append("short numeric codes indicate lost leading zeroes")
        if report.level_mismatches:
            reasons.append("codes contradict an explicit hierarchy level")
        if report.invalid_parent_codes:
            reasons.append("explicit parent codes are invalid")
        if report.duplicate_conflicts:
            reasons.append("duplicate definitions conflict structurally")
    if malformed_api_rows:
        reasons.append("the API returned malformed rows")
    if structural.errors:
        reasons.append("the built tree has structural errors")
    # Level gaps are warnings by default. Pass --strict-levels on fetch/import when
    # the source is expected to enumerate every intermediate level explicitly.
    if deviations:
        reasons.append("published level counts do not match")

    if reasons:
        _print_lines("a normal official fetch would refuse because", reasons, "red")
    else:
        suffix = " with level-gap warnings" if structural.level_gaps else ""
        console.print(f"[green]A normal official fetch would be eligible to save{suffix}.[/green]")
    return reasons


@taxonomy_app.command("diagnose")
def diagnose_taxonomy(
    scheme: Annotated[str, typer.Argument(help="psic, pcpc, or pscc")],
    version: Annotated[str | None, typer.Option(help="Override default version")] = None,
    input_path: Annotated[
        Path | None,
        typer.Option("--input", help="Local workbook; omit to use the official source"),
    ] = None,
    token: Annotated[
        str | None, typer.Option(help="PSA API token, or PSA_CLASSIFICATION_TOKEN")
    ] = None,
    code_column: Annotated[str | None, typer.Option()] = None,
    title_column: Annotated[str | None, typer.Option()] = None,
    parent_column: Annotated[str | None, typer.Option()] = None,
    section_column: Annotated[str | None, typer.Option()] = None,
    level_column: Annotated[str | None, typer.Option()] = None,
):
    """Parse a taxonomy source completely, report every finding, and save no taxonomy."""
    scheme = _check_scheme(scheme)
    version = version or DEFAULT_VERSIONS[scheme]

    if scheme == "pcpc" and input_path is None:
        api_token = token or os.getenv("PSA_CLASSIFICATION_TOKEN")
        if not api_token:
            raise typer.BadParameter("PCPC diagnose requires --token or PSA_CLASSIFICATION_TOKEN")
        malformed: list[str] = []
        taxonomy = fetch_pcpc_api(
            api_token,
            version,
            strict=False,
            strict_levels=False,
            diagnostic=True,
            malformed_rows_out=malformed,
        )
        _print_diagnostic_report(taxonomy, malformed_api_rows=malformed)
        console.print("[dim]Diagnostic only: no taxonomy file was written.[/dim]")
        return

    if input_path is None:
        key = (scheme, version)
        if key not in OFFICIAL_FILES:
            raise typer.BadParameter(f"no configured official workbook for {scheme} {version}")
        input_path = Path("reference/downloads") / f"{scheme}_{version}.xlsx"
        console.print(f"Downloading official {scheme.upper()} {version} source for diagnosis...")
        download_official(scheme, version, input_path)
        source_url = OFFICIAL_FILES[key]
    else:
        source_url = ""

    report = ImportReport()
    try:
        taxonomy = import_excel(
            input_path,
            scheme,
            version,
            source_url=source_url,
            code_column=code_column,
            title_column=title_column,
            parent_column=parent_column,
            section_column=section_column,
            level_column=level_column,
            strict=False,
            strict_levels=False,
            report=report,
            diagnostic=True,
        )
    except TaxonomyError as exc:
        if report.sheet_details:
            _print_lines("sheet/parser resolution", report.sheet_details, "cyan")
        _report_discards(report)
        console.print(f"[red]Could not construct even a diagnostic tree:[/red] {exc}")
        console.print("[dim]Diagnostic only: no taxonomy file was written.[/dim]")
        raise typer.Exit(2) from exc

    _print_diagnostic_report(taxonomy, report=report)
    console.print("[dim]Diagnostic only: no taxonomy file was written.[/dim]")


@taxonomy_app.command("fetch")
def fetch_taxonomy(
    scheme: Annotated[str, typer.Argument(help="psic, pcpc, or pscc")],
    version: Annotated[str | None, typer.Option(help="Override default version")] = None,
    output: Annotated[Path | None, typer.Option(help="Output nodes.parquet")] = None,
    token: Annotated[
        str | None, typer.Option(help="PSA classification API token (or PSA_CLASSIFICATION_TOKEN)")
    ] = None,
    allow_orphan_nodes: Annotated[
        bool, typer.Option(help="Accept a tree whose nodes lost their parent")
    ] = False,
    strict_levels: Annotated[
        bool,
        typer.Option(
            help="Reject a taxonomy with a missing intermediate level. Gaps are reported "
            "but accepted by default because current official workbooks can omit a level."
        ),
    ] = False,
    level_column: Annotated[
        str | None,
        typer.Option(help="Column naming each row's hierarchy level, used to repair codes"),
    ] = None,
    allow_structure_deviation: Annotated[
        bool,
        typer.Option(
            help="Save a known official taxonomy even when published level counts differ"
        ),
    ] = False,
):
    scheme = _check_scheme(scheme)
    version = version or DEFAULT_VERSIONS[scheme]
    output = output or _taxonomy_path(scheme, version)
    output.parent.mkdir(parents=True, exist_ok=True)
    strict = not allow_orphan_nodes

    if scheme == "pcpc":
        api_token = token or os.getenv("PSA_CLASSIFICATION_TOKEN")
        if not api_token:
            raise typer.BadParameter("PCPC fetch requires --token or PSA_CLASSIFICATION_TOKEN")
        taxonomy = fetch_pcpc_api(
            api_token, version, strict=strict, strict_levels=strict_levels
        )
        _warn_level_gaps(taxonomy.structural_report())
        _require_official_structure(
            taxonomy, allow_structure_deviation=allow_structure_deviation
        )
        taxonomy.save(output)
        console.print(f"Wrote {len(taxonomy.nodes):,} PCPC nodes to {output}")
        return

    key = (scheme, version)
    if key not in OFFICIAL_FILES:
        raise typer.BadParameter(f"no configured official direct file for {scheme} {version}")
    download = Path("reference/downloads") / f"{scheme}_{version}.xlsx"
    console.print(f"Downloading official {scheme.upper()} {version} source...")
    download_official(scheme, version, download)
    try:
        taxonomy, import_report = _import_workbook(
            path=download,
            scheme=scheme,
            version=version,
            source_url=OFFICIAL_FILES[key],
            strict=strict,
            strict_levels=strict_levels,
            level_column=level_column,
        )
    except TaxonomyError as exc:
        console.print(f"[red]Downloaded source but normalization failed:[/red] {exc}")
        console.print(
            f"Source retained at {download}. Use `placetype taxonomy import-xlsx` with "
            "explicit columns if necessary."
        )
        raise typer.Exit(2) from exc
    _report_discards(import_report)
    _warn_level_gaps(taxonomy.structural_report())
    _require_official_structure(
        taxonomy, allow_structure_deviation=allow_structure_deviation
    )
    taxonomy.save(output)
    console.print(f"Wrote {len(taxonomy.nodes):,} {scheme.upper()} nodes to {output}")


@taxonomy_app.command("import-xlsx")
def import_xlsx(
    input_path: Annotated[Path, typer.Argument()],
    scheme: Annotated[str, typer.Option()],
    version: Annotated[str, typer.Option()],
    output: Annotated[Path, typer.Option()],
    code_column: Annotated[str | None, typer.Option()] = None,
    title_column: Annotated[str | None, typer.Option()] = None,
    parent_column: Annotated[
        str | None, typer.Option(help="Column holding the direct parent code")
    ] = None,
    section_column: Annotated[
        str | None,
        typer.Option(help="PSIC section-membership column; used only to attach divisions"),
    ] = None,
    allow_orphan_nodes: Annotated[bool, typer.Option()] = False,
    strict_levels: Annotated[
        bool, typer.Option(help="Also reject a tree with a missing intermediate level")
    ] = False,
    level_column: Annotated[
        str | None,
        typer.Option(help="Column naming each row's hierarchy level, used to repair codes"),
    ] = None,
):
    taxonomy, import_report = _import_workbook(
        path=input_path,
        scheme=_check_scheme(scheme),
        version=version,
        code_column=code_column,
        title_column=title_column,
        parent_column=parent_column,
        section_column=section_column,
        strict=not allow_orphan_nodes,
        strict_levels=strict_levels,
        level_column=level_column,
    )
    _report_discards(import_report)
    _warn_level_gaps(taxonomy.structural_report())
    _warn_structure_deviations(taxonomy)
    taxonomy.save(output)
    console.print(f"Wrote {len(taxonomy.nodes):,} nodes to {output}")


@taxonomy_app.command("validate")
def validate_taxonomy(
    path: Annotated[Path, typer.Argument()],
    allow_orphan_nodes: Annotated[
        bool, typer.Option(help="Report structural errors without failing")
    ] = False,
    strict_levels: Annotated[
        bool, typer.Option(help="Treat a missing intermediate level as an error")
    ] = False,
    strict_structure: Annotated[
        bool,
        typer.Option(help="Fail when known published level counts do not match"),
    ] = False,
):
    """Report the shape of a taxonomy.

    The flags mirror the import flags so a tree that was deliberately accepted at import
    time can still be validated. Without them a tree imported with --allow-orphan-nodes
    could never pass this command.
    """
    taxonomy = Taxonomy.load(path)
    leaves = sum(not taxonomy.has_children(c) for c in taxonomy.nodes)
    report = taxonomy.structural_report()
    console.print(
        f"{taxonomy.scheme} {taxonomy.version}: {len(taxonomy.nodes):,} nodes, "
        f"{len(taxonomy.roots):,} roots, {leaves:,} leaves, max depth {taxonomy.max_depth}"
    )
    deviations = _warn_structure_deviations(taxonomy)
    if report.ok and not (strict_structure and deviations):
        console.print("[green]valid[/green]: every level nests under its immediate parent level")
        return
    if report.errors:
        _print_lines(f"{len(report.errors)} structural error(s)", report.errors, "red")
    if report.level_gaps:
        colour = "red" if strict_levels else "yellow"
        _print_lines(
            f"{len(report.level_gaps)} missing intermediate level(s)", report.level_gaps, colour
        )
        if not strict_levels:
            console.print(
                "  [dim]pass --strict-levels to apply the same gate as `taxonomy fetch`[/dim]"
            )
    fatal = (
        (report.errors and not allow_orphan_nodes)
        or (report.level_gaps and strict_levels)
        or (deviations and strict_structure)
    )
    if fatal:
        raise typer.Exit(1)
    console.print("[green]accepted[/green]: reported above, not treated as fatal")


@app.command("evaluate")
def evaluate(
    predictions: Annotated[Path, typer.Argument(help="entity_classifications.parquet")],
    gold: Annotated[
        Path,
        typer.Argument(
            help="CSV/Parquet with canonical_id, scheme, gold_code; optional gold_status"
        ),
    ],
    scheme: Annotated[str, typer.Option()] = "psic",
    version: Annotated[str | None, typer.Option()] = None,
    output_dir: Annotated[Path, typer.Option()] = Path("output/evaluation"),
):
    """Report accuracy and production rate separately at every hierarchy depth."""
    scheme = _check_scheme(scheme)
    taxonomy = _load_taxonomy(scheme, version or DEFAULT_VERSIONS[scheme])
    pred = pd.read_parquet(predictions)
    gold_frame = load_gold(gold)
    metrics, summary = evaluate_predictions(pred, gold_frame, taxonomy)
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = output_dir / f"{scheme}_depth_metrics.csv"
    summary_path = output_dir / f"{scheme}_summary.json"
    metrics.to_csv(metrics_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    console.print(metrics.to_string(index=False))
    console.print(summary)
    console.print(f"Wrote {metrics_path} and {summary_path}")


@app.command("classify")
def classify(
    input_path: Annotated[Path, typer.Argument(help="openplaces-ph canonical_pois.parquet")],
    output_dir: Annotated[Path, typer.Option()] = Path("output"),
    schemes: Annotated[str, typer.Option(help="Comma-separated: psic,pcpc,pscc")] = "psic",
    version: Annotated[
        str | None, typer.Option(help="Override the default version for every scheme")
    ] = None,
    crosswalk: Annotated[
        list[Path] | None, typer.Option(help="Repeat for one or more crosswalk CSVs")
    ] = None,
    llm: Annotated[str, typer.Option(help="none, hf, or ollama")] = "none",
    model: Annotated[str | None, typer.Option()] = None,
    passes: Annotated[int, typer.Option(min=1, max=7)] = 3,
    product_column: Annotated[
        str | None, typer.Option(help="Explicit product/commodity text for PCPC/PSCC")
    ] = None,
    limit: Annotated[int | None, typer.Option(help="Pilot on first N rows")] = None,
    cache_path: Annotated[Path, typer.Option()] = Path("cache/decisions.sqlite"),
    fail_on_ambiguous_crosswalk: Annotated[
        bool,
        typer.Option(
            help="Stop the run when overlapping crosswalk rules disagree, instead of "
            "flagging the affected rows for review"
        ),
    ] = False,
):
    selected = [_check_scheme(s) for s in schemes.split(",") if s.strip()]
    if not selected:
        raise typer.BadParameter("no scheme selected")

    cw = (
        Crosswalk.load(crosswalk or [], strict_ambiguity=fail_on_ambiguous_crosswalk)
        if crosswalk
        else None
    )
    backend = _make_backend(llm, model)
    cache = DecisionCache(cache_path) if backend is not None else None
    classifiers: dict[str, EntityClassifier] = {}
    try:
        for scheme in selected:
            taxonomy = _load_taxonomy(scheme, version or DEFAULT_VERSIONS[scheme])
            traverser = (
                HierarchicalTraverser(taxonomy, backend, passes=passes)
                if backend is not None
                else None
            )
            classifier = EntityClassifier(taxonomy, cw, traverser, cache)
            # A scheme/version typo in the crosswalk used to produce an all-EMPTY run
            # with no message at all.
            if cw is not None and classifier.applicable_crosswalk_entries == 0:
                console.print(
                    f"[yellow]warning[/yellow]: no crosswalk row targets "
                    f"{scheme} {taxonomy.version}; every row will fall back to the "
                    "LLM or to EMPTY"
                )
            classifiers[scheme] = classifier

        classifications, pois = classify_openplaces(
            input_path, classifiers, output_dir, product_column, limit
        )
        console.print(f"Wrote [bold]{classifications}[/bold]")
        console.print(f"Wrote [bold]{pois}[/bold]")
        ambiguous: dict[str, int] = {}
        for classifier in classifiers.values():
            for detail, count in classifier.ambiguities.items():
                ambiguous[detail] = ambiguous.get(detail, 0) + count
        if ambiguous:
            affected = sum(classifier.ambiguity_rows for classifier in classifiers.values())
            occurrences = sum(ambiguous.values())
            _print_lines(
                f"{len(ambiguous)} ambiguous crosswalk rule set(s) affected "
                f"{affected:,} scheme-row classification(s) ({occurrences:,} ambiguous "
                "field match(es)); affected rows are flagged CROSSWALK_AMBIGUOUS",
                sorted(ambiguous),
                "yellow",
            )
    finally:
        if cache is not None:
            cache.close()


if __name__ == "__main__":
    app()
