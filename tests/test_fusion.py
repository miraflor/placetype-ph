from __future__ import annotations

from placetype_ph.fusion import fuse
from placetype_ph.models import CrosswalkEntry, FusionStatus, MappingKind, SourceEvidence


def ev(source, dep, code, kind=MappingKind.SUBTREE):
    mapping = CrosswalkEntry(source, "x", "psic", "rev5", kind, (code,))
    return SourceEvidence(source, "x", "name", dep, mapping)


def test_nested_evidence_descends(toy_psic):
    result = fuse(toy_psic, [ev("fsq", "fsq", "10"), ev("osm", "osm", "1011")])
    assert result.code == "1011"
    assert result.status == FusionStatus.NESTED
    assert result.independent_groups == 2


def test_independent_disjoint_is_conflict(toy_psic):
    result = fuse(toy_psic, [ev("fsq", "fsq", "1011"), ev("osm", "osm", "2011")])
    assert result.code is None
    assert result.status == FusionStatus.CONFLICT


def test_dependent_disagreement_backs_off_and_flags(toy_psic):
    result = fuse(
        toy_psic,
        [ev("fsq", "fsq-lineage", "1011"), ev("overture", "fsq-lineage", "1021")],
    )
    assert result.code == "10"
    assert "DEPENDENT_CONFLICT:fsq-lineage" in result.flags
    assert result.independent_groups == 1


def test_single_union_across_roots_is_ambiguity_not_conflict(toy_psic):
    mapping = CrosswalkEntry(
        "fsq", "trading", "psic", "rev5", MappingKind.UNION, ("1011", "2011")
    )
    evidence = SourceEvidence("fsq", "trading", "ABC Trading", "fsq", mapping)
    result = fuse(toy_psic, [evidence])
    assert result.code is None
    assert result.status == FusionStatus.UNION
    assert set(result.candidate_codes) == {"1011", "2011"}
    assert "NO_COMMON_ANCESTOR" in result.flags
