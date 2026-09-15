from __future__ import annotations

import json
import warnings
from collections.abc import Mapping
from dataclasses import asdict, fields
from datetime import UTC, datetime
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
_MAX_AMBIGUITY_DETAILS = 50


def _iter_rows(frame: pd.DataFrame):
    """Yield plain dicts in chunks to reduce per-row conversion overhead.

    `DataFrame.iterrows` builds a Series per row and dominates the deterministic run on
    a large build. Chunked `to_dict("records")` is far cheaper. The current pipeline still
    materializes the input frame and result table as a whole; true streaming Parquet I/O is
    a separate scaling improvement.
    """
    for start in range(0, len(frame), _CHUNK):
        yield from frame.iloc[start : start + _CHUNK].to_dict("records")


def classify_openplaces(
    input_path: str | Path,
    classifiers: Mapping[str, EntityClassifier],
    output_dir: str | Path,
    product_column: str | None = None,
    limit: int | None = None,
) -> tuple[Path, Path]:
    frame = read_openplaces(input_path)
    for classifier in classifiers.values():
        classifier.reset_run_diagnostics()
    if limit is not None:
        frame = frame.head(limit).copy()
    if product_column is not None and product_column not in frame.columns:
        raise ValueError(f"product column {product_column!r} not found")

    rows: list[dict] = []
    for row in _iter_rows(frame):
        product_text = None if product_column is None else clean_text(row.get(product_column))
        for scheme, classifier in classifiers.items():
            result = classifier.classify_row(
                row, product_text=product_text if scheme in {"pcpc", "pscc"} else None
            )
            payload = asdict(result)
            for key in _JSON_FIELDS:
                payload[key] = json.dumps(
                    payload[key], ensure_ascii=False, separators=(",", ":")
                )
            rows.append(payload)

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
