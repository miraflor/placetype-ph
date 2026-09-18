from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import placetype_ph.cli as cli_module
from placetype_ph.joint_crosswalk import (
    PEER_CONTEXT_MARKER,
    PEER_SEPARATOR,
    UNION_SEPARATOR,
    ensure_joint_key,
    first_pass_codes,
    hierarchy_path,
    make_joint_key,
    recheck_joint_worklist,
)
from placetype_ph.models import TaxonomyNode
from placetype_ph.suggest import suggest_mapping
from placetype_ph.taxonomy import Taxonomy


class ScriptedRetriever:
    """Return scripted rankings and record hierarchical versus bounded search calls."""

    def __init__(
        self,
        control: list[tuple[str, float]],
        treatment: list[tuple[str, float]] | None = None,
    ):
        self.control = list(control)
        self.treatment = list(control if treatment is None else treatment)
        self.calls: list[tuple[str, str, int, tuple[str, ...]]] = []

    def _ranking(self, query: str):
        return self.treatment if PEER_CONTEXT_MARKER in query else self.control

    def search_hierarchical(self, query: str, *, top_n: int, branch_roots=()):
        self.calls.append(("hierarchical", query, top_n, tuple(branch_roots)))
        return [
            SimpleNamespace(code=code, score=score)
            for code, score in self._ranking(query)[:top_n]
        ]

    def search(self, query: str, top_n: int = 20, allowed_codes=None):
        allowed = None if allowed_codes is None else {str(code) for code in allowed_codes}
        marker = tuple(sorted(allowed)) if allowed is not None else ()
        self.calls.append(("search", query, top_n, marker))
        ranking = self._ranking(query)
        if allowed is not None:
            ranking = [(code, score) for code, score in ranking if code in allowed]
        return [
            SimpleNamespace(code=code, score=score)
            for code, score in ranking[:top_n]
        ]


def _taxonomies():
    return {
        ("psic", "rev5"): Taxonomy(
            [
                TaxonomyNode("psic", "rev5", "I", "section", "Accommodation and food service"),
                TaxonomyNode("psic", "rev5", "56", "division", "Food and beverage service", "I"),
            ]
        ),
        ("pcpc", "2002"): Taxonomy(
            [
                TaxonomyNode("pcpc", "2002", "6", "section", "Distributive trade services"),
                TaxonomyNode("pcpc", "2002", "63", "division", "Food serving services", "6"),
                TaxonomyNode("pcpc", "2002", "64", "division", "Other serving services", "6"),
            ]
        ),
        ("pscc", "2022"): Taxonomy(
            [
                TaxonomyNode("pscc", "2022", "21", "chapter", "Food preparations"),
                TaxonomyNode("pscc", "2022", "2106", "heading", "Food preparations n.e.s.", "21"),
            ]
        ),
    }


def _row(scheme: str, version: str, suggested: str, **overrides):
    candidate_codes = overrides.pop("candidate_codes", suggested)
    candidate_scores = overrides.pop(
        "candidate_scores",
        "0.900000" if candidate_codes else "",
    )
    record = {
        "source": "fsq",
        "source_value": "Restaurant",
        "source_field": "category",
        "scheme": scheme,
        "version": version,
        "mapping_kind": "",
        "codes": "",
        "suggested_codes": suggested,
        "candidate_codes": candidate_codes,
        "candidate_scores": candidate_scores,
        "query_text": "restaurant service",
        "branch_codes": "",
    }
    record.update(overrides)
    return record


def _frame(*rows):
    frame = pd.DataFrame(list(rows))
    ensure_joint_key(frame)
    return frame


def _stable_retrievers():
    return {
        ("psic", "rev5"): ScriptedRetriever([("56", 0.90), ("I", 0.40)]),
        ("pcpc", "2002"): ScriptedRetriever([("63", 0.90), ("64", 0.40)]),
        ("pscc", "2022"): ScriptedRetriever([("2106", 0.90), ("21", 0.40)]),
    }


def _three_rows():
    return (
        _row(
            "psic",
            "rev5",
            "56",
            candidate_codes="56|I",
            candidate_scores="0.900000|0.400000",
        ),
        _row(
            "pcpc",
            "2002",
            "63",
            candidate_codes="63|64",
            candidate_scores="0.900000|0.400000",
        ),
        _row(
            "pscc",
            "2022",
            "2106",
            candidate_codes="2106|21",
            candidate_scores="0.900000|0.400000",
        ),
    )


