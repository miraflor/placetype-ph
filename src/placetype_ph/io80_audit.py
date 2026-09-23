"""Audit helpers for the IO80 exact-name resolver (policy io80-v4).

This module never changes resolver output. It measures the same-run global-unanimity rule and
creates blinded manual-audit material. Resolver predictions are kept in a separate answer key.
"""

from __future__ import annotations

import hashlib
import math

import numpy as np
import pandas as pd

from .io80_resolver import (
    IO80_NAME_MIN_SUPPORT,
    name_evidence_table,
    normalize_io80_name,
    parse_candidate_codes,
)

SUPPORT_BAND_EDGES = (1, 2, 5, 10, math.inf)
SUPPORT_BAND_LABELS = ("2", "3-5", "6-10", ">10")
# In the original NCR audit, 55/56 -> 56 was rare. Census it by default rather than estimating
# its error rate from a small subsample. The setting is part of the audit design, not
# resolver policy.
DEFAULT_CENSUS_PAIR_CODES = frozenset({("55; 56", "56")})
AUDIT_CONTEXT_COLUMNS = (
    "canonical_id",
    "canonical_name",
    "io80_codes",
    "psic_code",
    "psic_level",
    "psic_status",
    "fsq_category",
    "overture_category",
    "osm_category",
)


def _support_band(support: pd.Series) -> pd.Series:
    return pd.cut(support, list(SUPPORT_BAND_EDGES), labels=list(SUPPORT_BAND_LABELS))


def _pair_label(codes: object) -> str:
    return "; ".join(sorted(parse_candidate_codes(codes)))


def name_rule_self_consistency(
    summary: pd.DataFrame,
    pair: tuple[str, str],
    min_support: int = IO80_NAME_MIN_SUPPORT,
) -> pd.DataFrame:
    """Leave one establishment out and test the v4 global-unanimity rule against its label.

    This is a self-consistency diagnostic, not an accuracy estimate. The hidden singleton label is
    upstream output, and upstream PSIC classification may itself use establishment names. A
    systematic upstream error shared by every record of a name is therefore invisible here.
    """
    refs = name_evidence_table(summary, None)
    columns = ["predictions", "disagreements", "disagreement_rate"]
    if refs.empty:
        return pd.DataFrame(columns=columns)

    pair_names = refs.loc[refs["label"].isin(pair), "name"].unique()
    refs = refs[refs["name"].isin(pair_names)].copy()
    # If one entity carries two singleton labels for the same name, it cannot serve as a clean
    # held-out truth observation. The full resolver would treat the name as contradictory.
    entity_labels = refs.groupby(["name", "entity"])["label"].nunique()
    valid_entities = entity_labels[entity_labels.eq(1)].index
    valid = refs.set_index(["name", "entity"]).loc[valid_entities].reset_index()
    if valid.empty:
        return pd.DataFrame(columns=columns)

    counts = refs.groupby(["name", "label"])["entity"].nunique()
    labels_by_name = {
        name: {str(label): int(count) for label, count in group.droplevel("name").items()}
        for name, group in counts.groupby(level="name")
    }

    records: list[dict[str, object]] = []
    for row in valid.itertuples(index=False):
        remaining = dict(labels_by_name[str(row.name)])
        remaining[str(row.label)] -= 1
        remaining = {label: count for label, count in remaining.items() if count > 0}
        if len(remaining) != 1:
            continue
        predicted, support = next(iter(remaining.items()))
        if predicted not in pair or support < min_support:
            continue
        records.append(
            {
                "support": support,
                "predictions": 1,
                "disagreements": int(predicted != str(row.label)),
            }
        )
    if not records:
        return pd.DataFrame(columns=columns)
    table = pd.DataFrame(records)
    table["support_band"] = _support_band(table["support"])
    out = table.groupby("support_band", observed=True)[["predictions", "disagreements"]].sum()
    out["disagreement_rate"] = out["disagreements"] / out["predictions"]
    return out


def name_impact(resolved: pd.DataFrame) -> pd.DataFrame:
    """Rank reference names by how many ambiguous rows they decide."""
    hit = resolved["io80_map_status"].eq("NAME_MATCH_RESOLVED")
    columns = ["pair", "name", "label", "support", "rows_decided", "rows_per_support"]
    if not hit.any():
        return pd.DataFrame(columns=[*columns, "cumulative_share"])
    frame = pd.DataFrame(
        {
            "pair": resolved.loc[hit, "io80_codes"].map(_pair_label),
            "name": resolved.loc[hit, "canonical_name"].map(normalize_io80_name),
            "label": resolved.loc[hit, "io80_map_code"].astype(str),
            "support": resolved.loc[hit, "io80_resolution_support"].astype(int),
        }
    )
    table = (
        frame.groupby(["pair", "name", "label", "support"])
        .size()
        .rename("rows_decided")
        .reset_index()
    )
    table["rows_per_support"] = table["rows_decided"] / table["support"]
    table = table.sort_values(["pair", "rows_decided", "name"], ascending=[True, False, True])
    decided = table.groupby("pair")["rows_decided"]
    table["cumulative_share"] = decided.cumsum() / decided.transform("sum")
    return table.reset_index(drop=True)


