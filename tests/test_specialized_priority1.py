"""Regression checks for the conservative source-evidence/fusion overlay."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from placetype_ph.specialized import assess, enrich_summary
from placetype_ph.specialized.activity import load_rules
from placetype_ph.specialized.fusion import fuse_activity
from placetype_ph.specialized.model import Evidence
from placetype_ph.specialized.source_evidence import from_row


def test_nonactivity_conflict_even_if_positive_activity_is_broad():
    decision = assess({"osm_category": "natural=beach", "overture_category": "bakery"})
    assert decision.status == "REVIEW"
    assert "47" in decision.candidate_codes
    assert "ACTIVITY_NONACTIVITY_CONFLICT" in decision.flags


def test_nonactivity_conflict_not_hidden_by_administrative_name():
    decision = assess({"name": "DepEd Schools Division Office", "osm_category": "natural=beach"})
    assert decision.status == "REVIEW"
    assert "ACTIVITY_NONACTIVITY_CONFLICT" not in decision.flags  # no matching activity rule
    assert "ADMINISTRATIVE_NONACTIVITY_CONFLICT" in decision.flags
    # Specific administrative evidence must not erase eligibility evidence.
    assert decision.administrative_code == "84120"
    assert "NOT_PSIC_ACTIVITY" in decision.audit["eligibility_status"]


def test_overture_primary_category_matches_existing_rules():
    decision = assess({"overture_primary_category": "bank"})
    assert decision.candidate_codes == ("641",)
    assert decision.status == "SUPPORTING_CANDIDATE"
    assert any(e.field == "overture_primary_category" for e in decision.evidence)


def test_overture_duplicate_fields_not_double_counted():
    decision = assess({"overture_category": "bank", "overture_primary_category": "bank"})
    bank_hits = [e for e in decision.evidence if e.code == "641"]
    assert len(bank_hits) == 1
    assert bank_hits[0].field == "overture_primary_category"


def test_alternate_category_is_dependent_evidence_not_independent_corroboration():
    decision = assess(
        {"overture_primary_category": "bank", "overture_alternate_category": "hospital"}
    )
    assert decision.status == "REVIEW"
    assert "DEPENDENT_SOURCE_CONFLICT:overture" in decision.flags


def test_basic_category_never_produces_precise_evidence():
    decision = assess({"overture_basic_category": "bank"})
    assert decision.status == "BROAD_CANDIDATE"
    assert decision.candidate_codes == ("641",)
    assert all(e.strength == "BROAD" for e in decision.evidence)


def test_foursquare_raw_category_preserved_without_invented_rules():
    evidence = from_row({"fsq_category": "bank"})
    assert [(e.source, e.field, e.value) for e in evidence] == [("fsq", "category", "bank")]
    assert assess({"fsq_category": "bank"}).status == "NO_EVIDENCE"
    assert not any(rule.source == "fsq" for rule in load_rules())


def test_osm_semicolon_multi_tags_are_parsed_and_conflicts_not_dropped():
    decision = assess({"osm_category": "shop=beauty;cosmetics"})
    assert decision.status == "REVIEW"
    assert set(decision.candidate_codes) == {"962", "4772"}
    assert "COMPOUND_CONFLICT_OSM" in decision.flags


def test_category_name_conflict_causes_review_even_if_category_is_broad():
    decision = assess({"name": "ABC Pharmacy", "overture_category": "professional_service"})
    assert decision.status == "REVIEW"
    assert "4772" in decision.candidate_codes


def test_nested_evidence_converges_to_precise_subtree():
    evidence = (
        Evidence("osm", "category", "shop=yes", "ACTIVITY_PROPOSAL", "47", "r1", "BROAD", "osm"),
        Evidence(
            "overture",
            "category",
            "pharmacy",
            "ACTIVITY_PROPOSAL",
            "4772",
            "r2",
            "PRECISE",
            "overture",
        ),
    )
    result = fuse_activity(evidence)
    assert result.candidate_codes == ("4772",)
    assert result.status == "SUPPORTING_CANDIDATE"
    assert result.hierarchy_basis == "numeric_prefix_provisional"


def test_disjoint_siblings_abstain_without_authoritative_hierarchy():
    evidence = (
        Evidence("osm", "category", "type_a", "ACTIVITY_PROPOSAL", "6411", "r1", "PRECISE", "osm"),
        Evidence(
            "overture",
            "category",
            "type_b",
            "ACTIVITY_PROPOSAL",
            "6412",
            "r2",
            "PRECISE",
            "overture",
        ),
    )
    result = fuse_activity(evidence)
    assert result.status == "REVIEW"
    assert "INDEPENDENT_SOURCE_CONFLICT" in result.flags
    assert result.candidate_codes == ("6411", "6412")


def test_authoritative_ancestry_can_back_off_independent_siblings():
    parents = {"6411": ("641", "64"), "6412": ("641", "64"), "641": ("64",), "64": ()}
    evidence = (
        Evidence("osm", "category", "type_a", "ACTIVITY_PROPOSAL", "6411", "r1", "PRECISE", "osm"),
        Evidence(
            "overture",
            "category",
            "type_b",
            "ACTIVITY_PROPOSAL",
            "6412",
            "r2",
            "PRECISE",
            "overture",
        ),
    )
    result = fuse_activity(evidence, ancestors=lambda code: parents[code])
    assert result.candidate_codes == ("641",)
    assert result.status == "BACKOFF_CANDIDATE"
    assert "INDEPENDENT_SIBLING_BACKOFF" in result.flags


def test_incompatible_within_single_source_never_backed_off():
    parents = {"6411": ("641", "64"), "6412": ("641", "64"), "641": ("64",), "64": ()}
    evidence = (
        Evidence(
            "overture",
            "overture_primary_category",
            "type_a",
            "ACTIVITY_PROPOSAL",
            "6411",
            "r1",
            "PRECISE",
            "overture",
        ),
        Evidence(
            "overture",
            "overture_alternate_category",
            "type_b",
            "ACTIVITY_PROPOSAL",
            "6412",
            "r2",
            "PRECISE",
            "overture",
        ),
    )
    result = fuse_activity(evidence, ancestors=lambda code: parents[code])
    assert result.status == "REVIEW"
    assert "DEPENDENT_SOURCE_CONFLICT:overture" in result.flags


def test_unknown_section_to_numeric_relationship_abstains():
    evidence = (
        Evidence("osm", "category", "tag", "ACTIVITY_PROPOSAL", "C", "r1", "BROAD", "osm"),
        Evidence(
            "overture", "category", "other", "ACTIVITY_PROPOSAL", "47", "r2", "PRECISE", "overture"
        ),
    )
    result = fuse_activity(evidence)
    assert result.status == "REVIEW"
    assert "INDEPENDENT_HIERARCHY_UNVERIFIED" in result.flags


def test_reviewed_original_never_retyped_even_by_matching_new_proposal():
    decision = assess(
        {"overture_primary_category": "bank"}, psic_code="N", psic_method="reviewed_crosswalk"
    )
    assert decision.status == "REVIEW"
    assert decision.audit["original_psic_code"] == "N"


def test_summary_is_additive_and_keeps_original_label_unchanged():
    summary = pd.DataFrame({"canonical_id": ["1"], "psic_code": ["N"], "psic_method": ["manual"]})
    places = pd.DataFrame({"canonical_id": ["1"], "overture_primary_category": ["bank"]})
    result = enrich_summary(summary, places)
    assert result["psic_code"].equals(summary["psic_code"])
    assert result.loc[0, "specialized_status"] == "REVIEW"
    assert json.loads(result.loc[0, "specialized_audit"])["fusion"]["automatic_promotion"] is False
    with pytest.raises(ValueError, match="already present"):
        enrich_summary(result, places)


def test_distant_common_root_does_not_back_off_cross_sector_conflict():
    parents = {
        "6411": ("641", "64", "ROOT"),
        "4711": ("471", "47", "ROOT"),
        "641": ("64", "ROOT"),
        "64": ("ROOT",),
        "471": ("47", "ROOT"),
        "47": ("ROOT",),
        "ROOT": (),
    }
    evidence = (
        Evidence("osm", "category", "first", "ACTIVITY_PROPOSAL", "6411", "r1", "PRECISE", "osm"),
        Evidence(
            "overture",
            "category",
            "second",
            "ACTIVITY_PROPOSAL",
            "4711",
            "r2",
            "PRECISE",
            "overture",
        ),
    )
    result = fuse_activity(evidence, ancestors=lambda code: parents[code])
    assert result.status == "REVIEW"
    assert result.candidate_codes == ("4711", "6411")
