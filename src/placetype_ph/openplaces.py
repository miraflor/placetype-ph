from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from .models import SourceEvidence
from .text import clean_text, combine_text

SOURCES = ("fsq", "overture", "osm")

# A row may arrive as a pandas Series or as a plain dict. Both expose __getitem__ and
# .get, and dicts are far cheaper to produce in bulk than Series.
Row = Mapping[str, Any]

# Columns copied into the convenience spatial table when present.
SUMMARY_COLUMNS = {
    "canonical_id",
    "canonical_name",
    "name",
    "lon",
    "lat",
    "longitude",
    "latitude",
    "geometry",
}


def _truthy(value: object) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        # pd.isna raises on arrays; anything array-like is not a scalar flag.
        pass
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "y", "t"}
    return bool(value)


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


class OpenPlacesSchemaError(ValueError):
    pass


def read_openplaces(path: str | Path) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    if "canonical_id" not in frame.columns:
        raise OpenPlacesSchemaError("canonical_pois.parquet must contain canonical_id")
    ids = frame["canonical_id"]
    if ids.isna().any() or ids.astype(str).str.strip().eq("").any() or ids.duplicated().any():
        raise OpenPlacesSchemaError("canonical_id must be non-null, non-blank, and unique")
    has_base_semantics = any(
        f"{source}_name" in frame.columns or f"{source}_category" in frame.columns
        for source in SOURCES
    )
    has_structured_overture = any(
        field in frame.columns
        for field in (
            "overture_primary_category",
            "overture_alternate_category",
            "overture_basic_category",
        )
    )
    # Older outputs carry one ``category`` with a ``sources`` list; the classifier infers
    # the per-source fields from them (``infer_legacy_source_fields``).
    has_legacy = {"category", "sources"} <= set(frame.columns)
    if not (has_base_semantics or has_structured_overture or has_legacy):
        raise OpenPlacesSchemaError("no FSQ, Overture, or OSM semantic fields found")
    return frame


def source_evidence(row: Row, *, include_structured_overture: bool = True) -> list[SourceEvidence]:
    overture_from_fsq = _truthy(row.get("overture_has_foursquare_provenance", False))
    evidence: list[SourceEvidence] = []
    for source in SOURCES:
        name = clean_text(row.get(f"{source}_name"))
        category = clean_text(row.get(f"{source}_category"))
        if source == "overture" and not category and include_structured_overture:
            # Keep PCPC/PSCC independent while recognizing structured source fields.
            # PSIC uses a separate typed expansion of primary and alternate labels.
            for field in (
                "overture_primary_category",
                "overture_alternate_category",
                "overture_basic_category",
            ):
                category = clean_text(row.get(field))
                if category:
                    break
        if not name and not category:
            continue
        if source in {"fsq", "overture"} and overture_from_fsq:
            dep = "fsq-lineage"
        else:
            dep = source
        evidence.append(SourceEvidence(source, category, name, dep))
    return evidence


_STRUCTURED_OVERTURE = (
    "overture_primary_category",
    "overture_alternate_category",
    "overture_basic_category",
)


def infer_legacy_source_fields(row: Row) -> tuple[Row, list[str]]:
    """Per-source fields for an older row that has only ``category`` and ``sources``.

    Rows written before per-source fields existed carry one ``category`` and a
    ``sources`` list such as ``osm`` or ``osm|overture``. A single source receives the
    category and name directly. With OSM and Overture together, ``key=value`` parts go to
    OSM and the other parts to Overture. Rows that already have any per-source field are
    returned unchanged. The returned flags record what was inferred.
    """
    if any(
        clean_text(row.get(f"{source}_{field}"))
        for source in SOURCES
        for field in ("name", "category")
    ) or any(clean_text(row.get(field)) for field in _STRUCTURED_OVERTURE):
        return row, []
    sources = {
        part.strip().casefold()
        for part in str(row.get("sources") or "").split("|")
        if part.strip()
    }
    category = clean_text(row.get("category"))
    name = clean_text(row.get("name")) or clean_text(row.get("canonical_name"))
    if not sources or not (category or name):
        return row, []
    inferred: dict[str, Any] = {}
    flag = "LEGACY_SOURCE_FIELDS_INFERRED"
    single = sources if len(sources) == 1 else set()
    if single and next(iter(single)) in SOURCES:
        source = next(iter(single))
        inferred = {f"{source}_name": name, f"{source}_category": category}
    elif {"osm", "overture"} <= sources:
        parts = [p.strip() for p in (category or "").split(" | ") if p.strip()]
        osm = " | ".join(p for p in parts if "=" in p)
        overture = " | ".join(p for p in parts if "=" not in p)
        if osm:
            inferred.update(osm_name=name, osm_category=osm)
        if overture:
            inferred.update(overture_name=name, overture_category=overture)
        flag = (
            "LEGACY_SOURCE_FIELDS_PARTIALLY_RECOVERED"
            if inferred
            else "LEGACY_MERGED_SEMANTICS_UNRECOVERABLE"
        )
    else:
        return row, []
    return {**row, **{k: v for k, v in inferred.items() if v}}, [flag]


def evidence_text(row: Row) -> str:
    parts: list[str] = []
    for source in SOURCES:
        name = clean_text(row.get(f"{source}_name"))
        category = clean_text(row.get(f"{source}_category"))
        if name:
            parts.append(f"{source.upper()} name: {name}")
        if category:
            parts.append(f"{source.upper()} category: {category}")
    if not parts:
        parts.append(f"canonical name: {combine_text(row.get('canonical_name'))}")
    return "\n".join(parts)


def entity_match_flags(row: Row, semantic_conflict: bool) -> list[str]:
    flags: list[str] = []
    if not semantic_conflict:
        return flags
    if _truthy(row.get("completed_transitively", False)):
        flags.append("ENTITY_MATCH_SUSPECT_TRANSITIVE")
    # OpenPlaces cluster columns, or the pairwise columns of older canonical outputs.
    max_dist = _as_float(row.get("cluster_max_pair_distance_m"))
    if max_dist is None:
        max_dist = _as_float(row.get("match_distance_m"))
    if max_dist is not None and max_dist >= 80:
        flags.append("ENTITY_MATCH_SUSPECT_DISTANCE")
    match_score = _as_float(row.get("match_score_min"))
    if match_score is None:
        match_score = _as_float(row.get("match_name_score"))
    if match_score is not None and match_score < 0.85:
        flags.append("ENTITY_MATCH_SUSPECT_LOW_MATCH_SCORE")
    return flags


def json_list(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))
