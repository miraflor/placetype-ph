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


def normalize_key(value: object) -> str:
    text = clean_text(value) or ""
    text = text.casefold()
    text = re.sub(r"[^\w=:+&/.-]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


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