# --- keys -----------------------------------------------------------------


def test_joint_key_is_shared_across_schemes():
    keys = {make_joint_key("fsq", "Restaurant") for _ in range(3)}
    assert len(keys) == 1


def test_joint_key_normalises_source_and_field_but_not_value():
    assert make_joint_key("FSQ", "Restaurant", "CATEGORY") == make_joint_key(
        " fsq ", "Restaurant", "category"
    )
    assert make_joint_key("fsq", "Restaurant") != make_joint_key("fsq", "restaurant")


def test_joint_key_separates_sources():
    assert make_joint_key("fsq", "Restaurant") != make_joint_key("osm", "Restaurant")


def test_ensure_joint_key_adds_and_preserves():
    frame = pd.DataFrame([{"source": "fsq", "source_value": "Restaurant"}])
    ensure_joint_key(frame)
    assert frame.loc[0, "joint_key"] == make_joint_key("fsq", "Restaurant")
    frame.loc[0, "joint_key"] = "manual"
    ensure_joint_key(frame)
    assert frame.loc[0, "joint_key"] == "manual"


# --- hierarchy and first-pass reading -------------------------------------


def test_hierarchy_path_keeps_all_levels():
    path = hierarchy_path(_taxonomies()[("pcpc", "2002")], "63")
    assert "section:6" in path
    assert "division:63" in path


def test_hierarchy_path_is_empty_for_unknown_code():
    assert hierarchy_path(_taxonomies()[("pcpc", "2002")], "999") == ""
    assert hierarchy_path(None, "63") == ""


def test_first_pass_codes_prefers_reviewed_codes_and_keeps_every_code():
    codes, column = first_pass_codes({"codes": "63|64", "suggested_codes": "63"})
    assert codes == ["63", "64"]
    assert column == "codes"


# --- the controlled recheck ------------------------------------------------


def test_recheck_reuses_first_pass_control_and_scores_only_the_treatment():
    retrievers = _stable_retrievers()
    out = recheck_joint_worklist(
        _frame(*_three_rows()), _taxonomies(), retrievers, top_n=2
    )
    calls = retrievers[("pcpc", "2002")].calls
    assert len(calls) == 1
    method, query, requested, allowed = calls[0]
    assert method == "search"
    assert PEER_CONTEXT_MARKER in query
    assert requested == 2
    assert set(allowed) == {"63", "64"}
    pcpc = out[out["scheme"] == "pcpc"].iloc[0]
    assert pcpc["control_top_code"] == "63"
    assert pcpc["recheck_top_code"] == "63"


def test_peer_context_carries_the_other_schemes_titles():
    retrievers = _stable_retrievers()
    out = recheck_joint_worklist(_frame(*_three_rows()), _taxonomies(), retrievers, top_n=2)
    peer_context = out.loc[out["scheme"] == "pcpc", "peer_context"].iloc[0]
    assert "Food and beverage service" in peer_context
    assert "Food preparations n.e.s." in peer_context
    assert "Food serving services" not in peer_context


def test_stable_when_peer_context_does_not_change_the_top():
    out = recheck_joint_worklist(
        _frame(*_three_rows()), _taxonomies(), _stable_retrievers(), top_n=2
    )
    assert set(out["recheck_status"]) == {"STABLE"}
    assert set(out["joint_status"]) == {"STABLE"}
    assert set(out["first_pass_status"]) == {"AGREES"}


def test_recheck_cannot_introduce_codes_outside_first_pass_candidates():
    retrievers = _stable_retrievers()
    retrievers[("pcpc", "2002")] = ScriptedRetriever(
        [("63", 0.90), ("64", 0.40)],
        treatment=[("999", 0.99), ("64", 0.90), ("63", 0.40)],
    )
    out = recheck_joint_worklist(
        _frame(*_three_rows()), _taxonomies(), retrievers, top_n=2
    )
    pcpc = out[out["scheme"] == "pcpc"].iloc[0]
    assert pcpc["suggested_codes"] == "63"
    assert pcpc["control_top_code"] == "63"
    assert pcpc["recheck_top_code"] == "64"
    assert set(pcpc["recheck_candidate_codes"].split("|")) <= {"63", "64"}
    assert "999" not in pcpc["recheck_candidate_codes"]
    assert pcpc["recheck_status"] == "SHIFT"
    assert set(out["joint_status"]) == {"RECHECK"}


