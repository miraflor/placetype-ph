"""PSIC title search for places that the reviewed rules leave without a code.

For a place whose PSIC status is ``EMPTY`` (no reviewed rule, floor or terminal policy
applies), this module searches the PSIC titles with the source category, or with the
place name when there is no category. It reports what the search finds in separate
``psic_search_*`` columns. It never writes ``psic_code``: a search result is review
material, and a category becomes coded only through a reviewed crosswalk row.

The search uses the same query plan, guards and acceptance rule as ``placetype suggest``
(``suggest.prepare_suggestion`` and ``suggest.finalize_suggestion``): the top hit must
score at least 0.45 and lead the second hit by at least 0.12, and no guard may apply
(compound source category, mixed category, several branch roots, store-name suffix,
ambiguous pet shop, short query). A name never yields an acceptable code.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pandas as pd

from .openplaces import clean_text, infer_legacy_source_fields
from .retrieval import TaxonomyRetriever
from .reviewed_psic import psic_source_evidence
from .suggest import finalize_suggestion, prepare_suggestion
from .taxonomy import Taxonomy

POLICY = "psic-title-search-v1"
MIN_SCORE = 0.45
MIN_MARGIN = 0.12
TOP_N = 5
TITLE_SEARCH_COLUMNS = (
    "psic_search_status",
    "psic_search_code",
    "psic_search_basis",
    "psic_search_query",
    "psic_search_candidates",
    "psic_search_reason",
)
# Statuses, in the order used when several source categories give different results.
ACCEPTABLE = "ACCEPTABLE"
DISAGREE = "SOURCES_DISAGREE"
GUARDED = "GUARDED"
BELOW_THRESHOLD = "BELOW_THRESHOLD"
NO_CANDIDATES = "NO_CANDIDATES"
NAME_ONLY = "NAME_ONLY_CANDIDATES"
NOT_SEARCHED = ""
_RANK = {ACCEPTABLE: 0, GUARDED: 1, BELOW_THRESHOLD: 2, NO_CANDIDATES: 3}


def _queries(
    record: dict[str, Any], taxonomy: Taxonomy, cache: dict[tuple[str, str], Any]
) -> tuple[list[tuple[str, Any]], str]:
    """Category plans for each source category, or a name query when there is none."""
    plans = []
    record = dict(infer_legacy_source_fields(record)[0])
    for evidence in psic_source_evidence(record):
        if evidence.category:
            key = (evidence.source, evidence.category)
            if key not in cache:
                cache[key] = prepare_suggestion(taxonomy, evidence.source, evidence.category)
            prepared = cache[key]
            if prepared.terminal is None:
                plans.append((evidence.source, prepared))
    if plans:
        return plans, "category"
    for field in ("canonical_name", "name", "osm_name", "overture_name", "fsq_name"):
        name = clean_text(record.get(field))
        if name:
            return [("name", name)], "name"
    return [], ""


def _candidates(hits: list, limit: int = TOP_N) -> str:
    return "; ".join(f"{hit.code} ({hit.score:.3f})" for hit in hits[:limit])


def _category_outcome(taxonomy: Taxonomy, prepared: Any, hits: list) -> tuple[str, str, str]:
    """Status, acceptable code and reason for one prepared category query."""
    suggestion = finalize_suggestion(
        taxonomy, prepared, hits, min_score=MIN_SCORE, min_margin=MIN_MARGIN
    )
    source = suggestion.suggestion_source
    if "retrieval:strong_separated_hit" in source and suggestion.suggested_codes:
        return ACCEPTABLE, suggestion.suggested_codes, prepared.rule
    if prepared.block_reason is not None:
        return GUARDED, "", prepared.block_reason
    if not hits:
        return NO_CANDIDATES, "", prepared.rule
    return BELOW_THRESHOLD, "", prepared.rule


def title_search_columns(
    summary: pd.DataFrame,
    places: pd.DataFrame,
    taxonomy: Taxonomy,
    *,
    retriever: TaxonomyRetriever | None = None,
) -> pd.DataFrame:
    """Return ``summary`` with the ``psic_search_*`` columns added."""
    out = summary.copy()
    for column in TITLE_SEARCH_COLUMNS:
        out[column] = ""
    if out.empty or "psic_status" not in out.columns:
        return out
    status = out["psic_status"].fillna("").astype(str)
    code = out["psic_code"].fillna("").astype(str) if "psic_code" in out.columns else ""
    wanted = out.loc[(status == "EMPTY") & (code == ""), "canonical_id"].astype(str)
    if wanted.empty:
        return out
    wanted_ids = set(wanted)
    subset = places[places["canonical_id"].astype(str).isin(wanted_ids)]
    records = {str(r["canonical_id"]): r for r in subset.to_dict("records")}
    cache: dict[tuple[str, str], Any] = {}
    plans = {cid: _queries(record, taxonomy, cache) for cid, record in records.items()}
    requests: list[tuple[str, tuple[str, ...]]] = []
    for items, basis in plans.values():
        for _, item in items:
            if basis == "category":
                requests.append((item.query_text, item.branch_codes))
            else:
                requests.append((item, ()))
    retriever = retriever or TaxonomyRetriever(taxonomy)
    hits = iter(retriever.search_hierarchical_many(requests, top_n=TOP_N))
    rows: dict[str, dict[str, str]] = {}
    for cid, (items, basis) in plans.items():
        if not items:
            continue
        if basis == "name":
            found = next(hits)
            rows[cid] = {
                "psic_search_status": NAME_ONLY if found else NO_CANDIDATES,
                "psic_search_basis": "name",
                "psic_search_query": items[0][1],
                "psic_search_candidates": _candidates(found),
                "psic_search_reason": "name_never_accepted_automatically",
            }
            continue
        outcomes = []
        for label, prepared in items:
            found = next(hits)
            outcomes.append((label, prepared, found, *_category_outcome(taxonomy, prepared, found)))
        accepted = sorted({item[4] for item in outcomes if item[3] == ACCEPTABLE})
        if len(accepted) > 1:
            best = outcomes[0]
            result_status, result_code = DISAGREE, ""
            reason = "acceptable_codes:" + "|".join(accepted)
        else:
            best = min(outcomes, key=lambda item: _RANK[item[3]])
            result_status, result_code, reason = best[3], best[4], best[5]
        rows[cid] = {
            "psic_search_status": result_status,
            "psic_search_code": result_code,
            "psic_search_basis": f"category:{best[0]}",
            "psic_search_query": " || ".join(item[1].query_text for item in outcomes),
            "psic_search_candidates": _candidates(best[2]),
            "psic_search_reason": reason,
        }
    if rows:
        ids = out["canonical_id"].astype(str)
        for column in TITLE_SEARCH_COLUMNS:
            values = {cid: row.get(column, "") for cid, row in rows.items()}
            out[column] = ids.map(values).fillna("").astype(str).to_numpy()
    return out


def title_search_metadata(summary: pd.DataFrame) -> dict[str, Any] | None:
    if "psic_search_status" not in summary.columns:
        return None
    counts = Counter(str(x) for x in summary["psic_search_status"].fillna("") if str(x))
    return {
        "policy": POLICY,
        "min_score": MIN_SCORE,
        "min_margin": MIN_MARGIN,
        "top_n": TOP_N,
        "status_counts": dict(sorted(counts.items())),
        "note": "search results never set psic_code; review them as crosswalk rows",
    }
