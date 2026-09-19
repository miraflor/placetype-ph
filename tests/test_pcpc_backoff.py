from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from placetype_ph.express import promote_auto_suggestions, recheck_verdict
from placetype_ph.joint_crosswalk import (
    PEER_CONTEXT_MARKER,
    ensure_joint_key,
    recheck_joint_worklist,
)
from placetype_ph.models import TaxonomyNode
from placetype_ph.taxonomy import Taxonomy


class ScriptedRetriever:
    def __init__(self, control, treatment=None):
        self.control = list(control)
        self.treatment = list(control if treatment is None else treatment)

    def search(self, query: str, top_n: int = 20, allowed_codes=None):
        ranking = self.treatment if PEER_CONTEXT_MARKER in query else self.control
        if allowed_codes is not None:
            allowed = {str(code) for code in allowed_codes}
            ranking = [(code, score) for code, score in ranking if code in allowed]
        return [
            SimpleNamespace(code=code, score=score)
            for code, score in ranking[:top_n]
        ]


def _taxonomies():
    return {
        ("pcpc", "2002"): Taxonomy(
            [
                TaxonomyNode("pcpc", "2002", "8", "section", "Business services"),
                TaxonomyNode("pcpc", "2002", "89", "division", "Other services", "8"),
                TaxonomyNode("pcpc", "2002", "891", "group", "Manufacturing services", "89"),
                TaxonomyNode("pcpc", "2002", "8912", "class", "Printing services", "891"),
                TaxonomyNode(
                    "pcpc",
                    "2002",
                    "89121",
                    "subclass",
                    "Printing services and services related to printing",
                    "8912",
                ),
                TaxonomyNode(
                    "pcpc", "2002", "891211", "item", "Printing services", "89121"
                ),
                TaxonomyNode(
                    "pcpc",
                    "2002",
                    "891212",
                    "item",
                    "Services related to printing",
                    "89121",
                ),
            ]
        ),
        ("psic", "rev5"): Taxonomy(
            [
                TaxonomyNode("psic", "rev5", "C", "section", "Manufacturing"),
                TaxonomyNode("psic", "rev5", "18", "division", "Printing", "C"),
                TaxonomyNode("psic", "rev5", "181", "group", "Printing activities", "18"),
            ]
        ),
    }


def _frame(pcpc_codes: str, pcpc_candidates: str) -> pd.DataFrame:
    rows = [
        {
            "source": "overture",
            "source_value": "printing_service",
            "source_field": "category",
            "scheme": "pcpc",
            "version": "2002",
            "mapping_kind": "",
            "codes": "",
            "suggested_codes": pcpc_codes,
            "candidate_codes": pcpc_candidates,
            "candidate_scores": "0.900000|0.700000",
            "query_text": "printing service",
            "branch_codes": "",
        },
        {
            "source": "overture",
            "source_value": "printing_service",
            "source_field": "category",
            "scheme": "psic",
            "version": "rev5",
            "mapping_kind": "",
            "codes": "",
            "suggested_codes": "181",
            "candidate_codes": "181|18",
            "candidate_scores": "0.900000|0.400000",
            "query_text": "printing service",
            "branch_codes": "",
        },
    ]
    frame = pd.DataFrame(rows)
    ensure_joint_key(frame)
    return frame


def test_decisive_pcpc_sibling_shift_coarsens_to_immediate_parent():
    taxonomies = _taxonomies()
    retrievers = {
        ("pcpc", "2002"): ScriptedRetriever(
            [("891211", 0.90), ("891212", 0.70)],
            [("891212", 0.95), ("891211", 0.50)],
        ),
        ("psic", "rev5"): ScriptedRetriever([("181", 0.90), ("18", 0.40)]),
    }
    out = recheck_joint_worklist(
        _frame("891211", "891211|891212"),
        taxonomies,
        retrievers,
        top_n=2,
        min_margin=0.12,
    )
    pcpc = out[out["scheme"] == "pcpc"].iloc[0]
    assert pcpc["recheck_status"] == "COARSENED"
    assert pcpc["recheck_resolution_code"] == "89121"
    assert set(out["joint_status"]) == {"STABLE"}


def test_weak_pcpc_sibling_shift_keeps_existing_shift_weak_policy():
    taxonomies = _taxonomies()
    retrievers = {
        ("pcpc", "2002"): ScriptedRetriever(
            [("891211", 0.90), ("891212", 0.88)],
            [("891212", 0.89), ("891211", 0.88)],
        ),
        ("psic", "rev5"): ScriptedRetriever([("181", 0.90), ("18", 0.40)]),
    }
    out = recheck_joint_worklist(
        _frame("891211", "891211|891212"),
        taxonomies,
        retrievers,
        top_n=2,
        min_margin=0.12,
    )
    pcpc = out[out["scheme"] == "pcpc"].iloc[0]
    assert pcpc["recheck_status"] == "SHIFT_WEAK"
    assert pcpc["recheck_resolution_code"] == ""


def test_stale_first_pass_code_is_not_silently_coarsened():
    taxonomies = _taxonomies()
    retrievers = {
        ("pcpc", "2002"): ScriptedRetriever(
            [("891212", 0.90), ("891211", 0.70)],
            [("891211", 0.95), ("891212", 0.50)],
        ),
        ("psic", "rev5"): ScriptedRetriever([("181", 0.90), ("18", 0.40)]),
    }
    out = recheck_joint_worklist(
        _frame("891211", "891212|891211"),
        taxonomies,
        retrievers,
        top_n=2,
        min_margin=0.12,
    )
    pcpc = out[out["scheme"] == "pcpc"].iloc[0]
    assert pcpc["recheck_status"] == "SHIFT"
    assert pcpc["recheck_resolution_code"] == ""


def _write_suggestion(path: Path, resolution: str) -> None:
    pd.DataFrame(
        [
            {
                "mapping_kind": "",
                "codes": "",
                "suggested_kind": "EXACT",
                "suggested_codes": "891211",
                "retrieval_score": "0.9",
                "suggestion_source": "semantic:raw_category;retrieval:strong_separated_hit",
                "recheck_status": "COARSENED",
                "recheck_resolution_code": resolution,
            }
        ]
    ).to_csv(path, index=False)


def test_coarsened_recheck_promotes_common_parent():
    assert recheck_verdict("COARSENED") == "CLEARED"


def test_coarsened_resolution_is_promoted_as_subtree(tmp_path: Path):
    suggested = tmp_path / "suggested.csv"
    automatic = tmp_path / "auto.csv"
    _write_suggestion(suggested, "89121")
    summary = promote_auto_suggestions(suggested, automatic)
    frame = pd.read_csv(automatic, dtype=str).fillna("")
    assert summary["promoted_cleared"] == 1
    assert frame.loc[0, "mapping_kind"] == "SUBTREE"
    assert frame.loc[0, "codes"] == "89121"
    assert frame.loc[0, "express_status"] == "AUTO_ACCEPTED"
    assert "recheck:coarsened_to=89121" in frame.loc[0, "notes"]


def test_missing_coarsened_resolution_is_held_even_with_default_policy(tmp_path: Path):
    suggested = tmp_path / "suggested.csv"
    automatic = tmp_path / "auto.csv"
    _write_suggestion(suggested, "")
    summary = promote_auto_suggestions(suggested, automatic)
    frame = pd.read_csv(automatic, dtype=str).fillna("")
    assert summary["held_no_verdict"] == 1
    assert frame.loc[0, "codes"] == ""
    assert frame.loc[0, "express_status"] == "HELD_NO_VERDICT"
