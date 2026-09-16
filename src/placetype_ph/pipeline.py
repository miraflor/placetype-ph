from __future__ import annotations

import json
import warnings
from collections.abc import Mapping
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, fields
from datetime import UTC, datetime
from multiprocessing import get_context
from pathlib import Path

import pandas as pd

from . import __version__
from .classifier import EntityClassifier
from .models import ClassificationResult
from .openplaces import SUMMARY_COLUMNS, read_openplaces
from .text import clean_text

_JSON_FIELDS = ("candidate_codes", "evidence_sources", "flags", "audit")
_RESULT_COLUMNS = [f.name for f in fields(ClassificationResult)]
_CHUNK = 10_000
_MIN_PARALLEL_CHUNK = 1_000
_MAX_AMBIGUITY_DETAILS = 50

_PROCESS_CLASSIFIERS: dict[str, EntityClassifier] | None = None
_PROCESS_PRODUCT_COLUMN: str | None = None


def _iter_row_chunks(frame: pd.DataFrame, chunk_size: int = _CHUNK):
    """Yield plain row dicts in chunks without constructing a Series per row."""
    for start in range(0, len(frame), chunk_size):
        yield frame.iloc[start : start + chunk_size].to_dict("records")


def _iter_rows(frame: pd.DataFrame):
    """Yield plain dicts in chunks to reduce per-row conversion overhead.

    `DataFrame.iterrows` builds a Series per row and dominates the deterministic run on
    a large build. Chunked `to_dict("records")` is far cheaper. The current pipeline still
    materializes the input frame and result table as a whole; true streaming Parquet I/O is
    a separate scaling improvement.
    """
    for chunk in _iter_row_chunks(frame):
        yield from chunk


def _serialize_result(result: ClassificationResult) -> dict:
    payload = asdict(result)
    for key in _JSON_FIELDS:
        payload[key] = json.dumps(payload[key], ensure_ascii=False, separators=(",", ":"))
    return payload


def _classify_records(
    records: list[dict],
    classifiers: Mapping[str, EntityClassifier],
    product_column: str | None,
) -> list[dict]:
    rows: list[dict] = []
    for row in records:
        product_text = None if product_column is None else clean_text(row.get(product_column))
        for scheme, classifier in classifiers.items():
            result = classifier.classify_row(
                row, product_text=product_text if scheme in {"pcpc", "pscc"} else None
            )
            rows.append(_serialize_result(result))
    return rows


def _diagnostics_snapshot(
    classifiers: Mapping[str, EntityClassifier],
) -> dict[str, tuple[dict[str, int], int]]:
    return {
        scheme: (dict(classifier.ambiguities), int(classifier.ambiguity_rows))
        for scheme, classifier in classifiers.items()
    }


def _merge_diagnostics(
    classifiers: Mapping[str, EntityClassifier],
    diagnostics: Mapping[str, tuple[dict[str, int], int]],
) -> None:
    for scheme, (ambiguities, ambiguity_rows) in diagnostics.items():
        classifier = classifiers[scheme]
        classifier.ambiguity_rows += ambiguity_rows
        for detail, count in ambiguities.items():
            classifier.ambiguities[detail] = classifier.ambiguities.get(detail, 0) + count


def _init_process_worker(
    classifiers: dict[str, EntityClassifier], product_column: str | None
) -> None:
    global _PROCESS_CLASSIFIERS, _PROCESS_PRODUCT_COLUMN
    _PROCESS_CLASSIFIERS = classifiers
    _PROCESS_PRODUCT_COLUMN = product_column


def _classify_chunk_in_worker(
    records: list[dict],
) -> tuple[list[dict], dict[str, tuple[dict[str, int], int]]]:
    if _PROCESS_CLASSIFIERS is None:
        raise RuntimeError("parallel classifier worker was not initialized")
    for classifier in _PROCESS_CLASSIFIERS.values():
        classifier.reset_run_diagnostics()
    rows = _classify_records(records, _PROCESS_CLASSIFIERS, _PROCESS_PRODUCT_COLUMN)
    return rows, _diagnostics_snapshot(_PROCESS_CLASSIFIERS)


def _parallel_chunk_size(row_count: int, workers: int) -> int:
    """Create enough chunks to balance workers without excessive IPC overhead."""
    if row_count <= 0:
        return _CHUNK
    target_chunks = max(1, workers * 4)
    balanced = (row_count + target_chunks - 1) // target_chunks
    return max(_MIN_PARALLEL_CHUNK, min(_CHUNK, balanced))