def test_trusted_floor_recheck_is_stable_when_only_descendant_ranking_changes():
    taxonomies = {
        ("psic", "rev5"): Taxonomy(
            [
                TaxonomyNode("psic", "rev5", "Q", "section", "Human health"),
                TaxonomyNode("psic", "rev5", "86", "division", "Human health", "Q"),
                TaxonomyNode("psic", "rev5", "861", "group", "Hospital activities", "86"),
                TaxonomyNode(
                    "psic",
                    "rev5",
                    "862",
                    "group",
                    "Medical and dental practice activities",
                    "86",
                ),
            ]
        ),
        ("pcpc", "2002"): _taxonomies()[("pcpc", "2002")],
    }
    rows = (
        _row(
            "psic",
            "rev5",
            "86",
            candidate_codes="861|862",
            candidate_scores="0.900000|0.800000",
            suggestion_source=(
                "rule:test;floor:trusted_source_ontology;"
                "retrieval:insufficient_for_refinement"
            ),
        ),
        _row(
            "pcpc",
            "2002",
            "63",
            candidate_codes="63|64",
            candidate_scores="0.900000|0.400000",
        ),
    )
    retrievers = {
        ("psic", "rev5"): ScriptedRetriever(
            [("861", 0.90), ("862", 0.80)],
            treatment=[("862", 0.95), ("861", 0.50)],
        ),
        ("pcpc", "2002"): ScriptedRetriever([("63", 0.90), ("64", 0.40)]),
    }
    out = recheck_joint_worklist(_frame(*rows), taxonomies, retrievers, top_n=2)
    psic = out[out["scheme"] == "psic"].iloc[0]
    assert psic["control_top_code"] == "861"
    assert psic["recheck_top_code"] == "862"
    assert psic["first_pass_status"] == "AGREES"
    assert psic["recheck_status"] == "STABLE"
    assert set(out["joint_status"]) == {"STABLE"}


def test_floor_marker_does_not_change_non_psic_recheck_semantics():
    rows = list(_three_rows())
    rows[1] = _row(
        "pcpc",
        "2002",
        "6",
        candidate_codes="63|64",
        candidate_scores="0.900000|0.800000",
        suggestion_source=(
            "rule:test;floor:trusted_source_ontology;"
            "retrieval:insufficient_for_refinement"
        ),
    )
    retrievers = _stable_retrievers()
    retrievers[("pcpc", "2002")] = ScriptedRetriever(
        [("63", 0.90), ("64", 0.80)],
        treatment=[("64", 0.95), ("63", 0.50)],
    )
    out = recheck_joint_worklist(
        _frame(*rows), _taxonomies(), retrievers, top_n=2, min_margin=0.12
    )
    pcpc = out[out["scheme"] == "pcpc"].iloc[0]
    assert pcpc["recheck_status"] == "SHIFT"
    assert set(out["joint_status"]) == {"RECHECK"}


def test_non_floor_coarse_suggestion_still_uses_existing_shift_logic():
    rows = list(_three_rows())
    rows[0] = _row(
        "psic",
        "rev5",
        "I",
        candidate_codes="56|I",
        candidate_scores="0.900000|0.400000",
        suggestion_source="semantic:test",
    )
    retrievers = _stable_retrievers()
    retrievers[("psic", "rev5")] = ScriptedRetriever(
        [("56", 0.90), ("I", 0.40)],
        treatment=[("I", 0.95), ("56", 0.40)],
    )
    out = recheck_joint_worklist(
        _frame(*rows), _taxonomies(), retrievers, top_n=2, min_margin=0.12
    )
    psic = out[out["scheme"] == "psic"].iloc[0]
    assert psic["recheck_status"] == "SHIFT"
    assert set(out["joint_status"]) == {"RECHECK"}


def test_shift_below_the_margin_is_reported_but_not_escalated():
    retrievers = _stable_retrievers()
    retrievers[("pcpc", "2002")] = ScriptedRetriever(
        [("63", 0.90), ("64", 0.88)], treatment=[("64", 0.89), ("63", 0.88)]
    )
    out = recheck_joint_worklist(
        _frame(*_three_rows()), _taxonomies(), retrievers, top_n=2, min_margin=0.12
    )
    pcpc = out[out["scheme"] == "pcpc"].iloc[0]
    assert pcpc["recheck_status"] == "SHIFT_WEAK"
    assert float(pcpc["recheck_margin"]) < 0.12
    assert set(out["joint_status"]) == {"STABLE"}


