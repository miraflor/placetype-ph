"""Add auditable specialized columns to an existing, complete POI classification."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from pathlib import Path

import pandas as pd

from .policy import as_columns, assess, fingerprint
from .registry import RegistryRecord, load_registry

ADDED_COLUMNS = (
    "specialized_status", "specialized_candidate_codes", "specialized_reason",
    "specialized_flags", "administrative_status", "administrative_candidate_code",
    "administrative_reason", "ownership_candidate", "ownership_status",
    "ownership_reason", "registry_status", "registry_reference", "registry_basis",
    "specialized_audit",
)


def enrich_summary(summary: pd.DataFrame, frame: pd.DataFrame,
                   *, registry: tuple[RegistryRecord, ...] = ()) -> pd.DataFrame:
    """One-to-one, no label overwrite and no dropped POIs. Fail on schema ambiguity."""
    if not {"canonical_id"} <= set(summary.columns) or "canonical_id" not in frame.columns:
        raise ValueError("both input frames must carry canonical_id")
    if not summary["canonical_id"].is_unique or not frame["canonical_id"].is_unique:
        raise ValueError("canonical_id must be unique in both inputs")
    if any(column in summary.columns for column in ADDED_COLUMNS):
        raise ValueError("specialized output columns already present; refusing to overwrite")
    original = summary.copy()
    # Source is the entity input, not the merged summary, which may have lost raw tags.
    by_id = {str(row["canonical_id"]): row for row in frame.to_dict("records")}
    rows: list[dict[str, object]] = []
    for record in original.to_dict("records"):
        canonical_id = str(record["canonical_id"])
        if canonical_id not in by_id:
            raise ValueError(f"classification output refers to a missing input entity: {canonical_id}")
        original_code = record.get("psic_code")
        original_method = record.get("psic_method")
        a = assess(by_id[canonical_id], psic_code="" if pd.isna(original_code) else str(original_code),
                   psic_method="" if pd.isna(original_method) else str(original_method), registry=registry)
        rows.append({"canonical_id": record["canonical_id"], **as_columns(a)})
    extra = pd.DataFrame.from_records(rows, columns=("canonical_id", *ADDED_COLUMNS))
    if extra.empty:
        extra = pd.DataFrame(columns=("canonical_id", *ADDED_COLUMNS))
    enriched = original.merge(extra, on="canonical_id", how="left", validate="one_to_one", sort=False)
    if len(enriched) != len(original) or enriched["canonical_id"].tolist() != original["canonical_id"].tolist():
        raise RuntimeError("specialized enrichment changed the canonical entity sequence")
    for key in original.columns:
        if not enriched[key].equals(original[key]):
            raise RuntimeError(f"specialized enrichment modified existing column {key}")
    return enriched


def report(summary: pd.DataFrame) -> dict[str, object]:
    return {
        "policy": fingerprint(),
        "rows": int(len(summary)),
        "specialized_status": dict(sorted(Counter(summary["specialized_status"].fillna("")).items())),
        "ownership_status": dict(sorted(Counter(summary["ownership_status"].fillna("")).items())),
        "registry_status": dict(sorted(Counter(summary["registry_status"].fillna("")).items())),
    }
