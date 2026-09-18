from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from placetype_ph.classifier import EntityClassifier
from placetype_ph.crosswalk import Crosswalk
from placetype_ph.economic_role import (
    RoleCrosswalk,
    RoleCrosswalkError,
    RoleRule,
    TaxonomyClassification,
    UnitType,
    infer_economic_profile,
)
from placetype_ph.pipeline import classify_openplaces


def _write_joint(path: Path) -> Path:
    rows = []
    for scheme, version in (("psic", "rev5"), ("pcpc", "2002"), ("pscc", "2022")):
        rows.append(
            {
                "source": "fsq",
                "source_value": "Bakery",
                "source_field": "category",
                "scheme": scheme,
                "version": version,
                "mapping_kind": "SUBTREE" if scheme == "psic" else "",
                "codes": "1011" if scheme == "psic" else "",
                "match_type": "exact",
                "unit_type": "ESTABLISHMENT",
                "establishment_status": "YES",
                "io_roles": "PRODUCER|INTERMEDIATE_DEMAND",
                "role_confidence": "1.0",
            }
        )
    rows.append(
        {
            "source": "fsq",
            "source_value": "Apartment or Condo",
            "source_field": "category",
            "scheme": "psic",
            "version": "rev5",
            "mapping_kind": "NOT_ACTIVITY",
            "codes": "",
            "match_type": "exact",
            "unit_type": "RESIDENTIAL",
            "establishment_status": "NO",
            "io_roles": "HOUSEHOLD_FINAL_DEMAND_PROXY",
            "role_confidence": "1.0",
        }
    )
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_role_crosswalk_collapses_equal_scheme_copies(tmp_path: Path):
    roles = RoleCrosswalk.load([_write_joint(tmp_path / "joint.csv")])
    bakery = [rule for rule in roles.rules if rule.source_value == "Bakery"]
    assert len(bakery) == 1
    assert bakery[0].unit_type == "ESTABLISHMENT"


def test_role_crosswalk_rejects_disagreement_between_scheme_copies(tmp_path: Path):
    path = tmp_path / "joint.csv"
    pd.DataFrame(
        [
            {
                "source": "fsq",
                "source_value": "Bakery",
                "source_field": "category",
                "scheme": "psic",
                "version": "rev5",
                "unit_type": "ESTABLISHMENT",
            },
            {
                "source": "fsq",
                "source_value": "Bakery",
                "source_field": "category",
                "scheme": "pcpc",
                "version": "2002",
                "unit_type": "CONTAINER",
            },
        ]
    ).to_csv(path, index=False)
    with pytest.raises(RoleCrosswalkError, match="disagree on unit_type"):
        RoleCrosswalk.load([path])


def test_taxonomy_api_is_joint_but_psic_activity_has_specific_io_semantics():
    profile = infer_economic_profile(
        {"canonical_id": "x", "fsq_category": "Unreviewed activity"},
        None,
        classifications={
            "psic": TaxonomyClassification(code="561", status="SINGLE"),
            "pcpc": TaxonomyClassification(code="63", status="POTENTIAL_PRODUCT_FAMILY_FULL"),
            "pscc": TaxonomyClassification(code="2106", status="SINGLE"),
        },
    )
    assert profile.unit_type == "UNCERTAIN"
    assert profile.establishment_status == "UNCERTAIN"
    assert profile.io_roles == ["PRODUCER", "INTERMEDIATE_DEMAND"]
    assert profile.role_status == "INFERRED_FROM_TAXONOMY"
    assert profile.role_flags == ["IO_ROLE_FROM_PSIC_ACTIVITY"]


def test_pcpc_or_pscc_code_alone_does_not_turn_a_place_into_a_producer():
    profile = infer_economic_profile(
        {"canonical_id": "x", "fsq_category": "Unreviewed place"},
        None,
        classifications={
            "pcpc": TaxonomyClassification(code="63", status="POTENTIAL_PRODUCT_FAMILY_FULL"),
            "pscc": TaxonomyClassification(code="2106", status="SINGLE"),
        },
    )
    assert profile.io_roles == []
    assert profile.role_status == "UNRESOLVED"


def test_union_psic_code_still_adds_activity_io_roles():
    profile = infer_economic_profile(
        {"canonical_id": "x", "fsq_category": "Ambiguous activity"},
        None,
        classifications={"psic": TaxonomyClassification(code="56", status="UNION")},
    )
    assert profile.io_roles == ["PRODUCER", "INTERMEDIATE_DEMAND"]
    assert profile.role_status == "INFERRED_FROM_TAXONOMY"


