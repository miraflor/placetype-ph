from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pandas as pd
import pytest

from placetype_ph import express
from placetype_ph.express import (
    atomic_write_text,
    classification_run_ready,
    crosswalk_has_mappings,
    csv_ready,
    default_output_path,
    file_state,
    find_local_taxonomy_source,
    input_cache_key,
    pipeline_state,
    promote_auto_suggestions,
    promoted_total,
    recheck_verdict,
    reference_is_joint_format,
    seed_joint_worklist,
    summarize_automatic_crosswalk,
)

BASE_STATES = ["taxonomy:psic:abc"]


def _suggestion_row(**overrides: str) -> dict[str, str]:
    row = {
        "mapping_kind": "",
        "codes": "",
        "suggested_kind": "EXACT",
        "suggested_codes": "47114",
        "confidence": "",
        "retrieval_score": "0.9",
        "suggestion_source": "semantic:overture:x",
        "notes": "",
        "recheck_status": "STABLE",
    }
    row.update(overrides)
    return row


def _write_suggestions(path: Path, rows: list[dict[str, str]]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


# --- output location and cache key ------------------------------------------


def test_default_output_stays_in_placetype_output(tmp_path: Path):
    input_path = (
        tmp_path
        / "openplaces-ph"
        / "data"
        / "output"
        / "areas_metro_manila"
        / "canonical_pois.parquet"
    )
    assert default_output_path(input_path) == Path(
        "output/areas_metro_manila_placetype.parquet"
    )


def test_input_cache_key_changes_with_input_content(tmp_path: Path):
    input_path = tmp_path / "canonical_pois.parquet"
    input_path.write_bytes(b"aaaa")
    first = input_cache_key(input_path, BASE_STATES)
    # Same size, different content: a size and mtime identity can miss this.
    input_path.write_bytes(b"aaab")
    assert input_cache_key(input_path, BASE_STATES) != first


def test_input_cache_key_survives_a_copy_of_the_same_file(tmp_path: Path):
    original = tmp_path / "a" / "canonical_pois.parquet"
    original.parent.mkdir()
    original.write_bytes(b"payload")
    copy = tmp_path / "b" / "canonical_pois.parquet"
    copy.parent.mkdir()
    shutil.copy(original, copy)
    os.utime(copy, (0, 0))
    assert input_cache_key(original, BASE_STATES) == input_cache_key(copy, BASE_STATES)


def test_file_state_falls_back_to_size_and_mtime_for_large_files(tmp_path, monkeypatch):
    monkeypatch.setattr(express, "HASH_BYTE_LIMIT", 0)
    path = tmp_path / "big.parquet"
    path.write_bytes(b"x")
    assert file_state(path).startswith("stat:")


def test_input_cache_key_covers_the_pipeline_parameters(tmp_path, monkeypatch):
    input_path = tmp_path / "canonical_pois.parquet"
    input_path.write_bytes(b"a")
    before = input_cache_key(input_path, [*BASE_STATES, pipeline_state()])
    monkeypatch.setitem(express.CLASSIFY_PARAMS, "passes", 4)
    after = input_cache_key(input_path, [*BASE_STATES, pipeline_state()])
    assert before != after


# --- taxonomy sources --------------------------------------------------------


def test_find_local_taxonomy_source_prefers_retained_workbook(tmp_path: Path):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    retained = downloads / "2002_PCPC.xlsx"
    retained.write_bytes(b"xlsx")
    assert find_local_taxonomy_source(tmp_path, "pcpc", "2002") == retained


# --- seeding -----------------------------------------------------------------


def test_seed_joint_worklist_copies_once(tmp_path: Path):
    reference = tmp_path / "joint.csv"
    worklist = tmp_path / "work" / "joint.csv"
    reference.write_text(
        "source,source_value,scheme,version,mapping_kind\n"
        "osm,restaurant,psic,rev5,EXACT\n",
        encoding="utf-8",
    )

    assert seed_joint_worklist(reference, worklist)
    assert worklist.read_text(encoding="utf-8") == reference.read_text(encoding="utf-8")

    reference.write_text("changed\n", encoding="utf-8")
    assert not seed_joint_worklist(reference, worklist)
    assert "source,source_value,scheme,version,mapping_kind" in worklist.read_text(
        encoding="utf-8"
    )


def test_seed_joint_worklist_filters_to_the_requested_schemes(tmp_path: Path):
    reference = tmp_path / "joint.csv"
    reference.write_text(
        "source,source_value,scheme,version,mapping_kind\n"
        "osm,a,psic,rev5,EXACT\n"
        "osm,a,PSCC,2022,EXACT\n"
        "osm,a,pcpc,2002,EXACT\n",
        encoding="utf-8",
    )
    worklist = tmp_path / "work" / "joint.csv"
    assert seed_joint_worklist(reference, worklist, schemes={"psic"})
    frame = pd.read_csv(worklist, dtype=str)
    assert list(frame["scheme"]) == ["psic"]


def test_seed_joint_worklist_declines_a_pre_joint_reference(tmp_path: Path):
    reference = tmp_path / "joint.csv"
    reference.write_text("source_value,mapping_kind\nbakery,EXACT\n", encoding="utf-8")
    worklist = tmp_path / "work" / "joint.csv"
    assert not reference_is_joint_format(reference)
    # No exception, and nothing written: crosswalk-init's legacy path handles this file.
    assert not seed_joint_worklist(reference, worklist, schemes={"psic"})
    assert not worklist.exists()


def test_reference_is_joint_format_requires_the_joint_identity_columns(tmp_path: Path):
    reference = tmp_path / "joint.csv"
    reference.write_text("scheme,mapping_kind\npsic,EXACT\n", encoding="utf-8")
    assert not reference_is_joint_format(reference)


def test_seed_joint_worklist_declines_when_the_filter_empties_it(tmp_path: Path):
    reference = tmp_path / "joint.csv"
    reference.write_text(
        "source,source_value,scheme,version,mapping_kind\n"
        "osm,a,pcpc,2002,EXACT\n",
        encoding="utf-8",
    )
    worklist = tmp_path / "work" / "joint.csv"
    assert not seed_joint_worklist(reference, worklist, schemes={"psic"})
    assert not worklist.exists()


# --- recheck verdicts --------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "verdict"),
    [
        ("STABLE", "CLEARED"),
        ("CANDIDATE_STABLE", "NO_VERDICT"),
        ("SHIFT", "CONTRADICTED"),
        ("SHIFT_WEAK", "INCONCLUSIVE"),
        ("CANDIDATE_SHIFT", "NO_VERDICT"),
        ("NO_PEERS", "NO_VERDICT"),
        ("NO_CONTROL_HIT", "NO_VERDICT"),
        ("NO_RECHECK_HIT", "NO_VERDICT"),
        ("MISSING_TAXONOMY", "NO_VERDICT"),
        ("", "NO_VERDICT"),
        ("A_STATUS_ADDED_LATER", "NO_VERDICT"),
    ],
)
def test_recheck_verdict_covers_the_whole_vocabulary(status: str, verdict: str):
    assert recheck_verdict(status) == verdict