def test_same_shift_is_escalated_once_the_margin_is_lowered():
    retrievers = _stable_retrievers()
    retrievers[("pcpc", "2002")] = ScriptedRetriever(
        [("63", 0.90), ("64", 0.88)], treatment=[("64", 0.89), ("63", 0.88)]
    )
    out = recheck_joint_worklist(
        _frame(*_three_rows()), _taxonomies(), retrievers, top_n=2, min_margin=0.005
    )
    assert out[out["scheme"] == "pcpc"].iloc[0]["recheck_status"] == "SHIFT"


def test_multi_code_first_pass_agrees_when_any_code_is_the_control_top():
    rows = list(_three_rows())
    rows[1] = _row(
        "pcpc",
        "2002",
        "",
        codes="64|63",
        mapping_kind="subtree",
        candidate_codes="63|64",
        candidate_scores="0.900000|0.400000",
    )
    out = recheck_joint_worklist(
        _frame(*rows), _taxonomies(), _stable_retrievers(), top_n=2
    )
    pcpc = out[out["scheme"] == "pcpc"].iloc[0]
    assert pcpc["selected_code_column"] == "codes"
    assert pcpc["selected_codes"] == "64|63"
    assert pcpc["first_pass_status"] == "AGREES"
    assert pcpc["recheck_status"] == "STABLE"


def test_first_pass_outside_control_top_n_is_reported_separately():
    rows = list(_three_rows())
    rows[1] = _row(
        "pcpc",
        "2002",
        "6",
        candidate_codes="63|64",
        candidate_scores="0.900000|0.400000",
    )
    out = recheck_joint_worklist(
        _frame(*rows), _taxonomies(), _stable_retrievers(), top_n=2
    )
    pcpc = out[out["scheme"] == "pcpc"].iloc[0]
    assert pcpc["first_pass_status"] == "OUTSIDE_CONTROL_TOPN"
    assert pcpc["recheck_status"] == "STABLE"


# --- degenerate groups -----------------------------------------------------


def test_single_scheme_group_has_no_peers():
    out = recheck_joint_worklist(
        _frame(_row("psic", "rev5", "56")), _taxonomies(), _stable_retrievers(), top_n=2
    )
    assert out.iloc[0]["recheck_status"] == "NO_PEERS"
    assert out.iloc[0]["joint_status"] == "SINGLE_SCHEME"


def test_missing_taxonomy_is_reported_and_excluded_from_the_group_test():
    taxonomies = _taxonomies()
    del taxonomies[("pscc", "2022")]
    retrievers = _stable_retrievers()
    del retrievers[("pscc", "2022")]
    out = recheck_joint_worklist(_frame(*_three_rows()), taxonomies, retrievers, top_n=2)
    pscc = out[out["scheme"] == "pscc"].iloc[0]
    assert pscc["recheck_status"] == "MISSING_TAXONOMY"
    assert set(out["joint_status"]) == {"STABLE"}
    assert "Food preparations" not in out.loc[out["scheme"] == "psic", "peer_context"].iloc[0]


def test_group_is_partial_when_one_evidence_row_has_no_treatment_hit():
    retrievers = _stable_retrievers()
    retrievers[("pscc", "2022")] = ScriptedRetriever(
        [("2106", 0.90), ("21", 0.40)],
        treatment=[],
    )
    out = recheck_joint_worklist(
        _frame(*_three_rows()), _taxonomies(), retrievers, top_n=2
    )
    assert out[out["scheme"] == "pscc"].iloc[0]["recheck_status"] == "NO_RECHECK_HIT"
    assert set(out["joint_status"]) == {"PARTIAL"}


def test_recheck_uses_first_pass_candidate_set_instead_of_branch_search():
    rows = list(_three_rows())
    rows[1] = _row(
        "pcpc",
        "2002",
        "63",
        branch_codes="6|999",
        candidate_codes="63|64",
        candidate_scores="0.900000|0.400000",
    )
    retrievers = _stable_retrievers()
    recheck_joint_worklist(_frame(*rows), _taxonomies(), retrievers, top_n=2)
    method, _query, _requested, allowed = retrievers[("pcpc", "2002")].calls[0]
    assert method == "search"
    assert set(allowed) == {"63", "64"}


# --- frame handling --------------------------------------------------------


