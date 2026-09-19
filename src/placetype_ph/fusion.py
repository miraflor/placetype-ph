from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from .models import CrosswalkEntry, FusionResult, FusionStatus, MappingKind, SourceEvidence
from .taxonomy import Taxonomy


def normalize_roots(taxonomy: Taxonomy, codes: Iterable[str]) -> list[str]:
    """Canonicalize a union of taxonomy subtrees.

    If one root contains another, the descendant adds nothing to the union and is
    removed. Crucially, this operates on the *declared roots* of the evidence rather
    than expanding them to leaves. Expanding a SUBTREE to leaves can invent precision
    on unary branches: a mapping to a division with one descendant chain would
    otherwise collapse all the way to a subclass.
    """
    valid = sorted(
        {str(code) for code in codes if str(code) in taxonomy.nodes},
        key=lambda code: (taxonomy.depth(code), code),
    )
    kept: list[str] = []
    for code in valid:
        if any(root in taxonomy.ancestors(code) for root in kept):
            continue
        kept.append(code)
    return kept


def entry_roots(taxonomy: Taxonomy, mapping: CrosswalkEntry | None) -> list[str]:
    """Rooted subtrees a single crosswalk row asserts. Empty for non-coding kinds."""
    if mapping is None:
        return []
    if mapping.mapping_kind in {MappingKind.NOT_ACTIVITY, MappingKind.UNCODEABLE}:
        return []
    return normalize_roots(taxonomy, mapping.codes)


def _mapping_roots(taxonomy: Taxonomy, evidence: SourceEvidence) -> list[str]:
    return entry_roots(taxonomy, evidence.mapping)


def intersect_subtrees(taxonomy: Taxonomy, left: Iterable[str], right: Iterable[str]) -> list[str]:
    """Intersect two unions of rooted subtrees without enumerating leaves."""
    out: list[str] = []
    for a in left:
        ancestors_a = set(taxonomy.ancestors(a))
        for b in right:
            if a == b:
                out.append(a)
            elif a in taxonomy.ancestors(b):
                # subtree(a) ∩ subtree(b) = subtree(b)
                out.append(b)
            elif b in ancestors_a:
                # subtree(a) ∩ subtree(b) = subtree(a)
                out.append(a)
    return normalize_roots(taxonomy, out)


def _union_subtrees(taxonomy: Taxonomy, *parts: Iterable[str]) -> list[str]:
    return normalize_roots(taxonomy, (code for part in parts for code in part))


def _representative_code(taxonomy: Taxonomy, evidence: SourceEvidence) -> str | None:
    roots = _mapping_roots(taxonomy, evidence)
    if not roots:
        return None
    if len(roots) == 1:
        return roots[0]
    return taxonomy.lca(roots)


def _independent_sibling_parent(
    taxonomy: Taxonomy,
    group_roots: list[list[str]],
) -> str | None:
    """Return a one-level PSIC backoff for independent sibling disagreement.

    This is deliberately narrower than arbitrary LCA backoff. Every independent
    dependency group must assert exactly one rooted subtree. At least two deepest
    asserted roots must be immediate siblings, and every shallower assertion must
    already be their common parent or one of its ancestors. We also refuse to back
    off all the way to a section.

    Examples:
      47111 + 47112 -> 4711
      47 + 47111 + 47112 -> 4711
      10111 + 10210 -> None
    """
    if taxonomy.scheme != "psic" or len(group_roots) < 2:
        return None
    if any(len(roots) != 1 for roots in group_roots):
        return None

    codes = list(dict.fromkeys(roots[0] for roots in group_roots))
    if len(codes) < 2:
        return None

    deepest_depth = max(taxonomy.depth(code) for code in codes)
    deepest = [code for code in codes if taxonomy.depth(code) == deepest_depth]
    if len(deepest) < 2:
        return None

    parents = {taxonomy.get(code).parent_code for code in deepest}
    if len(parents) != 1:
        return None
    parent = next(iter(parents))
    if parent is None or parent not in taxonomy.nodes:
        return None

    # Do not turn disagreement between whole divisions into an over-broad section.
    if taxonomy.depth(parent) < 2:
        return None

    ancestors_of_parent = set(taxonomy.ancestors(parent))
    for code in codes:
        if code in deepest:
            continue
        if code != parent and code not in ancestors_of_parent:
            return None

    return parent


