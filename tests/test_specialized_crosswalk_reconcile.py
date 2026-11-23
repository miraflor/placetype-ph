"""Strict regression tests for candidate-only PSIC crosswalk reconciliation."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from placetype_ph.specialized.crosswalk_reconcile import (
    Taxonomy,
    load_rules,
    main,
    publish,
    reconcile,
)


def csvfile(path: Path, rows: list[dict[str, str]], fields: tuple[str, ...] | None = None) -> Path:
    columns = fields or (
        "source",
        "source_value",
        "mapping_kind",
        "codes",
        "match_type",
        "source_field",
        "review_status",
        "scheme",
        "version",
    )
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def rule(value: str, kind: str = "SUBTREE", codes: str = "641", **kwargs: str) -> dict[str, str]:
    return {
        "source": "overture",
        "source_value": value,
        "mapping_kind": kind,
        "codes": codes,
        "source_field": "category",
        "match_type": "exact",
        "review_status": "REVIEWED",
        "scheme": "psic",
        "version": "rev5",
        **kwargs,
    }


def load_pair(tmp_path: Path, incoming: list[dict], local: list[dict]):
    src = csvfile(tmp_path / "incoming.csv", incoming)
    reviewed = csvfile(tmp_path / "local.csv", local)
    return load_rules(src, incoming=True), load_rules(reviewed, incoming=False)


def taxonomy(tmp_path: Path) -> Taxonomy:
    path = tmp_path / "nodes.csv"
    csvfile(
        path,
        [
            {"code": "ROOT", "parent_code": ""},
            {"code": "64", "parent_code": "ROOT"},
            {"code": "641", "parent_code": "64"},
            {"code": "6411", "parent_code": "641"},
            {"code": "47", "parent_code": "ROOT"},
            {"code": "471", "parent_code": "47"},
        ],
        fields=("code", "parent_code"),
    )
    return Taxonomy.load(path)


def test_matching_explicitly_reviewed_requires_no_review_with_taxonomy(tmp_path: Path):
    incoming, local = load_pair(tmp_path, [rule("bank")], [rule("bank")])
    reports, worklist, counts = reconcile(incoming, local, taxonomy(tmp_path))
    assert reports[0]["comparison"] == "MATCH_REVIEWED"
    assert reports[0]["manual_review_required"] == "NO"
    assert reports[0]["validation"] == "VALID_PSIC_NODES"
    assert worklist == []
    assert counts["review_required_rows"] == 0


def test_without_verified_taxonomy_matching_rule_still_requires_review(tmp_path: Path):
    incoming, local = load_pair(tmp_path, [rule("bank")], [rule("bank")])
    reports, worklist, _ = reconcile(incoming, local)
    assert reports[0]["comparison"] == "MATCH_REVIEWED"
    assert reports[0]["validation"] == "TAXONOMY_NOT_SUPPLIED"
    assert len(worklist) == 1


def test_conflicting_reviewed_rule_never_overwritten(tmp_path: Path):
    incoming, local = load_pair(tmp_path, [rule("bank", codes="6411")], [rule("bank", codes="641")])
    reports, worklist, _ = reconcile(incoming, local, taxonomy(tmp_path))
    assert reports[0]["comparison"] == "CONFLICT_WITH_REVIEWED"
    assert reports[0]["relationship"] == "INCOMING_NARROWER"
    assert worklist[0]["review_status"] == "REVIEW_REQUIRED"
    assert worklist[0]["mapping_kind"] == worklist[0]["codes"] == ""
    assert worklist[0]["proposed_mapping_kind"] == "SUBTREE"
    assert worklist[0]["proposed_codes"] == "6411"
    assert local[0].codes == ("641",)


def test_broader_detected_from_taxonomy_not_string_prefix(tmp_path: Path):
    incoming, local = load_pair(tmp_path, [rule("bank", codes="64")], [rule("bank", codes="6411")])
    reports, _, _ = reconcile(incoming, local, taxonomy(tmp_path))
    assert reports[0]["relationship"] == "INCOMING_BROADER"


def test_different_kind_is_not_equivalent_even_with_same_code(tmp_path: Path):
    incoming, local = load_pair(tmp_path, [rule("bank", kind="EXACT")], [rule("bank")])
    reports, _, _ = reconcile(incoming, local, taxonomy(tmp_path))
    assert reports[0]["relationship"] == "DIFFERENT_MAPPING_KIND"
    assert reports[0]["comparison"] == "CONFLICT_WITH_REVIEWED"


def test_unreviewed_local_row_not_automatically_authoritative(tmp_path: Path):
    incoming, local = load_pair(tmp_path, [rule("bank")], [rule("bank", review_status="PENDING")])
    reports, worklist, _ = reconcile(incoming, local, taxonomy(tmp_path))
    assert reports[0]["comparison"] == "MATCH_LOCAL_UNVERIFIED"
    assert len(worklist) == 1


def test_missing_rule_produces_review_only(tmp_path: Path):
    incoming, local = load_pair(tmp_path, [rule("bank")], [])
    reports, worklist, _ = reconcile(incoming, local, taxonomy(tmp_path))
    assert reports[0]["comparison"] == "ABSENT_LOCAL_MAPPING"
    assert worklist[0]["origin"] == "external_catalog_candidate_only"


def test_same_codes_in_reverse_order_equal(tmp_path: Path):
    incoming, local = load_pair(
        tmp_path, [rule("combination", "UNION", "641;47")], [rule("combination", "UNION", "47|641")]
    )
    reports, worklist, _ = reconcile(incoming, local, taxonomy(tmp_path))
    assert reports[0]["comparison"] == "MATCH_REVIEWED"
    assert worklist == []


def test_distinct_match_type_not_claimed_equivalent(tmp_path: Path):
    incoming, local = load_pair(tmp_path, [rule("bank")], [rule("bank", match_type="regex")])
    reports, worklist, _ = reconcile(incoming, local, taxonomy(tmp_path))
    assert reports[0]["comparison"] == "MATCH_TYPE_DISAGREEMENT"
    assert len(worklist) == 1


def test_duplicated_incoming_normalized_key_aborts(tmp_path: Path):
    incoming, local = load_pair(tmp_path, [rule("Bank"), rule(" bank ")], [])
    with pytest.raises(ValueError, match="duplicate incoming"):
        reconcile(incoming, local)


def test_multiple_local_rows_fail_closed(tmp_path: Path):
    incoming, local = load_pair(tmp_path, [rule("bank")], [rule("bank"), rule("BANK")])
    reports, worklist, _ = reconcile(incoming, local, taxonomy(tmp_path))
    assert reports[0]["comparison"] == "MULTIPLE_LOCAL_MATCHES"
    assert len(worklist) == 1


def test_invalid_taxonomy_code_requires_review(tmp_path: Path):
    incoming, local = load_pair(tmp_path, [rule("bank", codes="641999")], [])
    reports, worklist, _ = reconcile(incoming, local, taxonomy(tmp_path))
    assert reports[0]["validation"] == "INVALID_CODES:641999"
    assert len(worklist) == 1


def test_foreign_scheme_rows_excluded_before_psic_validation(tmp_path: Path):
    incoming, local = load_pair(
        tmp_path,
        [rule("bank")],
        [
            rule("product", scheme="pcpc", mapping_kind="CUSTOM", codes=""),
            rule("bank"),
        ],
    )
    assert len(local) == 1
    reports, _, _ = reconcile(incoming, local, taxonomy(tmp_path))
    assert reports[0]["comparison"] == "MATCH_REVIEWED"


def test_incoming_other_taxonomy_fails(tmp_path: Path):
    path = csvfile(tmp_path / "foreign.csv", [rule("bank", scheme="pcpc")])
    with pytest.raises(ValueError, match="not PSIC Rev. 5"):
        load_rules(path, incoming=True)


def test_invalid_regex_rejected(tmp_path: Path):
    path = csvfile(tmp_path / "invalid.csv", [rule("(", match_type="regex")])
    with pytest.raises(ValueError, match="invalid regex"):
        load_rules(path, incoming=True)


def test_duplicate_codes_rejected(tmp_path: Path):
    path = csvfile(tmp_path / "invalid.csv", [rule("bank", codes="641;641")])
    with pytest.raises(ValueError, match="duplicate code"):
        load_rules(path, incoming=True)


def test_zero_code_terminal_mapping_valid(tmp_path: Path):
    incoming, local = load_pair(
        tmp_path,
        [rule("beach", kind="NOT_ACTIVITY", codes="")],
        [rule("beach", kind="NOT_ACTIVITY", codes="")],
    )
    reports, worklist, _ = reconcile(incoming, local)
    assert reports[0]["validation"] == "NOT_APPLICABLE_TERMINAL"
    assert worklist == []


def test_taxonomy_cycles_rejected(tmp_path: Path):
    path = csvfile(
        tmp_path / "cycle.csv",
        [
            {"code": "641", "parent_code": "64"},
            {"code": "64", "parent_code": "641"},
        ],
        fields=("code", "parent_code"),
    )
    with pytest.raises(ValueError, match="cycle"):
        Taxonomy.load(path)


def test_atomic_reports_and_no_overwrite(tmp_path: Path):
    incoming, local = load_pair(tmp_path, [rule("bank")], [rule("bank")])
    reports, worklist, stats = reconcile(incoming, local, taxonomy(tmp_path))
    dest = tmp_path / "audit"
    publish(dest, reports, worklist, stats)
    assert len(list(csv.DictReader((dest / "reconciliation.csv").open(encoding="utf-8")))) == 1
    assert (dest / "summary.json").exists()
    assert (dest / "review_worklist.csv").exists()
    with pytest.raises(FileExistsError):
        publish(dest, reports, worklist, stats)


def test_report_rename_failure_rolls_back(tmp_path: Path, monkeypatch):
    import placetype_ph.specialized.crosswalk_reconcile as module

    def fail(*args):
        raise OSError("injected publication failure")

    monkeypatch.setattr(module.os, "rename", fail)
    with pytest.raises(OSError, match="injected"):
        publish(tmp_path / "report", [], [], {})
    assert not (tmp_path / "report").exists()
    assert not list(tmp_path.glob("report.pending-*"))


def test_dry_run_never_writes(tmp_path: Path, capsys):
    src = csvfile(tmp_path / "incoming.csv", [rule("bank")])
    local = csvfile(tmp_path / "local.csv", [rule("bank")])
    out = tmp_path / "out"
    assert (
        main(
            [
                "--incoming",
                str(src),
                "--existing",
                str(local),
                "--output-dir",
                str(out),
                "--dry-run",
            ]
        )
        == 0
    )
    assert not out.exists()
    assert json.loads(capsys.readouterr().out)["incoming_rows"] == 1


def test_cli_report_input_files_unchanged(tmp_path: Path):
    src = csvfile(tmp_path / "incoming.csv", [rule("bank")])
    local = csvfile(tmp_path / "local.csv", [rule("bank")])
    expected = {p: p.read_bytes() for p in (src, local)}
    out = tmp_path / "out"
    assert main(["--incoming", str(src), "--existing", str(local), "--output-dir", str(out)]) == 0
    assert all(path.read_bytes() == content for path, content in expected.items())
    assert out.is_dir()


def test_blank_joint_scheme_not_assumed_to_be_psic(tmp_path: Path):
    incoming, local = load_pair(tmp_path, [rule("bank")], [rule("bank", scheme="")])
    assert local == ()
    reports, worklist, _ = reconcile(incoming, local)
    assert reports[0]["comparison"] == "ABSENT_LOCAL_MAPPING"
    assert len(worklist) == 1


def test_specialized_catalog_same_and_conflicting_codes(tmp_path: Path):
    incoming, local = load_pair(tmp_path, [rule("bank")], [])
    rulekey = ("overture", "category", "bank")
    reports, worklist, stats = reconcile(
        incoming,
        local,
        taxonomy(tmp_path),
        specialized_index={rulekey: [("activity-bank", "641")]},
    )
    assert reports[0]["specialized_comparison"] == "SPECIALIZED_SAME_CODE"
    assert reports[0]["specialized_rule_ids"] == "activity-bank"
    assert worklist[0]["review_status"] == "REVIEW_REQUIRED"
    assert stats["specialized_comparison_counts"]["SPECIALIZED_SAME_CODE"] == 1
    reports2, _, _ = reconcile(incoming, local, specialized_index={rulekey: [("r2", "471")]})
    assert reports2[0]["specialized_comparison"] == "SPECIALIZED_HIERARCHY_UNVERIFIED"


def test_blank_local_mapping_is_preserved_as_unreviewed_placeholder(tmp_path: Path):
    incoming, local = load_pair(
        tmp_path,
        [rule("bank")],
        [rule("bank", mapping_kind="", codes="", review_status="PENDING")],
    )
    assert len(local) == 1
    assert local[0].kind == ""
    assert local[0].codes == ()
    assert local[0].review_state == "UNREVIEWED"
    reports, worklist, stats = reconcile(incoming, local, taxonomy(tmp_path))
    assert reports[0]["comparison"] == "LOCAL_UNREVIEWED_MAPPING"
    assert reports[0]["relationship"] == "LOCAL_UNMAPPED_PLACEHOLDER"
    assert reports[0]["local_review_state"] == "UNREVIEWED"
    assert reports[0]["local_file"]
    assert reports[0]["local_lines"] == "2"
    assert reports[0]["manual_review_required"] == "YES"
    assert worklist[0]["mapping_kind"] == worklist[0]["codes"] == ""
    assert worklist[0]["proposed_mapping_kind"] == "SUBTREE"
    assert worklist[0]["proposed_codes"] == "641"
    assert stats["comparison_counts"] == {"LOCAL_UNREVIEWED_MAPPING": 1}


def test_realistic_local_master_crosswalk_without_review_status(tmp_path: Path):
    fields = (
        "source",
        "source_value",
        "row_count",
        "row_share",
        "scheme",
        "version",
        "mapping_kind",
        "codes",
        "match_type",
        "confidence",
        "notes",
        "source_field",
    )
    incoming = csvfile(tmp_path / "incoming.csv", [rule("bank")])
    local = csvfile(
        tmp_path / "local.csv",
        [
            {
                "source": "overture",
                "source_value": "bank",
                "row_count": "8",
                "row_share": "0.4",
                "scheme": "psic",
                "version": "rev5",
                "mapping_kind": "",
                "codes": "",
                "match_type": "exact",
                "confidence": "",
                "notes": "not yet reviewed",
                "source_field": "category",
            }
        ],
        fields=fields,
    )
    out = tmp_path / "audit_not_created"
    assert (
        main(
            [
                "--incoming",
                str(incoming),
                "--existing",
                str(local),
                "--output-dir",
                str(out),
                "--dry-run",
            ]
        )
        == 0
    )
    assert not out.exists()
    loaded = load_rules(local, incoming=False)
    assert loaded[0].review_state == "UNREVIEWED"


def test_local_blank_mapping_not_valid_as_reviewed_approval(tmp_path: Path):
    local = csvfile(
        tmp_path / "local.csv", [rule("bank", mapping_kind="", codes="", review_status="REVIEWED")]
    )
    with pytest.raises(ValueError, match="marked 'REVIEWED' but has no mapping_kind"):
        load_rules(local, incoming=False)


def test_partial_blank_local_mapping_rejected_not_silently_skipped(tmp_path: Path):
    local = csvfile(tmp_path / "local.csv", [rule("bank", mapping_kind="", codes="641")])
    with pytest.raises(ValueError, match="unsupported mapping_kind='' with codes='641'"):
        load_rules(local, incoming=False)


def test_incoming_blank_mapping_remains_invalid(tmp_path: Path):
    incoming = csvfile(tmp_path / "incoming.csv", [rule("bank", mapping_kind="", codes="")])
    with pytest.raises(ValueError, match="unsupported mapping_kind='' with codes=''"):
        load_rules(incoming, incoming=True)


def test_unknown_local_matching_mode_exposes_offending_value(tmp_path: Path):
    local = csvfile(tmp_path / "local.csv", [rule("bank", match_type="substring")])
    with pytest.raises(ValueError, match="unsupported match_type='substring'"):
        load_rules(local, incoming=False)


def test_unknown_local_mapping_kind_exposes_offending_value(tmp_path: Path):
    local = csvfile(tmp_path / "local.csv", [rule("bank", mapping_kind="TENTATIVE")])
    with pytest.raises(ValueError, match="unsupported mapping_kind='TENTATIVE'"):
        load_rules(local, incoming=False)


def test_blank_local_and_authoritative_local_for_same_key_cause_review(tmp_path: Path):
    incoming, local = load_pair(
        tmp_path,
        [rule("bank")],
        [rule("bank", mapping_kind="", codes="", review_status="PENDING"), rule("bank")],
    )
    reports, worklist, _ = reconcile(incoming, local, taxonomy(tmp_path))
    assert reports[0]["comparison"] == "MULTIPLE_LOCAL_MATCHES"
    assert len(worklist) == 1
    assert worklist[0]["review_status"] == "REVIEW_REQUIRED"


def test_non_psic_placeholder_is_excluded_from_local_psic_index(tmp_path: Path):
    incoming, local = load_pair(
        tmp_path,
        [rule("bank")],
        [rule("bank", mapping_kind="", codes="", scheme="pcpc")],
    )
    assert local == ()
    reports, _, _ = reconcile(incoming, local)
    assert reports[0]["comparison"] == "ABSENT_LOCAL_MAPPING"


def test_specialized_comparison_uses_taxonomy_for_ancestry(tmp_path: Path):
    tree = taxonomy(tmp_path)
    incoming, local = load_pair(tmp_path, [rule("post_office", codes="471")], [])
    key = ("overture", "category", "post_office")
    reports, _, stats = reconcile(incoming, local, tree, specialized_index={key: [("r", "47")]})
    assert reports[0]["specialized_comparison"] == "SPECIALIZED_INCOMING_WITHIN_ROOT"
    assert stats["specialized_comparison_counts"] == {"SPECIALIZED_INCOMING_WITHIN_ROOT": 1}


def test_specialized_comparison_recognizes_broader_incoming_root(tmp_path: Path):
    incoming, local = load_pair(tmp_path, [rule("veterinarian", codes="64")], [])
    key = ("overture", "category", "veterinarian")
    reports, _, _ = reconcile(
        incoming, local, taxonomy(tmp_path), specialized_index={key: [("r", "6411")]}
    )
    assert reports[0]["specialized_comparison"] == "SPECIALIZED_ROOT_WITHIN_INCOMING"


def test_specialized_terminal_vs_activity_remains_conflict(tmp_path: Path):
    incoming, local = load_pair(tmp_path, [rule("community", kind="UNCODEABLE", codes="")], [])
    key = ("overture", "category", "community")
    reports, worklist, _ = reconcile(
        incoming, local, taxonomy(tmp_path), specialized_index={key: [("r", "641")]}
    )
    assert reports[0]["specialized_comparison"] == "SPECIALIZED_TERMINAL_CODE_CONFLICT"
    assert len(worklist) == 1
    assert worklist[0]["mapping_kind"] == worklist[0]["codes"] == ""


def test_specialized_different_branches_remain_distinct(tmp_path: Path):
    incoming, local = load_pair(tmp_path, [rule("other", codes="471")], [])
    key = ("overture", "category", "other")
    reports, _, _ = reconcile(
        incoming, local, taxonomy(tmp_path), specialized_index={key: [("r", "641")]}
    )
    assert reports[0]["specialized_comparison"] == "SPECIALIZED_DIFFERENT_BRANCHES"


def test_specialized_unknown_relation_without_taxonomy_is_not_disjoint(tmp_path: Path):
    incoming, local = load_pair(tmp_path, [rule("other", codes="471")], [])
    key = ("overture", "category", "other")
    reports, _, _ = reconcile(incoming, local, specialized_index={key: [("r", "47")]})
    assert reports[0]["specialized_comparison"] == "SPECIALIZED_HIERARCHY_UNVERIFIED"


def test_specialized_index_is_not_mutated_by_wildcard_merging(tmp_path: Path):
    incoming, local = load_pair(tmp_path, [rule("bank")], [])
    key = ("overture", "category", "bank")
    wildcard = ("*", "category", "bank")
    index = {key: [("source", "641")], wildcard: [("global", "6411")]}
    first, _, _ = reconcile(incoming, local, taxonomy(tmp_path), specialized_index=index)
    second, _, _ = reconcile(incoming, local, taxonomy(tmp_path), specialized_index=index)
    assert first == second
    assert len(index[key]) == 1
    assert first[0]["specialized_codes"] == "641;6411"


def test_specialized_unrecognized_code_flags_taxonomy_failure(tmp_path: Path):
    incoming, local = load_pair(tmp_path, [rule("bank")], [])
    key = ("overture", "category", "bank")
    reports, _, _ = reconcile(
        incoming, local, taxonomy(tmp_path), specialized_index={key: [("r", "99999")]}
    )
    assert reports[0]["specialized_comparison"] == "SPECIALIZED_CODE_NOT_IN_TAXONOMY"


def test_equal_root_codes_do_not_authorize_local_approval(tmp_path: Path):
    incoming, local = load_pair(tmp_path, [rule("bank", kind="EXACT")], [])
    key = ("overture", "category", "bank")
    reports, worklist, _ = reconcile(
        incoming, local, taxonomy(tmp_path), specialized_index={key: [("r", "641")]}
    )
    assert reports[0]["specialized_comparison"] == "SPECIALIZED_SAME_CODE"
    assert reports[0]["manual_review_required"] == "YES"
    assert worklist[0]["proposed_mapping_kind"] == "EXACT"
    assert worklist[0]["mapping_kind"] == ""


@pytest.mark.parametrize(
    ("category", "source", "kind", "incoming_codes", "specialized_code", "expected"),
    [
        (
            "flowers_and_gifts_store",
            "overture",
            "UNION",
            "47192;47735",
            "47",
            "SPECIALIZED_INCOMING_WITHIN_ROOT",
        ),
        (
            "lodging",
            "overture",
            "UNION",
            "551;552;553;559",
            "55",
            "SPECIALIZED_INCOMING_WITHIN_ROOT",
        ),
        (
            "veterinarian",
            "overture",
            "SUBTREE",
            "75",
            "750",
            "SPECIALIZED_ROOT_WITHIN_INCOMING",
        ),
        (
            "amenity=post_office",
            "osm",
            "SUBTREE",
            "531",
            "53",
            "SPECIALIZED_INCOMING_WITHIN_ROOT",
        ),
        (
            "social_or_community_service",
            "overture",
            "UNCODEABLE",
            "",
            "88",
            "SPECIALIZED_TERMINAL_CODE_CONFLICT",
        ),
    ],
)
def test_five_review_disagreements_are_not_string_disjoint(
    tmp_path: Path,
    category: str,
    source: str,
    kind: str,
    incoming_codes: str,
    specialized_code: str,
    expected: str,
):
    # Self-contained model of the relevant verified ancestry, including the
    # excluded accommodation-intermediation sibling 554 under division 55.
    parents = {
        "ROOT": "",
        "47": "ROOT",
        "471": "47",
        "4719": "471",
        "47192": "4719",
        "477": "47",
        "4773": "477",
        "47735": "4773",
        "55": "ROOT",
        "551": "55",
        "552": "55",
        "553": "55",
        "554": "55",
        "559": "55",
        "75": "ROOT",
        "750": "75",
        "53": "ROOT",
        "531": "53",
        "88": "ROOT",
    }
    nodes = csvfile(
        tmp_path / "nodes.csv",
        [{"code": code, "parent_code": parent} for code, parent in parents.items()],
        fields=("code", "parent_code"),
    )
    candidate = rule(category, kind=kind, codes=incoming_codes, source=source)
    incoming, local = load_pair(tmp_path, [candidate], [])
    key = (source, "category", category)
    reports, worklist, _ = reconcile(
        incoming,
        local,
        Taxonomy.load(nodes),
        specialized_index={key: [("advisory", specialized_code)]},
    )
    assert reports[0]["specialized_comparison"] == expected
    assert len(worklist) == 1
    assert worklist[0]["mapping_kind"] == worklist[0]["codes"] == ""
    if category == "lodging":
        assert "554" not in reports[0]["incoming_codes"]