def test_conflicting_source_role_annotations_are_reviewed(tmp_path: Path):
    path = tmp_path / "roles.csv"
    pd.DataFrame(
        [
            {
                "source": "fsq",
                "source_value": "Restaurant",
                "source_field": "category",
                "unit_type": "ESTABLISHMENT",
                "establishment_status": "YES",
                "io_roles": "PRODUCER",
            },
            {
                "source": "osm",
                "source_value": "building=residential",
                "source_field": "category",
                "unit_type": "RESIDENTIAL",
                "establishment_status": "NO",
                "io_roles": "HOUSEHOLD_FINAL_DEMAND_PROXY",
            },
        ]
    ).to_csv(path, index=False)
    roles = RoleCrosswalk.load([path])
    profile = infer_economic_profile(
        {
            "canonical_id": "x",
            "fsq_category": "Restaurant",
            "osm_category": "building=residential",
        },
        roles,
    )
    assert profile.unit_type == "UNCERTAIN"
    assert profile.establishment_status == "UNCERTAIN"
    assert profile.role_status == "REVIEW"
    assert "ROLE_UNIT_TYPE_CONFLICT" in profile.role_flags


def test_pipeline_keeps_every_poi_and_writes_role_columns(tmp_path: Path, toy_psic):
    input_path = tmp_path / "canonical_pois.parquet"
    pd.DataFrame(
        [
            {
                "canonical_id": "fsq:bakery",
                "canonical_name": "ABC Bakery",
                "lon": 122.0,
                "lat": 11.0,
                "fsq_name": "ABC Bakery",
                "fsq_category": "Bakery",
            },
            {
                "canonical_id": "fsq:home",
                "canonical_name": "ABC Residences",
                "lon": 122.1,
                "lat": 11.1,
                "fsq_name": "ABC Residences",
                "fsq_category": "Apartment or Condo",
            },
        ]
    ).to_parquet(input_path, index=False)

    joint = _write_joint(tmp_path / "joint.csv")
    taxonomy_crosswalk = Crosswalk.load([joint])
    role_crosswalk = RoleCrosswalk.load([joint])

    _, summary_path = classify_openplaces(
        input_path,
        {"psic": EntityClassifier(toy_psic, taxonomy_crosswalk)},
        tmp_path / "out",
        role_crosswalk=role_crosswalk,
    )
    summary = pd.read_parquet(summary_path).set_index("canonical_id")

    assert len(summary) == 2
    assert summary.index.is_unique

    bakery = summary.loc["fsq:bakery"]
    assert bakery["unit_type"] == "ESTABLISHMENT"
    assert bakery["establishment_status"] == "YES"
    assert set(json.loads(bakery["io_roles"])) == {"PRODUCER", "INTERMEDIATE_DEMAND"}

    home = summary.loc["fsq:home"]
    assert home["psic_status"] == "NOT_PSIC_ACTIVITY"
    assert home["unit_type"] == "RESIDENTIAL"
    assert home["establishment_status"] == "NO"
    assert json.loads(home["io_roles"]) == ["HOUSEHOLD_FINAL_DEMAND_PROXY"]


def test_rules_built_in_code_are_stored_with_normalized_fields():
    roles = RoleCrosswalk(
        [
            RoleRule(
                source="FSQ",
                source_value="Bakery",
                source_field=" Category ",
                match_type="Exact",
                unit_type=UnitType.ESTABLISHMENT,
            )
        ]
    )
    stored = roles.rules[0]
    assert stored.source == "fsq"
    assert stored.source_field == "category"
    assert stored.match_type == "exact"

    profile = infer_economic_profile({"canonical_id": "x", "fsq_category": "Bakery"}, roles)
    assert profile.unit_type == "ESTABLISHMENT"
    assert profile.role_status == "REVIEWED"


def test_whitespace_only_match_cells_take_the_defaults(tmp_path: Path):
    path = tmp_path / "roles.csv"
    pd.DataFrame(
        [
            {
                "source": "fsq",
                "source_value": "Bakery",
                "source_field": " ",
                "match_type": " ",
                "unit_type": "ESTABLISHMENT",
            }
        ]
    ).to_csv(path, index=False)

    stored = RoleCrosswalk.load([path]).rules[0]
    assert (stored.source_field, stored.match_type) == ("category", "exact")


def test_role_confidence_is_float_when_no_rule_gives_one(tmp_path: Path, toy_psic):
    input_path = tmp_path / "canonical_pois.parquet"
    pd.DataFrame(
        [
            {
                "canonical_id": "fsq:bakery",
                "canonical_name": "ABC Bakery",
                "lon": 122.0,
                "lat": 11.0,
                "fsq_name": "ABC Bakery",
                "fsq_category": "Bakery",
            },
            {
                "canonical_id": "fsq:home",
                "canonical_name": "ABC Residences",
                "lon": 122.1,
                "lat": 11.1,
                "fsq_name": "ABC Residences",
                "fsq_category": "Apartment or Condo",
            },
        ]
    ).to_parquet(input_path, index=False)
    taxonomy_crosswalk = Crosswalk.load([_write_joint(tmp_path / "joint.csv")])

    _, summary_path = classify_openplaces(
        input_path,
        {"psic": EntityClassifier(toy_psic, taxonomy_crosswalk)},
        tmp_path / "out",
    )

    assert pq.read_schema(summary_path).field("role_confidence").type == pa.float64()
    summary = pd.read_parquet(summary_path).set_index("canonical_id")
    assert summary["role_confidence"].isna().all()
    assert summary.loc["fsq:home", "role_status"] == "UNRESOLVED"