def test_first_pass_columns_are_never_written():
    frame = _frame(*_three_rows())
    before = frame.copy()
    out = recheck_joint_worklist(frame, _taxonomies(), _stable_retrievers(), top_n=2)
    for column in ("mapping_kind", "codes", "suggested_codes"):
        assert list(out[column]) == list(before[column])


def test_a_non_unique_index_is_preserved():
    frame = _frame(*_three_rows())
    frame.index = [7, 7, 7]
    out = recheck_joint_worklist(frame, _taxonomies(), _stable_retrievers(), top_n=2)
    assert list(out.index) == [7, 7, 7]
    assert len(out) == 3


def test_rerunning_replaces_rather_than_duplicates_recheck_columns():
    frame = _frame(*_three_rows())
    once = recheck_joint_worklist(frame, _taxonomies(), _stable_retrievers(), top_n=2)
    twice = recheck_joint_worklist(once, _taxonomies(), _stable_retrievers(), top_n=2)
    assert list(twice.columns).count("recheck_status") == 1
    assert list(twice["recheck_status"]) == list(once["recheck_status"])


def test_scope_of_override_is_used_for_lookup():
    rows = [dict(row, scheme=row["scheme"].upper()) for row in _three_rows()]
    out = recheck_joint_worklist(
        _frame(*rows),
        _taxonomies(),
        _stable_retrievers(),
        top_n=2,
        scope_of=lambda record: (
            str(record.get("scheme", "")).casefold(),
            str(record.get("version", "")),
        ),
    )
    assert set(out["recheck_status"]) == {"STABLE"}

# --- v3 peer-evidence boundaries ------------------------------------------


def test_raw_candidates_do_not_become_peer_evidence():
    rows = list(_three_rows())
    rows[2] = _row(
        "pscc",
        "2022",
        "",
        candidate_codes="2106",
        query_text="food preparation",
    )
    out = recheck_joint_worklist(_frame(*rows), _taxonomies(), _stable_retrievers(), top_n=2)
    pcpc_peers = out.loc[out["scheme"] == "pcpc", "peer_context"].iloc[0]
    pscc = out[out["scheme"] == "pscc"].iloc[0]
    assert "Food preparations n.e.s." not in pcpc_peers
    assert pscc["selected_codes"] == ""
    assert pscc["selected_code_column"] == ""
    assert pscc["first_pass_status"] == "NO_FIRST_PASS"


def test_different_versions_of_one_scheme_are_not_peers():
    taxonomies = {
        ("psic", "rev5"): _taxonomies()[("psic", "rev5")],
        ("psic", "rev4"): Taxonomy(
            [
                TaxonomyNode("psic", "rev4", "I", "section", "Accommodation and food service old"),
                TaxonomyNode("psic", "rev4", "57", "division", "Legacy food service", "I"),
            ]
        ),
    }
    retrievers = {
        ("psic", "rev5"): ScriptedRetriever([("56", 0.9)]),
        ("psic", "rev4"): ScriptedRetriever([("57", 0.9)]),
    }
    frame = _frame(
        _row("psic", "rev5", "56"),
        _row("psic", "rev4", "57"),
    )
    out = recheck_joint_worklist(frame, taxonomies, retrievers, top_n=1)
    assert set(out["recheck_status"]) == {"NO_PEERS"}
    assert set(out["joint_status"]) == {"SINGLE_SCHEME"}
    assert not any(out["peer_context"])


def test_multi_code_mapping_preserves_all_paths_and_peer_labels():
    rows = list(_three_rows())
    rows[1] = _row("pcpc", "2002", "", codes="63|64", mapping_kind="UNION")
    out = recheck_joint_worklist(_frame(*rows), _taxonomies(), _stable_retrievers(), top_n=2)
    pcpc = out[out["scheme"] == "pcpc"].iloc[0]
    psic_peers = out.loc[out["scheme"] == "psic", "peer_context"].iloc[0]
    assert "division:63" in pcpc["selected_path"]
    assert "division:64" in pcpc["selected_path"]
    assert "Food serving services" in psic_peers
    assert "Other serving services" in psic_peers


# --- v3 migration/default behavior ----------------------------------------


