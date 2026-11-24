"""Bundled institutional ownership enrichment; existing promotion policy is unchanged."""

from __future__ import annotations

import hashlib
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
    load_heida,
    load_hospitals,
    load_level1_hospitals,
    load_level2_hospitals,
    load_official_records,
    load_unifast,
)
from .institutional_shadow import InstitutionResolver, load_verified_links

ENVIRONMENT_KEY = "PLACETYPE_PH_INSTITUTION_REFERENCES"
BUNDLED_KEYS = frozenset({"beis", "unifast", "hospitals", "heida"})
CONFIG_SCHEMA = "placetype-ph-institution-reference-config-v1"
REFERENCE_KEYS = frozenset({
    "beis", "unifast", "hospitals", "official_records", "verified_links",
    "heida", "level1_hospitals", "level2_hospitals",
})


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


def selected_reference_config() -> tuple[Path | None, dict[str, Path]]:
    """Choose packaged defaults, a custom absolute configuration, or explicit off.

    The same selector is used by classification and the Express cache. Verify
    packaged snapshot hashes before returning paths; never silently fall back.
    """
    raw = os.environ.get(ENVIRONMENT_KEY)
    if raw is not None:
        if raw == "off":
            return None, {}
        if not raw.strip():
            raise ValueError(f"{ENVIRONMENT_KEY} was set but is empty")
        config = Path(raw)
        return config, load_reference_config(config)

    from .institutional_tools import REGISTRY_DATA, registry_manifest

    manifest = REGISTRY_DATA / "manifest.json"
    references: dict[str, Path] = {}
    for name, entry in registry_manifest()["files"].items():
        label = entry.get("reference_key")
        if label not in BUNDLED_KEYS:
            continue
        if label in references or Path(name).name != name:
            raise ValueError("invalid or duplicate bundled reference entry")
        path = REGISTRY_DATA / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"bundled registry file {name} does not match its manifest hash")
        references[label] = path
    if set(references) != BUNDLED_KEYS:
        raise ValueError("incomplete bundled institutional references")
    return manifest, references


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
        + (load_heida(references["heida"]) if "heida" in references else ())
        + (load_level1_hospitals(references["level1_hospitals"])
           if "level1_hospitals" in references else ())
        + (load_level2_hospitals(references["level2_hospitals"])
           if "level2_hospitals" in references else ())
        + (load_hospitals(references["hospitals"]) if "hospitals" in references else ())
        + (
            load_official_records(references["official_records"])
            if "official_records" in references
            else ()
        )
    )
    links = load_verified_links(references.get("verified_links"))
    resolver = InstitutionResolver(InstitutionIndex(records), links)
    return resolve_institutional_ownership(classified, places, resolver=resolver)


def maybe_resolve_institutions(
    classified: pd.DataFrame,
    places: pd.DataFrame,
) -> pd.DataFrame:
    """Run bundled enrichment unless explicitly disabled; invalid inputs fail closed."""
    _, references = selected_reference_config()
    if not references:
        return classified
    return resolve_from_references(classified, places, references=references)
