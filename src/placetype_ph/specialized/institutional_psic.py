"""Diagnostic: the public or private PSIC subtree implied by a known ownership side.

PSIC Revision 5 splits some education and health activities by ownership in the titles of
their leaves (for example 8611 "Public hospital activities" and 8612 "Private hospital
activities"). When a place has an ownership side from institution evidence or from a
labelled inference, this step reports which part of the place's PSIC subtree has that
side, and whether the I-O mapping of that part gives exactly the expected public or
private I-O pair. It never changes ``psic_code`` or any resolved I-O code.

Statuses in ``institution_psic_proposal_status``:

- ``MAPPED_FROM_PSIC``: every leaf of the PSIC code names its ownership, the leaves of
  the given side form the proposed subtree, and its I-O mapping is exactly the expected
  pair member (for example 8611 maps to IO80 76 and IO240 231). The mapping includes the
  draft class and subclass narrowing; the note then says ``io_narrowing_basis=draft``.
- ``MAPPED_IO_ONLY_PSIC_COARSE``: the PSIC code has leaves that do not name an ownership,
  so no PSIC subtree is proposed; both the row's candidate sets and the mapping of the
  unchanged PSIC code are exactly the public/private pair, so only the I-O side is
  proposed.
- ``SIDE_NOT_IN_PSIC_SUBTREE``: no leaf of the PSIC code has the given side.
- ``PSIC_IO_MAPPING_UNPROVEN``: the proposed subtree maps to other I-O codes.
- ``ROW_IO_PAIR_INCOMPLETE``: the row's candidate sets are not the full pair.
- ``PARENT_PSIC_IO_PAIR_MISMATCH``: the mapping of the PSIC code is not the pair.
- ``UNKNOWN_PSIC``: the PSIC code is not in the taxonomy.

``institution_psic_proposal_baseline_conflict`` records when the side differs from the
side the row already carries (a provisional name hint or a resolved I-O code).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Any

import pandas as pd

from ..io_mapping import IOCrosswalk
from ..taxonomy import Taxonomy

POLICY = "institution-psic-ownership-proposal-v1"
DOMAINS = {
    "education": {"public": ("74", "229"), "private": ("75", "230")},
    "health": {"public": ("76", "231"), "private": ("77", "232")},
}
SIDE_BY_IO80 = {"74": "public", "75": "private", "76": "public", "77": "private"}
PROPOSAL_COLUMNS = (
    "institution_psic_proposal_status",
    "institution_psic_proposal_side",
    "institution_psic_proposal_side_basis",
    "institution_psic_proposal_codes",
    "institution_psic_proposal_single",
    "institution_psic_proposal_io80",
    "institution_psic_proposal_io240",
    "institution_psic_proposal_note",
    "institution_psic_proposal_baseline_conflict",
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _codes(value: Any) -> frozenset[str]:
    return frozenset(x.strip() for x in re.split(r"[;|]", _text(value)) if x.strip())


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text.casefold()).strip()


def domain_of(row: dict[str, Any]) -> str:
    """The domain whose public/private pair is the row's IO80 or IO240 candidate set."""
    p80, p240 = _codes(row.get("io80_codes")), _codes(row.get("io240_codes"))
    for domain, sides in DOMAINS.items():
        if p80 == {v[0] for v in sides.values()} or p240 == {v[1] for v in sides.values()}:
            return domain
    return ""


def compress_tree(taxonomy: Taxonomy, root: str, allowed: frozenset[str]) -> tuple[str, ...]:
    """Smallest set of disjoint subtrees whose leaves are exactly ``allowed``."""

    def walk(code: str) -> list[str]:
        leaves = taxonomy.leaves(code)
        if leaves <= allowed:
            return [code]
        if not leaves & allowed:
            return []
        return [x for child in taxonomy.children(code) for x in walk(child)]

    return tuple(walk(root))


