from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from . import __version__
from .cache import DecisionCache
from .classifier import EntityClassifier
from .crosswalk import Crosswalk
from .economic_role import ROLE_REVIEW_COLUMNS, RoleCrosswalk
from .evaluate import evaluate_predictions, load_gold
from .express import (
    CLASSIFY_PARAMS,
    EXPRESS_AUTOMATIC_COLUMNS,
    EXPRESS_SUGGESTION_COLUMNS,
    SUGGEST_PARAMS,
    classification_run_ready,
    crosswalk_has_mappings,
    csv_ready,
    default_output_path,
    file_state,
    find_local_taxonomy_source,
    input_cache_key,
    pipeline_state,
    promote_auto_suggestions,
    promoted_total,
    reference_is_joint_format,
    seed_joint_worklist,
    summarize_automatic_crosswalk,
)
from .gis import GISExportError, export_gis_run
from .joint_crosswalk import (
    ensure_joint_key,
    make_joint_key,
    recheck_joint_worklist,
)
from .openplaces import read_openplaces
from .pipeline import classify_openplaces
from .retrieval import TaxonomyRetriever
from .suggest import (
    SUGGESTION_COLUMNS,
    finalize_suggestion,
    prepare_suggestion,
)
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
# Economic-role annotations are joint place semantics, not another taxonomy.
REVIEW_COLUMNS = (
    "mapping_kind",
    "codes",
    "match_type",
    "confidence",
    "notes",
    "source_field",
    *ROLE_REVIEW_COLUMNS,
)
# ``dict.fromkeys`` keeps the order and drops duplicates, so ``joint_key`` and
# ``source_field`` appear exactly once whether or not REVIEW_COLUMNS already holds
# them. A column absent here is silently dropped when the worklist frame is built,
# which is how ``source_field`` could go missing before.
WORKLIST_COLUMNS = tuple(
    dict.fromkeys(
        (
            "source",
            "source_value",
            "source_field",
            "joint_key",
            "row_count",
            "row_share",
            "scheme",
            "version",
            *REVIEW_COLUMNS,
        )
    )
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
    schemes: Annotated[
        str,
        typer.Option(
            "--schemes",
            "--scheme",
            help=(
                "Comma-separated classification schemes. By default one joint worklist "
                "is created for PSIC, PCPC and PSCC."
            ),
        ),
    ] = "psic,pcpc,pscc",
    version: Annotated[
        str | None,
        typer.Option(help="Version override; valid only when exactly one scheme is selected"),
    ] = None,
    adopt_legacy: Annotated[
        bool,
        typer.Option(
            "--adopt-legacy/--no-adopt-legacy",
            help=(
                "When the joint worklist does not exist yet, read reviewed rows from the "
                "per-scheme worklists written before the joint format was introduced."
            ),
        ),
    ] = True,
):
    """Create or refresh one joint hierarchical crosswalk review worklist.

    Each observed OpenPlaces category gets a stable ``joint_key``. The worklist then has
    one row per selected classification scheme under that key, so PSIC, PCPC and PSCC are
    reviewed together without flattening any taxonomy's hierarchy.

    Reviewed rows are never lost. They are read from the output file when it exists, and
    otherwise from the per-scheme files this command wrote before the joint format, unless
    ``--no-adopt-legacy`` is given. A reviewed row whose category no longer appears in the
    data is carried over with a row count and a row share of zero. No model calls are made.
    """
    frame = read_openplaces(input_path)
    selected: list[str] = []
    for raw in schemes.split(","):
        if not raw.strip():
            continue
        scheme = _check_scheme(raw.strip())
        if scheme not in selected:
            selected.append(scheme)
    if not selected:
        raise typer.BadParameter("no scheme selected")
    if version is not None and len(selected) != 1:
        raise typer.BadParameter("--version can only be used when exactly one scheme is selected")

    versions = {
        scheme: (version if version is not None else DEFAULT_VERSIONS[scheme])
        for scheme in selected
    }
    crosswalk_dir = Path("reference/crosswalks")
    if output is None:
        output = (
            crosswalk_dir / "joint.csv"
            if len(selected) > 1
            else crosswalk_dir / f"{selected[0]}_{versions[selected[0]]}.csv"
        )

    total = len(frame)
    rows: list[dict] = []
    for source in ("fsq", "overture", "osm"):
        col = f"{source}_category"
        if col not in frame.columns:
            continue
        counts = frame[col].dropna().astype(str).value_counts()
        for value, count in counts.items():
            source_value = str(value)
            joint_key = make_joint_key(source, source_value, "category")
            for scheme in selected:
                rows.append(
                    {
                        "source": source,
                        "source_value": source_value,
                        "source_field": "category",
                        "joint_key": joint_key,
                        "row_count": int(count),
                        "row_share": float(count / total) if total else 0.0,
                        "scheme": scheme,
                        "version": versions[scheme],
                        "mapping_kind": "",
                        "codes": "",
                        "match_type": "exact",
                        "confidence": "",
                        "notes": "",
                        **{column: "" for column in ROLE_REVIEW_COLUMNS},
                    }
                )
    work = pd.DataFrame(rows, columns=list(WORKLIST_COLUMNS))

    def review_key(record: dict) -> tuple[str, str, str, str, str]:
        return (
            str(record.get("scheme", "")).strip().casefold(),
            str(record.get("version", "")).strip(),
            str(record.get("source", "")).strip().casefold(),
            str(record.get("source_field", "category") or "category").strip().casefold(),
            str(record.get("source_value", "")).strip(),
        )

    def read_reviewed(path: Path) -> list[dict]:
        existing = pd.read_csv(path, dtype=str).fillna("")
        for column in ("scheme", "version", "source", "source_value"):
            if column not in existing.columns:
                raise typer.BadParameter(f"crosswalk {path} is missing {column!r}")
        for column in REVIEW_COLUMNS:
            if column not in existing.columns:
                existing[column] = ""
        ensure_joint_key(existing)
        return [
            record
            for record in existing.to_dict("records")
            if str(record.get("mapping_kind", "")).strip()
            or any(str(record.get(column, "")).strip() for column in ROLE_REVIEW_COLUMNS)
        ]

    read_from: list[Path] = []
    reviewed: dict[tuple[str, str, str, str, str], dict] = {}
    if output.exists():
        records = read_reviewed(output)
        if records:
            read_from.append(output)
            for record in records:
                reviewed[review_key(record)] = record
    elif adopt_legacy:
        for scheme in selected:
            legacy = crosswalk_dir / f"{scheme}_{versions[scheme]}.csv"
            if legacy == output or not legacy.exists():
                continue
            records = read_reviewed(legacy)
            if not records:
                continue
            read_from.append(legacy)
            for record in records:
                reviewed.setdefault(review_key(record), record)

    preserved = 0
    carried_over = 0
    if reviewed:
        merged: list[dict] = []
        for row in work.to_dict("records"):
            match = reviewed.get(review_key(row))
            if match is not None:
                for column in REVIEW_COLUMNS:
                    value = str(match.get(column, "") or "")
                    if value:
                        row[column] = value
                preserved += 1
            merged.append(row)

        current = {review_key(record) for record in merged}
        for key, match in reviewed.items():
            if key in current:
                continue
            carried = {column: str(match.get(column, "") or "") for column in WORKLIST_COLUMNS}
            carried["joint_key"] = str(match.get("joint_key", "")) or make_joint_key(
                str(match.get("source", "")),
                str(match.get("source_value", "")),
                str(match.get("source_field", "category") or "category"),
            )
            carried["row_count"] = 0
            carried["row_share"] = 0.0
            merged.append(carried)
            carried_over += 1
        work = pd.DataFrame(merged, columns=list(WORKLIST_COLUMNS))

    work["row_count"] = pd.to_numeric(work["row_count"], errors="coerce").fillna(0).astype(int)
    work["row_share"] = pd.to_numeric(work["row_share"], errors="coerce").fillna(0.0).astype(float)
    work = work.sort_values(
        ["row_count", "source", "source_value", "joint_key", "scheme", "version"],
        ascending=[False, True, True, True, True, True],
        kind="stable",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    work.to_csv(output, index=False)
    console.print(
        f"Wrote {len(work):,} scheme rows in "
        f"{work['joint_key'].nunique():,} joint source-category groups to {output}"
    )
    if read_from:
        console.print("Read reviewed rows from " + ", ".join(str(path) for path in read_from))
    if preserved or carried_over:
        console.print(
            f"Preserved [bold]{preserved:,}[/bold] reviewed rows; "
            f"carried over {carried_over:,} other reviewed rows"
        )


@app.command("crosswalk-suggest")
def crosswalk_suggest(
    input_path: Annotated[Path, typer.Argument(help="Joint crosswalk worklist CSV")],
    output: Annotated[Path | None, typer.Option()] = None,
    taxonomy_paths: Annotated[
        list[Path] | None,
        typer.Option(
            "--taxonomy",
            help="Optional taxonomy override; repeat once per scheme/version as needed",
        ),
    ] = None,
    top_n: Annotated[int, typer.Option(help="Retrieval candidates per category and scheme")] = 5,
    min_score: Annotated[
        float, typer.Option(help="Minimum score for an automatic SUBTREE suggestion")
    ] = 0.45,
    min_margin: Annotated[
        float,
        typer.Option(
            help=(
                "Required lead over the second retrieval hit in the first pass, and over the "
                "control candidate in the peer-context recheck"
            )
        ),
    ] = 0.12,
    recheck: Annotated[
        bool,
        typer.Option(
            "--recheck/--no-recheck",
            help=(
                "Rerank first-pass candidates with accepted peer labels; "
                "no new codes are introduced"
            ),
        ),
    ] = True,
    batch_size: Annotated[
        int,
        typer.Option(
            "--batch-size",
            help="Unique TF-IDF query texts scored together per taxonomy",
        ),
    ] = 256,
    show_progress: Annotated[
        bool,
        typer.Option(
            "--progress/--no-progress",
            help="Show stage and progress bars during suggestion generation",
        ),
    ] = True,
):
    """Suggest every taxonomy independently, then rerank its candidates with peer context.

    V9 prepares every row without retrieval, then batches and deduplicates TF-IDF queries by
    taxonomy. The first pass alone discovers codes. The second pass may reorder only those
    first-pass candidates. Reviewed mappings and first-pass suggestions are never overwritten.
    """
    console.print(f"[bold]1/5[/bold] Loading worklist and taxonomies: {input_path}")
    frame = pd.read_csv(input_path, dtype=str).fillna("")
    required = {"source", "source_value", "scheme", "version", "mapping_kind", "codes"}
    missing = required - set(frame.columns)
    if missing:
        raise typer.BadParameter(f"worklist is missing columns {sorted(missing)}")
    if top_n < 1:
        raise typer.BadParameter("--top-n must be at least 1")
    if not 0.0 <= min_score <= 1.0:
        raise typer.BadParameter("--min-score must be between 0 and 1")
    if min_margin < 0.0:
        raise typer.BadParameter("--min-margin must not be negative")
    if batch_size < 1:
        raise typer.BadParameter("--batch-size must be at least 1")

    ensure_joint_key(frame)

    def raw_scope(record: dict) -> tuple[str, str]:
        return (
            str(record.get("scheme", "")).strip(),
            str(record.get("version", "")).strip(),
        )

    canonical: dict[tuple[str, str], tuple[str, str]] = {}
    records = frame.to_dict("records")
    for raw in sorted({raw_scope(record) for record in records}):
        canonical[raw] = (_check_scheme(raw[0]), raw[1])

    def scope_of(record: dict) -> tuple[str, str]:
        raw = raw_scope(record)
        return canonical.get(raw, (raw[0].casefold(), raw[1]))

    overrides: dict[tuple[str, str], Taxonomy] = {}
    for path in taxonomy_paths or []:
        if not path.exists():
            raise typer.BadParameter(f"missing taxonomy {path}")
        taxonomy = Taxonomy.load(path)
        scope = (str(taxonomy.scheme).strip().casefold(), str(taxonomy.version).strip())
        if scope in overrides:
            raise typer.BadParameter(
                f"more than one --taxonomy override was supplied for {scope[0]} {scope[1]}"
            )
        overrides[scope] = taxonomy

    taxonomies: dict[tuple[str, str], Taxonomy] = {}
    for scope in sorted(set(canonical.values())):
        scheme, version = scope
        if scope in overrides:
            taxonomies[scope] = overrides[scope]
            continue
        path = _taxonomy_path(scheme, version)
        if not path.exists():
            raise typer.BadParameter(f"missing taxonomy {path}")
        taxonomy = Taxonomy.load(path)
        loaded = (str(taxonomy.scheme).strip().casefold(), str(taxonomy.version).strip())
        if loaded != scope:
            raise typer.BadParameter(
                f"{path} contains {taxonomy.scheme} {taxonomy.version}, "
                f"but the worklist requests {scheme} {version}"
            )
        taxonomies[scope] = taxonomy

    unused = sorted(set(overrides) - set(taxonomies))
    for scheme, version in unused:
        console.print(
            f"[yellow]--taxonomy override for {scheme} {version} was not used; "
            f"no worklist row requests it[/yellow]"
        )

    retrievers = {scope: TaxonomyRetriever(taxonomy) for scope, taxonomy in taxonomies.items()}
    console.print(
        "  loaded "
        + ", ".join(
            f"{scheme} {version} ({len(taxonomies[(scheme, version)].nodes):,} nodes)"
            for scheme, version in sorted(taxonomies)
        )
    )

    def row_count_of(record: dict) -> int:
        try:
            return int(float(str(record.get("row_count", "0")) or 0))
        except ValueError:
            return 0

    prepared_rows: list[object] = [None] * len(records)
    scopes: list[tuple[str, str]] = [("", "")] * len(records)
    pending_by_scope: dict[tuple[str, str], list[int]] = {}

    console.print(f"[bold]2/5[/bold] Preparing source evidence for {len(records):,} scheme rows")
    with Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        disable=not show_progress,
    ) as progress_bar:
        task = progress_bar.add_task("prepare", total=len(records))
        for position, record in enumerate(records):
            scope = scope_of(record)
            taxonomy = taxonomies.get(scope)
            if taxonomy is None:
                raise typer.BadParameter(
                    f"no taxonomy was loaded for scheme {scope[0]!r} version {scope[1]!r}"
                )
            prepared = prepare_suggestion(
                taxonomy,
                str(record.get("source", "")),
                str(record.get("source_value", "")),
            )
            scopes[position] = scope
            prepared_rows[position] = prepared
            if prepared.terminal is None:
                pending_by_scope.setdefault(scope, []).append(position)
            progress_bar.advance(task)

    unique_query_total = 0
    for _scope, positions in pending_by_scope.items():
        unique_query_total += len(
            {
                prepared_rows[position].query_text.strip()
                for position in positions
                if prepared_rows[position].query_text.strip()
            }
        )

    console.print(
        f"[bold]3/5[/bold] Batched first-pass retrieval: "
        f"{unique_query_total:,} unique query texts across {len(pending_by_scope):,} taxonomies "
        f"(batch size {batch_size:,})"
    )
    hits_by_position: list[list] = [[] for _ in records]
    with Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        disable=not show_progress,
    ) as progress_bar:
        task = progress_bar.add_task("retrieve", total=unique_query_total)

        def advance_retrieval(amount: int) -> None:
            progress_bar.advance(task, advance=amount)

        for scope in sorted(pending_by_scope):
            positions = pending_by_scope[scope]
            retriever = retrievers[scope]
            requests = [
                (
                    prepared_rows[position].query_text,
                    prepared_rows[position].branch_codes,
                )
                for position in positions
            ]
            batches = retriever.search_hierarchical_many(
                requests,
                top_n=top_n,
                batch_size=batch_size,
                batch_callback=advance_retrieval,
            )
            for position, hits in zip(positions, batches, strict=True):
                hits_by_position[position] = hits

    suggestion_rows: list[dict[str, str]] = []
    counts: dict[str, int] = {}
    occurrences: dict[str, int] = {}
    reviewed_decisions: dict[str, int] = {}
    reviewed_coded_occurrences: dict[str, int] = {}
    suggested_occurrences: dict[str, int] = {}

    for position, record in enumerate(records):
        scope = scopes[position]
        taxonomy = taxonomies[scope]
        prepared = prepared_rows[position]
        count = row_count_of(record)
        scheme = scope[0]
        occurrences[scheme] = occurrences.get(scheme, 0) + count

        result = finalize_suggestion(
            taxonomy,
            prepared,
            hits_by_position[position],
            min_score=min_score,
            min_margin=min_margin,
        )
        suggestion = result.as_dict()

        if str(record.get("mapping_kind", "")).strip():
            suggestion["suggested_kind"] = ""
            suggestion["suggested_codes"] = ""
            suggestion["review_status"] = "REVIEWED"
            reviewed_decisions[scheme] = reviewed_decisions.get(scheme, 0) + count
            if str(record.get("codes", "")).strip():
                reviewed_coded_occurrences[scheme] = (
                    reviewed_coded_occurrences.get(scheme, 0) + count
                )
        elif result.suggested_codes:
            suggested_occurrences[scheme] = suggested_occurrences.get(scheme, 0) + count

        status = suggestion["review_status"]
        counts[status] = counts.get(status, 0) + 1
        suggestion_rows.append(suggestion)

    for column in SUGGESTION_COLUMNS:
        frame[column] = [row[column] for row in suggestion_rows]

    if recheck:
        joint_groups = int(frame["joint_key"].nunique())
        console.print(
            f"[bold]4/5[/bold] Peer-context recheck across {joint_groups:,} joint groups"
        )
        with Progress(
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
            disable=not show_progress,
        ) as progress_bar:
            task = progress_bar.add_task("recheck", total=joint_groups)

            def advance_recheck(amount: int) -> None:
                progress_bar.advance(task, advance=amount)

            frame = recheck_joint_worklist(
                frame,
                taxonomies,
                retrievers,
                top_n=top_n,
                min_margin=min_margin,
                scope_of=scope_of,
                preferred_versions=DEFAULT_VERSIONS,
                progress_callback=advance_recheck,
            )
    else:
        console.print("[bold]4/5[/bold] Peer-context recheck skipped (--no-recheck)")

    output = output or input_path.with_name(f"{input_path.stem}_suggested.csv")
    console.print(f"[bold]5/5[/bold] Writing {len(frame):,} scheme rows to {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False)
    console.print(f"Wrote joint suggestions for {len(frame):,} scheme rows to {output}")

    for scheme in sorted(occurrences):
        total = occurrences[scheme]
        reviewed = reviewed_decisions.get(scheme, 0)
        already = reviewed_coded_occurrences.get(scheme, 0)
        fresh = suggested_occurrences.get(scheme, 0)
        if total:
            console.print(
                f"  {scheme}: {reviewed:,} reviewed decisions; "
                f"{already:,} reviewed codes and {fresh:,} newly suggested codes of "
                f"{total:,} source-category occurrences "
                f"({(already + fresh) / total:.1%} carrying a code)"
            )
        else:
            console.print(f"  {scheme}: no source-category occurrences recorded")
    for status, count in sorted(counts.items()):
        console.print(f"  first pass {status}: {count:,}")
    if recheck and "recheck_status" in frame.columns:
        for status, count in sorted(frame["recheck_status"].value_counts().items()):
            if status:
                console.print(f"  recheck {status}: {int(count):,}")
        if set(frame["recheck_status"]) <= {"NO_PEERS", ""}:
            console.print(
                "[yellow]  no row had peer evidence yet: only reviewed codes and accepted "
                "suggestions cross a taxonomy boundary[/yellow]"
            )
        joint_counts = (
            frame[["joint_key", "joint_status"]].drop_duplicates()["joint_status"].value_counts()
        )
        for status, count in sorted(joint_counts.items()):
            console.print(f"  joint {status}: {int(count):,}")


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


def _bootstrap_express_taxonomy(
    scheme: str,
    version: str,
    *,
    token: str | None,
    reference_dir: Path,
) -> Taxonomy:
    """Load an existing tree, rebuild it locally, or finally fetch it."""
    path = reference_dir / f"{scheme}_{version}" / "nodes.parquet"
    if path.is_file():
        taxonomy = Taxonomy.load(path)
        if (taxonomy.scheme, taxonomy.version) != (scheme, version):
            console.print(
                f"[red]{path} contains {taxonomy.scheme} {taxonomy.version}, "
                f"but {scheme} {version} was requested[/red]"
            )
            raise typer.Exit(1)
        console.print(
            f"Reusing {scheme.upper()} {version} taxonomy: [dim]{path}[/dim]"
        )
        return taxonomy

    local_source = find_local_taxonomy_source(reference_dir, scheme, version)
    if local_source is not None:
        console.print(
            f"Building missing {scheme.upper()} {version} taxonomy from "
            f"[dim]{local_source}[/dim]"
        )
        try:
            taxonomy, report = _import_workbook(
                path=local_source,
                scheme=scheme,
                version=version,
                source_url=OFFICIAL_FILES.get((scheme, version), ""),
                strict=True,
                strict_levels=False,
            )
        except TaxonomyError as exc:
            console.print(
                f"[yellow]Local taxonomy source could not be normalized:[/yellow] {exc}"
            )
        else:
            _report_discards(report)
            _warn_level_gaps(taxonomy.structural_report())
            if (scheme, version) in EXPECTED_STRUCTURE:
                _require_official_structure(
                    taxonomy, allow_structure_deviation=False
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            taxonomy.save(path)
            console.print(
                f"Wrote {len(taxonomy.nodes):,} {scheme.upper()} nodes to {path}"
            )
            return taxonomy

    console.print(
        f"No reusable local tree/source for {scheme.upper()} {version}; fetching it..."
    )
    fetch_taxonomy(
        scheme=scheme,
        version=version,
        output=path,
        token=token,
        allow_orphan_nodes=False,
        strict_levels=False,
        level_column=None,
        allow_structure_deviation=False,
    )
    return _load_taxonomy(scheme, version)


@app.command("express")
def express(
    input_path: Annotated[
        Path, typer.Argument(help="openplaces-ph canonical_pois.parquet")
    ],
    output: Annotated[
        Path | None,
        typer.Option(help="Final QGIS-ready GeoParquet"),
    ] = None,
    schemes: Annotated[
        str,
        typer.Option(
            help="Comma-separated schemes; defaults to the joint PSIC, PCPC and PSCC workflow"
        ),
    ] = "psic,pcpc,pscc",
    product_column: Annotated[
        str | None,
        typer.Option(
            help=(
                "Product/commodity text passed through to classify. With Express's "
                "deterministic llm=none default, this does not itself infer a taxonomy code"
            )
        ),
    ] = None,
    promote_unchecked: Annotated[
        bool,
        typer.Option(
            "--promote-unchecked/--no-promote-unchecked",
            help=(
                "Promote a first-pass suggestion when the peer recheck reached no verdict "
                "for it. Use --no-promote-unchecked to accept only suggestions the recheck "
                "actually cleared"
            ),
        ),
    ] = True,
    hold_inconclusive: Annotated[
        bool,
        typer.Option(
            "--hold-inconclusive/--no-hold-inconclusive",
            help=(
                "Hold a suggestion the peer recheck argued against without being decisive "
                "(SHIFT_WEAK), while still promoting suggestions the recheck never saw. "
                "Candidate-only shifts remain diagnostic"
            ),
        ),
    ] = False,
    workers: Annotated[
        int,
        typer.Option(
            min=1,
            help="Parallel worker processes for deterministic classification",
        ),
    ] = 1,
    with_status: Annotated[
        bool,
        typer.Option(
            "--with-status/--no-with-status",
            help="Include <scheme>_status columns in the QGIS layer",
        ),
    ] = True,
    with_canonical_name: Annotated[
        bool,
        typer.Option(
            "--with-canonical-name/--no-with-canonical-name",
            help="Include canonical_name in the final QGIS layer",
        ),
    ] = False,
    token: Annotated[
        str | None,
        typer.Option(
            help=(
                "PSA classification API token if PCPC must be fetched because neither "
                "its nodes nor its local source workbook exists"
            )
        ),
    ] = None,
):
    """Run the normal OpenPlaces-to-QGIS workflow with sensible automatic defaults.

    Express is intentionally an orchestration layer. It reuses the existing taxonomy
    importer, joint crosswalk workflow, classifier, and GIS exporter rather than defining
    a second classification path.
    """
    if not input_path.is_file():
        raise typer.BadParameter(f"input does not exist: {input_path}")

    selected: list[str] = []
    for raw in schemes.split(","):
        if not raw.strip():
            continue
        scheme = _check_scheme(raw.strip())
        if scheme not in selected:
            selected.append(scheme)
    if not selected:
        raise typer.BadParameter("no scheme selected")
    # Sorted so that --schemes pcpc,psic and --schemes psic,pcpc are one cached run.
    selected.sort()

    if len(selected) == 1:
        console.print(
            "[yellow]One scheme selected: the peer recheck has no peer taxonomy to compare "
            "against, so every suggestion will be unchecked.[/yellow]"
        )

    reference_dir = Path("reference")
    versions = {scheme: DEFAULT_VERSIONS[scheme] for scheme in selected}
    taxonomies: dict[str, Taxonomy] = {}
    for scheme in selected:
        taxonomies[scheme] = _bootstrap_express_taxonomy(
            scheme,
            versions[scheme],
            token=token,
            reference_dir=reference_dir,
        )

    crosswalk_dir = reference_dir / "crosswalks"
    joint_reference = crosswalk_dir / "joint.csv"
    # The seed path can fall back from joint.csv to legacy per-scheme files. Fingerprint
    # both so changing either possible source invalidates the cached express run.
    reference_states = [
        file_state(joint_reference),
        *[
            file_state(crosswalk_dir / f"{scheme}_{versions[scheme]}.csv")
            for scheme in selected
        ],
    ]

    states = [
        f"package:{__version__}",
        f"schemes:{','.join(selected)}",
        f"product_column:{product_column or ''}",
        f"promote_unchecked:{bool(promote_unchecked)}",
        f"hold_inconclusive:{bool(hold_inconclusive)}",
        pipeline_state(),
        *[
            f"taxonomy:{scheme}:{versions[scheme]}:{taxonomies[scheme].fingerprint}"
            for scheme in selected
        ],
        *reference_states,
    ]
    key = input_cache_key(input_path, states)
    work_dir = Path("data") / "work" / "express" / key
    run_dir = Path("output") / "express" / key

    worklist = work_dir / "joint.csv"
    suggested = work_dir / "joint_suggested.csv"
    automatic = work_dir / "joint_auto.csv"

    if csv_ready(automatic, EXPRESS_AUTOMATIC_COLUMNS):
        console.print(f"Reusing automatic joint crosswalk: [dim]{automatic}[/dim]")
    else:
        if csv_ready(suggested, EXPRESS_SUGGESTION_COLUMNS):
            console.print(f"Reusing joint suggestions: [dim]{suggested}[/dim]")
        else:
            if joint_reference.is_file() and not reference_is_joint_format(joint_reference):
                console.print(
                    f"[yellow]{joint_reference} is not a usable joint worklist; leaving it "
                    f"to the legacy adoption path in crosswalk-init[/yellow]"
                )
            elif not worklist.exists() and seed_joint_worklist(
                joint_reference, worklist, schemes=set(selected)
            ):
                console.print(
                    f"Seeded express worklist from reviewed mappings in "
                    f"[dim]{joint_reference}[/dim]"
                )

            crosswalk_init(
                input_path=input_path,
                output=worklist,
                schemes=",".join(selected),
                version=None,
                adopt_legacy=True,
            )
            crosswalk_suggest(
                input_path=worklist,
                output=suggested,
                taxonomy_paths=None,
                top_n=SUGGEST_PARAMS["top_n"],
                min_score=SUGGEST_PARAMS["min_score"],
                min_margin=SUGGEST_PARAMS["min_margin"],
                recheck=SUGGEST_PARAMS["recheck"],
                batch_size=SUGGEST_PARAMS["batch_size"],
                show_progress=True,
            )

        promote_auto_suggestions(
            suggested,
            automatic,
            promote_unchecked=promote_unchecked,
            hold_inconclusive=hold_inconclusive,
        )

    # Report the same evidence state on both a fresh run and a resumed run. Otherwise a
    # reused AUTO_ACCEPTED_UNCHECKED crosswalk would silently lose its warning.
    summary = summarize_automatic_crosswalk(automatic)
    console.print(
        "Express crosswalk: "
        f"{summary['reviewed']:,} reviewed, "
        f"{promoted_total(summary):,} auto-accepted, "
        f"{summary['held_recheck'] + summary['held_inconclusive'] + summary['held_no_verdict']:,}"
        " held, "
        f"{summary['unresolved']:,} unresolved"
    )
    console.print(
        f"  accepted: {summary['promoted_cleared']:,} cleared by the recheck, "
        f"{summary['promoted_inconclusive']:,} despite an inconclusive recheck, "
        f"{summary['promoted_unchecked']:,} with no verdict"
    )
    console.print(
        f"  held: {summary['held_recheck']:,} contradicted, "
        f"{summary['held_inconclusive']:,} inconclusive, "
        f"{summary['held_no_verdict']:,} for want of a verdict"
    )
    if summary["without_code"]:
        console.print(
            f"  of the promotions, {summary['without_code']:,} assign no taxonomy code "
            f"(NOT_ACTIVITY and similar kinds)"
        )
    if summary["held_unclearable"]:
        console.print(
            f"[yellow]  {summary['held_unclearable']:,} held rows assign no code; the peer "
            f"recheck cannot clear them. Review directly or allow unchecked promotion.[/yellow]"
        )
    if promoted_total(summary) and summary["promoted_cleared"] == 0:
        console.print(
            "[yellow]No automatic mapping in this run was cleared by the peer recheck. "
            "Review joint_auto.csv before trusting the layer, or rerun with "
            "--no-promote-unchecked.[/yellow]"
        )

    # Checked on both paths: a reused crosswalk can be just as empty as a fresh one.
    if not crosswalk_has_mappings(automatic):
        console.print(
            "[red]No reviewed or automatic mapping survived; classification would "
            f"produce an empty layer. Review {suggested} before continuing.[/red]"
        )
        raise typer.Exit(1)

    if classification_run_ready(run_dir):
        console.print(f"Reusing completed classification run: [dim]{run_dir}[/dim]")
    else:
        classify(
            input_path=input_path,
            output_dir=run_dir,
            schemes=",".join(selected),
            version=None,
            crosswalk=[automatic],
            llm=CLASSIFY_PARAMS["llm"],
            model=None,
            passes=CLASSIFY_PARAMS["passes"],
            product_column=product_column,
            limit=None,
            workers=workers,
            cache_path=run_dir / "decisions.sqlite",
            fail_on_ambiguous_crosswalk=CLASSIFY_PARAMS["fail_on_ambiguous_crosswalk"],
        )

    final_output = output or default_output_path(input_path)
    try:
        written = export_gis_run(
            run_dir,
            output_path=final_output,
            reference_dir=reference_dir,
            include_status=with_status,
            include_canonical_name=with_canonical_name,
            check_taxonomy_fingerprint=True,
        )
    except GISExportError as exc:
        console.print(f"[red]GIS export failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[green]Express complete:[/green] [bold]{written}[/bold]")


@app.command("classify")
def classify(
    input_path: Annotated[Path, typer.Argument(help="openplaces-ph canonical_pois.parquet")],
    output_dir: Annotated[Path, typer.Option()] = Path("output"),
    schemes: Annotated[str, typer.Option(help="Comma-separated: psic,pcpc,pscc")] = (
        "psic,pcpc,pscc"
    ),
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
    workers: Annotated[
        int,
        typer.Option(
            min=1,
            help="Parallel worker processes for deterministic classification",
        ),
    ] = 1,
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
    role_cw = RoleCrosswalk.load(crosswalk or []) if crosswalk else None
    backend = _make_backend(llm, model)
    if workers > 1 and backend is not None:
        raise typer.BadParameter("--workers > 1 currently supports --llm none only")
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

        if workers > 1:
            console.print(f"Using up to [bold]{workers}[/bold] deterministic worker processes")
        classifications, pois = classify_openplaces(
            input_path,
            classifiers,
            output_dir,
            product_column=product_column,
            limit=limit,
            workers=workers,
            role_crosswalk=role_cw,
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


@app.command("gis-export")
def gis_export(
    run_dir: Annotated[Path, typer.Argument(help="PlaceType classification output directory")],
    output: Annotated[
        Path | None, typer.Option(help="Default: <run_dir>/gis.parquet")
    ] = None,
    reference_dir: Annotated[
        Path, typer.Option(help="Taxonomy reference root")
    ] = Path("reference"),
    with_status: Annotated[
        bool,
        typer.Option(
            "--with-status/--no-with-status",
            help="Also export <scheme>_status so an empty code can be explained in QGIS",
        ),
    ] = False,
    with_canonical_name: Annotated[
        bool,
        typer.Option(
            "--with-canonical-name/--no-with-canonical-name",
            help="Also export canonical_name for labeling and inspection in QGIS",
        ),
    ] = False,
    check_taxonomy_fingerprint: Annotated[
        bool,
        typer.Option(
            help="Require the reference taxonomy to match the fingerprint recorded by the run"
        ),
    ] = True,
):
    """Export a compact GeoParquet layer for direct use in QGIS."""
    try:
        written = export_gis_run(
            run_dir,
            output_path=output,
            reference_dir=reference_dir,
            include_status=with_status,
            include_canonical_name=with_canonical_name,
            check_taxonomy_fingerprint=check_taxonomy_fingerprint,
        )
    except GISExportError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"Wrote [bold]{written}[/bold]")


if __name__ == "__main__":
    app()
