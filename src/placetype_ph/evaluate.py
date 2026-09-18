from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import cohen_kappa_score

from .taxonomy import Taxonomy
from .text import clean_literal


@dataclass(slots=True)
class DepthMetrics:
    depth: int
    level: str
    eligible_gold: int
    produced: int
    production_rate: float
    accuracy: float | None
    kappa: float | None


_GOLD_CODED = "CODED"
_GOLD_NOT_CODEABLE = {"NOT_CODEABLE", "NOT_PSIC_ACTIVITY", "NON_ECONOMIC_POI"}
_GOLD_STATUSES = {_GOLD_CODED, *_GOLD_NOT_CODEABLE}


def _node_at_depth(taxonomy: Taxonomy, code: str, depth: int) -> str | None:
    if code not in taxonomy.nodes:
        return None
    path = taxonomy.path_from_root(code)
    if len(path) < depth:
        return None
    return path[depth - 1]


def tree_distance(taxonomy: Taxonomy, left: str, right: str) -> int | None:
    if left not in taxonomy.nodes or right not in taxonomy.nodes:
        return None
    lca = taxonomy.lca([left, right])
    if lca is None:
        # Forest roots differ. Treat virtual root as one edge above each root.
        return taxonomy.depth(left) + taxonomy.depth(right)
    return taxonomy.depth(left) + taxonomy.depth(right) - 2 * taxonomy.depth(lca)


def _exact_accuracy(merged: pd.DataFrame) -> float | None:
    coded = merged[merged["code"].notna()]
    if coded.empty:
        return None
    return float((coded["code"].astype(str) == coded["gold_code"].astype(str)).mean())


