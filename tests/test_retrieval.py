from __future__ import annotations

from placetype_ph.retrieval import TaxonomyRetriever


def test_retriever_returns_positive_relevant_hits(toy_psic):
    retriever = TaxonomyRetriever(toy_psic)
    hits = retriever.search("bread bakery manufacture", top_n=5)
    assert hits
    assert any(hit.code in {"1011", "10111"} for hit in hits)
    assert all(hit.score > 0 for hit in hits)


def test_retriever_does_not_invent_zero_score_candidates(toy_psic):
    retriever = TaxonomyRetriever(toy_psic)
    assert retriever.search("zzzzqxxwv unmatched gibberish") == []


def test_hierarchical_retrieval_respects_explicit_branch(toy_psic):
    retriever = TaxonomyRetriever(toy_psic)
    hits = retriever.search_hierarchical("bakery", top_n=5, branch_roots=("20",))
    assert hits
    assert all(hit.code == "20" or hit.code.startswith("20") for hit in hits)
    assert any(hit.code in {"2011", "20110"} for hit in hits)

def test_hierarchical_retrieval_scores_branchless_query_once(toy_psic):
    class CountingRetriever(TaxonomyRetriever):
        def __init__(self, taxonomy):
            super().__init__(taxonomy)
            self.score_calls = 0

        def _score_query(self, query):
            self.score_calls += 1
            return super()._score_query(query)

    retriever = CountingRetriever(toy_psic)
    hits = retriever.search_hierarchical("bread bakery manufacture", top_n=5)
    assert hits
    assert retriever.score_calls == 1

def test_batched_hierarchical_retrieval_matches_single_queries(toy_psic):
    retriever = TaxonomyRetriever(toy_psic)
    requests = [
        ("bread bakery manufacture", ()),
        ("bakery", ("20",)),
        ("bread bakery manufacture", ()),
    ]
    batched = retriever.search_hierarchical_many(requests, top_n=5, batch_size=2)
    singles = [
        retriever.search_hierarchical(query, top_n=5, branch_roots=branches)
        for query, branches in requests
    ]
    assert [[hit.code for hit in hits] for hits in batched] == [
        [hit.code for hit in hits] for hits in singles
    ]
    assert [[hit.score for hit in hits] for hits in batched] == [
        [hit.score for hit in hits] for hits in singles
    ]


def test_batched_hierarchical_retrieval_scores_duplicate_text_once(toy_psic):
    class CountingRetriever(TaxonomyRetriever):
        def __init__(self, taxonomy):
            super().__init__(taxonomy)
            self.scored_queries = []

        def _score_queries(self, queries):
            self.scored_queries.extend(queries)
            return super()._score_queries(queries)

    retriever = CountingRetriever(toy_psic)
    progress = []
    results = retriever.search_hierarchical_many(
        [
            ("bakery", ()),
            ("bakery", ()),
            ("bakery", ("20",)),
            ("bread manufacture", ()),
        ],
        top_n=5,
        batch_size=1,
        batch_callback=progress.append,
    )
    assert len(results) == 4
    assert retriever.scored_queries.count("bakery") == 1
    assert retriever.scored_queries.count("bread manufacture") == 1
    assert sum(progress) == 2