def test_crosswalk_init_adopts_legacy_and_zeroes_retired_share(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    crosswalk_dir = tmp_path / "reference" / "crosswalks"
    crosswalk_dir.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "source": "fsq",
                "source_value": "Restaurant",
                "row_count": "50",
                "row_share": "0.5",
                "scheme": "psic",
                "version": "rev5",
                "mapping_kind": "SUBTREE",
                "codes": "56",
                "match_type": "exact",
                "confidence": "1.0",
                "notes": "keep me",
                "source_field": "category",
            },
            {
                "source": "fsq",
                "source_value": "Retired category",
                "row_count": "25",
                "row_share": "0.25",
                "scheme": "psic",
                "version": "rev5",
                "mapping_kind": "SUBTREE",
                "codes": "56",
                "match_type": "exact",
                "confidence": "0.9",
                "notes": "carry me",
                "source_field": "category",
            },
        ]
    ).to_csv(crosswalk_dir / "psic_rev5.csv", index=False)

    monkeypatch.setattr(
        cli_module,
        "read_openplaces",
        lambda _path: pd.DataFrame(
            {"canonical_id": ["x"], "fsq_category": ["Restaurant"]}
        ),
    )
    cli_module.crosswalk_init(
        Path("ignored.parquet"),
        output=None,
        schemes="psic,pcpc,pscc",
        version=None,
        adopt_legacy=True,
    )

    joint = pd.read_csv(crosswalk_dir / "joint.csv", dtype=str).fillna("")
    current = joint[
        (joint["scheme"] == "psic") & (joint["source_value"] == "Restaurant")
    ].iloc[0]
    retired = joint[
        (joint["scheme"] == "psic") & (joint["source_value"] == "Retired category")
    ].iloc[0]
    assert current["mapping_kind"] == "SUBTREE"
    assert current["codes"] == "56"
    assert retired["row_count"] == "0"
    assert float(retired["row_share"]) == pytest.approx(0.0)


def test_classify_defaults_to_all_three_schemes():
    default = inspect.signature(cli_module.classify).parameters["schemes"].default
    assert default == "psic,pcpc,pscc"


# --- v5 simultaneous first-pass eligibility ---------------------------------


def test_joint_first_pass_respects_scheme_evidence_policy():
    pcpc = Taxonomy(
        [
            TaxonomyNode("pcpc", "2002", "8", "section", "Services"),
            TaxonomyNode("pcpc", "2002", "851", "group", "Hospital services", "8"),
            TaxonomyNode("pcpc", "2002", "852", "group", "Dental services", "8"),
        ]
    )
    pscc = Taxonomy(
        [
            TaxonomyNode("pscc", "2022", "94", "chapter", "Furniture"),
            TaxonomyNode("pscc", "2022", "9402", "heading", "Medical furniture", "94"),
            TaxonomyNode(
                "pscc",
                "2022",
                "94029015000",
                "commodity",
                "Hospital furniture",
                "9402",
            ),
        ]
    )
    psic = Taxonomy(
        [
            TaxonomyNode("psic", "rev5", "Q", "section", "Human health activities"),
            TaxonomyNode("psic", "rev5", "861", "group", "Hospital activities", "Q"),
        ]
    )

    pcpc_result = suggest_mapping(
        pcpc,
        ScriptedRetriever([("851", 0.95), ("852", 0.20)]),
        "fsq",
        "[Health and Medicine > Hospital]",
    )
    psic_result = suggest_mapping(
        psic,
        ScriptedRetriever([("861", 0.95), ("Q", 0.20)]),
        "fsq",
        "[Health and Medicine > Hospital]",
    )
    pscc_result = suggest_mapping(
        pscc,
        ScriptedRetriever([("94029015000", 0.95), ("9402", 0.20)]),
        "fsq",
        "[Retail > Medical Furniture]",
    )

    for result in (pcpc_result, psic_result):
        assert result.suggested_codes
        assert result.suggested_kind in {"EXACT", "SUBTREE"}
        assert result.review_status == "REVIEW_MAPPING"

    assert pscc_result.candidate_codes
    assert not pscc_result.suggested_codes
    assert pscc_result.review_status == "REVIEW_CANDIDATES"
    assert "guard:commodity_evidence_required" in pscc_result.suggestion_source


def test_a_one_token_query_is_refused_and_the_reason_is_named():
    pcpc = Taxonomy(
        [
            TaxonomyNode("pcpc", "2002", "8", "section", "Services"),
            TaxonomyNode("pcpc", "2002", "851", "group", "Hospital services", "8"),
        ]
    )
    result = suggest_mapping(
        pcpc,
        ScriptedRetriever([("851", 0.99)]),
        "overture",
        "hospital",
        min_score=0.0,
        min_margin=-1.0,
    )
    assert not result.suggested_codes
    assert "guard:short_query" in result.suggestion_source


