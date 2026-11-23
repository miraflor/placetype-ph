"""Optional candidate-set projection to an industry reference; no forced sector assignment.

Data are supplied separately. This module does not bundle reference tables or select
geographic partitions. Reference rows can be draft and never become official by use.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


def _read(path: Path, required: set[str]) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not required <= set(reader.fieldnames or ()):
            raise ValueError(f"reference is missing required columns: {path}")
        return [{key: str(value or "").strip() for key, value in row.items()} for row in reader]


def _codes(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(part.strip() for part in value.split(";") if part.strip()))


@dataclass(frozen=True)
class CandidateProjection:
    io16: tuple[str, ...] = ()
    io80: tuple[str, ...] = ()
    io240: tuple[str, ...] = ()
    status: str = "UNMAPPED"
    provisional: bool = False
    basis: str = ""


class IndustryReferences:
    """Reference rows can constrain candidates, never silently create invented children."""

    def __init__(self, bridge: Path, concordance: Path, refinement: Path, parents: Path):
        b = _read(bridge, {"rev5_code", "psic2019_codes", "basis"})
        c = _read(concordance, {"psic_code", "io16_codes", "io80_codes", "io240_codes"})
        r = _read(refinement, {"rev5_code", "io240_codes", "basis", "rev5_title"})
        p = _read(parents, {"io240_code", "io80_code"})
        self.bridge = {x["rev5_code"]: x for x in b}
        self.concordance = {x["psic_code"]: x for x in c}
        self.refinement = {x["rev5_code"]: x for x in r}
        self.parents = {x["io240_code"]: x["io80_code"] for x in p}
        if (
            len(self.bridge) != len(b)
            or len(self.concordance) != len(c)
            or len(self.refinement) != len(r)
        ):
            raise ValueError("duplicate codes in industry references")
        if len(self.parents) != len(p):
            raise ValueError("duplicate fine-industry parent codes")
        if len(self.parents) != 240:
            raise ValueError("industry parent table must contain 240 distinct industries")
        if any(not x["io80_code"] or len(x["io80_code"]) != 2 for x in p):
            raise ValueError("invalid industry parent code")

    def project(self, psic_code: str, *, psic_title: str = "") -> CandidateProjection:
        code = str(psic_code or "").strip()
        # Textual longest ancestor in the correct code syntax. A letter section is
        # not inferred from numeric code prefixes; the bridge must specify it.
        ancestors = [
            key
            for key in self.bridge
            if key == code or (key.isdigit() and code.isdigit() and code.startswith(key))
        ]
        if not ancestors:
            return CandidateProjection(status="UNMAPPED_BRIDGE")
        link = self.bridge[max(ancestors, key=lambda a: (len(a), a))]
        peers = _codes(link["psic2019_codes"])
        if not peers or any(peer not in self.concordance for peer in peers):
            return CandidateProjection(status="UNMAPPED_PARTIAL_CONCORDANCE")
        mapped = [self.concordance[peer] for peer in peers]
        sets = [
            set().union(*(_codes(row[field]) for row in mapped))
            for field in ("io16_codes", "io80_codes", "io240_codes")
        ]
        provisional = link["basis"].casefold() in {"draft", "inferred"}
        basis = "bridge"
        matching_refinement = [
            key
            for key in self.refinement
            if key == code or (key.isdigit() and code.isdigit() and code.startswith(key))
        ]
        if matching_refinement:
            ref = self.refinement[max(matching_refinement, key=len)]
            proposal = set(_codes(ref["io240_codes"]))
            if (
                ref["rev5_code"] == code
                and psic_title
                and ref["rev5_title"].casefold() != psic_title.casefold()
            ):
                return CandidateProjection(
                    tuple(sorted(sets[0])),
                    tuple(sorted(sets[1])),
                    tuple(sorted(sets[2])),
                    "REFINEMENT_TITLE_CONFLICT",
                    True,
                    "bridge_only",
                )
            if proposal and proposal <= sets[2]:
                sets[2] = proposal
                sets[1] = {self.parents[x] for x in proposal}
                provisional = True  # this refinement is never upgraded to official
                basis = "bridge_plus_draft_refinement"
            elif proposal:
                return CandidateProjection(
                    tuple(sorted(sets[0])),
                    tuple(sorted(sets[1])),
                    tuple(sorted(sets[2])),
                    "REFINEMENT_WOULD_WIDEN",
                    True,
                    "bridge_only",
                )
        if any(code not in self.parents for code in sets[2]):
            raise ValueError("concordance contains unknown fine-industry code")
        if {self.parents[x] for x in sets[2]} - sets[1]:
            raise ValueError("industry parent links are inconsistent with concordance")
        return CandidateProjection(
            tuple(sorted(sets[0])),
            tuple(sorted(sets[1])),
            tuple(sorted(sets[2])),
            "CANDIDATES",
            provisional,
            basis,
        )
