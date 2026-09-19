from __future__ import annotations

from placetype_ph.fusion import fuse
from placetype_ph.models import (
    CrosswalkEntry,
    FusionStatus,
    MappingKind,
    SourceEvidence,
)
from placetype_ph.suggest import _auto_proposal_block_reason, category_plan


def _ev(source: str, dep: str, code: str) -> SourceEvidence:
    mapping = CrosswalkEntry(
        source,
        "x",
        "psic",
        "rev5",
        MappingKind.SUBTREE,
        (code,),
    )
    return SourceEvidence(source, "x", "name", dep, mapping)


def test_independent_immediate_siblings_back_off_one_level(toy_psic):
    result = fuse(
        toy_psic,
        [
            _ev("fsq", "fsq", "10111"),
            _ev("osm", "osm", "10112"),
        ],
    )
    assert result.code == "1011"
    assert result.status == FusionStatus.INTERSECT
    assert "INDEPENDENT_SIBLING_BACKOFF" in result.flags


def test_coarse_ancestor_does_not_prevent_sibling_backoff(toy_psic):
    result = fuse(
        toy_psic,
        [
            _ev("fsq", "fsq", "10"),
            _ev("overture", "overture", "10111"),
            _ev("osm", "osm", "10112"),
        ],
    )
    assert result.code == "1011"
    assert result.status == FusionStatus.INTERSECT
    assert "INDEPENDENT_SIBLING_BACKOFF" in result.flags


def test_non_sibling_cousins_remain_a_hard_conflict(toy_psic):
    result = fuse(
        toy_psic,
        [
            _ev("fsq", "fsq", "10111"),
            _ev("osm", "osm", "10210"),
        ],
    )
    assert result.code is None
    assert result.status == FusionStatus.CONFLICT
    assert "INDEPENDENT_SIBLING_BACKOFF" not in result.flags


def test_flowers_and_gifts_has_a_coarse_only_ceiling():
    plan = category_plan("overture", "flowers_and_gifts_store")
    assert plan.branch_roots == ("47",)
    assert (
        _auto_proposal_block_reason(
            "overture",
            "flowers_and_gifts_store",
            plan,
        )
        == "mixed_category_ceiling"
    )