def resolved_row_purity(resolved: pd.DataFrame) -> pd.Series:
    """Global singleton purity for each resolved row; v4 requires this to be 1.0 by policy."""
    hit = resolved["io80_map_status"].eq("NAME_MATCH_RESOLVED")
    if not hit.any():
        return pd.Series(dtype=float, name="global_singleton_purity")
    evidence = name_evidence_table(resolved, None)
    counts = evidence.groupby(["name", "label"])["entity"].nunique()
    share = counts / counts.groupby(level="name").transform("sum")
    names = resolved.loc[hit, "canonical_name"].map(normalize_io80_name)
    keys = pd.MultiIndex.from_arrays([names, resolved.loc[hit, "io80_map_code"].astype(str)])
    return pd.Series(
        share.reindex(keys).to_numpy(dtype=float),
        index=names.index,
        name="global_singleton_purity",
    )


def blind_audit_sample(
    resolved: pd.DataFrame,
    per_stratum: int = 25,
    seed: int = 0,
    census_pair_codes: frozenset[tuple[str, str]] = DEFAULT_CENSUS_PAIR_CODES,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return a blinded worksheet and a separate answer key for manual validation.

    Strata are pair x support band x predicted code, but the predicted code and a human-readable
    stratum label exist only in the answer key. The worksheet deliberately omits io80_map_code and
    io80_resolution_method. By default every 55/56 row predicted as 56 is included.
    """
    if per_stratum < 1:
        raise ValueError("per_stratum must be at least 1")
    hit = resolved["io80_map_status"].eq("NAME_MATCH_RESOLVED")
    context = [column for column in AUDIT_CONTEXT_COLUMNS if column in resolved.columns]
    worksheet_columns = [
        "row",
        *context,
        "support_band",
        "audit_label",
        "audit_evidence",
        "audit_note",
    ]
    key_columns = [
        "row",
        "stratum_id",
        "pair",
        "support_band",
        "predicted_code",
        "support",
        "stratum_rows",
        "sampled_in_stratum",
        "weight",
        "census",
    ]
    if not hit.any():
        return pd.DataFrame(columns=worksheet_columns), pd.DataFrame(columns=key_columns)

    frame = resolved.loc[hit, context].copy()
    frame.insert(0, "row", frame.index)
    frame["support"] = resolved.loc[hit, "io80_resolution_support"].astype(int).to_numpy()
    frame["pair"] = resolved.loc[hit, "io80_codes"].map(_pair_label).to_numpy()
    frame["predicted_code"] = resolved.loc[hit, "io80_map_code"].astype(str).to_numpy()
    frame["support_band"] = _support_band(frame["support"]).astype(str)
    frame["stratum"] = (
        frame["pair"] + " | support " + frame["support_band"] + " | code " + frame["predicted_code"]
    )
    frame["stratum_id"] = frame["stratum"].map(
        lambda text: "s-" + hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:12]
    )
    frame["census"] = [
        (pair, code) in census_pair_codes
        for pair, code in zip(frame["pair"], frame["predicted_code"], strict=True)
    ]

    rng = np.random.default_rng(seed)
    chosen: list[pd.DataFrame] = []
    for _, group in frame.groupby("stratum", sort=True):
        if bool(group["census"].iloc[0]) or len(group) <= per_stratum:
            chosen.append(group)
        else:
            order = rng.permutation(len(group))[:per_stratum]
            chosen.append(group.iloc[order])
    sample = pd.concat(chosen, ignore_index=True)
    stratum_rows = frame["stratum"].value_counts()
    sampled = sample["stratum"].value_counts()
    sample["stratum_rows"] = sample["stratum"].map(stratum_rows).astype(int)
    sample["sampled_in_stratum"] = sample["stratum"].map(sampled).astype(int)
    sample["weight"] = sample["stratum_rows"] / sample["sampled_in_stratum"]

    worksheet = sample[["row", *context, "support_band"]].copy()
    worksheet["audit_label"] = ""
    worksheet["audit_evidence"] = ""
    worksheet["audit_note"] = ""
    worksheet = worksheet.sort_values("row").reset_index(drop=True)

    key = sample[
        [
            "row",
            "stratum_id",
            "pair",
            "support_band",
            "predicted_code",
            "support",
            "stratum_rows",
            "sampled_in_stratum",
            "weight",
            "census",
        ]
    ].sort_values("row").reset_index(drop=True)
    return worksheet, key


def summarize_blind_audit(
    worksheet: pd.DataFrame,
    answer_key: pd.DataFrame,
    confidence: float = 0.95,
) -> tuple[pd.DataFrame, dict[str, float | int]]:
    """Score a completed blinded worksheet against its answer key.

    Every sampled row must be labelled before any estimate is produced. Per-stratum output reports
    both a binomial Clopper-Pearson upper bound and a finite-population hypergeometric upper bound.
    """
    required_work = {"row", "audit_label"}
    required_key = {
        "row",
        "stratum_id",
        "pair",
        "predicted_code",
        "stratum_rows",
        "sampled_in_stratum",
    }
    if required_work - set(worksheet.columns) or required_key - set(answer_key.columns):
        raise ValueError("worksheet or answer key is missing required audit columns")
    labels = worksheet["audit_label"].astype(str).str.strip()
    if labels.eq("").any():
        rows = worksheet.loc[labels.eq(""), "row"].tolist()[:10]
        raise ValueError(f"audit is incomplete; blank audit_label on row(s) {rows}")
    if worksheet["row"].duplicated().any() or answer_key["row"].duplicated().any():
        raise ValueError("audit row identifiers must be unique")

    joined = worksheet[["row", "audit_label"]].merge(answer_key, on="row", validate="one_to_one")
    if len(joined) != len(answer_key) or len(joined) != len(worksheet):
        raise ValueError("worksheet and answer key do not contain exactly the same sampled rows")
    joined["error"] = (
        joined["audit_label"].astype(str).str.strip()
        != joined["predicted_code"].astype(str)
    )

    records: list[dict[str, object]] = []
    for stratum_id, group in joined.groupby("stratum_id", sort=True):
        population = int(group["stratum_rows"].iloc[0])
        audited = len(group)
        errors = int(group["error"].sum())
        if audited != int(group["sampled_in_stratum"].iloc[0]):
            raise ValueError(f"stratum {stratum_id} is incomplete")
        records.append(
            {
                "stratum_id": stratum_id,
                "pair": str(group["pair"].iloc[0]),
                "predicted_code": str(group["predicted_code"].iloc[0]),
                "stratum_rows": population,
                "audited": audited,
                "errors": errors,
                "error_rate": errors / audited,
                "binomial_upper_bound": error_upper_bound(errors, audited, confidence),
                "finite_population_upper_bound": finite_population_error_upper_bound(
                    errors, audited, population, confidence
                ),
            }
        )
    per_stratum = pd.DataFrame(records).set_index("stratum_id")
    weights = per_stratum["stratum_rows"] / per_stratum["stratum_rows"].sum()
    overall = {
        "audited_rows": int(per_stratum["audited"].sum()),
        "errors": int(per_stratum["errors"].sum()),
        "weighted_error_rate": float((weights * per_stratum["error_rate"]).sum()),
        "resolved_population_rows": int(per_stratum["stratum_rows"].sum()),
    }
    return per_stratum, overall


def _log_comb(n: int, k: int) -> float:
    if k < 0 or k > n:
        return float("-inf")
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def _hypergeom_cdf(k: int, population: int, errors: int, draws: int) -> float:
    low = max(0, draws - (population - errors))
    high = min(k, draws, errors)
    if high < low:
        return 0.0
    denominator = _log_comb(population, draws)
    terms = [
        _log_comb(errors, x) + _log_comb(population - errors, draws - x) - denominator
        for x in range(low, high + 1)
    ]
    peak = max(terms)
    return min(1.0, math.exp(peak) * sum(math.exp(term - peak) for term in terms))


def finite_population_error_upper_bound(
    observed_errors: int,
    audited: int,
    population: int,
    confidence: float = 0.95,
) -> float:
    """Exact one-sided hypergeometric upper bound for sampling without replacement."""
    if not 0 < audited <= population:
        raise ValueError("need 0 < audited <= population")
    if not 0 <= observed_errors <= audited or not 0.0 < confidence < 1.0:
        raise ValueError("invalid observed_errors or confidence")
    if audited == population:
        return observed_errors / population
    alpha = 1.0 - confidence
    low = observed_errors
    high = population - (audited - observed_errors)
    # Find the largest total-error count whose lower-tail probability is still at least alpha.
    while low < high:
        middle = (low + high + 1) // 2
        if _hypergeom_cdf(observed_errors, population, middle, audited) >= alpha:
            low = middle
        else:
            high = middle - 1
    return low / population


def _binomial_cdf(k: int, n: int, p: float) -> float:
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 1.0 if k >= n else 0.0
    log_p, log_q = math.log(p), math.log1p(-p)
    terms = [
        math.lgamma(n + 1)
        - math.lgamma(i + 1)
        - math.lgamma(n - i + 1)
        + i * log_p
        + (n - i) * log_q
        for i in range(k + 1)
    ]
    peak = max(terms)
    return min(1.0, math.exp(peak) * sum(math.exp(term - peak) for term in terms))


def error_upper_bound(errors: int, audited: int, confidence: float = 0.95) -> float:
    """One-sided exact binomial (Clopper-Pearson) upper bound on an error rate."""
    if audited <= 0 or not 0 <= errors <= audited or not 0.0 < confidence < 1.0:
        raise ValueError("need 0 <= errors <= audited, audited > 0 and 0 < confidence < 1")
    if errors == audited:
        return 1.0
    alpha = 1.0 - confidence
    if errors == 0:
        return 1.0 - alpha ** (1.0 / audited)
    low, high = errors / audited, 1.0
    for _ in range(200):
        middle = (low + high) / 2.0
        if _binomial_cdf(errors, audited, middle) > alpha:
            low = middle
        else:
            high = middle
    return high
