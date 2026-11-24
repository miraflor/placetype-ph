"""Add user-supplied places, with optional explicit codes, to an OpenPlaces input file.

A user source is a CSV or Parquet file with at least ``source_id``, ``name``, ``lon`` and
``lat``. Each row either names an existing place in a ``canonical_id`` column (the row then
adds codes and columns to that place) or becomes a new place. Places are never matched by
distance or name: spatial reconciliation belongs to the canonical-layer build, and a
guessed match could attach codes to the wrong place.

Optional columns:

- ``explicit_psic_code``, ``explicit_io16_code``, ``explicit_io80_code``,
  ``explicit_io240_code`` (or the short names ``psic_code``, ``io16``, ``io80``, ``io240``):
  validated by the classification run, which also fills I-O parents and never infers a
  child code from a parent.
- source fields such as ``osm_category`` or ``overture_category``: used as source evidence;
  on an existing place they may fill empty fields or agree with existing evidence, never replace it.
- original source ID, name and coordinates: retained in source-qualified audit columns.
  At most one supplied row may target any existing canonical ID.
- any other column: kept as ``aug_<source>__<column>``.

The source label is added to ``sources`` as ``user:<source>``; a label already present is
refused, so the same file is not added twice.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import pandas as pd

SOURCE_RE = re.compile(r"[a-z][a-z0-9_]{0,39}")
RESERVED_SOURCES = frozenset({"osm", "overture", "fsq", "foursquare"})
REQUIRED = ("source_id", "name", "lon", "lat")
EXPLICIT = {
    "explicit_psic_code": ("explicit_psic_code", "psic_code"),
    "explicit_io16_code": ("explicit_io16_code", "io16"),
    "explicit_io80_code": ("explicit_io80_code", "io80"),
    "explicit_io240_code": ("explicit_io240_code", "io240"),
}
SOURCE_FIELDS = tuple(
    f"{source}_{field}"
    for source in ("osm", "overture", "fsq")
    for field in ("name", "category")
) + ("overture_primary_category", "overture_alternate_category", "overture_basic_category")


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def read_supplied(path: Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    elif path.suffix.lower() == ".parquet":
        frame = pd.read_parquet(path)
    else:
        raise ValueError("user places must be CSV or Parquet")
    _validate_supplied(frame)
    return frame


def _validate_supplied(frame: pd.DataFrame) -> None:
    if not frame.columns.is_unique:
        raise ValueError("user places have duplicate column names")
    missing = [c for c in REQUIRED if c not in frame.columns]
    if missing:
        raise ValueError(f"user places are missing required columns: {missing}")
    problems: list[str] = []
    seen: set[str] = set()
    targets: set[str] = set()
    for n, row in enumerate(frame.to_dict("records"), 2):
        sid = _text(row.get("source_id"))
        if not sid:
            problems.append(f"row {n}: empty source_id")
        elif sid in seen:
            problems.append(f"row {n}: duplicate source_id {sid!r}")
        seen.add(sid)
        target = _text(row.get("canonical_id"))
        if target:
            if target in targets:
                problems.append(f"row {n}: duplicate target canonical_id {target!r}")
            targets.add(target)
        if not _text(row.get("name")):
            problems.append(f"row {n}: empty name")
        try:
            lon, lat = float(row.get("lon")), float(row.get("lat"))
        except (TypeError, ValueError):
            problems.append(f"row {n}: lon/lat are not numbers")
            continue
        if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180
                and -90 <= lat <= 90):
            problems.append(f"row {n}: lon/lat out of range")
    if problems:
        shown = "; ".join(problems[:20])
        raise ValueError(f"{len(problems)} invalid user place row(s); nothing will be written: "
                         f"{shown}")


def _explicit(row: dict[str, Any]) -> dict[str, str]:
    out = {}
    for column, aliases in EXPLICIT.items():
        values = {_text(row.get(alias)) for alias in aliases} - {""}
        if len(values) > 1:
            raise ValueError(
                f"source_id={_text(row.get('source_id'))}: {column} given twice with "
                "different values"
            )
        out[column] = next(iter(values), "")
    return out


def augment_places(
    places: pd.DataFrame, supplied: pd.DataFrame, *, source: str
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return the places with the user source added, and a short report."""
    if not SOURCE_RE.fullmatch(source) or source in RESERVED_SOURCES:
        raise ValueError("source must match [a-z][a-z0-9_]{0,39} and not name a built-in source")
    _validate_supplied(supplied)
    if not places.columns.is_unique:
        raise ValueError("places have duplicate column names")
    if "canonical_id" not in places.columns:
        raise ValueError("places have no canonical_id column")
    ids = places["canonical_id"].map(_text)
    if (ids == "").any():
        raise ValueError("places have empty canonical_id values")
    if ids.duplicated().any():
        raise ValueError("places have duplicate canonical_id values")
    token = f"user:{source}"
    if "sources" in places.columns and places["sources"].fillna("").astype(str).map(
        lambda value: token in value.split("|")
    ).any():
        raise ValueError(f"source {source!r} already exists in these places; add it only once")
    out = places.copy().reset_index(drop=True)
    for column in (*EXPLICIT, "sources", *SOURCE_FIELDS):
        if column not in out.columns:
            if column in SOURCE_FIELDS and column not in supplied.columns:
                continue
            out[column] = ""
        out[column] = out[column].astype(object)
    position = {cid: i for i, cid in enumerate(ids)}
    passthrough = [
        c for c in supplied.columns
        if c not in {*REQUIRED, "canonical_id", *SOURCE_FIELDS}
        and c not in {a for aliases in EXPLICIT.values() for a in aliases}
    ]
    for column in (*REQUIRED, *passthrough):
        name = f"aug_{source}__{column}"
        if name in out.columns:
            raise ValueError(f"column collision: {name}")
        out[name] = None
    errors: list[str] = []
    new_rows: list[dict[str, Any]] = []
    matched = 0
    for row in supplied.to_dict("records"):
        sid = _text(row["source_id"])
        codes = _explicit(row)
        extras = {f"aug_{source}__{c}": row.get(c) for c in (*REQUIRED, *passthrough)}
        target = _text(row.get("canonical_id"))
        if target:
            if target not in position:
                errors.append(f"source_id={sid}: canonical_id {target!r} is not in the places")
                continue
            i = position[target]
            for column, value in codes.items():
                prior = _text(out.at[i, column])
                if value and prior and value != prior:
                    errors.append(f"source_id={sid}: {column} {prior} vs {value}")
                elif value:
                    out.at[i, column] = value
            for column in SOURCE_FIELDS:
                value = _text(row.get(column))
                if not value:
                    continue
                prior = _text(out.at[i, column])
                if prior and prior != value:
                    errors.append(f"source_id={sid}: conflicting {column} {prior!r} vs {value!r}")
                elif not prior:
                    out.at[i, column] = value
            prior_sources = _text(out.at[i, "sources"])
            out.at[i, "sources"] = "|".join(p for p in (prior_sources, token) if p)
            for column, value in extras.items():
                out.at[i, column] = value
            matched += 1
            continue
        record = {
            "canonical_id": f"user-{source}-{sid}",
            "canonical_name": _text(row["name"]),
            "lon": float(row["lon"]),
            "lat": float(row["lat"]),
            "sources": token,
            **{c: _text(row.get(c)) for c in SOURCE_FIELDS if _text(row.get(c))},
            **codes,
            **extras,
        }
        if record["canonical_id"] in position:
            errors.append(f"source_id={sid}: canonical_id {record['canonical_id']} exists")
            continue
        new_rows.append(record)
    if errors:
        raise ValueError(f"{len(errors)} conflicting or invalid row(s); nothing will be written: "
                         + "; ".join(errors[:20]))
    if new_rows:
        out = pd.concat([out, pd.DataFrame(new_rows)], ignore_index=True)
    for column in (*EXPLICIT, "sources"):
        out[column] = out[column].map(_text)
    return out, {"source": token, "input": len(supplied), "matched": matched,
                 "added": len(new_rows), "total": len(out)}
