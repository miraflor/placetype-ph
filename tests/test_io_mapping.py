from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Callable
from importlib import resources
from pathlib import Path

import pandas as pd
import pytest

from placetype_ph.classifier import EntityClassifier
from placetype_ph.crosswalk import Crosswalk
from placetype_ph.io_mapping import (
    BRIDGE_BASES,
    BRIDGE_FILE,
    BRIDGE_RELATIONS,
    CONCORDANCE_FILE,
    IO_GIS_COLUMNS,
    IO_OUTPUT_COLUMNS,
    RESOLUTIONS,
    IOCrosswalk,
    IOCrosswalkError,
    IOMappingStatus,
    _normalize_title,
    enrich_summary_with_io,
    io_status_counts,
)
from placetype_ph.models import TaxonomyNode
from placetype_ph.pipeline import classify_openplaces
from placetype_ph.taxonomy import Taxonomy

REV5_NODES = Path(__file__).resolve().parents[1] / "reference" / "psic_rev5" / "nodes.parquet"

# PSIC 2019 division ranges per section; used to check the concordance hierarchy.
PSIC2019_SECTIONS = {
    "A": (1, 3), "B": (5, 9), "C": (10, 33), "D": (35, 35), "E": (36, 39), "F": (41, 43),
    "G": (45, 47), "H": (49, 53), "I": (55, 56), "J": (58, 63), "K": (64, 66), "L": (68, 68),
    "M": (69, 75), "N": (77, 82), "O": (84, 84), "P": (85, 85), "Q": (86, 88), "R": (90, 93),
    "S": (94, 96), "T": (97, 98), "U": (99, 99),
}  # fmt: skip

EXPECTED_PSIC2019_COUNTS = {"section": 21, "division": 88, "group": 245}
# Ownership of dwellings is an I-O industry but not a direct PSIC establishment activity, so it
# is intentionally absent from this PSIC-to-I-O concordance at IO80/IO240.
EXPECTED_IO_CODES = {
    "io16": {f"{code:02d}" for code in range(1, 17)},
    "io80": {f"{code:02d}" for code in range(1, 81)} - {"71"},
    "io240": {f"{code:03d}" for code in range(1, 241)} - {"210"},
}


def _packaged_text(filename: str) -> str:
    return resources.files("placetype_ph").joinpath("data", filename).read_text(encoding="utf-8")


def _packaged_rows(filename: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(_packaged_text(filename))))


def _codes(value: str) -> set[str]:
    return {part.strip() for part in value.split(";") if part.strip()}


@pytest.fixture(scope="module")
def crosswalk() -> IOCrosswalk:
    return IOCrosswalk.load_default()


@pytest.fixture(scope="module")
def rev5() -> Taxonomy:
    return Taxonomy.load(REV5_NODES)


def _agriculture_taxonomy(group_title: str = "Growing of non-perennial crops") -> Taxonomy:
    return Taxonomy(
        [
            TaxonomyNode("psic", "rev5", "A", "section", "Agriculture, forestry and fishing"),
            TaxonomyNode(
                "psic",
                "rev5",
                "01",
                "division",
                "Crop and animal production, hunting and related service activities",
                "A",
            ),
            TaxonomyNode("psic", "rev5", "011", "group", group_title, "01"),
            TaxonomyNode("psic", "rev5", "0111", "class", "Example class", "011"),
        ]
    )


# --- mapping behaviour on small trees ---------------------------------------------------


def test_default_io_crosswalk_preserves_many_to_many_sets(crosswalk):
    mapped = crosswalk.map_psic("0111", _agriculture_taxonomy())

    assert mapped["io_mapping_status"] == IOMappingStatus.MAPPED_GROUP
    assert mapped["io_mapping_psic_code"] == "011"
    assert mapped["io_mapping_bridge_relation"] == "same"
    assert mapped["io_mapping_bridge_basis"] == "code_title_match"
    assert mapped["io16_codes"] == "01"
    assert mapped["io80_codes"] == "01; 02; 04; 09; 13; 14"
    assert mapped["io240_codes"].startswith("001; 002; 004")