def _side(row: dict[str, Any]) -> tuple[str, str]:
    """Ownership side and where it comes from, strongest evidence first."""
    resolved = _text(row.get("institution_resolved_ownership"))
    if resolved in {"public", "private"}:
        basis = _text(row.get("institution_resolution_basis")) or "verified"
        return resolved, f"institution_resolved:{basis}"
    candidate = _text(row.get("institution_candidate_ownership"))
    if candidate in {"public", "private"}:
        status = _text(row.get("institution_evidence_status")) or "candidate"
        return candidate, f"institution_candidate:{status}"
    if _text(row.get("io_ownership_inferred_status")) == "INFERRED":
        side = SIDE_BY_IO80.get(_text(row.get("io80_ownership_inferred_code")), "")
        if side:
            basis = _text(row.get("io_ownership_inferred_basis")) or "inferred"
            return side, f"inferred:{basis}"
    return "", ""


class ProposalBuilder:
    def __init__(self, taxonomy: Taxonomy, io_crosswalk: IOCrosswalk) -> None:
        self.taxonomy = taxonomy
        self.io = io_crosswalk
        self._cache: dict[tuple[str, str, str], dict[str, str]] = {}

    def _map(self, code: str) -> dict[str, str]:
        """I-O sets of a PSIC code, with the class and subclass narrowing applied.

        The narrowing table is draft; its basis is carried into the note so that a
        proposal resting on it stays visibly provisional.
        """
        base = self.io.map_psic(code, self.taxonomy)
        narrowed = self.io.subgroup(code, self.taxonomy, base)
        return {
            "io80_codes": narrowed["io80_subgroup_codes"],
            "io240_codes": narrowed["io240_subgroup_codes"],
            "basis": narrowed["io_subgroup_refinement_basis"],
        }

    def _subtree(self, psic_code: str, side: str, domain: str) -> dict[str, str]:
        key = (psic_code, side, domain)
        if key in self._cache:
            return self._cache[key]
        result = {"status": "", "codes": "", "single": "", "io80": "", "io240": "", "note": ""}
        if psic_code not in self.taxonomy.nodes:
            result["status"] = "UNKNOWN_PSIC"
            self._cache[key] = result
            return result
        typed: dict[str, str] = {}
        for leaf in self.taxonomy.leaves(psic_code):
            title = _norm(self.taxonomy.get(leaf).title)
            public = bool(re.search(r"\bpublic\b", title))
            private = bool(re.search(r"\bprivate\b", title))
            typed[leaf] = (
                "public" if public and not private else "private" if private and not public else ""
            )
        if any(not value for value in typed.values()):
            untyped = sum(not value for value in typed.values())
            result.update(status="PSIC_OWNERSHIP_NOT_EXHAUSTIVE", note=f"untyped_leaves={untyped}")
        elif not any(value == side for value in typed.values()):
            result["status"] = "SIDE_NOT_IN_PSIC_SUBTREE"
        else:
            allowed = frozenset(leaf for leaf, value in typed.items() if value == side)
            roots = compress_tree(self.taxonomy, psic_code, allowed)
            result["codes"] = "|".join(roots)
            result["single"] = roots[0] if len(roots) == 1 else ""
            maps = [self._map(code) for code in roots]
            io80 = frozenset().union(*(_codes(m.get("io80_codes")) for m in maps))
            io240 = frozenset().union(*(_codes(m.get("io240_codes")) for m in maps))
            want80, want240 = DOMAINS[domain][side]
            bases = sorted({m["basis"] for m in maps if m["basis"]})
            if io80 == {want80} and io240 == {want240}:
                result.update(status="MAPPED_FROM_PSIC", io80=want80, io240=want240)
                if bases:
                    result["note"] = "io_narrowing_basis=" + "|".join(bases)
            else:
                result.update(
                    status="PSIC_IO_MAPPING_UNPROVEN",
                    note=(
                        f"mapped_io80={','.join(sorted(io80))};"
                        f"mapped_io240={','.join(sorted(io240))}"
                    ),
                )
        self._cache[key] = result
        return result

    def propose(self, row: dict[str, Any]) -> dict[str, str]:
        out = dict.fromkeys(PROPOSAL_COLUMNS, "")
        side, basis = _side(row)
        domain = domain_of(row)
        psic_code = _text(row.get("psic_code"))
        if not side or not domain or not psic_code:
            return out
        out["institution_psic_proposal_side"] = side
        out["institution_psic_proposal_side_basis"] = basis
        # The side the row already carries: a provisional name hint or a resolved I-O code.
        baseline = _text(row.get("ownership_candidate")) or SIDE_BY_IO80.get(
            _text(row.get("io80_resolved_code")), ""
        )
        if baseline in {"public", "private"} and baseline != side:
            out["institution_psic_proposal_baseline_conflict"] = f"baseline_{baseline}_vs_{side}"
        refined = dict(self._subtree(psic_code, side, domain))
        if refined["status"] == "PSIC_OWNERSHIP_NOT_EXHAUSTIVE":
            # Without ownership-typed leaves no PSIC subclass is proposed. The I-O side may
            # still be proposed when both the row's candidate sets and the mapping of the
            # unchanged PSIC code are exactly the public/private pair.
            pair80 = frozenset(v[0] for v in DOMAINS[domain].values())
            pair240 = frozenset(v[1] for v in DOMAINS[domain].values())
            row80, row240 = _codes(row.get("io80_codes")), _codes(row.get("io240_codes"))
            if row80 == pair80 and row240 == pair240:
                mapped = self._map(psic_code)
                if (
                    _codes(mapped.get("io80_codes")) == pair80
                    and _codes(mapped.get("io240_codes")) == pair240
                ):
                    want80, want240 = DOMAINS[domain][side]
                    refined = {
                        "status": "MAPPED_IO_ONLY_PSIC_COARSE",
                        "codes": "",
                        "single": "",
                        "io80": want80,
                        "io240": want240,
                        "note": f"psic_retained={psic_code};{refined['note']}",
                    }
                else:
                    refined["status"] = "PARENT_PSIC_IO_PAIR_MISMATCH"
                    refined["note"] = (
                        f"psic={psic_code};mapping_io80={mapped.get('io80_codes', '')};"
                        f"mapping_io240={mapped.get('io240_codes', '')}"
                    )
            else:
                refined["status"] = "ROW_IO_PAIR_INCOMPLETE"
                refined["note"] = (
                    f"psic={psic_code};row_io80={','.join(sorted(row80))};"
                    f"row_io240={','.join(sorted(row240))}"
                )
        out.update(
            institution_psic_proposal_status=refined["status"],
            institution_psic_proposal_codes=refined["codes"],
            institution_psic_proposal_single=refined["single"],
            institution_psic_proposal_io80=refined["io80"],
            institution_psic_proposal_io240=refined["io240"],
            institution_psic_proposal_note=refined["note"],
        )
        return out


def institution_psic_proposals(
    summary: pd.DataFrame, taxonomy: Taxonomy, io_crosswalk: IOCrosswalk
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return ``summary`` with the diagnostic proposal columns, and run metadata."""
    builder = ProposalBuilder(taxonomy, io_crosswalk)
    out = summary.copy()
    rows = [builder.propose(record) for record in out.to_dict("records")]
    frame = pd.DataFrame(rows, columns=list(PROPOSAL_COLUMNS), index=out.index)
    for column in PROPOSAL_COLUMNS:
        out[column] = frame[column].astype(str)
    counts = Counter(x for x in out["institution_psic_proposal_status"] if x)
    return out, {
        "policy": POLICY,
        "status_counts": dict(sorted(counts.items())),
        "note": "diagnostic only; psic_code and resolved I-O codes are not changed",
    }
