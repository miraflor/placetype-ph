from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from functools import cached_property
from pathlib import Path

import pandas as pd

from .models import TaxonomyNode

# Level order per scheme, shallowest first. Used by structural validation to catch a
# tree that parsed without error but lost a whole level (for example PSIC divisions
# that never attached to their section and silently became extra roots).
LEVEL_ORDER: dict[str, tuple[str, ...]] = {
    "psic": ("section", "division", "group", "class", "subclass"),
    "pcpc": ("section", "division", "group", "class", "subclass", "item"),
    "pscc": ("chapter", "heading", "hs_subheading", "ahtn_subheading", "commodity"),
}


# The classification schemes this package understands. Single definition; the crosswalk
# loader and the CLI validate against this rather than repeating the literal set.
SCHEMES = frozenset(LEVEL_ORDER)


class TaxonomyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StructuralReport:
    """Two different kinds of structural problem, kept apart on purpose.

    `errors` are trees that lost information: a node that should have a parent has none,
    or its parent sits at an equal or deeper level. `level_gaps` are trees where an
    intermediate level is simply absent from the source, which is common in partial PSA
    extracts and is not by itself a defect. Treating the second as fatal by default makes
    a routine workbook gap block an entire import.
    """

    errors: list[str] = field(default_factory=list)
    level_gaps: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.level_gaps