def classify_openplaces(
    input_path: str | Path,
    classifiers: Mapping[str, EntityClassifier],
    output_dir: str | Path,
    product_column: str | None = None,
    limit: int | None = None,
    workers: int = 1,
) -> tuple[Path, Path]:
    if workers < 1:
        raise ValueError("workers must be at least 1")
    if workers > 1:
        unsafe = [
            scheme
            for scheme, classifier in classifiers.items()
            if classifier.traverser is not None or classifier.cache is not None
        ]
        if unsafe:
            joined = ", ".join(unsafe)
            raise ValueError(
                "parallel workers currently support deterministic classifiers only; "
                f"disable LLM/cache for: {joined}"
            )

    frame = read_openplaces(input_path)
    for classifier in classifiers.values():
        classifier.reset_run_diagnostics()
    if limit is not None:
        frame = frame.head(limit).copy()
    if product_column is not None and product_column not in frame.columns:
        raise ValueError(f"product column {product_column!r} not found")

    rows: list[dict] = []
    if workers == 1 or frame.empty:
        for chunk in _iter_row_chunks(frame):
            rows.extend(_classify_records(chunk, classifiers, product_column))
    else:
        chunk_size = _parallel_chunk_size(len(frame), workers)
        chunk_count = (len(frame) + chunk_size - 1) // chunk_size
        pool_workers = min(workers, chunk_count)
        # Spawn is explicit so CI exercises the same serialization boundary used on Windows.
        # Each process receives its own classifier state; only compact diagnostics are merged.
        with ProcessPoolExecutor(
            max_workers=pool_workers,
            mp_context=get_context("spawn"),
            initializer=_init_process_worker,
            initargs=(dict(classifiers), product_column),
        ) as executor:
            for chunk_rows, diagnostics in executor.map(
                _classify_chunk_in_worker,
                _iter_row_chunks(frame, chunk_size),
                chunksize=1,
            ):
                rows.extend(chunk_rows)
                _merge_diagnostics(classifiers, diagnostics)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    classifications = out_dir / "entity_classifications.parquet"
    # An empty input must still produce a well-formed table rather than a KeyError.
    results = pd.DataFrame(rows, columns=_RESULT_COLUMNS)
    results.to_parquet(classifications, index=False)

    # Keep the OpenPlaces geometry and identity layer separate; add only compact summary
    # fields here.
    summary = frame[[c for c in frame.columns if c in SUMMARY_COLUMNS]].copy()
    for scheme in classifiers:
        part = results[results["scheme"] == scheme][
            ["canonical_id", "code", "level", "status"]
        ].copy()
        part = part.rename(columns={c: f"{scheme}_{c}" for c in ("code", "level", "status")})
        summary = summary.merge(part, on="canonical_id", how="left")
    summary_path = out_dir / "classified_pois.parquet"
    summary.to_parquet(summary_path, index=False)

    # Preserve GeoParquet metadata from OpenPlaces when a geometry column is retained.
    # Failure is non-fatal because lon/lat remain available, but it must not be silent:
    # downstream GIS users need to know whether `classified_pois.parquet` is GeoParquet.
    geo_metadata_status = "not_applicable"
    if "geometry" in summary.columns:
        geo_metadata_status = "source_has_no_geo_metadata"
        try:
            import pyarrow.parquet as pq

            source_meta = pq.read_schema(input_path).metadata or {}
            if b"geo" in source_meta:
                table = pq.read_table(summary_path)
                meta = dict(table.schema.metadata or {})
                meta[b"geo"] = source_meta[b"geo"]
                pq.write_table(table.replace_schema_metadata(meta), summary_path)
                geo_metadata_status = "preserved"
        except Exception as exc:  # metadata preservation is best-effort, classification is not
            geo_metadata_status = f"failed:{type(exc).__name__}"
            warnings.warn(
                f"could not preserve GeoParquet metadata: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )

    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "package_version": __version__,
        "input": str(Path(input_path)),
        "input_rows": int(len(frame)),
        "product_column": product_column,
        "workers": workers,
        "schemes": {
            scheme: {
                "version": classifier.taxonomy.version,
                "taxonomy_fingerprint": classifier.taxonomy.fingerprint,
                "nodes": len(classifier.taxonomy.nodes),
                "max_depth": classifier.taxonomy.max_depth,
                "applicable_crosswalk_entries": classifier.applicable_crosswalk_entries,
                "crosswalk_ambiguities": {
                    "rule_sets": len(classifier.ambiguities),
                    "rows_affected": classifier.ambiguity_rows,
                    "match_occurrences": sum(classifier.ambiguities.values()),
                    "details": sorted(classifier.ambiguities)[:_MAX_AMBIGUITY_DETAILS],
                },
                "llm_model": (
                    classifier.traverser.backend.model_name if classifier.traverser else None
                ),
                "llm_passes": classifier.traverser.passes if classifier.traverser else 0,
                "prompt_fingerprint": (
                    classifier.traverser.prompt_fingerprint if classifier.traverser else None
                ),
            }
            for scheme, classifier in classifiers.items()
        },
        "outputs": {
            "entity_classifications": classifications.name,
            "classified_pois": summary_path.name,
            "classified_pois_geo_metadata": geo_metadata_status,
        },
    }
    (out_dir / "run.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return classifications, summary_path