def test_changed_group_title_backs_off_to_division(crosswalk):
    mapped = crosswalk.map_psic("0111", _agriculture_taxonomy("New Revision 5 group meaning"))

    assert mapped["io_mapping_status"] == IOMappingStatus.MAPPED_DIVISION
    assert mapped["io_mapping_psic_level"] == "division"
    assert mapped["io_mapping_psic_code"] == "01"
    assert mapped["io16_codes"] == "01"


def test_draft_bridge_exposes_candidates_without_promoting_them(crosswalk, rev5):
    # 64220 (financing conduits) reaches draft row 642 -> 2019 642; 643. Its accepted parent,
    # division 64, also holds banks (IO240 204), so the draft row narrows the answer.
    mapped = crosswalk.map_psic("64220", rev5)

    assert mapped["io_mapping_status"] == IOMappingStatus.PROVISIONAL_GROUP
    assert mapped["io_mapping_bridge_relation"] == "changed_scope"
    assert mapped["io_mapping_bridge_basis"] == "draft"
    assert mapped["io_mapping_psic2019_codes"] == "642; 643"
    assert mapped["io240_codes"] == "205"


def test_tree_that_differs_from_the_bridge_is_not_mapped_by_code_alone(crosswalk):
    taxonomy = Taxonomy(
        [
            TaxonomyNode("psic", "rev5", "A", "section", "Different section meaning"),
            TaxonomyNode("psic", "rev5", "01", "division", "Different division meaning", "A"),
            TaxonomyNode("psic", "rev5", "011", "group", "Different group meaning", "01"),
        ]
    )
    mapped = crosswalk.map_psic("011", taxonomy)

    assert mapped["io_mapping_status"] == IOMappingStatus.UNMAPPED_NO_BRIDGE
    assert all(mapped[f"{res}_codes"] == "" for res in RESOLUTIONS)


def test_unknown_psic_code_is_reported_not_raised(crosswalk):
    mapped = crosswalk.map_psic("ZZZ", _agriculture_taxonomy())
    assert mapped["io_mapping_status"] == IOMappingStatus.UNMAPPED_UNKNOWN_PSIC_CODE


def test_summary_enrichment_adds_io_columns(crosswalk):
    summary = pd.DataFrame([{"canonical_id": "poi:1", "psic_code": "0111"}])
    results = pd.DataFrame([{"canonical_id": "poi:1", "scheme": "psic", "code": "0111"}])

    enriched = enrich_summary_with_io(summary, results, _agriculture_taxonomy(), crosswalk)

    assert list(enriched.columns) == ["canonical_id", "psic_code", *IO_OUTPUT_COLUMNS]
    assert enriched.loc[0, "io_mapping_status"] == "MAPPED_GROUP"
    assert enriched.loc[0, "io80_codes"] == "01; 02; 04; 09; 13; 14"


def test_poi_without_psic_result_row_gets_unmapped_status(crosswalk):
    summary = pd.DataFrame({"canonical_id": ["poi:1", "poi:2", "poi:3"]})
    results = pd.DataFrame(
        [
            {"canonical_id": "poi:1", "scheme": "psic", "code": "0111"},
            {"canonical_id": "poi:2", "scheme": "psic", "code": None},
            {"canonical_id": "poi:3", "scheme": "pcpc", "code": "01"},
        ]
    )

    enriched = enrich_summary_with_io(summary, results, _agriculture_taxonomy(), crosswalk)

    assert enriched["io_mapping_status"].tolist() == [
        "MAPPED_GROUP",
        "UNMAPPED_NO_PSIC",
        "UNMAPPED_NO_PSIC",
    ]
    assert enriched.loc[2, "io240_codes"] == ""
    assert io_status_counts(enriched) == {"MAPPED_GROUP": 1, "UNMAPPED_NO_PSIC": 2}


def test_gis_columns_exclude_names_and_psic2019_provenance():
    assert not any(column.endswith("_names") for column in IO_GIS_COLUMNS)
    assert "io_mapping_psic2019_codes" not in IO_GIS_COLUMNS
    assert "io_mapping_bridge_relation" in IO_GIS_COLUMNS
    assert set(IO_GIS_COLUMNS) < set(IO_OUTPUT_COLUMNS)


# --- mapping behaviour on the real PSIC Revision 5 tree --------------------------------


