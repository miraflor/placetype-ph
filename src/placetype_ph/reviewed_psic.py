"""Accepted PSIC Rev. 5 source mappings and conservative source interpretation.

The packaged reviewed CSV is a classification resource, never a runtime network lookup.
Its rules are kept distinct from unreviewed specialized proposals.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from .models import CrosswalkEntry, MappingKind, SourceEvidence
from .openplaces import Row, source_evidence
from .specialized.source_evidence import (
    AUXILIARY_OSM_KEYS,
    osm_pairs,
    split_categories,
)
from .suggest import prepare_suggestion
from .taxonomy import Taxonomy
from .text import clean_text


def bundled_psic_crosswalk_path() -> Path:
    """Return the installed, reviewed PSIC Rev. 5 crosswalk file."""
    path = Path(__file__).resolve().parent / "data" / "psic_rev5_reviewed.csv"
    if not path.is_file():
        raise FileNotFoundError(
            "packaged reviewed PSIC crosswalk is missing; reinstall the package"
        )
    return path


def _is_auxiliary_osm(key: str) -> bool:
    return (
        key in AUXILIARY_OSM_KEYS
        or key == "atm"
        or key.startswith(("contact:", "payment:", "addr:", "source:", "isced:"))
    )


def psic_source_evidence(
    row: Row, keep_whole: Callable[[str, str], bool] | None = None
) -> list[SourceEvidence]:
    """Use independent source groups, not concatenated category strings, for PSIC.

    Preserve source names and the FSQ lineage group from the ordinary OpenPlaces
    adapter. OSM attributes are key/value assertions, and Overture primary, alternate,
    and basic fields are dependent statements of the same source.

    ``keep_whole(source, raw)`` may declare that a multi-tag OSM string has its own
    reviewed decision (for example ``amenity=bus_station | public_transport=station`` is
    reviewed as UNCODEABLE). Such a string is kept as one category instead of being split,
    because splitting would make its reviewed row unreachable.
    """
    expanded: list[SourceEvidence] = []
    original = source_evidence(row)
    if not any(item.source == "overture" for item in original):
        if any(
            clean_text(row.get(field))
            for field in (
                "overture_primary_category",
                "overture_alternate_category",
                "overture_basic_category",
            )
        ):
            group = (
                "fsq-lineage"
                if any(item.dependency_group == "fsq-lineage" for item in original)
                else "overture"
            )
            original.append(SourceEvidence("overture", None, None, group))
    for evidence in original:
        if evidence.source == "osm":
            raw = evidence.category
            pairs = osm_pairs(raw)
            if raw and keep_whole is not None and len(pairs) > 1 and keep_whole("osm", raw):
                categories = [raw]
            elif pairs:
                categories = list(
                    dict.fromkeys(
                        f"{key}={value}" for key, value in pairs if not _is_auxiliary_osm(key)
                    )
                )
            else:
                categories = [raw] if raw else []
        elif evidence.source == "overture":
            categories = []
            for field in (
                "overture_primary_category",
                "overture_category",
                "overture_alternate_category",
            ):
                value = clean_text(row.get(field))
                if value:
                    categories.extend(split_categories(value))
            # Basic categories are source-level context only. They are already
            # retained in specialized audit evidence; they cannot establish a
            # specific primary PSIC activity or veto a primary/alternate label.
            categories = list(dict.fromkeys(categories))
        else:
            # FSQ source category strings may encode a hierarchical category path;
            # splitting the hierarchy would destroy that category's reviewed identity.
            categories = [evidence.category] if evidence.category else []

        if categories:
            expanded.extend(replace(evidence, category=value) for value in categories)
        elif evidence.name:
            expanded.append(replace(evidence, category=None))
    return expanded


def trusted_psic_source_rule(
    taxonomy: Taxonomy,
    source: str,
    category: str | None,
) -> CrosswalkEntry | None:
    """Apply only curated ontology floors or explicit high-precision terminal policy.

    Retrieval suggestions, unreviewed specialist proposals, generic suffixes and
    arbitrary names cannot create accepted PSIC codes here. No LLM is called.
    """
    if (
        taxonomy.scheme != "psic"
        or taxonomy.version != "rev5"
        or not category
        or source not in {"osm", "overture", "fsq"}
    ):
        return None
    prepared = prepare_suggestion(taxonomy, source, category)
    terminal = prepared.terminal
    if terminal is not None:
        terminal_kind = terminal.suggested_kind
        if terminal_kind in {"NOT_ACTIVITY", "UNCODEABLE"}:
            return CrosswalkEntry(
                source=source,
                source_value=category,
                scheme=taxonomy.scheme,
                version=taxonomy.version,
                mapping_kind=(
                    MappingKind.NOT_ACTIVITY
                    if terminal_kind == "NOT_ACTIVITY"
                    else MappingKind.UNCODEABLE
                ),
            )
        return None
    floor = prepared.floor_code
    if (
        floor is None
        or floor not in taxonomy.nodes
        or prepared.block_reason
        in {
            "compound_source",
            "ambiguous_branch_roots",
            "mixed_category_ceiling",
            "heuristic_store_suffix",
            "ambiguous_pet_shop",
        }
    ):
        return None
    return CrosswalkEntry(
        source=source,
        source_value=category,
        scheme=taxonomy.scheme,
        version=taxonomy.version,
        # A curated category floor asserts a subtree, not an exact activity.
        # A partial taxonomy may currently have no loaded children below that
        # code; that does not increase the precision of the source evidence.
        mapping_kind=MappingKind.SUBTREE,
        codes=(floor,),
    )


def accepted_administrative_role(
    taxonomy: Taxonomy,
    row: Row,
    evidence: list[SourceEvidence],
) -> tuple[str | None, str]:
    """Resolve a narrowly recognized administrative function, with abstention gates.

    A precise role pattern can correct an overly broad service category, but it does
    not override reviewed name rules, negative activity assertions, an exact coded
    activity, or a disjoint unrelated activity. Ownership is irrelevant here.
    """
    if taxonomy.scheme != "psic" or taxonomy.version != "rev5" or "84120" not in taxonomy.nodes:
        return None, "unavailable_administration_code"
    from .specialized.institutions import administrative

    decision = administrative(row)
    if decision.status != "PROPOSED" or decision.code != "84120":
        return None, decision.reason or "no_specific_administrative_role"
    allowed_divisions = {"84", "85", "86"}
    for item in evidence:
        rule = item.mapping
        if rule is None:
            continue
        if rule.mapping_kind == MappingKind.NOT_ACTIVITY:
            return None, "terminal_source_evidence"
        if rule.mapping_kind == MappingKind.UNCODEABLE:
            # Lack of a codeable category is not evidence against a narrowly
            # recognized function with a precise administrative name.
            continue
        if rule.mapping_kind == MappingKind.EXACT:
            return None, "exact_classification_preserved"
        if rule.source_field.value == "name":
            return None, "reviewed_name_classification_preserved"
        for code in rule.codes:
            if code not in taxonomy.nodes:
                return None, "invalid_existing_code"
            ancestors = set(taxonomy.ancestors(code))
            if not (allowed_divisions & (ancestors | {code})):
                return None, "unrelated_activity_evidence"
    return "84120", decision.reason


def unsafe_osm_compound(row: Row, evidence: list[SourceEvidence]) -> bool:
    """Abstain when multiple primary OSM tags imply an unreviewed activity mix.

    Two different tags that independently map to the *same* accepted activity
    may corroborate it; unknown, terminal or disjoint primary tags do not.
    """
    from .specialized.source_evidence import compound_status

    if compound_status("osm", row.get("osm_category")) != "MULTIPLE_PRIMARY":
        return False
    whole = clean_text(row.get("osm_category"))
    if whole and any(
        item.source == "osm" and item.category == whole and item.mapping is not None
        for item in evidence
    ):
        # The whole multi-tag string has its own reviewed or terminal decision.
        return False
    primaries = {
        f"{key}={value}"
        for key, value in osm_pairs(row.get("osm_category"))
        if not _is_auxiliary_osm(key)
    }
    resolved: set[str] = set()
    for primary in primaries:
        mappings = [
            item.mapping
            for item in evidence
            if item.source == "osm"
            and item.category == primary
            and item.mapping is not None
            and item.mapping.source_field.value == "category"
        ]
        if not mappings or any(
            mapping.mapping_kind != MappingKind.EXACT
            and mapping.mapping_kind != MappingKind.SUBTREE
            for mapping in mappings
        ):
            return True
        codes = {code for mapping in mappings for code in mapping.codes}
        if len(codes) != 1:
            return True
        resolved.update(codes)
    return len(resolved) != 1


@lru_cache(maxsize=1)
def packaged_name_rules() -> frozenset[CrosswalkEntry]:
    """The reviewed name rules shipped in the package.

    All of them are keyword patterns over names (for example "college", "hotel",
    "restaurant"), not judgments about one named establishment. When such a rule points to
    a subtree that does not overlap a reviewed category rule or a curated category floor for
    the same source, the category is the more direct statement of what the place is, and it
    takes precedence. Name rules supplied by the user keep their documented precedence.
    """
    from .crosswalk import Crosswalk

    return frozenset(
        entry
        for entry in Crosswalk.load([bundled_psic_crosswalk_path()]).entries
        if entry.source_field.value == "name"
    )


def crosswalk_fingerprint(crosswalk: object | None) -> str | None:
    """Stable content-based fingerprint for a run's accepted crosswalk decisions."""
    if crosswalk is None:
        return None
    import hashlib
    import json

    entries = getattr(crosswalk, "entries", None)
    if entries is None:
        raise TypeError("crosswalk must expose its reviewed entries")
    items = sorted(
        (
            str(e.source),
            str(e.source_field),
            e.source_value,
            e.scheme,
            e.version,
            str(e.mapping_kind),
            list(e.codes),
            e.match_type,
        )
        for e in entries
    )
    payload = json.dumps(items, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
