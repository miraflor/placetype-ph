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
    if (
        ids.isna().any()
        or ids.astype(str).str.strip().eq("").any()
        or ids.duplicated().any()
    ):
        raise OpenPlacesSchemaError("canonical_id must be non-null, non-blank, and unique")
    if not any(f"{s}_name" in frame.columns or f"{s}_category" in frame.columns for s in SOURCES):
        raise OpenPlacesSchemaError("no FSQ, Overture, or OSM semantic fields found")
    return frame


def source_evidence(row: Row) -> list[SourceEvidence]:
    overture_from_fsq = _truthy(row.get("overture_has_foursquare_provenance", False))
    evidence: list[SourceEvidence] = []
    for source in SOURCES:
        name = clean_text(row.get(f"{source}_name"))
        category = clean_text(row.get(f"{source}_category"))
        if not name and not category:
            continue
        if source in {"fsq", "overture"} and overture_from_fsq:
            dep = "fsq-lineage"
        else:
            dep = source
        evidence.append(SourceEvidence(source, category, name, dep))
    return evidence


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
    max_dist = _as_float(row.get("cluster_max_pair_distance_m"))
    if max_dist is not None and max_dist >= 80:
        flags.append("ENTITY_MATCH_SUSPECT_DISTANCE")
    match_score = _as_float(row.get("match_score_min"))
    if match_score is not None and match_score < 0.85:
        flags.append("ENTITY_MATCH_SUSPECT_LOW_MATCH_SCORE")
    return flags


def json_list(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))