@pytest.mark.parametrize(
    ("code", "status", "io80", "io240"),
    [
        ("47119", "PROVISIONAL_GROUP", "55", "178"),  # retail bridge is still draft
        ("47300", "PROVISIONAL_GROUP", "55", "178"),  # renamed, not yet reviewed
        ("47721", "PROVISIONAL_GROUP", "55", "178"),  # retail bridge is still draft
        ("95311", "PROVISIONAL_GROUP", "56", "179"),  # motor-vehicle repair bridge draft
        ("96100", "PROVISIONAL_GROUP", "80", "238"),  # reused code; bridge still draft
        ("96211", "PROVISIONAL_GROUP", "80", "236; 237"),  # hairdressing bridge draft
        ("80111", "PROVISIONAL_GROUP", "72", "225"),  # merged scope; bridge still draft
        ("91210", "PROVISIONAL_GROUP", "79", "234"),  # split scope; bridge still draft
        ("142", "PROVISIONAL_GROUP", "31", "090"),  # recoding still draft
        ("144", "PROVISIONAL_GROUP", "31", "089"),  # recoding still draft
        ("99001", "NO_IO_CORRESPONDENCE", "", ""),  # draft 990 = accepted division 99
        ("85201", "MAPPED_DIVISION", "74; 75", "229; 230"),  # draft 852 = accepted 85
        ("09100", "MAPPED_DIVISION", "26", "045"),  # draft 091 = accepted 09
        ("56400", "MAPPED_DIVISION", "63", "193"),  # draft 564 = accepted 56
        ("10301", "PROVISIONAL_GROUP", "27", "050"),  # draft 103 narrows division 10
        ("55400", "PROVISIONAL_GROUP", "72", "224"),  # reservation line, 2019 group 799
    ],
)
def test_real_rev5_named_cases(crosswalk, rev5, code, status, io80, io240):
    mapped = crosswalk.map_psic(code, rev5)

    assert mapped["io_mapping_status"] == status
    assert mapped["io80_codes"] == io80
    assert mapped["io240_codes"] == io240


def test_every_real_rev5_code_is_accounted_for_without_promoting_drafts(crosswalk, rev5):
    statuses = {}
    for code in rev5.nodes:
        mapped = crosswalk.map_psic(code, rev5)
        status = mapped["io_mapping_status"]
        basis = mapped["io_mapping_bridge_basis"]
        statuses[code] = status

        if basis == "draft":
            assert status.startswith("PROVISIONAL_"), code
        elif basis in {"code_title_match", "reviewed"}:
            assert status.startswith("MAPPED_") or status in {
                IOMappingStatus.NO_IO_CORRESPONDENCE,
                IOMappingStatus.NO_PSIC2019_COUNTERPART,
            }

        if status in {
            IOMappingStatus.NO_IO_CORRESPONDENCE,
            IOMappingStatus.PROVISIONAL_NO_IO_CORRESPONDENCE,
        }:
            # Rev 5 section U (households) and V (extraterritorial bodies) only.
            assert rev5.ancestors(code)[-1] in {"U", "V"}, code

    # Known Rev 5 codes may honestly remain UNMAPPED_NO_BRIDGE in a future revision, but they
    # must not appear as missing or unknown inputs. Nothing here requires draft rows to exist,
    # so the test keeps passing after every draft row has been reviewed.
    assert IOMappingStatus.UNMAPPED_NO_PSIC not in statuses.values()
    assert IOMappingStatus.UNMAPPED_UNKNOWN_PSIC_CODE not in statuses.values()


def _io_sets_of(crosswalk: IOCrosswalk, targets: tuple[str, ...]) -> dict[str, set[str]]:
    return {
        res: {c for t in targets for c in crosswalk.concordance_row(t).resolution(res).codes}
        for res in RESOLUTIONS
    }


