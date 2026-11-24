"""Resolved I-O codes with provenance, from PSIC, direct tag evidence and explicit input.

The PSIC-derived candidate sets in ``io*_codes`` stay authoritative and unchanged. This
module adds three groups of columns after them:

* ``direct_io_*``: IO80 evidence read directly from explicit OSM tags (packaged
  ``direct_io_rules.csv``; for example ``amenity=bank`` is banking, IO80 66). Names never
  trigger these rules. Primary tags decide first; ``cuisine``, ``brand`` and ``operator``
  decide only when no primary tag matches, so ``tourism=hotel | cuisine=filipino`` is
  accommodation.
* ``io*_resolved_code`` / ``_status`` / ``_source``: one code per resolution when the evidence
  supports one, and the reason when it does not. Order of authority: explicit user input,
  verified institutional ownership, the PSIC map code, then direct tag evidence. A direct
  tag can confirm a PSIC map code, choose inside an ambiguous PSIC candidate set, or stand
  alone when PSIC has no I-O candidates; it never overrides a PSIC result it contradicts,
  and it is not used when the PSIC status says the place is not an activity, is
  uncodeable, is in conflict, or needs entity review. Finally the three codes are made to
  nest in the official PSA 2018 hierarchy: a finer code from a stronger source sets its
  parents; otherwise the finer code is cleared with status ``LEVEL_CONFLICT``.
* ``io_ownership_inferred_*`` / ``io*_ownership_inferred_code``: the public/private side
  suggested by explicit source attributes (OSM ``ownership=public``, Overture
  ``public_school``) or by name and category hints, when the candidate sets are exactly an
  education or health ownership pair. These are labelled inferences. They never enter strict
  resolved columns.

Explicit user codes are read from optional input columns ``explicit_io16_code``,
``explicit_io80_code`` and ``explicit_io240_code``. The finest supplied level decides, its
parents are taken from the official hierarchy, and any unknown code or contradictory
parent-child combination stops the run with a bounded diagnostic.
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from typing import Any

import pandas as pd

from .io_mapping import RESOLUTIONS, IOCrosswalk, IOHierarchy
from .specialized.institutional_shadow import _explicit_sides, operator_evidence
from .specialized.source_evidence import osm_pairs

POLICY = "io-resolution-v4"
RESOLUTION_POLICIES = frozenset({"strict", "evidence-weighted", "extended"})
DIRECT_RULES_FILE = "direct_io_rules.csv"
EXPLICIT_IO_COLUMNS = {
    "io16": "explicit_io16_code",
    "io80": "explicit_io80_code",
    "io240": "explicit_io240_code",
}
DIRECT_COLUMNS = (
    "direct_io_status",
    "direct_io80_candidates",
    "direct_io80_code",
    "direct_io16_code",
    "direct_io_reason",
)
RESOLVED_COLUMNS = tuple(
    f"{resolution}_resolved_{part}"
    for resolution in RESOLUTIONS
    for part in ("code", "status", "source")
)
OWNERSHIP_INFERRED_COLUMNS = (
    "io_ownership_inferred_status",
    "io_ownership_inferred_basis",
    "io80_ownership_inferred_code",
    "io240_ownership_inferred_code",
)
IO_RESOLUTION_COLUMNS = (*DIRECT_COLUMNS, *RESOLVED_COLUMNS, *OWNERSHIP_INFERRED_COLUMNS)
# Carried into the QGIS layer next to the candidate sets.
IO_RESOLVED_GIS_COLUMNS = (*RESOLVED_COLUMNS, "direct_io_status")

DIRECT_BLOCKED_PSIC = frozenset(
    {"NOT_PSIC_ACTIVITY", "UNCODEABLE", "REVIEW_ENTITY_MATCH", "CONFLICT"}
)
AUXILIARY_DIRECT_KEYS = frozenset({"brand", "cuisine", "operator"})
OWNERSHIP_ACCEPTED_PSIC = frozenset({"SINGLE", "INTERSECT", "NESTED", "MANUAL"})
# domain -> side -> (IO80, IO240); checked against the packaged concordance by ownership_io.
OWNERSHIP_PAIRS = {
    "education": {"public": ("74", "229"), "private": ("75", "230")},
    "health": {"public": ("76", "231"), "private": ("77", "232")},
}
_RANK = {
    "explicit": 4,
    "psic+institution": 3,
    "psic": 2,
    "psic+direct": 1,
    "direct": 1,
    "draft_subgroup": 1,
    "ownership_source": 1,
    "ownership_hint": 1,
    "draft_bridge": 1,
}


@dataclass(frozen=True, slots=True)
class DirectRule:
    io80_code: str
    any_terms: tuple[str, ...]
    all_terms: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class DirectRules:
    rules: tuple[DirectRule, ...]
    sha256: str


def scalar_text(value: Any) -> str:
    """Normalize scalar missing values without ambiguous array truth tests."""
    if not pd.api.types.is_scalar(value):
        raise ValueError("classification fields must be scalar")
    if value is None or (not isinstance(value, str) and bool(pd.isna(value))):
        return ""
    return str(value).strip()


_text = scalar_text


def _checked_ids(frame: pd.DataFrame) -> list[str]:
    if "canonical_id" not in frame.columns:
        raise ValueError("missing canonical_id")
    ids = [scalar_text(value) for value in frame["canonical_id"]]
    if any(not cid for cid in ids) or len(set(ids)) != len(ids):
        raise ValueError("missing or duplicate canonical_id")
    return ids


def _codes(value: Any) -> tuple[str, ...]:
    return tuple(dict.fromkeys(p.strip() for p in _text(value).split(";") if p.strip()))


def draft_dependencies(value: Any, *, provisional: bool) -> frozenset[str]:
    """Validate per-level draft metadata, treating legacy omissions conservatively.

    An explicit ``none`` records that comparison found no draft-dependent level.
    Missing/blank metadata on a provisional row is not proof of independence.
    """
    text = scalar_text(value)
    if not text:
        return frozenset(RESOLUTIONS) if provisional else frozenset()
    if text == "none" and provisional:
        return frozenset()
    tokens = [part.strip() for part in text.split(";")]
    if (
        not provisional
        or len(tokens) != len(set(tokens))
        or not set(tokens) <= set(RESOLUTIONS)
    ):
        raise ValueError(f"invalid io_mapping_draft_levels: {text!r}")
    return frozenset(tokens)


def _term(value: str, where: str) -> str:
    text = value.strip().casefold()
    if "=" not in text:
        raise ValueError(f"{where}: direct I-O term must be key=value or key=, got {value!r}")
    key, val = (part.strip() for part in text.split("=", 1))
    if not key:
        raise ValueError(f"{where}: direct I-O term has no key: {value!r}")
    return f"{key}={val}"


def load_direct_rules(hierarchy: IOHierarchy) -> DirectRules:
    """Packaged tag rules, grouped by IO80 code and reason, each code checked in the hierarchy."""
    data = resources.files("placetype_ph").joinpath("data", DIRECT_RULES_FILE).read_bytes()
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
    if tuple(reader.fieldnames or ()) != ("io80_code", "term", "term_kind", "reason"):
        raise ValueError(f"{DIRECT_RULES_FILE} must have io80_code,term,term_kind,reason")
    grouped: dict[tuple[str, str], dict[str, list[str]]] = {}
    for line, row in enumerate(reader, start=2):
        where = f"{DIRECT_RULES_FILE} line {line}"
        code = (row["io80_code"] or "").strip()
        if code not in hierarchy.io80_parent:
            raise ValueError(f"{where}: unknown IO80 code {code!r}")
        kind = (row["term_kind"] or "").strip()
        if kind not in {"any", "all"}:
            raise ValueError(f"{where}: term_kind must be any or all")
        reason = (row["reason"] or "").strip() or "direct tag rule"
        grouped.setdefault((code, reason), {"any": [], "all": []})[kind].append(
            _term(row["term"] or "", where)
        )
    rules = tuple(
        DirectRule(code, tuple(terms["any"]), tuple(terms["all"]), reason)
        for (code, reason), terms in grouped.items()
    )
    return DirectRules(rules, hashlib.sha256(data).hexdigest())


def parse_tags(category: Any) -> list[tuple[str, str]]:
    """Use the same normalized OSM parser as the activity evidence stage."""
    return list(osm_pairs(_text(category)))


def _term_matches(term: str, tags: list[tuple[str, str]]) -> bool:
    key, value = term.split("=", 1)
    return (key, value) in tags if value else any(k == key for k, _ in tags)


def direct_matches(category: Any, rules: DirectRules) -> list[tuple[str, str]]:
    """Distinct (IO80, reason) hits; primary tags first, auxiliary tags only as a fallback."""
    tags = parse_tags(category)
    primary = [tag for tag in tags if tag[0] not in AUXILIARY_DIRECT_KEYS]
    for subset in (primary, tags):
        hits: dict[str, str] = {}
        for rule in rules.rules:
            all_ok = all(_term_matches(t, subset) for t in rule.all_terms)
            any_ok = not rule.any_terms or any(_term_matches(t, subset) for t in rule.any_terms)
            if (rule.any_terms or rule.all_terms) and all_ok and any_ok:
                hits.setdefault(rule.io80_code, rule.reason)
        if hits:
            return list(hits.items())
    return []


def direct_evidence(
    place: Mapping[str, Any], rules: DirectRules, hierarchy: IOHierarchy
) -> dict[str, str]:
    hits = direct_matches(place.get("osm_category"), rules)
    out = dict.fromkeys(DIRECT_COLUMNS, "")
    if not hits:
        out["direct_io_status"] = "NO_RULE"
        return out
    codes = list(dict.fromkeys(code for code, _ in hits))
    out["direct_io80_candidates"] = "; ".join(codes)
    out["direct_io_reason"] = " | ".join(dict.fromkeys(reason for _, reason in hits))
    if len(codes) > 1:
        out["direct_io_status"] = "CONFLICT"
        return out
    out["direct_io_status"] = "SINGLE"
    out["direct_io80_code"] = codes[0]
    out["direct_io16_code"] = hierarchy.io80_parent[codes[0]]
    return out


def explicit_codes(places: pd.DataFrame, hierarchy: IOHierarchy) -> dict[str, dict[str, str]]:
    """Validated explicit IO codes per canonical_id, parents filled from the hierarchy."""
    _checked_ids(places)
    present = [col for col in EXPLICIT_IO_COLUMNS.values() if col in places.columns]
    if not present:
        return {}
    io16_codes = set(hierarchy.io80_parent.values())
    errors: list[str] = []
    out: dict[str, dict[str, str]] = {}
    for record in places[["canonical_id", *present]].to_dict("records"):
        given = {res: _text(record.get(col)) for res, col in EXPLICIT_IO_COLUMNS.items()}
        if not any(given.values()):
            continue
        cid = _text(record["canonical_id"])
        c16, c80, c240 = given["io16"], given["io80"], given["io240"]
        problems: list[str] = []
        if c240 and c240 not in hierarchy.io240_parent:
            problems.append(f"unknown io240 {c240!r}")
        if c80 and c80 not in hierarchy.io80_parent:
            problems.append(f"unknown io80 {c80!r}")
        if c16 and c16 not in io16_codes:
            problems.append(f"unknown io16 {c16!r}")
        if not problems:
            if c240:
                parent80 = hierarchy.io240_parent[c240]
                if c80 and c80 != parent80:
                    problems.append(f"io240 {c240} belongs to io80 {parent80}, not {c80}")
                c80 = c80 or parent80
            if c80:
                parent16 = hierarchy.io80_parent[c80]
                if c16 and c16 != parent16:
                    problems.append(f"io80 {c80} belongs to io16 {parent16}, not {c16}")
                c16 = c16 or parent16
        if problems:
            errors.append(f"{cid}: " + "; ".join(problems))
            continue
        out[cid] = {"io16": c16, "io80": c80, "io240": c240}
    if errors:
        shown = "\n  ".join(errors[:20])
        more = f"\n  ... and {len(errors) - 20} more" if len(errors) > 20 else ""
        raise ValueError(f"invalid explicit I-O codes on {len(errors)} row(s):\n  {shown}{more}")
    return out


def _resolve_level(
    *,
    candidates: tuple[str, ...],
    map_code: str,
    map_status: str,
    refined: str,
    direct_code: str,
    direct_status: str,
    direct_allowed: bool,
    explicit: str,
) -> tuple[str, str, str]:
    if explicit:
        if map_code and map_code != explicit:
            return explicit, "EXPLICIT_OVERRIDES_PSIC", "explicit"
        if candidates and explicit not in candidates:
            return explicit, "EXPLICIT_OUTSIDE_PSIC_SET", "explicit"
        return explicit, "EXPLICIT_INPUT", "explicit"
    if refined:
        return refined, "VERIFIED_OWNERSHIP", "psic+institution"
    if map_code:
        if direct_allowed and direct_status == "SINGLE" and direct_code:
            if direct_code == map_code:
                return map_code, "PSIC_DIRECT_AGREE", "psic"
            return map_code, "PSIC_DIRECT_DISAGREE", "psic"
        status = "PSIC_MAP" if map_status in {"", "SINGLETON_CANDIDATE"} else "PSIC_RESOLVER"
        return map_code, status, "psic"
    if candidates:
        if direct_status == "SINGLE" and direct_code:
            if not direct_allowed:
                return "", "DIRECT_BLOCKED_BY_PSIC_STATUS", ""
            if direct_code in candidates:
                return direct_code, "HYBRID_RESOLVED", "psic+direct"
            return "", "PSIC_DIRECT_CONFLICT", "conflict"
        if direct_status == "CONFLICT":
            return "", "DIRECT_SOURCE_CONFLICT", "conflict"
        return "", "PSIC_AMBIGUOUS", ""
    if direct_status == "SINGLE" and direct_code:
        if direct_allowed:
            return direct_code, "DIRECT_FALLBACK", "direct"
        return "", "DIRECT_BLOCKED_BY_PSIC_STATUS", ""
    if direct_status == "CONFLICT":
        return "", "DIRECT_SOURCE_CONFLICT", "conflict"
    return "", "UNRESOLVED", ""


def _nest(
    levels: dict[str, tuple[str, str, str]],
    candidates: dict[str, tuple[str, ...]],
    hierarchy: IOHierarchy,
) -> dict[str, tuple[str, str, str]]:
    """Accept stronger constraints first; equal-rank ties prefer the coarser level.

    Never discard an otherwise compatible original parent when a different fine
    constraint conflicts with a stronger coarse constraint. Derived parents carry
    the authority of their strongest supporting constraint.
    """
    order = ("io16", "io80", "io240")
    conflict = ("", "LEVEL_CONFLICT", "conflict")
    result = dict(levels)
    accepted: dict[str, tuple[str, str, str]] = {}

    def parents(res: str, code: str) -> dict[str, str]:
        if res == "io240":
            if code not in hierarchy.io240_parent:
                raise ValueError(f"unknown resolved IO240 code {code!r}")
            p80 = hierarchy.io240_parent[code]
            return {"io240": code, "io80": p80, "io16": hierarchy.io80_parent[p80]}
        if res == "io80":
            if code not in hierarchy.io80_parent:
                raise ValueError(f"unknown resolved IO80 code {code!r}")
            return {"io80": code, "io16": hierarchy.io80_parent[code]}
        if code not in hierarchy.io80_parent.values():
            raise ValueError(f"unknown resolved IO16 code {code!r}")
        return {"io16": code}

    ranked = sorted(order, key=lambda res: (-_RANK.get(levels[res][2], 0), order.index(res)))
    for res in ranked:
        code, status, source = levels[res]
        if not code:
            continue
        claims = parents(res, code)
        rank = _RANK.get(source, 0)
        if any(k in accepted and accepted[k][0] != v for k, v in claims.items()):
            result[res] = conflict
            continue
        if rank < 3 and any(
            k not in accepted and k != res and candidates[k] and v not in candidates[k]
            for k, v in claims.items()
        ):
            result[res] = conflict
            continue
        for k, v in claims.items():
            if k not in accepted:
                reason = status if k == res else f"DERIVED_FROM_{res.upper()}"
                # A compatible original parent keeps its more informative status
                # when supported at the same or stronger rank.
                original = levels[k]
                if original[0] == v and _RANK.get(original[2], 0) >= rank:
                    accepted[k] = original
                else:
                    accepted[k] = (v, reason, source)
    result.update(accepted)
    return result


def _ownership_inference(record: Mapping[str, Any], place: Mapping[str, Any]) -> dict[str, str]:
    out = {
        "io_ownership_inferred_status": "ABSTAIN",
        "io_ownership_inferred_basis": "not_an_ownership_pair",
        "io80_ownership_inferred_code": "",
        "io240_ownership_inferred_code": "",
    }
    io80, io240 = set(_codes(record.get("io80_codes"))), set(_codes(record.get("io240_codes")))
    domain = next(
        (
            name
            for name, sides in OWNERSHIP_PAIRS.items()
            if io80 == {v[0] for v in sides.values()} and io240 == {v[1] for v in sides.values()}
        ),
        "",
    )
    if not domain:
        return out
    if _text(record.get("psic_status")) not in OWNERSHIP_ACCEPTED_PSIC:
        out["io_ownership_inferred_basis"] = "psic_status_not_accepted"
        return out
    explicit = _explicit_sides(place, domain)
    hint = (
        _text(record.get("ownership_candidate"))
        if _text(record.get("ownership_status")) == "PROVISIONAL"
        else ""
    )
    sides = set(explicit) | ({hint} if hint in {"public", "private"} else set())
    if len(sides) > 1:
        out["io_ownership_inferred_basis"] = "conflicting_ownership_evidence"
        return out
    if not sides:
        out["io_ownership_inferred_basis"] = "no_ownership_evidence"
        return out
    side = next(iter(sides))
    if side == "private" and operator_evidence(place):
        out["io_ownership_inferred_basis"] = "government_operator_vs_private_conflict"
        return out
    code80, code240 = OWNERSHIP_PAIRS[domain][side]
    out.update(
        {
            "io_ownership_inferred_status": "INFERRED",
            "io_ownership_inferred_basis": (
                "explicit_source_attribute" if explicit else "name_or_category_hint"
            ),
            "io80_ownership_inferred_code": code80,
            "io240_ownership_inferred_code": code240,
        }
    )
    return out


def resolve_io(
    summary: pd.DataFrame,
    places: pd.DataFrame,
    crosswalk: IOCrosswalk,
    *,
    rules: DirectRules | None = None,
    policy: str = "strict",
) -> pd.DataFrame:
    """Append direct evidence, resolved codes and ownership inferences; change no column."""
    if policy not in RESOLUTION_POLICIES:
        raise ValueError(f"unknown I-O resolution policy {policy!r}")
    hierarchy = crosswalk.hierarchy
    if hierarchy is None:
        raise ValueError("I-O resolution requires a crosswalk loaded with the official hierarchy")
    if set(IO_RESOLUTION_COLUMNS) & set(summary.columns):
        raise ValueError("I-O resolution columns already present; refusing to overwrite")
    rules = rules or load_direct_rules(hierarchy)
    summary_ids = _checked_ids(summary)
    place_ids = _checked_ids(places)
    if set(summary_ids) != set(place_ids):
        raise ValueError("summary and input canonical_id sets differ")
    relevant = [
        col for col in places.columns
        if col == "canonical_id" or col.startswith(("osm_", "overture_"))
    ]
    by_id = dict(zip(place_ids, places[relevant].to_dict("records"), strict=True))
    explicit = explicit_codes(places, hierarchy)
    has_refined = "io_ownership_refinement_status" in summary.columns
    has_draft_levels = "io_mapping_draft_levels" in summary.columns
    rows: list[dict[str, str]] = []
    for record in summary.to_dict("records"):
        cid = _text(record["canonical_id"])
        place = by_id[cid]
        direct = direct_evidence(place, rules, hierarchy)
        status = _text(record.get("psic_status"))
        allowed = status not in DIRECT_BLOCKED_PSIC
        provisional = _text(record.get("io_mapping_status")).startswith("PROVISIONAL")
        try:
            listed = draft_dependencies(
                record.get("io_mapping_draft_levels") if has_draft_levels else None,
                provisional=provisional,
            )
        except ValueError as exc:
            raise ValueError(f"canonical_id {cid!r}: {exc}") from exc
        draft = {res: res in listed for res in RESOLUTIONS}
        inference = _ownership_inference(record, place)
        refined = has_refined and _text(record.get("io_ownership_refinement_status")) == "REFINED"
        given = explicit.get(cid, {})
        candidates = {res: _codes(record.get(f"{res}_codes")) for res in RESOLUTIONS}
        levels = {}
        for res in RESOLUTIONS:
            map_code = _text(record.get(f"{res}_map_code"))
            withheld = len(candidates[res]) == 1 and draft[res] and policy != "extended"
            if draft[res] and policy != "extended":
                map_code = ""
            direct_code = {"io16": direct["direct_io16_code"],
                           "io80": direct["direct_io80_code"]}.get(res, "")
            direct_status = direct["direct_io_status"] if res != "io240" else ""
            if allowed and (
                policy == "strict" or (policy == "evidence-weighted" and not candidates[res])
            ):
                if not map_code:
                    direct_code, direct_status = "", ""
            if draft[res] and policy != "extended":
                direct_code, direct_status = "", ""
            levels[res] = _resolve_level(
                candidates=candidates[res],
                map_code=map_code,
                map_status=_text(record.get(f"{res}_map_status")),
                refined=_text(record.get(f"{res}_refined_map_code")) if refined else "",
                direct_code=direct_code,
                direct_status=direct_status,
                direct_allowed=allowed,
                explicit=given.get(res, ""),
            )
            if withheld and not levels[res][0] and levels[res][1] == "PSIC_AMBIGUOUS":
                # A single candidate exists but rests on a draft bridge row; it is not an
                # ambiguous set, and the strict and evidence-weighted policies do not adopt it.
                levels[res] = ("", "PROVISIONAL_BRIDGE_NOT_RESOLVED", "")
        if policy == "extended":
            for res, level in levels.items():
                if draft[res] and level[0] and level[2] == "psic":
                    levels[res] = (level[0], "PROVISIONAL_PSIC_MAP", "draft_bridge")
        # Inference is never allowed to replace an explicit, verified or mapped code.
        if (
            allowed and status in OWNERSHIP_ACCEPTED_PSIC
            and (not (draft["io80"] or draft["io240"]) or policy == "extended")
        ):
            if policy == "extended":
                for res in RESOLUTIONS:
                    subgroup = _codes(record.get(f"{res}_subgroup_codes"))
                    basis = _text(record.get("io_subgroup_refinement_basis"))
                    if (not levels[res][0] and basis == "draft" and len(subgroup) == 1
                            and subgroup[0] in candidates[res]):
                        levels[res] = (subgroup[0], "DRAFT_SUBGROUP", "draft_subgroup")
            if policy != "strict" and inference["io_ownership_inferred_status"] == "INFERRED":
                basis = inference["io_ownership_inferred_basis"]
                if policy == "extended" or basis == "explicit_source_attribute":
                    source = (
                        "ownership_source"
                        if basis == "explicit_source_attribute"
                        else "ownership_hint"
                    )
                    proposed = {
                        res: inference[f"{res}_ownership_inferred_code"]
                        for res in ("io80", "io240")
                    }
                    if all(
                        not levels[res][0] or levels[res][0] == val for res, val in proposed.items()
                    ):
                        for res, val in proposed.items():
                            if not levels[res][0] and val in candidates[res]:
                                levels[res] = (val, "INFERRED_OWNERSHIP", source)
        levels = _nest(levels, candidates, hierarchy)
        out = dict(direct)
        for res in RESOLUTIONS:
            code, res_status, source = levels[res]
            out[f"{res}_resolved_code"] = code
            out[f"{res}_resolved_status"] = res_status
            out[f"{res}_resolved_source"] = source
        for res in ("io80", "io240"):
            inferred_code = inference[f"{res}_ownership_inferred_code"]
            resolved_code = levels[res][0]
            if inferred_code and resolved_code and inferred_code != resolved_code:
                inference["io_ownership_inferred_status"] = "ABSTAIN"
                inference["io_ownership_inferred_basis"] = "contradicts_resolved_classification"
                inference["io80_ownership_inferred_code"] = ""
                inference["io240_ownership_inferred_code"] = ""
                break
        out.update(inference)
        rows.append(out)
    result = summary.copy()
    extra = pd.DataFrame(rows, index=summary.index, columns=list(IO_RESOLUTION_COLUMNS))
    for column in IO_RESOLUTION_COLUMNS:
        result[column] = extra[column].astype("string")
    for column in summary.columns:
        if not result[column].equals(summary[column]):
            raise RuntimeError(f"I-O resolution modified existing column {column}")
    return result


def resolution_metadata(summary: pd.DataFrame, rules: DirectRules | None) -> dict[str, object]:
    """Counts for run.json; the reference hashes of the mapping are recorded elsewhere."""
    out: dict[str, object] = {"policy": POLICY}
    if rules is not None:
        out["direct_rules"] = {
            "file": DIRECT_RULES_FILE,
            "sha256": rules.sha256,
            "rules": len(rules.rules),
        }
    if "direct_io_status" in summary.columns:
        out["direct_status_counts"] = _counts(summary["direct_io_status"])
        for res in RESOLUTIONS:
            out[f"{res}_resolved"] = {
                "coded_rows": int(summary[f"{res}_resolved_code"].fillna("").ne("").sum()),
                "status_counts": _counts(summary[f"{res}_resolved_status"]),
                "source_counts": _counts(summary[f"{res}_resolved_source"]),
            }
        out["ownership_inferred_counts"] = _counts(summary["io_ownership_inferred_status"])
    return out


def _counts(series: pd.Series) -> dict[str, int]:
    counts = Counter(_text(v) for v in series.tolist())
    return {str(k): int(v) for k, v in sorted(counts.items())}
