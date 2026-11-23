"""Conservative source-dependent activity fusion; never promotes primary taxonomy labels.

A source is one dependency group, even if represented by several category fields.
A canonical name is an additional clue, not independent corroboration of source facts.
The optional ancestors callback is the authoritative taxonomy hierarchy; without it,
only numeric prefix nesting is inferred provisionally. Disagreements abstain.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .model import Evidence

AncestorLookup = Callable[[str], Iterable[str]]
REVIEWED_METHODS = frozenset({"reviewed_crosswalk", "reviewed", "manual", "user_supplied"})


@dataclass(frozen=True)
class FusionResult:
    candidate_codes: tuple[str, ...]
    status: str
    reason: str
    flags: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    hierarchy_basis: str = "numeric_prefix_provisional"


def _relation(left: str, right: str, ancestors: AncestorLookup | None) -> str:
    """Determine whether left is a parent/child of right (not a similarity score)."""
    if left == right:
        return "equal"
    if ancestors is not None:
        parent_left = set(ancestors(left))
        parent_right = set(ancestors(right))
        if left in parent_right:
            return "left_ancestor"
        if right in parent_left:
            return "right_ancestor"
        return "disjoint"
    # Without a loaded official hierarchy, do not infer letter-section ancestry.
    if not left.isdigit() or not right.isdigit():
        return "unknown"
    if right.startswith(left):
        return "left_ancestor"
    if left.startswith(right):
        return "right_ancestor"
    return "disjoint"


def _deepest_nested(values: set[str], ancestors: AncestorLookup | None) -> tuple[str, str]:
    """Return deepest compatible code; otherwise a conflict/uncertainty status."""
    if not values:
        return "", "empty"
    ordered = sorted(values, key=lambda code: (-len(code), code))
    winner = ordered[0]
    for code in ordered[1:]:
        relation = _relation(code, winner, ancestors)
        if relation == "unknown":
            return "", "unknown"
        if relation not in {"equal", "left_ancestor"}:
            return "", "disjoint"
    return winner, "nested"


def _shared_parent(codes: set[str], ancestors: AncestorLookup | None) -> str:
    """Back off only when all independent candidates share an *immediate* parent."""
    if ancestors is None or len(codes) < 2:
        return ""
    immediate: set[str] = set()
    for code in codes:
        lineage = set(ancestors(code))
        direct = [
            parent
            for parent in lineage
            if not any(parent != other and parent in set(ancestors(other)) for other in lineage)
        ]
        if len(direct) != 1:
            return ""
        immediate.add(direct[0])
    return next(iter(immediate)) if len(immediate) == 1 else ""


def fuse_activity(
    hits: tuple[Evidence, ...],
    *,
    original_code: str = "",
    original_method: str = "",
    ancestors: AncestorLookup | None = None,
) -> FusionResult:
    """Fuse evidence with abstention on disjoint/unknown category constraints.

    A compatible broad category cannot erase a precise name interpretation; a
    conflicting broad category also cannot be silently ignored. Independent
    disjoint branches require review, except for a verifiable shared ancestor,
    returned as a *candidate* rather than an automatic classifier decision.
    """
    grouped: dict[str, list[Evidence]] = defaultdict(list)
    for hit in hits:
        if hit.code:
            grouped[hit.dependency_group or hit.source].append(hit)
    groups = tuple(sorted(grouped))
    precision = {hit.code for hit in hits if hit.strength == "PRECISE" and hit.code}
    broad = {hit.code for hit in hits if hit.strength == "BROAD" and hit.code}
    all_codes = tuple(sorted(precision | broad))
    basis = "taxonomy_ancestors" if ancestors is not None else "numeric_prefix_provisional"
    if not all_codes:
        return FusionResult(
            (), "NO_EVIDENCE", "no_matching_activity_rule", groups=groups, hierarchy_basis=basis
        )

    group_winners: set[str] = set()
    group_flags: set[str] = set()
    for group, evidence in sorted(grouped.items()):
        codes = {entry.code for entry in evidence}
        code, state = _deepest_nested(codes, ancestors)
        if state != "nested":
            kind = (
                "DEPENDENT_HIERARCHY_UNVERIFIED"
                if state == "unknown"
                else "DEPENDENT_SOURCE_CONFLICT"
            )
            group_flags.add(f"{kind}:{group}")
        else:
            group_winners.add(code)
    if group_flags:
        return FusionResult(
            all_codes,
            "REVIEW",
            "dependent_source_activity_disagreement",
            tuple(sorted(group_flags)),
            groups,
            basis,
        )

    best, state = _deepest_nested(group_winners, ancestors)
    if state == "unknown":
        return FusionResult(
            all_codes,
            "REVIEW",
            "unverified_source_hierarchy",
            ("INDEPENDENT_HIERARCHY_UNVERIFIED",),
            groups,
            basis,
        )
    if state == "disjoint":
        parent = _shared_parent(group_winners, ancestors)
        if parent:
            if (
                original_code
                and original_code != parent
                and original_method.casefold() in REVIEWED_METHODS
            ):
                return FusionResult(
                    (parent,),
                    "REVIEW",
                    "reviewed_classification_requires_review",
                    ("INDEPENDENT_SIBLING_BACKOFF",),
                    groups,
                    basis,
                )
            return FusionResult(
                (parent,),
                "BACKOFF_CANDIDATE",
                "verified_common_ancestor_backoff",
                ("INDEPENDENT_SIBLING_BACKOFF",),
                groups,
                basis,
            )
        return FusionResult(
            all_codes,
            "REVIEW",
            "independent_source_activity_disagreement",
            ("INDEPENDENT_SOURCE_CONFLICT",),
            groups,
            basis,
        )
    if not best:
        raise RuntimeError("activity fusion did not resolve a nonempty evidence set")

    # Broad observations never establish a precise code by themselves.
    if not precision:
        return FusionResult(
            (best,),
            "BROAD_CANDIDATE",
            "broad_activity_evidence_only",
            groups=groups,
            hierarchy_basis=basis,
        )
    precise_best, precise_state = _deepest_nested(precision, ancestors)
    if precise_state != "nested":
        return FusionResult(
            all_codes,
            "REVIEW",
            "precise_activity_disagreement",
            (
                "PRECISE_HIERARCHY_UNVERIFIED"
                if precise_state == "unknown"
                else "PRECISE_SOURCE_CONFLICT",
            ),
            groups,
            basis,
        )
    if best != precise_best:
        # Can occur when only a broad descendant contradicts the precise evidence.
        return FusionResult(
            all_codes,
            "REVIEW",
            "broad_category_exceeds_precise_evidence",
            ("BROAD_PRECISE_SCOPE_CONFLICT",),
            groups,
            basis,
        )

    if original_code and original_code != best:
        if original_method.casefold() in REVIEWED_METHODS:
            return FusionResult(
                (best,),
                "REVIEW",
                "reviewed_classification_requires_review",
                groups=groups,
                hierarchy_basis=basis,
            )
        relation = _relation(original_code, best, ancestors)
        if relation == "left_ancestor":
            return FusionResult(
                (best,),
                "REFINEMENT_CANDIDATE",
                "candidate_is_descendant_of_original",
                groups=groups,
                hierarchy_basis=basis,
            )
        return FusionResult(
            (best,),
            "REVIEW",
            "candidate_disagrees_with_original",
            groups=groups,
            hierarchy_basis=basis,
        )
    return FusionResult(
        (best,),
        "SUPPORTING_CANDIDATE",
        "compatible_precise_activity_evidence",
        groups=groups,
        hierarchy_basis=basis,
    )
