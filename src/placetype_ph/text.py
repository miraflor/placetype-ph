from __future__ import annotations

import re
import unicodedata

import pandas as pd
from pandas.api.types import is_scalar

# Values that are useful to treat as missing in noisy POI/type fields. Do not use this
# vocabulary for official taxonomy text: classification nodes legitimately contain titles
# such as "Other" and those must be preserved verbatim.
_NULLS = {"", "n/a", "na", "none", "null", "-", "others", "other", "unknown"}


def clean_literal(value: object) -> str | None:
    """Normalize a scalar text cell without imposing POI-specific null semantics.

    This is the safe cleaner for official taxonomy cells, crosswalk metadata, and other
    authoritative text. Pandas missing scalars are handled before string conversion so
    ``NaN`` and ``pd.NA`` never become the literal evidence strings ``"nan"``/``"<NA>"``.
    """
    if value is None:
        return None
    try:
        if is_scalar(value) and bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    text = unicodedata.normalize("NFKC", str(value)).strip()
    return text or None


def clean_text(value: object) -> str | None:
    """Normalize noisy OpenPlaces/user text and map generic null labels to missing."""
    text = clean_literal(value)
    if text is None or text.casefold() in _NULLS:
        return None
    return text


def _fold_key(text: str) -> str:
    text = text.casefold()
    text = re.sub(r"[^\w=:+&/.-]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def normalize_key(value: object) -> str:
    """Index key for noisy OpenPlaces/user text, with POI null semantics applied."""
    return _fold_key(clean_text(value) or "")


def normalize_match_key(value: object) -> str:
    """Index key for an authoritative reviewed match value.

    A crosswalk row is reviewed input, so POI null semantics must not be applied to it.
    Under `normalize_key` a rule keyed on the literal source value `Other` folds to the
    empty string: it then collides in the index with every other rule whose value is a
    generic label, a `contains` rule built from it can never match, and an exact rule
    built from it matches any record whose category is `n/a`. Official taxonomy nodes are
    legitimately titled `Other`, and so are source categories, so the value is kept.
    """
    return _fold_key(clean_literal(value) or "")


def combine_text(*parts: object) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        text = clean_text(part)
        if not text:
            continue
        key = normalize_key(text)
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return " | ".join(out)