def _prepare_gold(merged: pd.DataFrame, taxonomy: Taxonomy) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Validate adjudicated gold instead of silently dropping malformed rows.

    Without `gold_status`, every row is a coded gold row and therefore must contain a
    valid taxonomy code. With `gold_status`, `NOT_CODEABLE`, `NOT_PSIC_ACTIVITY`, and the
    legacy `NON_ECONOMIC_POI` label are explicit negative judgments and must have no gold
    code. This preserves the original
    design's ability to evaluate abstention/eligibility rather than measuring only the
    rows that happen to have a code.
    """
    work = merged.copy()
    work["gold_code"] = work["gold_code"].map(clean_literal)

    if "gold_status" not in work.columns:
        work["gold_status"] = _GOLD_CODED
    else:
        statuses: list[str] = []
        for raw_status, gold_code in zip(
            work["gold_status"], work["gold_code"], strict=False
        ):
            status = (clean_literal(raw_status) or "").upper()
            if not status:
                status = _GOLD_CODED if gold_code else ""
            statuses.append(status)
        work["gold_status"] = statuses

    invalid_status = ~work["gold_status"].isin(_GOLD_STATUSES)
    if invalid_status.any():
        examples = work.loc[invalid_status, ["canonical_id", "gold_status"]].head(10)
        detail = ", ".join(
            f"{row.canonical_id}:{row.gold_status!r}"
            for row in examples.itertuples(index=False)
        )
        raise ValueError(
            "gold_status must be CODED, NOT_CODEABLE, NOT_PSIC_ACTIVITY, "
            "or legacy NON_ECONOMIC_POI; "
            f"invalid rows include {detail}"
        )

    coded_mask = work["gold_status"] == _GOLD_CODED
    invalid_coded = coded_mask & ~work["gold_code"].isin(taxonomy.nodes)
    if invalid_coded.any():
        examples = work.loc[invalid_coded, ["canonical_id", "gold_code"]].head(10)
        detail = ", ".join(
            f"{row.canonical_id}:{row.gold_code!r}"
            for row in examples.itertuples(index=False)
        )
        raise ValueError(
            f"{int(invalid_coded.sum())} coded gold row(s) have a missing or unknown "
            f"{taxonomy.scheme} code; examples: {detail}"
        )

    negative_mask = work["gold_status"].isin(_GOLD_NOT_CODEABLE)
    contradictory = negative_mask & work["gold_code"].notna()
    if contradictory.any():
        examples = work.loc[contradictory, ["canonical_id", "gold_status", "gold_code"]].head(10)
        detail = ", ".join(
            f"{row.canonical_id}:{row.gold_status}/{row.gold_code}"
            for row in examples.itertuples(index=False)
        )
        raise ValueError(
            "not-codeable gold rows must leave gold_code blank; contradictory rows "
            f"include {detail}"
        )

    return work[coded_mask].copy(), work[negative_mask].copy()


def evaluate_predictions(
    predictions: pd.DataFrame,
    gold: pd.DataFrame,
    taxonomy: Taxonomy,
) -> tuple[pd.DataFrame, dict]:
    required_pred = {"canonical_id", "scheme", "code"}
    required_gold = {"canonical_id", "scheme", "gold_code"}
    if missing := required_pred - set(predictions.columns):
        raise ValueError(f"predictions missing columns: {sorted(missing)}")
    if missing := required_gold - set(gold.columns):
        raise ValueError(f"gold missing columns: {sorted(missing)}")

    p = predictions[predictions["scheme"].astype(str).str.casefold() == taxonomy.scheme].copy()
    g = gold[gold["scheme"].astype(str).str.casefold() == taxonomy.scheme].copy()
    if "version" in p.columns:
        p = p[p["version"].astype(str) == taxonomy.version].copy()
    if "version" in g.columns:
        g = g[g["version"].astype(str) == taxonomy.version].copy()
    if p["canonical_id"].duplicated().any():
        raise ValueError("predictions contain duplicate canonical_id rows for this scheme/version")
    if g["canonical_id"].duplicated().any():
        raise ValueError("gold contains duplicate canonical_id rows for this scheme/version")
    if g.empty:
        raise ValueError("no gold rows remain for this scheme/version")

    pred_columns = ["canonical_id", "code"]
    merged = g.merge(p[pred_columns], on="canonical_id", how="left")
    merged["code"] = merged["code"].map(clean_literal)
    coded_gold, negative_gold = _prepare_gold(merged, taxonomy)

    max_depth = taxonomy.max_depth
    depth_rows: list[DepthMetrics] = []
    for depth in range(1, max_depth + 1):
        gold_at = coded_gold["gold_code"].map(
            lambda c, d=depth: _node_at_depth(taxonomy, str(c), d)
        )
        pred_at = coded_gold["code"].map(
            lambda c, d=depth: _node_at_depth(taxonomy, str(c), d) if c else None
        )
        eligible = gold_at.notna()
        produced = eligible & pred_at.notna()
        n_eligible = int(eligible.sum())
        n_produced = int(produced.sum())
        production_rate = n_produced / n_eligible if n_eligible else 0.0
        if n_produced:
            y_true = gold_at[produced].astype(str)
            y_pred = pred_at[produced].astype(str)
            accuracy = float((y_true == y_pred).mean())
            labels = sorted(set(y_true) | set(y_pred))
            kappa = (
                float(cohen_kappa_score(y_true, y_pred, labels=labels))
                if len(labels) > 1
                else None
            )
        else:
            accuracy = None
            kappa = None

        level_names = [
            taxonomy.get(c).level for c in taxonomy.nodes if taxonomy.depth(c) == depth
        ]
        level = pd.Series(level_names).mode().iloc[0] if level_names else f"depth_{depth}"
        depth_rows.append(
            DepthMetrics(
                depth, str(level), n_eligible, n_produced, production_rate, accuracy, kappa
            )
        )

    distances = [
        tree_distance(taxonomy, str(pred), str(gold_code))
        for pred, gold_code in zip(coded_gold["code"], coded_gold["gold_code"], strict=False)
        if pred is not None and str(pred) in taxonomy.nodes
    ]

    relations = {"exact": 0, "ancestor_backoff": 0, "over_specific": 0, "wrong_branch": 0}
    invalid_prediction_codes = 0
    for pred, gold_code in zip(coded_gold["code"], coded_gold["gold_code"], strict=False):
        if pred is None:
            continue
        pred = str(pred)
        gold_code = str(gold_code)
        if pred not in taxonomy.nodes:
            invalid_prediction_codes += 1
            continue
        if pred == gold_code:
            relations["exact"] += 1
        elif pred in taxonomy.ancestors(gold_code, include_self=False):
            relations["ancestor_backoff"] += 1
        elif gold_code in taxonomy.ancestors(pred, include_self=False):
            relations["over_specific"] += 1
        else:
            relations["wrong_branch"] += 1

    valid_coded = sum(relations.values())
    coded_total = int(coded_gold["code"].notna().sum())
    hierarchy_compatible = relations["exact"] + relations["ancestor_backoff"]
    invalid_prediction_codes_all = int(
        (merged["code"].notna() & ~merged["code"].isin(taxonomy.nodes)).sum()
    )
    negative_false_positives = int(negative_gold["code"].notna().sum())
    negative_rows = len(negative_gold)
    summary = {
        "scheme": taxonomy.scheme,
        "version": taxonomy.version,
        "gold_rows": int(len(merged)),
        "coded_gold_rows": int(len(coded_gold)),
        "not_codeable_gold_rows": int(negative_rows),
        "coded_rows": coded_total,
        "valid_coded_rows": valid_coded,
        "invalid_prediction_codes": invalid_prediction_codes_all,
        "invalid_prediction_codes_on_coded_gold": invalid_prediction_codes,
        # Backward-compatible meaning: production/coverage among rows with a coded gold
        # target, not among explicit NOT_CODEABLE gold rows.
        "coverage": float(coded_gold["code"].notna().mean()) if len(coded_gold) else 0.0,
        "exact_code_accuracy": _exact_accuracy(coded_gold),
        "hierarchy_compatible_accuracy": (
            hierarchy_compatible / coded_total if coded_total else None
        ),
        "ancestor_backoff_rows": relations["ancestor_backoff"],
        "over_specific_rows": relations["over_specific"],
        "wrong_branch_rows": relations["wrong_branch"],
        "mean_tree_distance": float(np.mean(distances)) if distances else None,
        # This is intentionally named code-avoidance, not accuracy: a model that simply
        # failed to classify also avoids a false code. Review-queue analysis can later
        # distinguish principled abstention from failure once adjudicated statuses exist.
        "not_codeable_false_positive_rows": negative_false_positives,
        "not_codeable_code_avoidance": (
            1.0 - negative_false_positives / negative_rows if negative_rows else None
        ),
        "overall_auto_code_rate": float(merged["code"].notna().mean()),
    }
    return pd.DataFrame([asdict(x) for x in depth_rows]), summary


def load_gold(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, dtype=str)
