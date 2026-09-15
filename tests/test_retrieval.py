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