# --- promotion ---------------------------------------------------------------


def test_promote_auto_suggestions_preserves_reviewed_and_respects_recheck(tmp_path: Path):
    suggested = tmp_path / "suggested.csv"
    automatic = tmp_path / "auto.csv"
    _write_suggestions(
        suggested,
        [
            _suggestion_row(
                mapping_kind="EXACT",
                codes="47114",
                suggested_kind="SUBTREE",
                suggested_codes="47",
                confidence="1.0",
                notes="human reviewed",
                recheck_status="SHIFT",
            ),
            _suggestion_row(
                suggested_kind="SUBTREE",
                suggested_codes="55",
                retrieval_score="0.8",
                suggestion_source="semantic:overture:lodging",
                recheck_status="STABLE",
            ),
            _suggestion_row(suggested_codes="56302", recheck_status="SHIFT"),
            _suggestion_row(
                suggested_kind="", suggested_codes="", recheck_status="CANDIDATE_SHIFT"
            ),
            _suggestion_row(
                suggested_kind="NOT_ACTIVITY",
                suggested_codes="",
                retrieval_score="",
                suggestion_source="rule:high_precision_non_activity",
                recheck_status="CANDIDATE_STABLE",
            ),
        ],
    )

    summary = promote_auto_suggestions(suggested, automatic)
    frame = pd.read_csv(automatic, dtype=str).fillna("")

    assert summary == {
        "reviewed": 1,
        "promoted_cleared": 1,
        "promoted_inconclusive": 0,
        "promoted_unchecked": 1,
        "held_recheck": 1,
        "held_inconclusive": 0,
        "held_no_verdict": 0,
        "unresolved": 1,
        "without_code": 1,
        "held_unclearable": 0,
    }
    assert promoted_total(summary) == 2

    assert frame.loc[0, "mapping_kind"] == "EXACT"
    assert frame.loc[0, "codes"] == "47114"
    assert frame.loc[0, "express_status"] == "REVIEWED"

    assert frame.loc[1, "mapping_kind"] == "SUBTREE"
    assert frame.loc[1, "codes"] == "55"
    assert frame.loc[1, "confidence"] == "0.8"
    assert frame.loc[1, "express_status"] == "AUTO_ACCEPTED"

    assert frame.loc[2, "mapping_kind"] == ""
    assert frame.loc[2, "express_status"] == "HELD_RECHECK"

    assert frame.loc[3, "mapping_kind"] == ""
    assert frame.loc[3, "express_status"] == "UNRESOLVED"

    assert frame.loc[4, "mapping_kind"] == "NOT_ACTIVITY"
    assert frame.loc[4, "codes"] == ""
    assert frame.loc[4, "express_status"] == "AUTO_ACCEPTED_UNCHECKED"


