"""Additive I/O candidate narrowing from verified institutional ownership.

Existing IOCrosswalk is the only primary I/O projection authority. This module
neither modifies candidate sets nor infers economic activity; refined columns
are produced only after the Priority 3c institutional stage was explicitly run.
"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from typing import Any

import pandas as pd

from ..io_mapping import IOCrosswalk
from .institutional_promotion import NEW_FIELDS, PROMOTABLE

POLICY = "institutional-io-candidate-narrowing-v2"
# Agreement between rules or sources (INTERSECT, NESTED) is stronger activity evidence than a
# single rule, not weaker; a user-supplied PSIC code (MANUAL) is accepted as given.
ACCEPTED_PSIC_STATUSES = frozenset({"SINGLE", "INTERSECT", "NESTED", "MANUAL"})
EXACT_PAIR_REASON = "verified_ownership_exact_reference_pair"

# Guardrails verified against the bundled PSIC 2019/PSA IO2018 concordance.
# Not a substitute for loading and checking the actual reference data.
_EXPECTED = {
    "education": {
        "division": "85",
        "io16": "14",
        "public": ("74", "229", "Public education"),
        "private": ("75", "230", "Private education"),
    },
    "health": {
        "division": "86",
        "io16": "15",
        "public": ("76", "231", "Public human health"),
        "private": ("77", "232", "Private human health"),
    },
}

REFINED_FIELDS = (
    "io_ownership_refinement_status",
    "io_ownership_refinement_reason",
    "io_ownership_refinement_basis",
    "io_ownership_refinement_reference",
    "io16_refined_codes",
    "io80_refined_codes",
    "io240_refined_codes",
    "io80_refined_map_code",
    "io240_refined_map_code",
)

_REQUIRED_IO = frozenset(
    {
        "canonical_id",
        "psic_code",
        "psic_status",
        "io_mapping_status",
        "io_mapping_psic_level",
        "io_mapping_psic_code",
        "io_mapping_bridge_basis",
        "io16_codes",
        "io80_codes",
        "io240_codes",
        "io80_map_code",
        "io240_map_code",
        "io80_map_status",
        "io240_map_status",
    }
)


def _text(value: Any) -> str:
    if value is None or (not isinstance(value, str) and bool(pd.isna(value))):
        return ""
    return str(value).strip()


def _parse(value: Any) -> tuple[str, ...]:
    raw = _text(value)
    if not raw:
        return ()
    parts = tuple(p.strip() for p in raw.split(";"))
    if any(not p for p in parts) or len(set(parts)) != len(parts):
        raise ValueError("invalid or duplicate I/O candidate code")
    return parts


@lru_cache(maxsize=1)
def _verified_pairs() -> dict[str, dict[str, Any]]:
    """Refuse to narrow if bundled mapping versions/codes/names drift."""
    table = IOCrosswalk.load_default()
    result: dict[str, dict[str, Any]] = {}
    for domain, expected in _EXPECTED.items():
        row = table.concordance_row(expected["division"])
        if row is None:
            raise ValueError(f"reference lacks expected PSIC division for {domain}")
        io16 = row.io16
        io80 = row.io80
        io240 = row.io240
        if tuple(io16.codes) != (expected["io16"],):
            raise ValueError(f"{domain}: IO16 industry reference drift")
        if len(io80.codes) != 2 or len(io240.codes) != 2:
            raise ValueError(f"{domain}: public/private reference pair not exact")
        expected80 = {expected[side][0]: expected[side][2] for side in ("public", "private")}
        expected240 = {expected[side][1]: expected[side][2] for side in ("public", "private")}
        observed80 = dict(zip(io80.codes, io80.names, strict=True))
        observed240 = dict(zip(io240.codes, io240.names, strict=True))
        if observed80 != expected80 or observed240 != expected240:
            raise ValueError(f"{domain}: packaged public/private reference changed")
        result[domain] = {
            "division": expected["division"],
            "io16": expected["io16"],
            "io80": frozenset(expected80),
            "io240": frozenset(expected240),
            "sides": {
                side: (expected[side][0], expected[side][1]) for side in ("public", "private")
            },
        }
    return result


def refine_verified_institutional_io(summary: pd.DataFrame) -> pd.DataFrame:
    """Append optional I/O refinement while preserving every existing field.

    If Priority 3c was not enabled, return the original object unchanged.
    On opt-in input, malformed schema fails closed. Non-eligible rows preserve
    their original sets in new refinement columns and record abstention reasons.
    """
    institutional = set(NEW_FIELDS) & set(summary.columns)
    if not institutional:
        return summary
    if institutional != set(NEW_FIELDS):
        raise ValueError("partial institutional resolution columns")
    missing = _REQUIRED_IO - set(summary.columns)
    if missing:
        raise ValueError(f"I/O refinement requires existing crosswalk fields: {sorted(missing)}")
    if set(REFINED_FIELDS) & set(summary.columns):
        raise ValueError("I/O refinement output fields already exist; refusing overwrite")
    if not summary.index.is_unique:
        raise ValueError("classified row index must be unique")
    ids = summary["canonical_id"]
    if (
        ids.isna().any()
        or ids.astype("string").str.strip().eq("").any()
        or ids.astype("string").duplicated().any()
    ):
        raise ValueError("missing or duplicate canonical_id")
    pairs = _verified_pairs()
    decisions: list[dict[str, str]] = []
    for record in summary.to_dict("records"):
        io16 = _parse(record["io16_codes"])
        io80 = _parse(record["io80_codes"])
        io240 = _parse(record["io240_codes"])
        decision = {
            "io_ownership_refinement_status": "ABSTAIN",
            "io_ownership_refinement_reason": "no_verified_ownership",
            "io_ownership_refinement_basis": "",
            "io_ownership_refinement_reference": "",
            "io16_refined_codes": _text(record["io16_codes"]),
            "io80_refined_codes": _text(record["io80_codes"]),
            "io240_refined_codes": _text(record["io240_codes"]),
            "io80_refined_map_code": _text(record["io80_map_code"]),
            "io240_refined_map_code": _text(record["io240_map_code"]),
        }
        status = _text(record["institution_resolution_status"])
        ownership = _text(record["institution_resolved_ownership"]).casefold()
        if status != "RESOLVED":
            if ownership:
                raise ValueError("unresolved institution unexpectedly has verified ownership")
            decisions.append(decision)
            continue
        if ownership not in {"public", "private"}:
            raise ValueError("resolved institution has invalid ownership")
        if _text(record["institution_evidence_status"]) not in PROMOTABLE:
            decision["io_ownership_refinement_reason"] = "institution_evidence_not_verifiable"
        elif not _text(record["institution_resolution_reference"]) or not _text(
            record["institution_resolution_basis"]
        ):
            decision["io_ownership_refinement_reason"] = "institution_provenance_incomplete"
        elif _text(record["psic_status"]) not in ACCEPTED_PSIC_STATUSES:
            decision["io_ownership_refinement_reason"] = "psic_status_not_accepted"
        elif _text(record["io_mapping_status"]) not in {"MAPPED_GROUP", "MAPPED_DIVISION"}:
            decision["io_ownership_refinement_reason"] = "mapping_not_confirmed_at_specific_level"
        elif _text(record["io_mapping_bridge_basis"]) not in {"reviewed", "code_title_match"}:
            decision["io_ownership_refinement_reason"] = "bridge_not_reviewed"
        elif _text(record["io80_map_code"]) or _text(record["io240_map_code"]):
            decision["io_ownership_refinement_reason"] = "preexisting_io_resolution"
        elif (
            _text(record["io80_map_status"]) != "AMBIGUOUS_SET"
            or _text(record["io240_map_status"]) != "AMBIGUOUS_SET"
        ):
            decision["io_ownership_refinement_reason"] = "io_candidate_status_not_ambiguous"
        else:
            psic_code = _text(record["psic_code"])
            bridge_code = _text(record["io_mapping_psic_code"])
            domain = next(
                (
                    name
                    for name, p in pairs.items()
                    if psic_code.startswith(p["division"])
                    and bridge_code.startswith(p["division"])
                    and _text(record["io_mapping_psic_level"]) in {"division", "group"}
                ),
                None,
            )
            if domain is None:
                decision["io_ownership_refinement_reason"] = "unsupported_psic_domain"
            else:
                pair = pairs[domain]
                if (
                    io16 != (pair["io16"],)
                    or frozenset(io80) != pair["io80"]
                    or frozenset(io240) != pair["io240"]
                    or len(io80) != 2
                    or len(io240) != 2
                ):
                    decision["io_ownership_refinement_reason"] = "candidate_set_not_exact_pair"
                else:
                    code80, code240 = pair["sides"][ownership]
                    decision.update(
                        {
                            "io_ownership_refinement_status": "REFINED",
                            "io_ownership_refinement_reason": EXACT_PAIR_REASON,
                            "io_ownership_refinement_basis": _text(
                                record["institution_resolution_basis"]
                            ),
                            "io_ownership_refinement_reference": _text(
                                record["institution_resolution_reference"]
                            ),
                            "io80_refined_codes": code80,
                            "io240_refined_codes": code240,
                            "io80_refined_map_code": code80,
                            "io240_refined_map_code": code240,
                        }
                    )
        decisions.append(decision)
    result = summary.copy(deep=True)
    for field in REFINED_FIELDS:
        result[field] = [row[field] for row in decisions]
    if result.shape[0] != summary.shape[0]:
        raise RuntimeError("I/O refinement changed entity count")
    for field in summary.columns:
        if not result[field].equals(summary[field]):
            raise RuntimeError(f"I/O refinement modified existing field {field}")
    return result


def ownership_io_metadata(summary: pd.DataFrame) -> dict[str, Any] | None:
    """Manifest-ready counts; actual reference hashes are in io_mapping provenance."""
    if "io_ownership_refinement_status" not in summary:
        return None
    counts = Counter(summary["io_ownership_refinement_status"].tolist())
    return {
        "policy": POLICY,
        "status_counts": dict(sorted((str(k), int(v)) for k, v in counts.items())),
        "refined_rows": int(counts.get("REFINED", 0)),
        "original_io_sets_preserved": True,
        "reference_validation": "packaged_concordance_division_pairs_checked",
    }
