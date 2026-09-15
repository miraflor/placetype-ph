from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from .taxonomy import LEVEL_ORDER, Taxonomy


@dataclass(frozen=True, slots=True)
class RetrievedNode:
    code: str
    score: float


class TaxonomyRetriever:
    """Small local lexical retriever; no neural model download is required."""

    def __init__(self, taxonomy: Taxonomy):
        self.taxonomy = taxonomy
        self.codes = list(taxonomy.nodes)
        corpus = [taxonomy.get(c).retrieval_text or taxonomy.get(c).title for c in self.codes]
        self.word = TfidfVectorizer(
            lowercase=True, ngram_range=(1, 2), min_df=1, sublinear_tf=True
        )
        self.char = TfidfVectorizer(
            lowercase=True, analyzer="char_wb", ngram_range=(3, 5), min_df=1, sublinear_tf=True
        )
        self.word_matrix = self.word.fit_transform(corpus)
        self.char_matrix = self.char.fit_transform(corpus)

    def search(
        self, query: str, top_n: int = 20, allowed_codes: set[str] | None = None
    ) -> list[RetrievedNode]:
        if not query.strip():
            return []
        q_word = self.word.transform([query])
        q_char = self.char.transform([query])
        word_scores = (self.word_matrix @ q_word.T).toarray().ravel()
        char_scores = (self.char_matrix @ q_char.T).toarray().ravel()
        scores = 0.6 * word_scores + 0.4 * char_scores
        if allowed_codes is not None:
            mask = np.array([c in allowed_codes for c in self.codes], dtype=bool)
            scores = np.where(mask, scores, -np.inf)
        positive = np.isfinite(scores) & (scores > 0.0)
        if not positive.any():
            return []
        candidate_idx = np.flatnonzero(positive)
        ranked = candidate_idx[np.argsort(scores[candidate_idx])[::-1]]
        return [RetrievedNode(self.codes[i], float(scores[i])) for i in ranked[:top_n]]

    def codes_under(self, roots: list[str] | tuple[str, ...] | set[str]) -> set[str]:
        """Return the supplied roots plus all descendants that exist in this taxonomy."""
        allowed: set[str] = set()
        for root in roots:
            code = str(root)
            if code not in self.taxonomy.nodes:
                continue
            allowed.add(code)
            allowed.update(self.taxonomy.descendants(code))
        return allowed

    def search_hierarchical(
        self,
        query: str,
        *,
        top_n: int = 20,
        branch_roots: tuple[str, ...] = (),
        beam_width: int = 4,
    ) -> list[RetrievedNode]:
        """Retrieve within plausible branches before comparing fine-grained nodes.

        Source ontologies often provide stronger branch evidence than lexical similarity:
        OSM ``shop=*`` means retail, while ``amenity=school`` means education.  When such
        roots are supplied, retrieval is restricted to those rooted subtrees.

        Without a source branch, use a small coarse beam over the first two taxonomy
        levels, then compare fine nodes only inside those branches.  This is a candidate
        generator, not a classifier; callers still decide whether any hit is defensible.
        """
        if not query.strip():
            return []

        if branch_roots:
            allowed = self.codes_under(branch_roots)
            if allowed:
                return self.search(query, top_n=top_n, allowed_codes=allowed)

        order = LEVEL_ORDER.get(self.taxonomy.scheme)
        if not order or len(order) < 2:
            return self.search(query, top_n=top_n)

        coarse_levels = set(order[:2])
        coarse_codes = {
            code for code, node in self.taxonomy.nodes.items() if node.level in coarse_levels
        }
        coarse = self.search(query, top_n=max(1, beam_width), allowed_codes=coarse_codes)
        if not coarse:
            return self.search(query, top_n=top_n)

        allowed: set[str] = set()
        for hit in coarse:
            allowed.update(self.codes_under((hit.code,)))
        return self.search(query, top_n=top_n, allowed_codes=allowed)