def test_weak_shift_is_inconclusive_and_candidate_shift_stays_candidate_only(tmp_path: Path):
    """Only an accepted-code weak shift participates in the promotion policy."""
    suggested = tmp_path / "suggested.csv"
    automatic = tmp_path / "auto.csv"
    _write_suggestions(
        suggested,
        [
            _suggestion_row(recheck_status="SHIFT_WEAK"),
            _suggestion_row(
                suggested_kind="", suggested_codes="", recheck_status="CANDIDATE_SHIFT"
            ),
        ],
    )
    summary = promote_auto_suggestions(suggested, automatic)
    frame = pd.read_csv(automatic, dtype=str).fillna("")
    assert summary["promoted_inconclusive"] == 1
    assert summary["unresolved"] == 1
    assert frame.loc[0, "express_recheck"] == "INCONCLUSIVE"
    assert frame.loc[0, "express_status"] == "AUTO_ACCEPTED_INCONCLUSIVE"
    assert frame.loc[1, "mapping_kind"] == ""
    assert frame.loc[1, "express_status"] == "UNRESOLVED"

    held = tmp_path / "held.csv"
    strict = promote_auto_suggestions(suggested, held, promote_unchecked=False)
    assert strict["held_inconclusive"] == 1
    assert strict["unresolved"] == 1
    assert promoted_total(strict) == 0


def test_hold_inconclusive_keeps_unseen_suggestions_flowing(tmp_path: Path):
    """The middle policy: hold what the recheck argued against, keep what it never saw."""
    suggested = tmp_path / "suggested.csv"
    out = tmp_path / "auto.csv"
    _write_suggestions(
        suggested,
        [
            _suggestion_row(recheck_status="SHIFT_WEAK"),
            _suggestion_row(recheck_status="NO_PEERS"),
            _suggestion_row(recheck_status="STABLE"),
        ],
    )
    summary = promote_auto_suggestions(suggested, out, hold_inconclusive=True)
    frame = pd.read_csv(out, dtype=str).fillna("")
    assert list(frame["express_status"]) == [
        "HELD_INCONCLUSIVE",
        "AUTO_ACCEPTED_UNCHECKED",
        "AUTO_ACCEPTED",
    ]
    assert summary["held_inconclusive"] == 1
    assert summary["promoted_unchecked"] == 1
    assert summary["promoted_cleared"] == 1


def test_held_code_less_suggestions_are_reported_as_unclearable(tmp_path: Path):
    """A no-code suggestion cannot be peer-cleared; direct review can still resolve it."""
    suggested = tmp_path / "suggested.csv"
    out = tmp_path / "auto.csv"
    _write_suggestions(
        suggested,
        [
            _suggestion_row(
                suggested_kind="NOT_ACTIVITY",
                suggested_codes="",
                recheck_status="CANDIDATE_STABLE",
            ),
            _suggestion_row(recheck_status="NO_PEERS"),
        ],
    )
    summary = promote_auto_suggestions(suggested, out, promote_unchecked=False)
    assert summary["held_no_verdict"] == 2
    assert summary["held_unclearable"] == 1
    assert summarize_automatic_crosswalk(out)["held_unclearable"] == 1


def test_an_absent_verdict_is_recorded_and_can_be_held(tmp_path: Path):
    suggested = tmp_path / "suggested.csv"
    _write_suggestions(suggested, [_suggestion_row(recheck_status="NO_PEERS")])

    promoted = tmp_path / "promoted.csv"
    summary = promote_auto_suggestions(suggested, promoted, promote_unchecked=True)
    frame = pd.read_csv(promoted, dtype=str).fillna("")
    assert summary["promoted_unchecked"] == 1
    assert summary["promoted_cleared"] == 0
    assert frame.loc[0, "express_status"] == "AUTO_ACCEPTED_UNCHECKED"
    assert frame.loc[0, "express_recheck"] == "NO_VERDICT"
    assert frame.loc[0, "codes"] == "47114"

    held = tmp_path / "held.csv"
    summary = promote_auto_suggestions(suggested, held, promote_unchecked=False)
    frame = pd.read_csv(held, dtype=str).fillna("")
    assert summary["held_no_verdict"] == 1
    assert promoted_total(summary) == 0
    assert frame.loc[0, "express_status"] == "HELD_NO_VERDICT"
    assert frame.loc[0, "codes"] == ""


