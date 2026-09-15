from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest

from placetype_ph.classifier import EntityClassifier
from placetype_ph.crosswalk import Crosswalk, CrosswalkError
from placetype_ph.evaluate import evaluate_predictions
from placetype_ph.models import CrosswalkEntry, MappingKind, SourceField, TaxonomyNode
from placetype_ph.taxonomy import Taxonomy
from placetype_ph.taxonomy_import import _api_get_all


def test_specific_name_rule_can_correct_activity_to_not_activity(toy_psic):
    crosswalk = Crosswalk(
        [
            CrosswalkEntry(
                "fsq", "restaurant", "psic", "rev5", MappingKind.SUBTREE, ("1011",)
            ),
            CrosswalkEntry(
                "fsq",
                "Rizal Park",
                "psic",
                "rev5",
                MappingKind.NOT_ACTIVITY,
                (),
                source_field=SourceField.NAME,
            ),
        ]
    )
    result = EntityClassifier(toy_psic, crosswalk).classify_row(
        {"canonical_id": "x", "fsq_category": "restaurant", "fsq_name": "Rizal Park"}
    )
    assert result.code is None
    assert result.status == "NON_ECONOMIC_POI"
    assert "CROSSWALK_NAME_RULE_OVERRODE_CATEGORY" in result.flags


def test_specific_name_rule_can_correct_not_activity_to_activity(toy_psic):
    crosswalk = Crosswalk(
        [
            CrosswalkEntry("fsq", "park", "psic", "rev5", MappingKind.NOT_ACTIVITY),
            CrosswalkEntry(
                "fsq",
                "Jurassic Park Restaurant",
                "psic",
                "rev5",
                MappingKind.SUBTREE,
                ("1011",),
                source_field=SourceField.NAME,
            ),
        ]
    )
    result = EntityClassifier(toy_psic, crosswalk).classify_row(
        {
            "canonical_id": "x",
            "fsq_category": "park",
            "fsq_name": "Jurassic Park Restaurant",
        }
    )
    assert result.code == "1011"
    assert "CROSSWALK_NAME_RULE_OVERRODE_CATEGORY" in result.flags


def test_wrong_branch_parent_prefix_is_structural_error():
    taxonomy = Taxonomy(
        [
            TaxonomyNode("pcpc", "2002", "1", "section", "Section 1"),
            TaxonomyNode("pcpc", "2002", "12", "division", "Division 12", "1"),
            TaxonomyNode("pcpc", "2002", "123", "group", "Group 123", "12"),
            TaxonomyNode("pcpc", "2002", "4567", "class", "Wrong branch", "123"),
        ]
    )
    errors = taxonomy.structural_report().errors
    assert any("does not extend the parent code prefix" in error for error in errors)


def test_union_code_order_does_not_create_false_rule_ambiguity(toy_psic):
    crosswalk = Crosswalk(
        [
            CrosswalkEntry(
                "fsq",
                "bakery",
                "psic",
                "rev5",
                MappingKind.UNION,
                ("10111", "10112"),
                match_type="contains",
            ),
            CrosswalkEntry(
                "fsq",
                "counter",
                "psic",
                "rev5",
                MappingKind.UNION,
                ("10112", "10111"),
                match_type="contains",
            ),
        ]
    )
    found = crosswalk.matches("fsq", "psic", "rev5", category="bakery counter")
    assert len(found.entries) == 1
    assert not found.ambiguities
    assert EntityClassifier(toy_psic, crosswalk).classify_row(
        {"canonical_id": "x", "fsq_category": "bakery counter"}
    ).code == "1011"


def test_redundant_union_is_rejected(toy_psic):
    crosswalk = Crosswalk(
        [
            CrosswalkEntry(
                "fsq", "food", "psic", "rev5", MappingKind.UNION, ("10", "101")
            )
        ]
    )
    with pytest.raises(ValueError, match="collapse to one subtree"):
        EntityClassifier(toy_psic, crosswalk)