def test_rule_based_rejections_carry_no_code_and_no_peer_evidence():
    """Rule decisions are reviewable decisions, not mappings."""
    psic = Taxonomy(
        [
            TaxonomyNode("psic", "rev5", "R", "section", "Arts and recreation"),
            TaxonomyNode("psic", "rev5", "910", "group", "Heritage activities", "R"),
        ]
    )
    result = suggest_mapping(
        psic, ScriptedRetriever([("910", 0.99)]), "overture", "historic_site"
    )
    assert result.suggested_kind == "NOT_ACTIVITY"
    assert result.review_status == "REVIEW_DECISION"
    assert not result.suggested_codes
    assert first_pass_codes({"codes": "", "suggested_codes": result.suggested_codes}) == ([], "")


# --- v4 peer encoding and evidence accounting ------------------------------


def test_peer_context_and_query_text_are_separate_renderings():
    rows = list(_three_rows())
    rows[1] = _row("pcpc", "2002", "", codes="63|64", mapping_kind="UNION")
    out = recheck_joint_worklist(_frame(*rows), _taxonomies(), _stable_retrievers(), top_n=2)
    psic = out[out["scheme"] == "psic"].iloc[0]
    assert "[pcpc 2002] 63 Food serving services" in psic["peer_context"]
    assert "63" not in psic["peer_query_context"]
    assert "Food serving services" in psic["peer_query_context"]
    assert psic["peer_query_context"] in psic["recheck_query"]
    assert psic["peer_context"] not in psic["recheck_query"]


def test_separators_never_collide_with_the_code_separator():
    rows = list(_three_rows())
    rows[1] = _row("pcpc", "2002", "", codes="63|64", mapping_kind="UNION")
    out = recheck_joint_worklist(_frame(*rows), _taxonomies(), _stable_retrievers(), top_n=2)
    for column in ("peer_context", "peer_query_context", "selected_path"):
        for value in out[column]:
            assert "|" not in value
    pscc = out[out["scheme"] == "pscc"].iloc[0]
    systems = pscc["peer_context"].split(PEER_SEPARATOR)
    assert len(systems) == 2
    assert all(part.startswith("[") for part in systems)
    pcpc = out[out["scheme"] == "pcpc"].iloc[0]
    assert len(pcpc["selected_path"].split(UNION_SEPARATOR)) == 2


def test_one_peer_label_per_classification_system():
    taxonomies = dict(_taxonomies())
    taxonomies[("psic", "rev4")] = Taxonomy(
        [
            TaxonomyNode("psic", "rev4", "I", "section", "Accommodation old"),
            TaxonomyNode("psic", "rev4", "57", "division", "Legacy food service", "I"),
        ]
    )
    retrievers = _stable_retrievers()
    retrievers[("psic", "rev4")] = ScriptedRetriever([("57", 0.9), ("I", 0.4)])
    frame = _frame(
        _row("psic", "rev5", "", codes="56", mapping_kind="SUBTREE"),
        _row("psic", "rev4", "57"),
        _row("pcpc", "2002", "63"),
    )
    out = recheck_joint_worklist(frame, taxonomies, retrievers, top_n=2)
    pcpc = out[out["scheme"] == "pcpc"].iloc[0]
    systems = pcpc["peer_context"].split(PEER_SEPARATOR)
    assert len(systems) == 1
    # reviewed codes outrank accepted suggestions, so rev5 speaks for PSIC
    assert systems[0].startswith("[psic rev5]")
    assert "Legacy food service" not in pcpc["peer_context"]


def test_configured_version_wins_when_both_peers_are_only_suggestions():
    taxonomies = dict(_taxonomies())
    taxonomies[("psic", "rev4")] = Taxonomy(
        [
            TaxonomyNode("psic", "rev4", "I", "section", "Accommodation old"),
            TaxonomyNode("psic", "rev4", "57", "division", "Legacy food service", "I"),
        ]
    )
    retrievers = _stable_retrievers()
    retrievers[("psic", "rev4")] = ScriptedRetriever([("57", 0.9)])
    frame = _frame(
        _row("psic", "rev4", "57"),
        _row("psic", "rev5", "56"),
        _row("pcpc", "2002", "63"),
    )
    out = recheck_joint_worklist(
        frame,
        taxonomies,
        retrievers,
        top_n=2,
        preferred_versions={"psic": "rev5"},
    )
    pcpc = out[out["scheme"] == "pcpc"].iloc[0]
    assert pcpc["peer_context"].startswith("[psic rev5]")