def test_reused_auto_crosswalk_reconstructs_the_same_warning_summary(tmp_path: Path):
    suggested = tmp_path / "suggested.csv"
    automatic = tmp_path / "auto.csv"
    _write_suggestions(
        suggested,
        [
            _suggestion_row(recheck_status="NO_PEERS"),
            _suggestion_row(recheck_status="STABLE", suggested_codes="55"),
            _suggestion_row(
                suggested_kind="NOT_ACTIVITY",
                suggested_codes="",
                recheck_status="NO_PEERS",
            ),
        ],
    )
    fresh = promote_auto_suggestions(suggested, automatic)
    reused = summarize_automatic_crosswalk(automatic)
    assert reused == fresh
    assert reused["promoted_unchecked"] == 2
    assert reused["promoted_cleared"] == 1
    assert reused["without_code"] == 1


def test_promote_auto_suggestions_requires_a_recheck_column(tmp_path: Path):
    suggested = tmp_path / "suggested.csv"
    row = _suggestion_row()
    del row["recheck_status"]
    _write_suggestions(suggested, [row])
    with pytest.raises(ValueError, match="recheck_status"):
        promote_auto_suggestions(suggested, tmp_path / "auto.csv")


# --- resumability ------------------------------------------------------------


def test_csv_ready_rejects_an_unusable_file(tmp_path: Path):
    missing = tmp_path / "absent.csv"
    assert not csv_ready(missing, ["a"])

    truncated = tmp_path / "truncated.csv"
    truncated.write_text('a,b\n"unterminated,2\n', encoding="utf-8")
    assert not csv_ready(truncated, ["a", "b"])

    empty = tmp_path / "empty.csv"
    empty.write_bytes(b"")
    assert not csv_ready(empty, ["a"])

    # What an interrupted write actually looks like: a short final row. pandas pads it
    # rather than raising, so the missing terminator is what rejects it.
    cut = tmp_path / "cut.csv"
    cut.write_text("a,b\n1,2\n3,", encoding="utf-8")
    assert not csv_ready(cut, ["a", "b"])

    partial = tmp_path / "partial.csv"
    partial.write_text("a,b\n1,2\n", encoding="utf-8")
    assert csv_ready(partial, ["a", "b"])
    assert not csv_ready(partial, ["a", "c"])


def test_atomic_write_leaves_nothing_behind_when_the_move_fails(tmp_path, monkeypatch):
    target = tmp_path / "out" / "joint_auto.csv"
    atomic_write_text(target, "a,b\n1,2\n")
    assert target.read_text(encoding="utf-8") == "a,b\n1,2\n"

    def boom(*_args, **_kwargs):
        raise OSError("interrupted")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="interrupted"):
        atomic_write_text(target, "clobbered")
    assert target.read_text(encoding="utf-8") == "a,b\n1,2\n"
    assert list(target.parent.glob("*.tmp")) == []


def test_crosswalk_has_mappings_detects_an_empty_result(tmp_path: Path):
    empty = tmp_path / "empty.csv"
    pd.DataFrame([{"mapping_kind": "", "codes": ""}]).to_csv(empty, index=False)
    assert not crosswalk_has_mappings(empty)

    populated = tmp_path / "populated.csv"
    pd.DataFrame(
        [{"mapping_kind": "", "codes": ""}, {"mapping_kind": "EXACT", "codes": "47114"}]
    ).to_csv(populated, index=False)
    assert crosswalk_has_mappings(populated)

    assert not crosswalk_has_mappings(tmp_path / "absent.csv")


def test_classification_run_ready_uses_manifest_named_outputs(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "outputs": {
                    "entity_classifications": "entities.parquet",
                    "classified_pois": "places.parquet",
                }
            }
        ),
        encoding="utf-8",
    )
    assert not classification_run_ready(run_dir)

    (run_dir / "entities.parquet").write_bytes(b"x")
    (run_dir / "places.parquet").write_bytes(b"x")
    assert classification_run_ready(run_dir)