def test_blank_regex_source_value_is_rejected():
    with pytest.raises(CrosswalkError, match="blank source_value"):
        Crosswalk(
            [
                CrosswalkEntry(
                    "fsq", "", "psic", "rev5", MappingKind.SUBTREE, ("10",), "regex"
                )
            ]
        )


def test_ambiguity_rows_are_distinct_from_ambiguity_occurrences(toy_psic):
    crosswalk = Crosswalk(
        [
            CrosswalkEntry(
                "fsq", "bakery", "psic", "rev5", MappingKind.SUBTREE, ("1011",), "contains"
            ),
            CrosswalkEntry(
                "fsq", "shop", "psic", "rev5", MappingKind.SUBTREE, ("2011",), "contains"
            ),
            CrosswalkEntry(
                "osm", "bakery", "psic", "rev5", MappingKind.SUBTREE, ("1011",), "contains"
            ),
            CrosswalkEntry(
                "osm", "shop", "psic", "rev5", MappingKind.SUBTREE, ("2011",), "contains"
            ),
        ]
    )
    classifier = EntityClassifier(toy_psic, crosswalk)
    classifier.classify_row(
        {
            "canonical_id": "x",
            "fsq_category": "bakery shop",
            "osm_category": "bakery shop",
        }
    )
    assert classifier.ambiguity_rows == 1
    assert sum(classifier.ambiguities.values()) == 2
    classifier.reset_run_diagnostics()
    assert classifier.ambiguity_rows == 0
    assert classifier.ambiguities == {}


@dataclass
class _FakeResponse:
    payload: object

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _paged_endpoint(total: int, server_cap: int, calls: list[int]):
    """A PSA-like endpoint returning a plain JSON list and capping page_size."""
    rows = [{"id": i} for i in range(total)]

    def fake_get(url, params, headers, timeout):
        page = int(params["page"])
        size = min(int(params["page_size"]), server_cap)
        calls.append(page)
        return _FakeResponse(rows[(page - 1) * size : (page - 1) * size + size])

    return fake_get


