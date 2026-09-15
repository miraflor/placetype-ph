from __future__ import annotations


def test_lca_and_backoff(toy_psic):
    assert toy_psic.parent("10111") == "1011"
    assert toy_psic.lca(["10111", "10112"]) == "1011"
    assert toy_psic.lca(["10111", "10210"]) == "10"
    assert toy_psic.lca(["10111", "20110"]) is None
    assert toy_psic.level_of("1011") == "class"


def test_leaves(toy_psic):
    assert toy_psic.leaves("1011") == frozenset({"10111", "10112"})
