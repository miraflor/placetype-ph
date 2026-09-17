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
        self._code_index = {code: index for index, code in enumerate(self.codes)}
        corpus = [taxonomy.get(c).retrieval_text or taxonomy.get(c).title for c in self.codes]
        self.word = TfidfVectorizer(
            lowercase=True, ngram_range=(1, 2), min_df=1, sublinear_tf=True
        )
        self.char = TfidfVectorizer(
            lowercase=True, analyzer="char_wb", ngram_range=(3, 5), min_df=1, sublinear_tf=True
        )
        self.word_matrix = self.word.fit_transform(corpus)
        self.char_matrix = self.char.fit_transform(corpus)
        order = LEVEL_ORDER.get(self.taxonomy.scheme, ())
        coarse_levels = set(order[:2]) if len(order) >= 2 else set()
        self._coarse_codes = frozenset(
            code
            for code, node in self.taxonomy.nodes.items()
            if node.level in coarse_levels
        )
        self._codes_under_cache: dict[tuple[str, ...], frozenset[str]] = {}

    def _score_queries(self, queries: list[str]) -> np.ndarray:
        """Score a batch of queries against every taxonomy node.

        The returned matrix has shape ``(node_count, query_count)``. Keeping batches bounded
        avoids materialising the full Metro Manila query-by-taxonomy matrix at once.
        """
        if not queries:
            return np.empty((len(self.codes), 0), dtype=float)
        q_word = self.word.transform(queries)
        q_char = self.char.transform(queries)
        scores = (self.word_matrix @ q_word.T).toarray()
        scores *= 0.6
        scores += 0.4 * (self.char_matrix @ q_char.T).toarray()
        return scores

    def _score_query(self, query: str) -> np.ndarray:
        """Score one query against every taxonomy node."""
        return self._score_queries([query])[:, 0]

    def _rank_scores(
        self,
        scores: np.ndarray,
        *,
        top_n: int,
        allowed_codes: set[str] | frozenset[str] | None = None,
    ) -> list[RetrievedNode]:
        if top_n < 1:
            return []
        if allowed_codes is None:
            candidate_idx = np.flatnonzero(np.isfinite(scores) & (scores > 0.0))
        else:
            allowed_idx = np.fromiter(
                sorted(
                    self._code_index[code]
                    for code in allowed_codes
                    if code in self._code_index
                ),
                dtype=np.intp,
            )
            if allowed_idx.size == 0:
                return []
            allowed_scores = scores[allowed_idx]
            candidate_idx = allowed_idx[
                np.isfinite(allowed_scores) & (allowed_scores > 0.0)
            ]
        if candidate_idx.size == 0:
            return []
        ranked = candidate_idx[np.argsort(scores[candidate_idx])[::-1]]
        return [RetrievedNode(self.codes[i], float(scores[i])) for i in ranked[:top_n]]

    def search(
        self, query: str, top_n: int = 20, allowed_codes: set[str] | None = None
    ) -> list[RetrievedNode]:
        if not query.strip():
            return []
        return self._rank_scores(
            self._score_query(query),
            top_n=top_n,
            allowed_codes=allowed_codes,
        )

    def codes_under(self, roots: list[str] | tuple[str, ...] | set[str]) -> set[str]:
        """Return supplied roots plus descendants, caching repeated branch closures."""
        key = tuple(sorted({str(root) for root in roots}))
        cached = self._codes_under_cache.get(key)
        if cached is not None:
            return set(cached)

        allowed: set[str] = set()
        for code in key:
            if code not in self.taxonomy.nodes:
                continue
            allowed.add(code)
            allowed.update(self.taxonomy.descendants(code))
        frozen = frozenset(allowed)
        self._codes_under_cache[key] = frozen
        return set(frozen)

    def _hierarchical_from_scores(
        self,
        scores: np.ndarray,
        *,
        top_n: int,
        branch_roots: tuple[str, ...] = (),
        beam_width: int = 4,
    ) -> list[RetrievedNode]:
        if branch_roots:
            allowed = self.codes_under(branch_roots)
            if allowed:
                return self._rank_scores(scores, top_n=top_n, allowed_codes=allowed)

        order = LEVEL_ORDER.get(self.taxonomy.scheme)
        if not order or len(order) < 2 or not self._coarse_codes:
            return self._rank_scores(scores, top_n=top_n)

        coarse = self._rank_scores(
            scores,
            top_n=max(1, beam_width),
            allowed_codes=self._coarse_codes,
        )
        if not coarse:
            return self._rank_scores(scores, top_n=top_n)

        allowed: set[str] = set()
        for hit in coarse:
            allowed.update(self.codes_under((hit.code,)))
        return self._rank_scores(scores, top_n=top_n, allowed_codes=allowed)

    def search_hierarchical(
        self,
        query: str,
        *,
        top_n: int = 20,
        branch_roots: tuple[str, ...] = (),
        beam_width: int = 4,
    ) -> list[RetrievedNode]:
        """Retrieve hierarchically while computing TF-IDF scores once per query."""
        if not query.strip():
            return []
        return self._hierarchical_from_scores(
            self._score_query(query),
            top_n=top_n,
            branch_roots=branch_roots,
            beam_width=beam_width,
        )

    def search_hierarchical_many(
        self,
        requests: list[tuple[str, tuple[str, ...]]],
        *,
        top_n: int = 20,
        beam_width: int = 4,
        batch_size: int = 256,
        batch_callback=None,
    ) -> list[list[RetrievedNode]]:
        """Batch hierarchical retrieval while preserving single-query semantics.

        Identical query text is vectorised only once within a taxonomy, even when several
        rows use it or use different branch restrictions. Results remain aligned with the
        input requests. ``batch_callback``, when supplied, receives the number of newly scored
        unique query texts after each batch and is used only for CLI progress reporting.
        """
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        if not requests:
            return []

        keys: list[tuple[str, tuple[str, ...]]] = []
        query_to_keys: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
        seen_keys: set[tuple[str, tuple[str, ...]]] = set()
        cache: dict[tuple[str, tuple[str, ...]], list[RetrievedNode]] = {}

        for query, branch_roots in requests:
            key = (str(query).strip(), tuple(branch_roots))
            keys.append(key)
            if not key[0]:
                cache[key] = []
                continue
            if key in seen_keys:
                continue
            seen_keys.add(key)
            query_to_keys.setdefault(key[0], []).append(key)

        unique_queries = list(query_to_keys)
        for start in range(0, len(unique_queries), batch_size):
            batch = unique_queries[start : start + batch_size]
            score_matrix = self._score_queries(batch)
            for column, query in enumerate(batch):
                scores = score_matrix[:, column]
                for key in query_to_keys[query]:
                    cache[key] = self._hierarchical_from_scores(
                        scores,
                        top_n=top_n,
                        branch_roots=key[1],
                        beam_width=beam_width,
                    )
            if batch_callback is not None:
                batch_callback(len(batch))

        return [cache[key] for key in keys]
