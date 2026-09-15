"""Regression tests for the defects found in the v0.1 review."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from placetype_ph.cache import DecisionCache
from placetype_ph.classifier import EntityClassifier
from placetype_ph.crosswalk import Crosswalk
from placetype_ph.llm.mock import MockBackend
from placetype_ph.models import CrosswalkEntry, MappingKind, SourceField, TaxonomyNode
from placetype_ph.openplaces import _truthy
from placetype_ph.pipeline import classify_openplaces
from placetype_ph.taxonomy import Taxonomy, TaxonomyError
from placetype_ph.taxonomy_import import import_excel
from placetype_ph.traversal import HierarchicalTraverser

REFUSALS = ['{"decision":"INSUFFICIENT","reason":"unclear"}'] * 60


def _union_crosswalk() -> Crosswalk:
    return Crosswalk(
        [CrosswalkEntry("fsq", "bakery", "psic", "rev5", MappingKind.UNION, ("10111", "10112"))]
    )


def test_llm_failure_keeps_the_deterministic_code(toy_psic):
    """Turning the model on must never lose a code fusion could already defend."""
    row = pd.Series({"canonical_id": "x", "fsq_name": "ABC", "fsq_category": "bakery"})
    without = EntityClassifier(toy_psic, _union_crosswalk()).classify_row(row)
    assert without.code == "1011"

    traverser = HierarchicalTraverser(toy_psic, MockBackend(REFUSALS), passes=3)
    with_llm = EntityClassifier(toy_psic, _union_crosswalk(), traverser).classify_row(row)
    assert with_llm.code == "1011"
    assert with_llm.status == "FUSION_BACKOFF"
    assert "LLM_FELL_BACK_TO_FUSION" in with_llm.flags


def test_review_is_still_returned_when_nothing_is_deterministic(toy_psic):
    row = pd.Series({"canonical_id": "x", "fsq_name": "ABC", "fsq_category": "unmapped"})
    traverser = HierarchicalTraverser(toy_psic, MockBackend(REFUSALS), passes=3)
    result = EntityClassifier(toy_psic, None, traverser).classify_row(row)
    assert result.code is None
    assert result.status == "REVIEW"
    assert "INSUFFICIENT_AT_ROOT" in result.flags


def test_truthy_handles_pandas_na():
    assert _truthy(pd.NA) is False
    assert _truthy(float("nan")) is False
    assert _truthy("yes") is True


def test_pcpc_label_does_not_depend_on_the_deciding_component():
    taxonomy = Taxonomy(
        [
            TaxonomyNode("pcpc", "2002", "1", "section", "Food"),
            TaxonomyNode("pcpc", "2002", "11", "division", "Bakery products", "1"),
        ]
    )
    row = pd.Series({"canonical_id": "x", "fsq_name": "ABC", "fsq_category": "bakery"})
    cw = Crosswalk([CrosswalkEntry("fsq", "bakery", "pcpc", "2002", MappingKind.EXACT, ("11",))])
    assert EntityClassifier(taxonomy, cw).classify_row(row).status.startswith(
        "POTENTIAL_PRODUCT_FAMILY_"
    )


def test_empty_input_produces_a_well_formed_table(tmp_path: Path, toy_psic):
    path = tmp_path / "empty.parquet"
    pd.DataFrame(
        {"canonical_id": pd.Series([], dtype=str), "fsq_category": pd.Series([], dtype=str)}
    ).to_parquet(path, index=False)
    classifications, summary = classify_openplaces(
        path, {"psic": EntityClassifier(toy_psic)}, tmp_path / "out"
    )
    assert list(pd.read_parquet(classifications).columns)[:3] == [
        "canonical_id",
        "scheme",
        "version",
    ]
    assert pd.read_parquet(summary).empty


def test_cache_row_without_an_agreement_does_not_crash(tmp_path: Path, toy_psic):
    cache = DecisionCache(tmp_path / "c.sqlite")
    traverser = HierarchicalTraverser(toy_psic, MockBackend(REFUSALS), passes=1)
    classifier = EntityClassifier(toy_psic, None, traverser, cache)
    classifier.retriever = None  # keep the restriction set empty so the key is stable
    row = pd.Series({"canonical_id": "x", "fsq_name": "ABC", "fsq_category": "bakery"})
    key = DecisionCache.key(
        "psic",
        "rev5",
        toy_psic.fingerprint,
        "mock",
        traverser.prompt_fingerprint,
        "FSQ name: ABC\nFSQ category: bakery",
        [],
        1,
    )
    cache.put(key, {"code": "10", "agreement": None, "audit": {}})
    cache.flush()
    result = classifier.classify_row(row)
    assert result.code == "10"
    assert result.traversal_agreement == 0.0
    cache.close()


def test_every_bad_crosswalk_row_is_reported_at_once(toy_psic):
    bad = [
        CrosswalkEntry("fsq", f"cat{i}", "psic", "rev5", MappingKind.EXACT, ("9999",))
        for i in range(5)
    ]
    with pytest.raises(ValueError) as exc:
        EntityClassifier(toy_psic, Crosswalk(bad))
    assert "5 invalid crosswalk row(s)" in str(exc.value)


def test_name_keyed_crosswalk_rows_are_reachable(toy_psic):
    cw = Crosswalk(
        [
            CrosswalkEntry(
                "fsq",
                r"bread\s*house",
                "psic",
                "rev5",
                MappingKind.SUBTREE,
                ("1011",),
                match_type="regex",
                source_field=SourceField.NAME,
            )
        ]
    )
    row = pd.Series({"canonical_id": "x", "fsq_name": "Bread House", "fsq_category": "shop"})
    assert EntityClassifier(toy_psic, cw).classify_row(row).code == "1011"


def test_name_rule_can_refine_a_broad_category_rule_from_the_same_source(toy_psic):
    cw = Crosswalk(
        [
            CrosswalkEntry(
                "fsq", "bakery", "psic", "rev5", MappingKind.SUBTREE, ("10",)
            ),
            CrosswalkEntry(
                "fsq",
                r"bread\s*house",
                "psic",
                "rev5",
                MappingKind.SUBTREE,
                ("1011",),
                match_type="regex",
                source_field=SourceField.NAME,
            ),
        ]
    )
    row = pd.Series(
        {"canonical_id": "x", "fsq_name": "Bread House", "fsq_category": "Bakery"}
    )
    result = EntityClassifier(toy_psic, cw).classify_row(row)
    assert result.code == "1011"
    assert result.evidence_sources == ["fsq"]


def test_structural_validation_catches_detached_divisions(tmp_path: Path):
    path = tmp_path / "sections_last.xlsx"
    pd.DataFrame(
        [
            ["10", "Food division"],
            ["101", "Processing group"],
            ["1011", "Bakery class"],
            ["A", "Agriculture section"],
        ]
    ).to_excel(path, index=False, header=False)
    with pytest.raises(TaxonomyError) as exc:
        import_excel(path, "psic", "rev5")
    assert "orphaned" in str(exc.value)
    # The escape hatch still produces the old, permissive tree.
    loose = import_excel(path, "psic", "rev5", strict=False)
    assert "10" in loose.roots


def test_explicit_parent_column_survives_a_bad_row_order(tmp_path: Path):
    path = tmp_path / "with_parent.xlsx"
    pd.DataFrame(
        [
            ["Code", "Title", "Parent code"],
            ["10", "Food division", "A"],
            ["101", "Processing group", "10"],
            ["A", "Agriculture section", ""],
        ]
    ).to_excel(path, index=False, header=False)
    taxonomy = import_excel(path, "psic", "rev5", parent_column="Parent code")
    assert taxonomy.parent("10") == "A"
    assert taxonomy.structural_errors() == []


def test_branch_and_global_depth_are_both_reported(toy_psic):
    row = pd.Series({"canonical_id": "x", "fsq_name": "ABC", "fsq_category": "bakery"})
    cw = Crosswalk(
        [CrosswalkEntry("fsq", "bakery", "psic", "rev5", MappingKind.SUBTREE, ("10",))]
    )
    result = EntityClassifier(toy_psic, cw).classify_row(row)
    assert result.code == "10"
    assert result.classification_depth == 2
    assert result.branch_max_depth == 5
    assert result.max_depth == 5


def test_subtree_mapping_does_not_invent_depth_on_a_unary_branch():
    taxonomy = Taxonomy(
        [
            TaxonomyNode("psic", "rev5", "A", "section", "A"),
            TaxonomyNode("psic", "rev5", "10", "division", "D", "A"),
            TaxonomyNode("psic", "rev5", "101", "group", "G", "10"),
            TaxonomyNode("psic", "rev5", "1011", "class", "C", "101"),
            TaxonomyNode("psic", "rev5", "10110", "subclass", "S", "1011"),
        ]
    )
    row = pd.Series({"canonical_id": "x", "fsq_category": "coarse"})
    cw = Crosswalk(
        [CrosswalkEntry("fsq", "coarse", "psic", "rev5", MappingKind.SUBTREE, ("10",))]
    )
    result = EntityClassifier(taxonomy, cw).classify_row(row)
    assert result.code == "10"
    assert result.level == "division"


def test_section_membership_column_only_attaches_psic_divisions(tmp_path: Path):
    path = tmp_path / "section_membership.xlsx"
    pd.DataFrame(
        [
            ["Code", "Title", "Section"],
            ["A", "Agriculture section", "A"],
            ["10", "Food division", "A"],
            ["101", "Processing group", "A"],
            ["1011", "Bakery class", "A"],
        ]
    ).to_excel(path, index=False, header=False)
    taxonomy = import_excel(path, "psic", "rev5")
    assert taxonomy.parent("10") == "A"
    assert taxonomy.parent("101") == "10"
    assert taxonomy.parent("1011") == "101"


def test_a_skipped_level_is_a_gap_not_a_lost_parent():
    taxonomy = Taxonomy(
        [
            TaxonomyNode("psic", "rev5", "A", "section", "A"),
            TaxonomyNode("psic", "rev5", "101", "group", "G", "A"),
        ]
    )
    report = taxonomy.structural_report()
    assert report.errors == []
    assert any("the division level is missing" in gap for gap in report.level_gaps)
    assert taxonomy.structural_errors() == []
    assert taxonomy.structural_errors(require_adjacent_levels=True) == report.level_gaps


def test_a_lost_parent_is_still_an_error():
    taxonomy = Taxonomy(
        [
            TaxonomyNode("psic", "rev5", "A", "section", "A"),
            TaxonomyNode("psic", "rev5", "10", "division", "D"),
        ]
    )
    assert any("orphaned" in error for error in taxonomy.structural_report().errors)


def test_pscc_blank_product_text_does_not_bypass_policy():
    taxonomy = Taxonomy(
        [
            TaxonomyNode("pscc", "2022", "10", "chapter", "Cereals"),
            TaxonomyNode("pscc", "2022", "1001", "heading", "Wheat", "10"),
        ]
    )
    row = pd.Series({"canonical_id": "x", "fsq_name": "Wheat Shop"})
    traverser = HierarchicalTraverser(taxonomy, MockBackend(REFUSALS), passes=1)
    result = EntityClassifier(taxonomy, None, traverser).classify_row(row, product_text="   ")
    assert result.status == "NO_PRODUCT_EVIDENCE"


def test_one_ambiguous_row_does_not_abort_the_run(tmp_path: Path, toy_psic):
    """An ambiguous rule set is a review problem for one value, not a lost build."""
    crosswalk = Crosswalk(
        [
            CrosswalkEntry(
                "fsq", "bakery", "psic", "rev5", MappingKind.SUBTREE, ("1011",), "contains"
            ),
            CrosswalkEntry(
                "fsq", "shop", "psic", "rev5", MappingKind.SUBTREE, ("2011",), "contains"
            ),
        ]
    )
    categories = ["bakery counter"] * 5
    categories[2] = "bakery shop"  # matches both rules, with different decisions
    path = tmp_path / "pois.parquet"
    pd.DataFrame(
        {
            "canonical_id": [f"id{i}" for i in range(5)],
            "fsq_name": [f"P{i}" for i in range(5)],
            "fsq_category": categories,
        }
    ).to_parquet(path, index=False)

    classifier = EntityClassifier(toy_psic, crosswalk)
    classifications, _ = classify_openplaces(path, {"psic": classifier}, tmp_path / "out")
    frame = pd.read_parquet(classifications).set_index("canonical_id")
    assert frame.loc["id0", "code"] == "1011"
    assert frame.loc["id4", "code"] == "1011"
    assert pd.isna(frame.loc["id2", "code"])
    assert "CROSSWALK_AMBIGUOUS:fsq" in frame.loc["id2", "flags"]
    manifest = json.loads((tmp_path / "out" / "run.json").read_text())
    assert manifest["schemes"]["psic"]["crosswalk_ambiguities"]["rows_affected"] == 1


def test_a_name_rule_overrides_a_category_rule_it_contradicts(toy_psic):
    crosswalk = Crosswalk(
        [
            CrosswalkEntry("fsq", "bakery", "psic", "rev5", MappingKind.SUBTREE, ("101",)),
            CrosswalkEntry(
                "fsq",
                r"bread\s*house",
                "psic",
                "rev5",
                MappingKind.SUBTREE,
                ("102",),
                match_type="regex",
                source_field=SourceField.NAME,
            ),
        ]
    )
    row = pd.Series({"canonical_id": "x", "fsq_name": "Bread House", "fsq_category": "bakery"})
    result = EntityClassifier(toy_psic, crosswalk).classify_row(row)
    assert result.code == "102"
    assert "CROSSWALK_NAME_RULE_OVERRODE_CATEGORY" in result.flags


def test_blank_product_text_falls_back_to_place_evidence(toy_psic):
    seen: list[str] = []

    class Recorder(MockBackend):
        def complete(self, system, user, temperature=0.0):
            seen.append(user)
            return '{"decision":"INSUFFICIENT","reason":"x"}'

    traverser = HierarchicalTraverser(toy_psic, Recorder([]), passes=1)
    classifier = EntityClassifier(toy_psic, None, traverser)
    row = pd.Series({"canonical_id": "x", "fsq_name": "ABC Bakery", "fsq_category": "Bakery"})
    classifier.classify_row(row, product_text="   ")
    evidence = seen[0].split("ESTABLISHMENT EVIDENCE")[1].split("CURRENT NODE")[0]
    assert "ABC Bakery" in evidence