def _provisional_rule_violations(crosswalk: IOCrosswalk, rev5: Taxonomy) -> list[str]:
    """Codes marked provisional although an accepted row above gives the same answer."""
    violations = []
    for code in rev5.nodes:
        mapped = crosswalk.map_psic(code, rev5)
        if not mapped["io_mapping_status"].startswith("PROVISIONAL_"):
            continue
        if mapped["io_mapping_bridge_relation"] == "no_counterpart":
            continue  # a draft no_counterpart row is never replaced by an ancestor
        candidate = {r: set(filter(None, mapped[f"{r}_codes"].split("; "))) for r in RESOLUTIONS}
        for ancestor in rev5.ancestors(mapped["io_mapping_psic_code"], include_self=False):
            link = crosswalk.bridge_row(str(rev5.get(ancestor).level), ancestor)
            if link is None or link.basis == "draft":
                continue
            # Only an ordinary accepted row confirms an answer; no_counterpart confirms none.
            same = _io_sets_of(crosswalk, link.psic2019_codes) == candidate
            if link.relation != "no_counterpart" and same:
                violations.append(code)
            break
    return violations


def test_provisional_only_when_the_draft_row_changes_the_answer(crosswalk, rev5):
    assert not _provisional_rule_violations(crosswalk, rev5)


def _psic2019_inside(code: str, container: str) -> bool:
    """Whether PSIC 2019 ``code`` equals ``container`` or lies inside it."""
    if code == container:
        return True
    if len(container) == 2 and len(code) == 3:
        return code[:2] == container
    if container.isalpha() and not code.isalpha():
        low, high = PSIC2019_SECTIONS[container]
        return low <= int(code[:2]) <= high
    return False


def _containment_violations(crosswalk: IOCrosswalk, rev5: Taxonomy) -> list[tuple]:
    """Bridge rows whose PSIC 2019 codes fall outside the nearest ancestor row with codes.

    A ``no_counterpart`` row lists no codes, so its children are checked against the next
    ancestor up instead of being skipped.
    """
    violations = []
    for link in crosswalk.bridge:
        if link.relation == "no_counterpart":
            continue
        container = None
        ancestor = rev5.get(link.rev5_code).parent_code
        while ancestor is not None and container is None:
            row = crosswalk.bridge_row(str(rev5.get(ancestor).level), ancestor)
            if row is not None and row.relation != "no_counterpart":
                container = row
            ancestor = rev5.get(ancestor).parent_code
        if container is None:
            continue
        outside = [
            code
            for code in link.psic2019_codes
            if not any(_psic2019_inside(code, p) for p in container.psic2019_codes)
        ]
        if outside:
            violations.append((link.rev5_code, outside, container.rev5_code))
    return violations


def test_bridge_rows_stay_inside_their_parent_rows(crosswalk, rev5):
    """A Rev 5 category lies inside its parent, so its PSIC 2019 codes must as well.

    This found that group 554 (intermediation for accommodation) came from 2019 group 799,
    outside 2019 division 55, although Rev 5 division 55 kept its code and title.
    """
    assert not _containment_violations(crosswalk, rev5)


# --- data-file invariants -----------------------------------------------------------


def test_bridge_has_one_row_per_rev5_bridge_node_with_its_current_title(crosswalk, rev5):
    frame = rev5.to_frame()
    expected = frame[frame["level"].isin(["section", "division", "group"])]

    assert len(crosswalk.bridge) == len(expected)
    rows = zip(expected["level"], expected["code"], expected["title"], strict=True)
    for level, code, title in rows:
        link = crosswalk.bridge_row(level, code)
        assert link is not None, (level, code)
        assert link.rev5_title == title, (level, code)
        assert link.relation in BRIDGE_RELATIONS
        assert link.basis in BRIDGE_BASES


def test_code_title_match_rows_really_match_by_code_and_title(crosswalk):
    for link in crosswalk.bridge:
        if link.basis != "code_title_match":
            continue
        row = crosswalk.concordance_row(link.rev5_code)
        assert row is not None and row.psic_level == link.rev5_level, link.rev5_code
        assert _normalize_title(row.psic_name) == _normalize_title(link.rev5_title), link


def test_concordance_has_expected_psic2019_coverage():
    counts = {}
    for row in _packaged_rows(CONCORDANCE_FILE):
        counts[row["psic_level"]] = counts.get(row["psic_level"], 0) + 1

    assert counts == EXPECTED_PSIC2019_COUNTS


def test_concordance_has_expected_io_code_universe():
    rows = _packaged_rows(CONCORDANCE_FILE)
    for resolution, expected in EXPECTED_IO_CODES.items():
        observed = set().union(*(_codes(row[f"{resolution}_codes"]) for row in rows))
        assert observed == expected, resolution