class Taxonomy:
    """Validated rooted forest with convenience methods for hierarchical backoff."""

    def __init__(self, nodes: Iterable[TaxonomyNode]):
        node_list = list(nodes)
        self.nodes = {str(n.code): n for n in node_list}
        if not self.nodes:
            raise TaxonomyError("taxonomy is empty")
        if len(self.nodes) != len(node_list):
            raise TaxonomyError("duplicate taxonomy code")

        schemes = {(n.scheme, n.version) for n in self.nodes.values()}
        if len(schemes) != 1:
            raise TaxonomyError(f"taxonomy must contain one scheme/version, got {schemes}")
        self.scheme, self.version = next(iter(schemes))

        self._children: dict[str | None, list[str]] = defaultdict(list)
        for node in self.nodes.values():
            if node.parent_code is not None and node.parent_code not in self.nodes:
                raise TaxonomyError(f"missing parent {node.parent_code!r} for {node.code!r}")
            self._children[node.parent_code].append(node.code)
        for children in self._children.values():
            children.sort(key=lambda c: (len(c), c))
        self._validate_acyclic()
        self._leaf_cache: dict[str, frozenset[str]] = {}

    def _validate_acyclic(self) -> None:
        for code in self.nodes:
            seen: set[str] = set()
            cur: str | None = code
            while cur is not None:
                if cur in seen:
                    raise TaxonomyError(f"cycle detected at {cur}")
                seen.add(cur)
                cur = self.nodes[cur].parent_code

    @property
    def roots(self) -> list[str]:
        return list(self._children.get(None, []))

    @cached_property
    def max_depth(self) -> int:
        """Deepest level present in the whole tree. Constant; never recompute per row."""
        return max(self.depth(code) for code in self.nodes)

    @cached_property
    def fingerprint(self) -> str:
        """Content fingerprint used to invalidate model decisions after taxonomy edits.

        Scheme/version alone is not enough for a cache key. A corrected import, changed
        explanatory note, or repaired parent edge can materially change traversal while the
        official version string stays the same.
        """
        payload = [asdict(self.nodes[code]) for code in sorted(self.nodes)]
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    def get(self, code: str) -> TaxonomyNode:
        return self.nodes[str(code)]

    def children(self, code: str | None) -> list[str]:
        return list(self._children.get(code, []))

    def has_children(self, code: str | None) -> bool:
        return bool(self._children.get(code))

    def parent(self, code: str) -> str | None:
        return self.get(code).parent_code

    def ancestors(self, code: str, include_self: bool = True) -> list[str]:
        out: list[str] = []
        cur: str | None = str(code) if include_self else self.parent(str(code))
        while cur is not None:
            out.append(cur)
            cur = self.parent(cur)
        return out

    def path_from_root(self, code: str) -> list[str]:
        return list(reversed(self.ancestors(code)))

    def depth(self, code: str) -> int:
        return len(self.path_from_root(code))

    def descendants(self, code: str, include_self: bool = False) -> list[str]:
        out = [code] if include_self else []
        q = deque(self.children(code))
        while q:
            cur = q.popleft()
            out.append(cur)
            q.extend(self.children(cur))
        return out

    def leaves(self, code: str) -> frozenset[str]:
        code = str(code)
        if code in self._leaf_cache:
            return self._leaf_cache[code]
        children = self.children(code)
        if not children:
            leaves = frozenset({code})
        else:
            acc: set[str] = set()
            for child in children:
                acc.update(self.leaves(child))
            leaves = frozenset(acc)
        self._leaf_cache[code] = leaves
        return leaves

    def branch_max_depth(self, code: str) -> int:
        """Deepest level reachable under `code`. More informative than the global max."""
        return max(self.depth(leaf) for leaf in self.leaves(code))

    def lca(self, codes: Iterable[str]) -> str | None:
        codes = [str(c) for c in codes]
        if not codes:
            return None
        paths = [self.path_from_root(c) for c in codes]
        lca: str | None = None
        for level in zip(*paths, strict=False):
            if len(set(level)) == 1:
                lca = level[0]
            else:
                break
        return lca

    def deepest_common_supported_node(self, candidate_leaves: Iterable[str]) -> str | None:
        return self.lca(candidate_leaves)

    def overlaps_subtree(self, code: str, restriction_codes: Iterable[str]) -> bool:
        leaves = self.leaves(code)
        for r in restriction_codes:
            if leaves.intersection(self.leaves(str(r))):
                return True
        return False

    def level_of(self, code: str | None) -> str | None:
        return None if code is None else self.get(code).level

    def structural_report(self) -> StructuralReport:
        """Check every node against the scheme's level order.

        A tree can satisfy the parent/child integrity checks and still be wrong: if the
        source workbook lists sections after divisions, every division ends up a root and
        the section level disappears without any error being raised.
        """
        order = LEVEL_ORDER.get(self.scheme)
        if not order:
            return StructuralReport()
        rank = {level: i for i, level in enumerate(order)}
        errors: list[str] = []
        gaps: list[str] = []
        for code, node in self.nodes.items():
            if node.level not in rank:
                continue
            own = rank[node.level]
            if own == 0:
                if node.parent_code is not None:
                    errors.append(f"{code} ({node.level}) is a top level but has a parent")
                continue
            if node.parent_code is None:
                errors.append(f"{code} ({node.level}) is orphaned; expected a shallower parent")
                continue
            parent = self.get(node.parent_code)
            parent_level = parent.level
            parent_rank = rank.get(parent_level)
            if parent_rank is None or parent_rank >= own:
                errors.append(
                    f"{code} ({node.level}) has parent {node.parent_code} ({parent_level}), "
                    "which is not a shallower level"
                )
                continue

            # Below the PSIC section layer, and throughout PCPC/PSCC, the child code
            # must extend its numeric parent code. An explicit but wrong parent edge can
            # otherwise pass the level-order check while attaching a node to a different
            # branch of the taxonomy.
            if code.isdigit() and parent.code.isdigit() and not code.startswith(parent.code):
                errors.append(
                    f"{code} ({node.level}) has parent {parent.code} ({parent_level}), "
                    "but the child code does not extend the parent code prefix"
                )
                continue

            if parent_rank != own - 1:
                gaps.append(
                    f"{code} ({node.level}) attaches to {node.parent_code} ({parent_level}); "
                    f"the {order[own - 1]} level is missing between them"
                )
        return StructuralReport(errors, gaps)

    def structural_errors(self, require_adjacent_levels: bool = False) -> list[str]:
        """Structural errors, optionally including missing intermediate levels."""
        report = self.structural_report()
        return report.errors + (report.level_gaps if require_adjacent_levels else [])

    def to_frame(self) -> pd.DataFrame:
        rows = [asdict(n) for n in self.nodes.values()]
        # Insertion order is the source workbook order; keep it so a saved tree can be
        # re-derived the same way it was built.
        return pd.DataFrame(rows)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        frame = self.to_frame()
        if path.suffix.lower() == ".csv":
            frame.to_csv(path, index=False)
        else:
            frame.to_parquet(path, index=False)

    @classmethod
    def load(cls, path: str | Path) -> Taxonomy:
        path = Path(path)
        if path.suffix.lower() == ".csv":
            frame = pd.read_csv(path, dtype=str).fillna("")
        else:
            frame = pd.read_parquet(path).fillna("")
        required = {"scheme", "version", "code", "level", "title", "parent_code"}
        missing = required - set(frame.columns)
        if missing:
            raise TaxonomyError(f"missing taxonomy columns: {sorted(missing)}")
        nodes: list[TaxonomyNode] = []
        for row in frame.to_dict("records"):
            nodes.append(
                TaxonomyNode(
                    scheme=str(row["scheme"]).casefold(),
                    version=str(row["version"]),
                    code=str(row["code"]),
                    level=str(row["level"]),
                    title=str(row["title"]),
                    parent_code=str(row.get("parent_code") or "") or None,
                    description=str(row.get("description") or ""),
                    includes=str(row.get("includes") or ""),
                    excludes=str(row.get("excludes") or ""),
                    source_url=str(row.get("source_url") or ""),
                )
            )
        return cls(nodes)