def fuse(taxonomy: Taxonomy, evidence: list[SourceEvidence]) -> FusionResult:
    """Fuse mapped source evidence in taxonomy space.

    Each coded mapping denotes one or more *subtrees rooted at the codes the reviewer
    actually supplied*. We intersect those rooted subtrees directly. This preserves the
    epistemic ceiling of a coarse mapping: a SUBTREE at division level stays at division
    level unless another source supplies evidence inside it.

    Dependence groups constrain the answer but do not add independent confidence.
    Inside a dependence group we intersect compatible evidence; if dependent sources
    disagree, we widen to their union and flag it. Across independent groups, disjoint
    admissible subtrees are a true semantic conflict.
    """
    mapped = [
        e
        for e in evidence
        if e.mapping is not None
        and e.mapping.scheme == taxonomy.scheme
        and e.mapping.version == taxonomy.version
        and e.mapping.mapping_kind not in {MappingKind.NOT_ACTIVITY, MappingKind.UNCODEABLE}
    ]
    if not mapped:
        return FusionResult(taxonomy.scheme, taxonomy.version, FusionStatus.EMPTY, None)

    evidence_sources = list(dict.fromkeys(e.source for e in mapped))

    groups: dict[str, list[SourceEvidence]] = defaultdict(list)
    for item in mapped:
        groups[item.dependency_group].append(item)

    group_roots: list[list[str]] = []
    flags: list[str] = []
    all_candidate_codes: list[str] = []

    for dep_group, items in groups.items():
        constraints = [_mapping_roots(taxonomy, item) for item in items]
        constraints = [roots for roots in constraints if roots]
        if not constraints:
            continue

        current = list(constraints[0])
        for roots in constraints[1:]:
            intersection = intersect_subtrees(taxonomy, current, roots)
            if intersection:
                current = intersection
            else:
                current = _union_subtrees(taxonomy, current, roots)
                flags.append(f"DEPENDENT_CONFLICT:{dep_group}")
        group_roots.append(current)
        for item in items:
            if item.mapping:
                all_candidate_codes.extend(item.mapping.codes)

    if not group_roots:
        return FusionResult(taxonomy.scheme, taxonomy.version, FusionStatus.EMPTY, None)

    sibling_parent = _independent_sibling_parent(taxonomy, group_roots)
    if sibling_parent is not None:
        reps = sorted(
            set(all_candidate_codes),
            key=lambda code: (
                taxonomy.depth(code) if code in taxonomy.nodes else 999,
                code,
            ),
        )
        return FusionResult(
            taxonomy.scheme,
            taxonomy.version,
            FusionStatus.INTERSECT,
            sibling_parent,
            candidate_codes=reps,
            evidence_sources=evidence_sources,
            independent_groups=len(groups),
            flags=flags + ["INDEPENDENT_SIBLING_BACKOFF"],
        )

    current = list(group_roots[0])
    for roots in group_roots[1:]:
        intersection = intersect_subtrees(taxonomy, current, roots)
        if not intersection:
            reps = sorted(
                set(all_candidate_codes),
                key=lambda code: (
                    taxonomy.depth(code) if code in taxonomy.nodes else 999,
                    code,
                ),
            )
            return FusionResult(
                taxonomy.scheme,
                taxonomy.version,
                FusionStatus.CONFLICT,
                None,
                candidate_codes=reps,
                evidence_sources=evidence_sources,
                independent_groups=len(groups),
                flags=flags,
            )
        current = intersection

    if len(current) == 1:
        code = current[0]
    else:
        code = taxonomy.lca(current)

    if code is None:
        # The surviving admissible set can span multiple roots (for example, an
        # intentionally broad UNION such as wholesale-or-retail). That is unresolved
        # ambiguity, not evidence that independent sources conflict.
        return FusionResult(
            taxonomy.scheme,
            taxonomy.version,
            FusionStatus.UNION,
            None,
            candidate_codes=sorted(set(all_candidate_codes)),
            evidence_sources=evidence_sources,
            independent_groups=len(groups),
            flags=flags + ["NO_COMMON_ANCESTOR"],
        )

    reps = [c for c in (_representative_code(taxonomy, e) for e in mapped) if c]
    if len(mapped) == 1:
        only = mapped[0].mapping
        is_union = bool(only) and only.mapping_kind == MappingKind.UNION
        status = FusionStatus.UNION if is_union else FusionStatus.SINGLE
    else:
        ordered = sorted((r for r in reps if r in taxonomy.nodes), key=taxonomy.depth)
        nested = bool(ordered) and all(
            ordered[i] in taxonomy.ancestors(ordered[i + 1]) for i in range(len(ordered) - 1)
        )
        varied = len({taxonomy.depth(r) for r in ordered}) > 1
        status = FusionStatus.NESTED if nested and varied else FusionStatus.INTERSECT

    return FusionResult(
        taxonomy.scheme,
        taxonomy.version,
        status,
        code,
        candidate_codes=sorted(set(all_candidate_codes)),
        evidence_sources=evidence_sources,
        independent_groups=len(groups),
        flags=flags,
    )