def test_concordance_is_vertically_consistent():
    rows = _packaged_rows(CONCORDANCE_FILE)
    by_key = {(r["psic_level"], r["psic_code"]): r for r in rows}
    section_of = {
        f"{d:02d}": s for s, (lo, hi) in PSIC2019_SECTIONS.items() for d in range(lo, hi + 1)
    }
    for res in RESOLUTIONS:
        column = f"{res}_codes"
        for (level, code), row in by_key.items():
            if level == "division":
                groups = [r for (lv, c), r in by_key.items() if lv == "group" and c[:2] == code]
                assert set().union(*(_codes(g[column]) for g in groups)) == _codes(row[column])
            if level == "section":
                divisions = [
                    r for (lv, c), r in by_key.items() if lv == "division" and section_of[c] == code
                ]
                assert set().union(*(_codes(d[column]) for d in divisions)) == _codes(row[column])


def test_concordance_has_no_blank_finer_resolution():
    """An IO80 industry is the sum of its IO240 industries, so IO80 implies IO240."""
    for row in _packaged_rows(CONCORDANCE_FILE):
        key = (row["psic_level"], row["psic_code"])
        if row["io16_codes"]:
            assert row["io80_codes"], key
        if row["io80_codes"]:
            assert row["io240_codes"], key


def test_provenance_records_file_hashes(crosswalk):
    provenance = crosswalk.provenance()

    for role, filename in (("concordance", CONCORDANCE_FILE), ("bridge", BRIDGE_FILE)):
        data = resources.files("placetype_ph").joinpath("data", filename).read_bytes()
        assert provenance[role]["file"] == filename
        assert provenance[role]["sha256"] == hashlib.sha256(data).hexdigest()
    assert provenance["concordance"]["rows"] == len(crosswalk.rows)
    assert sum(provenance["bridge"]["rows_by_basis"].values()) == len(crosswalk.bridge)
    assert sum(provenance["bridge"]["rows_by_relation"].values()) == len(crosswalk.bridge)


# --- loader validation --------------------------------------------------------------


