"""Opt-in institutional ownership for the normal classified-place export.

Only activates when PLACETYPE_PH_INSTITUTION_REFERENCES points to an explicit,
validated external configuration. Reuses Priority 3b without new promotion policy.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from .institutional_promotion import resolve_institutional_ownership
from .institutional_registry import (
    InstitutionIndex,
    load_beis,
    load_hospitals,
    load_unifast,
)
from .institutional_shadow import InstitutionResolver, load_verified_links

ENVIRONMENT_KEY = "PLACETYPE_PH_INSTITUTION_REFERENCES"
CONFIG_SCHEMA = "placetype-ph-institution-reference-config-v1"
REFERENCE_KEYS = frozenset({"beis", "unifast", "hospitals", "verified_links"})


def load_reference_config(path: Path) -> dict[str, Path]:
    """Validate explicit local file references without I/O-side effects."""
    path = Path(path)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("institution reference config must be an existing absolute path")
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("institution reference config cannot be decoded") from exc
    if not isinstance(document, dict) or set(document) != {"schema", "references"}:
        raise ValueError("institution reference config must contain schema and references only")
    if document["schema"] != CONFIG_SCHEMA:
        raise ValueError("unknown institution reference config schema")
    references: Any = document["references"]
    if not isinstance(references, dict) or not references:
        raise ValueError("institution references must be a nonempty object")
    if not set(references) <= REFERENCE_KEYS:
        raise ValueError("unknown institutional reference type")
    result: dict[str, Path] = {}
    for label, value in references.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"invalid path for institutional reference {label}")
        ref = Path(value)
        if not ref.is_absolute() or not ref.is_file():
            raise FileNotFoundError(
                f"institutional reference {label} must be an existing absolute file"
            )
        suffixes = ref.suffixes
        if label == "verified_links":
            valid_extension = ref.suffix.lower() == ".json"
        else:
            valid_extension = ref.suffix.lower() == ".csv" or suffixes[-2:] == [".csv", ".gz"]
        if not valid_extension:
            raise ValueError(f"invalid institutional reference file type for {label}")
        if ref.resolve() == path.resolve():
            raise ValueError("config cannot refer to itself")
        result[label] = ref
    if len({ref.resolve() for ref in result.values()}) != len(result):
        raise ValueError("institution reference file paths must not be reused")
    return result


def resolve_from_references(
    classified: pd.DataFrame,
    places: pd.DataFrame,
    *,
    references: Mapping[str, Path],
) -> pd.DataFrame:
    """Invoke the unchanged Priority 3b promotion contract on in-memory frames."""
    if not references or not set(references) <= REFERENCE_KEYS:
        raise ValueError("unsupported or missing institutional reference configuration")
    records = (
        (load_beis(references["beis"]) if "beis" in references else ())
        + (load_unifast(references["unifast"]) if "unifast" in references else ())
        + (load_hospitals(references["hospitals"]) if "hospitals" in references else ())
    )
    links = load_verified_links(references.get("verified_links"))
    resolver = InstitutionResolver(InstitutionIndex(records), links)
    return resolve_institutional_ownership(classified, places, resolver=resolver)


def maybe_resolve_institutions(
    classified: pd.DataFrame,
    places: pd.DataFrame,
) -> pd.DataFrame:
    """No-op by default; invalid opt-in configuration fails the pipeline closed."""
    raw = os.environ.get(ENVIRONMENT_KEY)
    if raw is None:
        return classified
    if not raw.strip():
        raise ValueError(f"{ENVIRONMENT_KEY} was set but is empty")
    references = load_reference_config(Path(raw))
    return resolve_from_references(classified, places, references=references)