def test_psa_plain_list_api_paginates_until_an_empty_page(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(
        "placetype_ph.taxonomy_import.requests.get",
        _paged_endpoint(1003, 1000, calls),
    )
    rows = _api_get_all("https://example.invalid/items", "token")
    assert len(rows) == 1003
    assert calls == [1, 2, 3]


def test_psa_api_pagination_survives_a_server_that_caps_page_size(monkeypatch):
    """A short page is not proof of the last page when the server caps page_size.

    Treating it as such truncated the taxonomy to the first page whenever the cap was
    below the requested size, which is the common framework default.
    """
    calls: list[int] = []
    monkeypatch.setattr(
        "placetype_ph.taxonomy_import.requests.get",
        _paged_endpoint(250, 100, calls),
    )
    rows = _api_get_all("https://example.invalid/items", "token")
    assert len(rows) == 250
    assert calls == [1, 2, 3, 4]


def test_psa_api_repeated_page_is_not_silently_truncated(monkeypatch):
    page = [{"id": i} for i in range(1000)]

    def fake_get(url, params, headers, timeout):
        return _FakeResponse(page)

    monkeypatch.setattr("placetype_ph.taxonomy_import.requests.get", fake_get)
    with pytest.raises(Exception, match="repeated page content"):
        _api_get_all("https://example.invalid/items", "token")


def test_evaluation_summarizes_honest_backoff_and_wrong_branch(toy_psic):
    predictions = pd.DataFrame(
        [
            {"canonical_id": "a", "scheme": "psic", "version": "rev5", "code": "1011"},
            {"canonical_id": "b", "scheme": "psic", "version": "rev5", "code": "10"},
            {"canonical_id": "c", "scheme": "psic", "version": "rev5", "code": "2011"},
        ]
    )
    gold = pd.DataFrame(
        [
            {"canonical_id": "a", "scheme": "psic", "version": "rev5", "gold_code": "1011"},
            {"canonical_id": "b", "scheme": "psic", "version": "rev5", "gold_code": "10210"},
            {"canonical_id": "c", "scheme": "psic", "version": "rev5", "gold_code": "10111"},
        ]
    )
    _, summary = evaluate_predictions(predictions, gold, toy_psic)
    assert summary["hierarchy_compatible_accuracy"] == pytest.approx(2 / 3)
    assert summary["ancestor_backoff_rows"] == 1
    assert summary["wrong_branch_rows"] == 1


def test_evaluation_rejects_duplicate_prediction_ids(toy_psic):
    predictions = pd.DataFrame(
        [
            {"canonical_id": "a", "scheme": "psic", "code": "10"},
            {"canonical_id": "a", "scheme": "psic", "code": "101"},
        ]
    )
    gold = pd.DataFrame([{"canonical_id": "a", "scheme": "psic", "gold_code": "10111"}])
    with pytest.raises(ValueError, match="duplicate canonical_id"):
        evaluate_predictions(predictions, gold, toy_psic)


def test_excel_integer_like_codes_do_not_gain_a_zero():
    from placetype_ph.taxonomy_import import canonical_code, infer_level

    assert canonical_code("psic", "10.0") == "10"
    assert infer_level("psic", "10.0") == "division"
    assert canonical_code("pcpc", "001.0") == "001"
    assert infer_level("pcpc", "001.0") == "group"


def test_strict_levels_requires_every_expected_level():
    from placetype_ph.taxonomy_import import _build

    nodes = [
        TaxonomyNode("pcpc", "2002", "0", "section", "Section"),
        TaxonomyNode("pcpc", "2002", "01", "division", "Division"),
        TaxonomyNode("pcpc", "2002", "011", "group", "Group"),
        TaxonomyNode("pcpc", "2002", "0111", "class", "Class"),
        TaxonomyNode("pcpc", "2002", "01111", "subclass", "Subclass"),
        # Entire item level absent: adjacency alone cannot reveal this.
    ]
    with pytest.raises(Exception, match="expected item level"):
        _build(nodes, strict=True, strict_levels=True)


def test_programmatic_crosswalk_validates_source_field_and_confidence():
    with pytest.raises(CrosswalkError, match="unknown source_field"):
        Crosswalk(
            [
                CrosswalkEntry(
                    "fsq",
                    "restaurant",
                    "psic",
                    "rev5",
                    MappingKind.SUBTREE,
                    ("10",),
                    source_field="bad-field",  # type: ignore[arg-type]
                )
            ]
        )
    with pytest.raises(CrosswalkError, match="confidence must be between 0 and 1"):
        Crosswalk(
            [
                CrosswalkEntry(
                    "fsq",
                    "restaurant",
                    "psic",
                    "rev5",
                    MappingKind.SUBTREE,
                    ("10",),
                    confidence=1.1,
                )
            ]
        )


def test_pcpc_item_endpoint_falls_back_to_plural_on_404(monkeypatch):
    import requests

    from placetype_ph.taxonomy_import import fetch_pcpc_api

    rows_by_suffix = {
        "/sections": [{"section": "0", "title": "Section"}],
        "/divisions": [{"division": "01", "title": "Division"}],
        "/groups": [{"group": "011", "title": "Group"}],
        "/classes": [{"class_code": "0111", "title": "Class"}],
        "/sub-classes": [{"subclasscode": "01111", "title": "Subclass"}],
        "/items": [{"itemcode": "011111", "title": "Item"}],
    }
    calls: list[str] = []

    def fake_get_all(url, token):
        calls.append(url)
        if url.endswith("/item"):
            response = requests.Response()
            response.status_code = 404
            raise requests.HTTPError("not found", response=response)
        for suffix, rows in rows_by_suffix.items():
            if url.endswith(suffix):
                return rows
        raise AssertionError(url)

    monkeypatch.setattr("placetype_ph.taxonomy_import._api_get_all", fake_get_all)
    taxonomy = fetch_pcpc_api("token", strict=True, strict_levels=True)
    assert "011111" in taxonomy.nodes
    assert any(url.endswith("/item") for url in calls)
    assert any(url.endswith("/items") for url in calls)