def _write_rows(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _drop_last_name(row: dict[str, str]) -> None:
    row["io80_names"] = row["io80_names"].rsplit(" | ", 1)[0]


def _duplicate_first_row(rows: list[dict[str, str]]) -> None:
    rows.append(dict(rows[0]))


CONCORDANCE_DEFECTS: dict[str, Callable[[list[dict[str, str]]], None]] = {
    "duplicate row": _duplicate_first_row,
    "joined confidence": lambda rows: rows[0].update(io80_confidence="High with caveat | High"),
    "names misaligned": lambda rows: _drop_last_name(rows[0]),
    "wrong psic version": lambda rows: rows[0].update(psic_version="2009 PSIC"),
    "malformed code": lambda rows: rows[0].update(io16_codes="1"),
    "two names for one code": lambda rows: rows[1].update(io16_names="Another name"),
}


def test_reviewed_no_counterpart_is_explicit(tmp_path: Path):
    concordance = _write_rows(tmp_path / "c.csv", _packaged_rows(CONCORDANCE_FILE))
    rows = _packaged_rows(BRIDGE_FILE)
    row = next(r for r in rows if r["rev5_level"] == "group" and r["rev5_code"] == "011")
    row.update(psic2019_codes="", relation="no_counterpart", basis="reviewed")
    crosswalk = IOCrosswalk.load(concordance, _write_rows(tmp_path / "b.csv", rows))

    mapped = crosswalk.map_psic("0111", _agriculture_taxonomy())

    assert mapped["io_mapping_status"] == IOMappingStatus.NO_PSIC2019_COUNTERPART
    assert mapped["io_mapping_bridge_relation"] == "no_counterpart"
    assert mapped["io_mapping_psic2019_codes"] == ""
    assert all(mapped[f"{res}_codes"] == "" for res in RESOLUTIONS)


def test_draft_no_counterpart_is_not_collapsed_into_blank_parent_io(tmp_path: Path, rev5):
    concordance = _write_rows(tmp_path / "c.csv", _packaged_rows(CONCORDANCE_FILE))
    rows = _packaged_rows(BRIDGE_FILE)
    row = next(r for r in rows if r["rev5_level"] == "group" and r["rev5_code"] == "990")
    row.update(psic2019_codes="", relation="no_counterpart", basis="draft")
    crosswalk = IOCrosswalk.load(concordance, _write_rows(tmp_path / "b.csv", rows))

    mapped = crosswalk.map_psic("99001", rev5)

    assert mapped["io_mapping_status"] == IOMappingStatus.PROVISIONAL_NO_PSIC2019_COUNTERPART
    assert mapped["io_mapping_bridge_relation"] == "no_counterpart"
    assert mapped["io_mapping_bridge_basis"] == "draft"


def _crosswalk_with(tmp_path: Path, changes: dict[tuple[str, str], dict]) -> IOCrosswalk:
    concordance = _write_rows(tmp_path / "c.csv", _packaged_rows(CONCORDANCE_FILE))
    rows = _packaged_rows(BRIDGE_FILE)
    for key, update in changes.items():
        next(r for r in rows if (r["rev5_level"], r["rev5_code"]) == key).update(update)
    return IOCrosswalk.load(concordance, _write_rows(tmp_path / "b.csv", rows))


NO_COUNTERPART = {"psic2019_codes": "", "relation": "no_counterpart"}


def test_invariants_accept_a_draft_no_counterpart_row(tmp_path: Path, rev5):
    # The documented use of no_counterpart must not make the data invariants fail.
    crosswalk = _crosswalk_with(tmp_path, {("group", "990"): {**NO_COUNTERPART, "basis": "draft"}})

    assert not _provisional_rule_violations(crosswalk, rev5)
    assert not _containment_violations(crosswalk, rev5)


def test_accepted_no_counterpart_ancestor_does_not_replace_a_draft_answer(tmp_path: Path, rev5):
    # Draft group 990 names 2019 group 990, which has no I-O line. Its parent now says that no
    # PSIC 2019 counterpart exists; that confirms no answer, so the draft result stays visible.
    crosswalk = _crosswalk_with(
        tmp_path, {("division", "99"): {**NO_COUNTERPART, "basis": "reviewed"}}
    )

    mapped = crosswalk.map_psic("99001", rev5)

    assert mapped["io_mapping_status"] == IOMappingStatus.PROVISIONAL_NO_IO_CORRESPONDENCE
    assert mapped["io_mapping_psic_code"] == "990"
    assert mapped["io_mapping_bridge_basis"] == "draft"


def test_containment_uses_nearest_ancestor_with_codes(tmp_path: Path, rev5):
    # With division 47 marked no_counterpart, group 471 must still lie inside section G.
    crosswalk = _crosswalk_with(
        tmp_path,
        {
            ("division", "47"): {**NO_COUNTERPART, "basis": "reviewed"},
            ("group", "471"): {"psic2019_codes": "011"},
        },
    )

    assert [violation[0] for violation in _containment_violations(crosswalk, rev5)] == ["471"]


def test_unchanged_copies_load(tmp_path: Path):
    concordance = _write_rows(tmp_path / "c.csv", _packaged_rows(CONCORDANCE_FILE))
    bridge = _write_rows(tmp_path / "b.csv", _packaged_rows(BRIDGE_FILE))
    assert len(IOCrosswalk.load(concordance, bridge).bridge) == len(_packaged_rows(BRIDGE_FILE))


@pytest.mark.parametrize("defect", sorted(CONCORDANCE_DEFECTS))
def test_loader_rejects_concordance_defects(tmp_path: Path, defect: str):
    rows = _packaged_rows(CONCORDANCE_FILE)
    CONCORDANCE_DEFECTS[defect](rows)
    with pytest.raises(IOCrosswalkError):
        IOCrosswalk.load(_write_rows(tmp_path / "c.csv", rows))


BRIDGE_DEFECTS: dict[str, Callable[[list[dict[str, str]]], None]] = {
    "unknown relation": lambda rows: rows[0].update(relation="similar"),
    "unknown basis": lambda rows: rows[0].update(basis="guessed"),
    "code_title_match with non-same relation": lambda rows: next(
        r for r in rows if r["basis"] == "code_title_match"
    ).update(relation="renamed"),
    "duplicate targets": lambda rows: rows[0].update(psic2019_codes="A; A"),
    "same pointing elsewhere": lambda rows: next(
        r for r in rows if r["relation"] == "same"
    ).update(psic2019_codes="B", basis="reviewed"),
    "renamed pointing elsewhere": lambda rows: next(
        r for r in rows if r["relation"] == "renamed"
    ).update(psic2019_codes="D"),
    "recoded pointing to itself": lambda rows: next(
        r for r in rows if r["relation"] == "recoded"
    ).update(psic2019_codes=next(r for r in rows if r["relation"] == "recoded")["rev5_code"]),
    "target absent from concordance": lambda rows: rows[0].update(psic2019_codes="999"),
    "blank targets without no_counterpart": lambda rows: rows[0].update(psic2019_codes=""),
    "no_counterpart with targets": lambda rows: rows[0].update(relation="no_counterpart"),
}


@pytest.mark.parametrize("defect", sorted(BRIDGE_DEFECTS))
def test_loader_rejects_bridge_defects(tmp_path: Path, defect: str):
    concordance = _write_rows(tmp_path / "c.csv", _packaged_rows(CONCORDANCE_FILE))
    rows = _packaged_rows(BRIDGE_FILE)
    BRIDGE_DEFECTS[defect](rows)
    with pytest.raises(IOCrosswalkError):
        IOCrosswalk.load(concordance, _write_rows(tmp_path / "b.csv", rows))


# --- pipeline integration -------------------------------------------------------------


def _store_rows() -> list[dict]:
    return [
        {
            "canonical_id": "fsq:1",
            "canonical_name": "Corner Store",
            "lon": 121.0,
            "lat": 14.6,
            "fsq_name": "Corner Store",
            "fsq_category": "Convenience Store",
            "osm_name": "Corner Store",
            "osm_category": "shop=convenience",
            "overture_has_foursquare_provenance": False,
        }
    ]


def _store_crosswalk(path: Path, code: str) -> Crosswalk:
    pd.DataFrame(
        [
            {
                "source": source,
                "source_value": value,
                "scheme": "psic",
                "version": "rev5",
                "mapping_kind": "SUBTREE",
                "codes": code,
                "match_type": "exact",
            }
            for source, value in (("fsq", "Convenience Store"), ("osm", "shop=convenience"))
        ]
    ).to_csv(path, index=False)
    return Crosswalk.load([path])


def _run(tmp_path: Path, taxonomy: Taxonomy, code: str) -> tuple[pd.DataFrame, dict]:
    input_path = tmp_path / "canonical_pois.parquet"
    pd.DataFrame(_store_rows()).to_parquet(input_path, index=False)
    classifier = EntityClassifier(taxonomy, _store_crosswalk(tmp_path / "crosswalk.csv", code))
    _, summary_path = classify_openplaces(input_path, {"psic": classifier}, tmp_path / "out")
    manifest = json.loads((tmp_path / "out" / "run.json").read_text(encoding="utf-8"))
    return pd.read_parquet(summary_path), manifest


def test_pipeline_writes_io_columns_and_manifest_for_real_rev5_path(tmp_path: Path, rev5):
    # A small tree cut from the real Rev 5 tree keeps the run fast but keeps real titles.
    taxonomy = Taxonomy([rev5.get(code) for code in rev5.path_from_root("47119")])
    summary, manifest = _run(tmp_path, taxonomy, "47119")

    assert summary.loc[0, "psic_code"] == "47119"
    assert summary.loc[0, "io_mapping_status"] == "PROVISIONAL_GROUP"
    assert summary.loc[0, "io_mapping_bridge_relation"] == "changed_scope"
    assert summary.loc[0, "io_mapping_bridge_basis"] == "draft"
    assert summary.loc[0, "io240_codes"] == "178"
    assert manifest["io_mapping"]["status_counts"] == {"PROVISIONAL_GROUP": 1}
    assert len(manifest["io_mapping"]["concordance"]["sha256"]) == 64
    assert len(manifest["io_mapping"]["bridge"]["sha256"]) == 64


def test_pipeline_does_not_map_a_tree_with_other_titles(tmp_path: Path, toy_psic):
    summary, manifest = _run(tmp_path, toy_psic, "20110")

    assert summary.loc[0, "io_mapping_status"] == "UNMAPPED_NO_BRIDGE"
    assert manifest["io_mapping"]["status_counts"] == {"UNMAPPED_NO_BRIDGE": 1}
