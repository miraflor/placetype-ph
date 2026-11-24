"""Regression tests for classification, institutional evidence and I-O resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from placetype_ph.classifier import EntityClassifier
from placetype_ph.crosswalk import Crosswalk
from placetype_ph.io_mapping import (
    CONCORDANCE_FILE,
    IOCrosswalk,
    IOCrosswalkError,
    _packaged,
)
from placetype_ph.io_resolution import (
    _nest,
    direct_matches,
    explicit_codes,
    load_direct_rules,
    resolve_io,
)
from placetype_ph.models import CrosswalkEntry, MappingKind, SourceField
from placetype_ph.pipeline import classify_openplaces
from placetype_ph.reviewed_psic import bundled_psic_crosswalk_path
from placetype_ph.specialized.institutional_shadow import (
    InstitutionResolver,
    load_verified_links,
)
from placetype_ph.specialized.institutions import administrative, ownership
from placetype_ph.suggest import category_plan
from placetype_ph.taxonomy import Taxonomy

ROOT = Path(__file__).resolve().parents[1]
PSIC_TREE = ROOT / "reference" / "psic_rev5" / "nodes.parquet"

pytestmark = pytest.mark.skipif(not PSIC_TREE.is_file(), reason="PSIC Rev. 5 tree not present")


@pytest.fixture(scope="module")
def taxonomy() -> Taxonomy:
    return Taxonomy.load(PSIC_TREE)


@pytest.fixture(scope="module")
def classifier(taxonomy: Taxonomy) -> EntityClassifier:
    return EntityClassifier(taxonomy, Crosswalk.load([bundled_psic_crosswalk_path()]))


@pytest.fixture(scope="module")
def io_crosswalk() -> IOCrosswalk:
    return IOCrosswalk.load_default()


def _row(**fields: object) -> dict[str, object]:
    return {"canonical_id": "x", **fields}


def _sets(mapped: dict[str, str], resolution: str) -> set[str]:
    return {c.strip() for c in mapped[f"{resolution}_codes"].split(";") if c.strip()}


# Official PSA 2018 I-O hierarchy ------------------------------------------------


def test_basic_metal_groups_use_official_io80_parent(io_crosswalk: IOCrosswalk) -> None:
    assert set(io_crosswalk.concordance_row("24").io80.codes) == {"41", "42"}
    assert io_crosswalk.concordance_row("242").io80.codes == ("42",)
    assert io_crosswalk.concordance_row("243").io80.codes == ("42",)
    assert {io_crosswalk.hierarchy.io240_parent[c] for c in ("124", "125", "126")} == {"42"}
    assert io_crosswalk.hierarchy.io240_parent["123"] == "41"


def test_concordance_that_contradicts_hierarchy_is_refused(tmp_path: Path) -> None:
    text = _packaged(CONCORDANCE_FILE).text.splitlines()
    old = [
        line.replace(
            ',42,"Fabricated metal products, except machinery and equipment",', ",41,Basic metals,"
        )
        if ",group,242," in line
        else line
        for line in text
    ]
    path = tmp_path / CONCORDANCE_FILE
    path.write_text("\n".join(old) + "\n", encoding="utf-8")
    with pytest.raises(IOCrosswalkError, match="official PSA 2018 hierarchy"):
        IOCrosswalk.load(path)
    # The check can be switched off for a deliberately non-PSA concordance.
    IOCrosswalk.load(path, check_hierarchy=False)


def test_nesting_derives_parents_or_reports_conflict(io_crosswalk: IOCrosswalk) -> None:
    h = io_crosswalk.hierarchy
    candidates = {"io16": ("03",), "io80": ("41", "42"), "io240": ("124", "125")}
    derived = _nest(
        {
            "io16": ("03", "PSIC_MAP", "psic"),
            "io80": ("", "PSIC_AMBIGUOUS", ""),
            "io240": ("125", "PSIC_MAP", "psic"),
        },
        candidates,
        h,
    )
    assert derived["io80"] == ("42", "DERIVED_FROM_IO240", "psic")
    conflict = _nest(
        {
            "io16": ("03", "PSIC_MAP", "psic"),
            "io80": ("41", "DIRECT_FALLBACK", "direct"),
            "io240": ("125", "PSIC_MAP", "psic"),
        },
        candidates,
        h,
    )
    assert conflict["io80"] == ("42", "DERIVED_FROM_IO240", "psic")
    weaker = _nest(
        {
            "io16": ("03", "PSIC_MAP", "psic"),
            "io80": ("41", "PSIC_MAP", "psic"),
            "io240": ("125", "PSIC_MAP", "psic"),
        },
        candidates,
        h,
    )
    assert weaker["io240"][1] == "LEVEL_CONFLICT"


# Results that admit several subtrees --------------------------------------------------


def test_union_maps_members_not_parent(
    classifier: EntityClassifier, taxonomy: Taxonomy, io_crosswalk: IOCrosswalk
) -> None:
    result = classifier.classify_row(_row(overture_name="Seaside", overture_category="lodging"))
    assert result.code == "55" and result.status == "UNION"
    assert result.audit["admissible_roots"] == ["551", "552", "553", "559"]
    union = io_crosswalk.map_union(result.audit["admissible_roots"], taxonomy)
    parent = io_crosswalk.map_psic("55", taxonomy)
    assert _sets(union, "io80") == {"62"}
    assert "224" not in _sets(union, "io240")  # travel agency and reservation services
    assert "224" in _sets(parent, "io240")
    assert union["io_mapping_status"] in {"MAPPED_UNION", "PROVISIONAL_UNION"}


def test_flowers_and_gifts_excludes_motor_vehicle_trade(
    classifier: EntityClassifier, taxonomy: Taxonomy, io_crosswalk: IOCrosswalk
) -> None:
    result = classifier.classify_row(
        _row(overture_name="Rose Gifts", overture_category="flowers_and_gifts_store")
    )
    union = io_crosswalk.map_union(result.audit["admissible_roots"], taxonomy)
    assert _sets(union, "io80") == {"55"}
    assert "56" in _sets(io_crosswalk.map_psic("47", taxonomy), "io80")


# Administrative offices ---------------------------------------------------------


ADMIN_CASES = [
    ("Quezon City Health Department", "PROPOSED"),
    ("DOH-Regional Office 4b Mimaropa", "PROPOSED"),
    ("Department of Education NCR Regional Office", "PROPOSED"),
    ("DepEd Regional Office VII", "PROPOSED"),
    ("DepEd - Regional Office IV-A", "PROPOSED"),
    ("Department of Health, Regional Office VI", "PROPOSED"),
    ("Quezon Province Health Department", "PROPOSED"),
    ("Bacoor Municipality Health Department", "PROPOSED"),
    ("Quezon City  Health Department", "PROPOSED"),
    ("Schools Division Office of Quezon City", "PROPOSED"),
    ("BiteMed Animal Bite Center - DOH Accredited", ""),
    ("DepEd Nabangka Elementary School", ""),
    ("DepEd Teachers Union", ""),
    ("Landbank DepEd NCR ATM", ""),
    ("DOH Eye Center", ""),
    ("Poveda-Higher Education Department", ""),
    ("Ateneo School of Government", ""),
    ("UP Diliman Department of Computer Science", ""),
    ("Health Education and Promotion Unit - DOH CHD Calabarzon", "REVIEW"),
    ("DepEd Schools Division Office - Health Clinics", "REVIEW"),
]


@pytest.mark.parametrize(("name", "expected"), ADMIN_CASES)
def test_administrative_rule_matches_expected(name: str, expected: str) -> None:
    assert administrative({"name": name}).status == expected


def test_regional_office_reassigned_in_classifier(classifier: EntityClassifier) -> None:
    result = classifier.classify_row(
        _row(
            overture_name="DOH-Regional Office 4b Mimaropa",
            overture_category="hospital",
            name="DOH-Regional Office 4b Mimaropa",
        )
    )
    assert result.code == "84120"
    assert "ADMINISTRATIVE_ROLE_ACCEPTED" in result.flags


# Category against packaged keyword name rules ----------------------------------


@pytest.mark.parametrize(
    ("name", "category", "code"),
    [
        ("University of Santo Tomas Hospital", "amenity=hospital", "861"),
        ("St. Luke's College Hospital Annex", "amenity=hospital", "861"),
        ("Shell Car Wash", "amenity=fuel", "473"),
        ("UP College of Law Library", "amenity=library", "9111"),
    ],
)
def test_reviewed_category_outranks_disjoint_keyword_rule(
    classifier: EntityClassifier, name: str, category: str, code: str
) -> None:
    result = classifier.classify_row(_row(osm_name=name, osm_category=category))
    assert result.code == code
    assert "CROSSWALK_CATEGORY_RULE_OVERRULED_NAME" in result.flags


def test_trusted_floor_outranks_disjoint_keyword_rule(classifier: EntityClassifier) -> None:
    result = classifier.classify_row(
        _row(overture_name="Seoul Hotel", overture_category="korean_restaurant")
    )
    assert result.code == "561"
    assert "NAME_RULE_OVERRULED_BY_CATEGORY" in result.flags


def test_user_name_rule_keeps_its_precedence(taxonomy: Taxonomy) -> None:
    user_rule = CrosswalkEntry(
        "osm",
        r"\bspecific chain\b",
        "psic",
        "rev5",
        MappingKind.SUBTREE,
        ("551",),
        match_type="regex",
        source_field=SourceField.NAME,
    )
    bundled = Crosswalk.load([bundled_psic_crosswalk_path()]).entries
    classifier = EntityClassifier(taxonomy, Crosswalk([*bundled, user_rule]))
    result = classifier.classify_row(
        _row(osm_name="Specific Chain Branch", osm_category="amenity=restaurant")
    )
    assert "CROSSWALK_NAME_RULE_OVERRODE_CATEGORY" in result.flags
    assert "CROSSWALK_CATEGORY_RULE_OVERRULED_NAME" not in result.flags


# Public/private hints ----------------------------------------------------------------


OWNERSHIP_CASES = [
    ("De La Salle University", "85", "private"),
    ("Ateneo de Manila University", "85", "private"),
    ("Don Bosco Technical College", "85", "private"),
    ("STI College Cubao", "85", "private"),
    ("AMA Computer College", "85", "private"),
    ("Colegio de San Juan de Letran", "85", "private"),
    ("Polytechnic University of the Philippines", "85", "public"),
    ("Pamantasan ng Lungsod ng Maynila", "85", "public"),
    ("TESDA Training Center", "85", "public"),
    ("Philippine Normal University", "85", "public"),
    ("Bagong Silang Integrated School", "85", "public"),
    ("Holy Spirit Elementary School", "85", ""),
    ("Quezon City National High School", "85", "public"),
    ("Mother and Child Lying-in Clinic", "86", "private"),
    ("Asian Eye Center", "86", "private"),
    ("Bacoor Medicare Community Hospital", "86", "public"),
    ("Pasig City Health Center", "86", "public"),
    ("Super Health Center Taguig", "86", "public"),
    ("BiteMed Animal Bite Center - DOH Accredited", "86", ""),
    ("Private Rural Health Unit", "86", ""),
]


@pytest.mark.parametrize(("name", "code", "side"), OWNERSHIP_CASES)
def test_ownership_hints_include_extended_patterns(name: str, code: str, side: str) -> None:
    decision = ownership({"name": name}, (code,))
    assert decision.side == side
    if side:
        assert decision.status == "PROVISIONAL"


def test_private_practice_category_is_a_hint() -> None:
    decision = ownership({"name": "Smile Studio", "overture_category": "dental_clinic"}, ("862",))
    assert decision.side == "private"


# Direct tag evidence --------------------------------------------------------------


def test_direct_rules_primary_tags_first(io_crosswalk: IOCrosswalk) -> None:
    rules = load_direct_rules(io_crosswalk.hierarchy)
    assert direct_matches("tourism=hotel | cuisine=filipino", rules)[0][0] == "62"
    assert direct_matches("cuisine=pizza", rules)[0][0] == "63"
    assert direct_matches("amenity=bank", rules)[0][0] == "66"
    assert {c for c, _ in direct_matches("amenity=bank | shop=car", rules)} == {"66", "56"}
    assert direct_matches("shop=bakery", rules) == []


# Draft subclass refinement --------------------------------------------------------


def test_subgroup_refinement_is_separate_and_draft(
    taxonomy: Taxonomy, io_crosswalk: IOCrosswalk
) -> None:
    base = io_crosswalk.map_psic("86223", taxonomy)  # private dialysis center activities
    assert _sets(base, "io240") == {"231", "232"}
    extra = io_crosswalk.subgroup("86223", taxonomy, base)
    assert extra["io240_subgroup_codes"] == "232"
    assert extra["io_subgroup_refinement_basis"] == "draft"
    assert _sets(base, "io240") == {"231", "232"}


# Institution gate --------------------------------------------------------


def test_government_operator_contradicts_private_claim() -> None:
    result = InstitutionResolver().assess(
        {
            "name": "Example Hospital",
            "psic_code": "861",
            "osm_category": "amenity=hospital|ownership=private|operator=Department of Health",
        }
    )
    assert result.status == "REVIEW"
    assert result.reason == "government_operator_vs_private_conflict"


def test_floor_subunit_and_chapel_are_gated() -> None:
    floor = InstitutionResolver().assess(
        {"name": "St. Luke's 3rd Floor Pediatrics", "psic_code": "861"}
    )
    assert floor.status == "REVIEW"
    chapel = InstitutionResolver().assess(
        {"name": "Hospital Chapel", "psic_code": "861", "overture_category": "chapel"}
    )
    assert chapel.status in {"ABSTAIN", "REVIEW"}


# Explicit codes -------------------------------------------------------------------


def test_explicit_psic_is_manual_and_recorded(classifier: EntityClassifier) -> None:
    result = classifier.classify_row(
        _row(overture_name="Seaside", overture_category="lodging", explicit_psic_code="55101")
    )
    assert result.code == "55101" and result.status == "MANUAL"
    assert "USER_SUPPLIED_PSIC" in result.flags
    assert "EXPLICIT_PSIC_AGREES_WITH_SOURCES" in result.flags


def test_unknown_explicit_psic_stops(classifier: EntityClassifier) -> None:
    with pytest.raises(ValueError, match="explicit_psic_code"):
        classifier.classify_row(_row(osm_category="amenity=bank", explicit_psic_code="99999"))


def test_explicit_io_parents_and_contradictions(io_crosswalk: IOCrosswalk) -> None:
    good = pd.DataFrame([{"canonical_id": "a", "explicit_io240_code": "125"}])
    assert explicit_codes(good, io_crosswalk.hierarchy)["a"] == {
        "io16": "03",
        "io80": "42",
        "io240": "125",
    }
    bad = pd.DataFrame(
        [
            {"canonical_id": "b", "explicit_io240_code": "125", "explicit_io80_code": "41"},
            {"canonical_id": "c", "explicit_io80_code": "99"},
        ]
    )
    with pytest.raises(ValueError, match="2 row"):
        explicit_codes(bad, io_crosswalk.hierarchy)


# Verified-link files: only the documented schema is accepted ------------------------------


def test_verified_links_other_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "links.json"
    path.write_text(
        json.dumps({"schema": "another-links-schema-v1", "links": []}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="unsupported verified institution link schema"):
        load_verified_links(path)


# Source rules ---------------------------------------------------------------


def test_floor_tables_use_curated_depth() -> None:
    assert category_plan("overture", "bank").branch_roots == ("641",)
    assert category_plan("osm", "shop=beauty").branch_roots == ("962",)
    assert category_plan("osm", "shop=bakery").branch_roots == ("47214",)


def test_reviewed_compound_osm_string_is_reachable(classifier: EntityClassifier) -> None:
    result = classifier.classify_row(
        _row(
            osm_name="Cubao Terminal", osm_category="amenity=bus_station | public_transport=station"
        )
    )
    assert result.status == "UNCODEABLE"


# Pipeline: verified ownership with agreeing rules; inferred columns stay separate -----


def test_verified_ownership_refines_intersect_and_nested(
    tmp_path: Path, taxonomy: Taxonomy, monkeypatch: pytest.MonkeyPatch
) -> None:
    links = tmp_path / "links.json"
    links.write_text(
        json.dumps(
            {
                "schema": "placetype-ph-verified-institution-links-v1",
                "links": [
                    {
                        "canonical_id": cid,
                        "verified": True,
                        "domain": "education",
                        "ownership": "public",
                        "source": "deped_beis",
                        "source_id": sid,
                        "source_url": "https://www.deped.gov.ph/",
                        "verified_as_of": "2026-11-01",
                    }
                    for cid, sid in (("two", "305001"), ("one", "305002"))
                ],
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema": "placetype-ph-institution-reference-config-v1",
                "references": {"verified_links": str(links)},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PLACETYPE_PH_INSTITUTION_REFERENCES", str(config))
    places = pd.DataFrame(
        [
            {
                "canonical_id": "two",
                "osm_name": "Pasig Elementary School",
                "osm_category": "amenity=school",
                "overture_name": "Pasig Elementary School",
                "overture_category": "elementary_school",
                "name": "Pasig Elementary School",
                "canonical_name": "Pasig Elementary School",
            },
            {
                "canonical_id": "one",
                "osm_name": "Rizal Elementary School",
                "osm_category": "amenity=school",
                "name": "Rizal Elementary School",
                "canonical_name": "Rizal Elementary School",
            },
            {
                "canonical_id": "hint",
                "osm_name": "Ateneo de Manila University",
                "osm_category": "amenity=university",
                "name": "Ateneo de Manila University",
                "canonical_name": "Ateneo de Manila University",
            },
        ]
    )
    source = tmp_path / "in.parquet"
    places.to_parquet(source, index=False)
    classifier = EntityClassifier(taxonomy, Crosswalk.load([bundled_psic_crosswalk_path()]))
    _, summary_path = classify_openplaces(source, {"psic": classifier}, tmp_path / "out")
    summary = pd.read_parquet(summary_path).set_index("canonical_id")
    for cid in ("one", "two"):
        assert summary.loc[cid, "psic_status"] in {"INTERSECT", "NESTED"}
        assert summary.loc[cid, "io_ownership_refinement_status"] == "REFINED"
        assert summary.loc[cid, "io80_resolved_code"] == "74"
        assert summary.loc[cid, "io80_resolved_status"] == "VERIFIED_OWNERSHIP"
    # A name hint is reported as an inference and never enters the resolved columns.
    assert summary.loc["hint", "io_ownership_inferred_status"] == "INFERRED"
    assert summary.loc["hint", "io80_ownership_inferred_code"] == "75"
    assert summary.loc["hint", "io80_resolved_code"] == ""
    assert summary.loc["hint", "io80_codes"] == "74; 75"


def test_resolution_never_overrides_conflicting_psic(
    taxonomy: Taxonomy, io_crosswalk: IOCrosswalk
) -> None:
    summary = pd.DataFrame(
        [
            {
                "canonical_id": "a",
                "psic_status": "SINGLE",
                "io16_codes": "06",
                "io16_map_code": "06",
                "io16_map_status": "SINGLETON_CANDIDATE",
                "io80_codes": "55; 56",
                "io80_map_code": "",
                "io80_map_status": "AMBIGUOUS_SET",
                "io240_codes": "178; 179",
                "io240_map_code": "",
                "io240_map_status": "AMBIGUOUS_SET",
            },
            {
                "canonical_id": "b",
                "psic_status": "NOT_PSIC_ACTIVITY",
                "io16_codes": "",
                "io16_map_code": "",
                "io16_map_status": "UNMAPPED",
                "io80_codes": "",
                "io80_map_code": "",
                "io80_map_status": "UNMAPPED",
                "io240_codes": "",
                "io240_map_code": "",
                "io240_map_status": "UNMAPPED",
            },
        ]
    )
    places = pd.DataFrame(
        [
            {"canonical_id": "a", "osm_category": "shop=car"},
            {"canonical_id": "b", "osm_category": "amenity=bank"},
        ]
    )
    out = resolve_io(summary, places, io_crosswalk, policy="extended").set_index("canonical_id")
    strict = resolve_io(summary, places, io_crosswalk).set_index("canonical_id")
    assert strict.loc["a", "io80_resolved_code"] == ""
    assert strict.loc["a", "direct_io80_code"] == "56"
    with pytest.raises(ValueError, match="unknown I-O resolution policy"):
        resolve_io(summary, places, io_crosswalk, policy="invalid")
    assert out.loc["a", "io80_resolved_code"] == "56"
    assert out.loc["a", "io80_resolved_status"] == "HYBRID_RESOLVED"
    assert out.loc["b", "io80_resolved_code"] == ""
    assert out.loc["b", "io80_resolved_status"] == "DIRECT_BLOCKED_BY_PSIC_STATUS"


def test_secondary_osm_attributes_do_not_create_activity_conflict(classifier):
    for category in (
        "amenity=school | ownership=public",
        "ownership=public | amenity=school | emergency=yes",
    ):
        result = classifier.classify_row(_row(osm_category=category))
        assert result.code == classifier.classify_row(_row(osm_category="amenity=school")).code
        assert result.status != "CONFLICT"
    hospital = classifier.classify_row(_row(osm_category="amenity=hospital"))
    compound = classifier.classify_row(
        _row(osm_category="amenity=hospital | healthcare=hospital | emergency=yes")
    )
    assert compound.code == hospital.code
    assert compound.status != "CONFLICT"


def test_nullable_explicit_psic(classifier):
    result = classifier.classify_row(_row(osm_category="amenity=school", explicit_psic_code=pd.NA))
    assert result.status != "MANUAL"


def test_explicit_psic_excluded_union_branch(classifier, taxonomy):
    excluded = next(
        code for code in taxonomy.nodes
        if len(code) == 3 and code.startswith("55")
        and code not in {"551", "552", "553", "559"}
    )
    result = classifier.classify_row(
        _row(overture_category="lodging", explicit_psic_code=excluded)
    )
    assert "EXPLICIT_PSIC_DIFFERS_FROM_SOURCES" in result.flags


def test_missing_union_roots_rejected(taxonomy):
    from placetype_ph.io_mapping import _admissible_roots

    with pytest.raises(ValueError, match="UNION"):
        _admissible_roots({"status": "UNION", "admissible_roots": "[]"}, taxonomy, "55")
    with pytest.raises(ValueError):
        _admissible_roots({"status": "UNION", "admissible_roots": "{bad"}, taxonomy, "55")
    assert _admissible_roots(
        {"status": "UNION", "admissible_roots": '["552","551","551"]'}, taxonomy, "55"
    ) == ("551", "552")


def test_duplicate_explicit_ids_rejected(io_crosswalk):
    with pytest.raises(ValueError, match="duplicate"):
        explicit_codes(pd.DataFrame({"canonical_id": ["x", "x"]}), io_crosswalk.hierarchy)


def test_verified_parent_authority_propagates(io_crosswalk):
    hierarchy = io_crosswalk.hierarchy
    parent = hierarchy.io240_parent["229"]
    coarse = hierarchy.io80_parent[parent]
    wrong = next(code for code in hierarchy.io80_parent.values() if code != coarse)
    resolved = _nest(
        {"io16": (wrong, "PSIC_MAP", "psic"), "io80": (parent, "PSIC_MAP", "psic"),
         "io240": ("229", "VERIFIED_OWNERSHIP", "psic+institution")},
        {"io16": (), "io80": (), "io240": ()}, hierarchy,
    )
    assert resolved["io16"][0] == coarse
    assert resolved["io240"][0] == "229"


def test_provisional_hint_does_not_veto_verified_ownership():
    from types import SimpleNamespace

    from placetype_ph.specialized.institutional_promotion import resolve_institutional_ownership

    classified = pd.DataFrame([{
        "canonical_id": "x", "psic_code": "85", "psic_status": "SINGLE",
        "ownership_candidate": "private", "ownership_status": "PROVISIONAL",
    }])
    places = pd.DataFrame([{"canonical_id": "x"}])
    assessment = SimpleNamespace(
        domain="education", status="VERIFIED_LINK", promotion_eligible=True,
        ownership_candidate="public", basis="verified_link", source_references=("beis:123456",),
        reason="verified_link",
    )
    resolver = SimpleNamespace(assess=lambda row: assessment)
    result = resolve_institutional_ownership(classified, places, resolver=resolver)
    assert result.loc[0, "institution_resolution_status"] == "RESOLVED"
    assert result.loc[0, "institution_resolved_ownership"] == "public"
    assert result.loc[0, "ownership_candidate"] == "private"
    reason = result.loc[0, "institution_resolution_reason"]
    assert reason == "verified_ownership_overrides_provisional_hint"


# Draft bridge rows: per-level dependence and labelling -------------------------------------


def _mapped_summary(io_crosswalk, taxonomy, codes, with_draft_levels=True):
    rows = []
    for cid, code in codes.items():
        base = io_crosswalk.map_psic(code, taxonomy)
        row = {"canonical_id": cid, "psic_code": code, "psic_status": "SINGLE", **base}
        if with_draft_levels:
            row["io_mapping_draft_levels"] = io_crosswalk.draft_levels((code,), taxonomy, base)
        rows.append(row)
    summary = pd.DataFrame(rows)
    places = pd.DataFrame([{"canonical_id": cid, "osm_category": ""} for cid in codes])
    return summary, places


def test_draft_levels_are_reported_per_resolution(io_crosswalk, taxonomy):
    bus = io_crosswalk.map_psic("492", taxonomy)  # passenger land transport by buses
    assert bus["io_mapping_status"].startswith("PROVISIONAL")
    assert io_crosswalk.draft_levels(("492",), taxonomy, bus) == "io240"
    bank = io_crosswalk.map_psic("641", taxonomy)  # monetary intermediation
    assert io_crosswalk.draft_levels(("641",), taxonomy, bank) == ""
    shop = io_crosswalk.map_psic("4711", taxonomy)  # retail rows above 4711 are all draft
    assert io_crosswalk.draft_levels(("4711",), taxonomy, shop) == "io16; io80; io240"


def test_strict_keeps_levels_a_draft_row_does_not_change(io_crosswalk, taxonomy):
    summary, places = _mapped_summary(io_crosswalk, taxonomy, {"bus": "492"})
    out = resolve_io(summary, places, io_crosswalk).set_index("canonical_id")
    assert out.loc["bus", "io16_resolved_code"] == summary.loc[0, "io16_map_code"]
    assert out.loc["bus", "io80_resolved_code"] == summary.loc[0, "io80_map_code"]
    assert out.loc["bus", "io80_resolved_status"] == "PSIC_MAP"
    assert out.loc["bus", "io240_resolved_code"] == ""


def test_withheld_single_candidate_is_not_called_ambiguous(io_crosswalk, taxonomy):
    summary, places = _mapped_summary(io_crosswalk, taxonomy, {"shop": "4711"})
    assert summary.loc[0, "io80_map_code"] == "55"
    strict = resolve_io(summary, places, io_crosswalk).set_index("canonical_id")
    assert strict.loc["shop", "io80_resolved_code"] == ""
    assert strict.loc["shop", "io80_resolved_status"] == "PROVISIONAL_BRIDGE_NOT_RESOLVED"
    extended = resolve_io(summary, places, io_crosswalk, policy="extended").set_index(
        "canonical_id"
    )
    assert extended.loc["shop", "io80_resolved_code"] == "55"
    assert extended.loc["shop", "io80_resolved_status"] == "PROVISIONAL_PSIC_MAP"


def test_summary_without_draft_column_treats_row_as_draft(io_crosswalk, taxonomy):
    summary, places = _mapped_summary(
        io_crosswalk, taxonomy, {"bus": "492"}, with_draft_levels=False
    )
    out = resolve_io(summary, places, io_crosswalk).set_index("canonical_id")
    assert out.loc["bus", "io80_resolved_code"] == ""
    assert out.loc["bus", "io80_resolved_status"] == "PROVISIONAL_BRIDGE_NOT_RESOLVED"


@pytest.mark.parametrize("policy", ["strict", "evidence-weighted", "extended"])
def test_invalid_draft_level_rejected_before_resolution(io_crosswalk, taxonomy, policy):
    summary, places = _mapped_summary(io_crosswalk, taxonomy, {"bus": "492"})
    summary.loc[0, "io_mapping_draft_levels"] = "io420"
    with pytest.raises(ValueError, match="io_mapping_draft_levels"):
        resolve_io(summary, places, io_crosswalk, policy=policy)


def test_blank_provisional_metadata_does_not_accept_draft(io_crosswalk, taxonomy):
    summary, places = _mapped_summary(io_crosswalk, taxonomy, {"shop": "4711"})
    summary.loc[0, "io_mapping_draft_levels"] = ""
    out = resolve_io(summary, places, io_crosswalk)
    assert out.loc[0, "io80_resolved_code"] == ""
    assert out.loc[0, "io80_resolved_status"] == "PROVISIONAL_BRIDGE_NOT_RESOLVED"


@pytest.mark.parametrize("policy, expected", [("strict", ""), ("extended", "55")])
def test_classify_cli_policy_reaches_pipeline(tmp_path, taxonomy, monkeypatch, policy, expected):
    from typer.testing import CliRunner

    from placetype_ph import cli

    monkeypatch.setattr(cli, "_load_taxonomy", lambda *args: taxonomy)
    source = tmp_path / "input.parquet"
    pd.DataFrame([{
        "canonical_id": "shop", "osm_category": "shop=convenience",
        "canonical_name": "Example shop", "explicit_psic_code": "4711",
    }]).to_parquet(source, index=False)
    output = tmp_path / "out"
    result = CliRunner().invoke(cli.app, [
        "classify", str(source), "--schemes", "psic", "--llm", "none",
        "--output-dir", str(output), "--io-resolution-policy", policy,
    ])
    assert result.exit_code == 0, (result.output, result.exception)
    summary = pd.read_parquet(output / "classified_pois.parquet")
    assert summary.loc[0, "io80_resolved_code"] == expected
    manifest = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert manifest["io_mapping"]["resolution"]["selected_policy"] == policy