def test_no_evidence_group_status_when_nothing_is_accepted_yet():
    rows = [
        _row("psic", "rev5", "", candidate_codes="56"),
        _row("pcpc", "2002", "", candidate_codes="63"),
        _row("pscc", "2022", "", candidate_codes="2106"),
    ]
    out = recheck_joint_worklist(_frame(*rows), _taxonomies(), _stable_retrievers(), top_n=2)
    assert set(out["recheck_status"]) == {"NO_PEERS"}
    assert set(out["joint_status"]) == {"NO_EVIDENCE"}


def test_partial_is_kept_for_a_group_where_only_one_row_has_evidence():
    rows = [
        _row("psic", "rev5", "56", candidate_codes="56|I"),
        _row("pcpc", "2002", "", candidate_codes="63|64"),
        _row("pscc", "2022", "", candidate_codes="2106|21"),
    ]
    out = recheck_joint_worklist(
        _frame(*rows), _taxonomies(), _stable_retrievers(), top_n=2
    )
    assert out[out["scheme"] == "psic"].iloc[0]["recheck_status"] == "NO_PEERS"
    assert out[out["scheme"] == "pcpc"].iloc[0]["recheck_status"] == "CANDIDATE_STABLE"
    assert set(out["joint_status"]) == {"PARTIAL"}


def test_base_query_is_uniform_within_a_group():
    rows = list(_three_rows())
    rows[0] = _row(
        "psic",
        "rev5",
        "",
        codes="56",
        mapping_kind="SUBTREE",
        query_text="",
        candidate_codes="56|I",
    )
    retrievers = _stable_retrievers()
    out = recheck_joint_worklist(_frame(*rows), _taxonomies(), retrievers, top_n=2)
    queries = {value for value in out["control_query"] if value}
    assert queries == {"Restaurant"}
    for call in retrievers[("pcpc", "2002")].calls:
        assert call[1].startswith("Restaurant")


def test_query_text_is_used_when_every_backed_row_has_one():
    out = recheck_joint_worklist(
        _frame(*_three_rows()), _taxonomies(), _stable_retrievers(), top_n=2
    )
    assert {value for value in out["control_query"] if value} == {"restaurant service"}

def test_candidate_only_shift_is_diagnostic_and_does_not_escalate_joint_status():
    rows = list(_three_rows())
    rows[2] = _row(
        "pscc",
        "2022",
        "",
        candidate_codes="2106|21",
        candidate_scores="0.900000|0.400000",
    )
    retrievers = _stable_retrievers()
    retrievers[("pscc", "2022")] = ScriptedRetriever(
        [("2106", 0.90), ("21", 0.40)],
        treatment=[("21", 0.90), ("2106", 0.40)],
    )
    out = recheck_joint_worklist(
        _frame(*rows), _taxonomies(), retrievers, top_n=2, min_margin=0.12
    )
    pscc = out[out["scheme"] == "pscc"].iloc[0]
    assert pscc["selected_codes"] == ""
    assert pscc["recheck_status"] == "CANDIDATE_SHIFT"
    assert pscc["recheck_top_code"] == "21"
    assert set(out["joint_status"]) == {"STABLE"}

def test_crosswalk_init_does_not_claim_an_empty_legacy_review(
    tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    crosswalk_dir = tmp_path / "reference" / "crosswalks"
    crosswalk_dir.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "source": "fsq",
                "source_value": "Restaurant",
                "row_count": "1",
                "row_share": "1.0",
                "scheme": "psic",
                "version": "rev5",
                "mapping_kind": "",
                "codes": "",
                "match_type": "exact",
                "confidence": "",
                "notes": "",
                "source_field": "category",
            }
        ]
    ).to_csv(crosswalk_dir / "psic_rev5.csv", index=False)

    monkeypatch.setattr(
        cli_module,
        "read_openplaces",
        lambda _path: pd.DataFrame(
            {"canonical_id": ["x"], "fsq_category": ["Restaurant"]}
        ),
    )
    cli_module.crosswalk_init(
        Path("ignored.parquet"),
        output=None,
        schemes="psic,pcpc,pscc",
        version=None,
        adopt_legacy=True,
    )
    assert "Read reviewed rows from" not in capsys.readouterr().out
