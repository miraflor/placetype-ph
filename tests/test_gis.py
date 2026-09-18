from __future__ import annotations

import json
import struct
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from placetype_ph import __version__
from placetype_ph.cli import app
from placetype_ph.gis import (
    LEVEL_SUFFIX,
    GISExportError,
    build_gis_export,
    export_gis_run,
)
from placetype_ph.models import TaxonomyNode
from placetype_ph.taxonomy import LEVEL_ORDER, Taxonomy

# Module-level so it can be a default argument without tripping ruff B008.
_WKB_BINARY = pa.binary()

PSIC_COLUMNS = [
    "psic_code",
    "psic_subclass",
    "psic_class",
    "psic_group",
    "psic_division",
    "psic_section",
]
PCPC_COLUMNS = [
    "pcpc_code",
    "pcpc_item",
    "pcpc_subclass",
    "pcpc_class",
    "pcpc_group",
    "pcpc_division",
    "pcpc_section",
]
PSCC_COLUMNS = [
    "pscc_code",
    "pscc_commodity",
    "pscc_ahtn_subheading",
    "pscc_hs_subheading",
    "pscc_heading",
    "pscc_chapter",
]


def _point_wkb(x: float, y: float) -> bytes:
    return struct.pack("<BI2d", 1, 1, x, y)


def _geo_json(**overrides: object) -> bytes:
    geometry: dict[str, object] = {"encoding": "WKB", "geometry_types": ["Point"]}
    geometry.update(overrides)
    return json.dumps(
        {
            "version": "1.0.0",
            "primary_column": "geometry",
            "columns": {"geometry": geometry},
        },
        separators=(",", ":"),
    ).encode()


def _write_geo_source(
    path: Path,
    frame: pd.DataFrame,
    *,
    geo: bytes | None,
    geometry_type: pa.DataType = _WKB_BINARY,
) -> None:
    table = pa.Table.from_pandas(frame, preserve_index=False)
    index = table.schema.get_field_index("geometry")
    table = table.set_column(
        index,
        pa.field("geometry", geometry_type),
        table.column("geometry").cast(geometry_type),
    )
    if geo is not None:
        metadata = dict(table.schema.metadata or {})
        metadata[b"geo"] = geo
        table = table.replace_schema_metadata(metadata)
    pq.write_table(table, path)


def _taxonomies() -> dict[str, Taxonomy]:
    psic = Taxonomy(
        [
            TaxonomyNode("psic", "rev5", "A", "section", "A"),
            TaxonomyNode("psic", "rev5", "10", "division", "10", "A"),
            TaxonomyNode("psic", "rev5", "101", "group", "101", "10"),
            TaxonomyNode("psic", "rev5", "1011", "class", "1011", "101"),
            TaxonomyNode("psic", "rev5", "10111", "subclass", "10111", "1011"),
        ]
    )
    pcpc = Taxonomy(
        [
            TaxonomyNode("pcpc", "2002", "1", "section", "1"),
            TaxonomyNode("pcpc", "2002", "12", "division", "12", "1"),
            TaxonomyNode("pcpc", "2002", "123", "group", "123", "12"),
            TaxonomyNode("pcpc", "2002", "1234", "class", "1234", "123"),
            TaxonomyNode("pcpc", "2002", "12345", "subclass", "12345", "1234"),
            TaxonomyNode("pcpc", "2002", "123456", "item", "123456", "12345"),
        ]
    )
    pscc = Taxonomy(
        [
            TaxonomyNode("pscc", "2022", "12", "chapter", "12"),
            TaxonomyNode("pscc", "2022", "1234", "heading", "1234", "12"),
            TaxonomyNode("pscc", "2022", "123456", "hs_subheading", "123456", "1234"),
            TaxonomyNode("pscc", "2022", "12345678", "ahtn_subheading", "12345678", "123456"),
            TaxonomyNode("pscc", "2022", "12345678901", "commodity", "12345678901", "12345678"),
        ]
    )
    return {"psic": psic, "pcpc": pcpc, "pscc": pscc}


def _rows(schemes: tuple[str, ...]) -> list[dict]:
    assigned = {
        "psic": ("10111", "1011", None),
        "pcpc": ("123456", "1234", None),
        "pscc": ("12345678901", "1234", None),
    }
    levels = {
        "psic": ("subclass", "class", None),
        "pcpc": ("item", "class", None),
        "pscc": ("commodity", "heading", None),
    }
    statuses = ("ASSIGNED", "ASSIGNED", "EMPTY")
    rows = []
    for position in range(3):
        row = {
            "canonical_id": f"poi:{position + 1}",
            "canonical_name": "drop me",
            "geometry": _point_wkb(121.0 + position / 10, 14.0 + position / 10),
            "lon": 121.0 + position / 10,
            "lat": 14.0 + position / 10,
        }
        for scheme in schemes:
            row[f"{scheme}_code"] = assigned[scheme][position]
            row[f"{scheme}_level"] = levels[scheme][position]
            row[f"{scheme}_status"] = statuses[position]
        rows.append(row)
    return rows


def _write_run(
    tmp_path: Path,
    *,
    schemes: tuple[str, ...] = ("psic", "pcpc", "pscc"),
    geo: bytes | None = None,
    geometry_type: pa.DataType = _WKB_BINARY,
    summary_name: str = "classified_pois.parquet",
    fingerprints: bool = True,
    extra_outputs: dict | None = None,
    package_version: str | None = __version__,
) -> tuple[Path, bytes | None]:
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    geo = _geo_json() if geo is None else geo
    rows = _rows(schemes)
    _write_geo_source(
        run_dir / summary_name,
        pd.DataFrame(rows),
        geo=geo,
        geometry_type=geometry_type,
    )

    taxonomies = _taxonomies()
    reference = tmp_path / "reference"
    manifest_schemes = {}
    for scheme in schemes:
        taxonomy = taxonomies[scheme]
        taxonomy.save(reference / f"{scheme}_{taxonomy.version}" / "nodes.parquet")
        details: dict[str, str] = {"version": taxonomy.version}
        if fingerprints:
            details["taxonomy_fingerprint"] = taxonomy.fingerprint
        manifest_schemes[scheme] = details
    outputs = {"classified_pois": summary_name}
    outputs.update(extra_outputs or {})
    manifest: dict[str, object] = {"schemes": manifest_schemes, "outputs": outputs}
    if package_version is not None:
        manifest["package_version"] = package_version
    (run_dir / "run.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run_dir, geo


def test_level_suffixes_cover_every_taxonomy_level():
    # The export names columns; `taxonomy.LEVEL_ORDER` owns the levels themselves.
    for scheme, levels in LEVEL_ORDER.items():
        assert set(LEVEL_SUFFIX[scheme]) == set(levels), scheme


def test_export_gis_run_is_compact_hierarchical_geoparquet(tmp_path: Path):
    run_dir, geo = _write_run(tmp_path)
    output = export_gis_run(run_dir, reference_dir=tmp_path / "reference")

    result = pd.read_parquet(output)
    assert list(result.columns) == [
        "canonical_id",
        "geometry",
        *PSIC_COLUMNS,
        *PCPC_COLUMNS,
        *PSCC_COLUMNS,
    ]
    assert result.loc[0, PSIC_COLUMNS].tolist() == ["10111", "10111", "1011", "101", "10", "A"]
    assert result.loc[0, PCPC_COLUMNS].tolist() == [
        "123456",
        "123456",
        "12345",
        "1234",
        "123",
        "12",
        "1",
    ]
    assert result.loc[0, PSCC_COLUMNS].tolist() == [
        "12345678901",
        "12345678901",
        "12345678",
        "123456",
        "1234",
        "12",
    ]

    # A coarse assignment populates its own level and its ancestors; deeper levels stay null.
    assert pd.isna(result.loc[1, "psic_subclass"])
    coarse = ["psic_class", "psic_group", "psic_division", "psic_section"]
    assert result.loc[1, coarse].tolist() == ["1011", "101", "10", "A"]
    assert pd.isna(result.loc[1, "pcpc_item"])
    assert pd.isna(result.loc[1, "pcpc_subclass"])
    assert result.loc[1, "pcpc_class"] == "1234"
    assert pd.isna(result.loc[1, "pscc_commodity"])
    assert pd.isna(result.loc[1, "pscc_ahtn_subheading"])
    assert pd.isna(result.loc[1, "pscc_hs_subheading"])
    assert result.loc[1, "pscc_heading"] == "1234"

    # An unclassified row carries geometry and nothing else.
    assert result.loc[2, [c for c in result.columns if c.startswith("psic_")]].isna().all()

    # Bulky, non-spatial and explanatory fields are absent unless asked for.
    for absent in ("canonical_name", "lon", "lat", "psic_level", "psic_status"):
        assert absent not in result.columns
    assert json.loads(pq.read_schema(output).metadata[b"geo"]) == json.loads(geo)


def test_export_declares_string_columns_even_when_a_level_is_never_reached(tmp_path: Path):
    """A level no row reached must still be a string column.

    Inferred types make the schema depend on how deep the classifier happened to get, so two
    runs of the same pipeline would produce layers that cannot be appended in QGIS.
    """
    run_dir, _ = _write_run(tmp_path, schemes=("psic",))
    frame = pd.read_parquet(run_dir / "classified_pois.parquet")
    frame["psic_code"] = ["1011", "1011", None]
    frame["psic_level"] = ["class", "class", None]
    _write_geo_source(run_dir / "classified_pois.parquet", frame, geo=_geo_json())

    output = export_gis_run(run_dir, reference_dir=tmp_path / "reference")
    schema = pq.read_schema(output)
    assert schema.field("psic_subclass").type == pa.string()
    assert schema.field("psic_code").type == pa.string()


def test_export_preserves_source_geometry_and_identity_types(tmp_path: Path):
    run_dir, _ = _write_run(tmp_path, schemes=("psic",), geometry_type=pa.large_binary())
    output = export_gis_run(run_dir, reference_dir=tmp_path / "reference")

    source = pq.read_schema(run_dir / "classified_pois.parquet")
    result = pq.read_schema(output)
    assert result.field("geometry").type == source.field("geometry").type == pa.large_binary()
    assert result.field("canonical_id").type == source.field("canonical_id").type


def test_export_only_covers_schemes_the_run_produced(tmp_path: Path):
    run_dir, _ = _write_run(tmp_path, schemes=("psic",))
    output = export_gis_run(run_dir, reference_dir=tmp_path / "reference")

    result = pd.read_parquet(output)
    assert list(result.columns) == ["canonical_id", "geometry", *PSIC_COLUMNS]


def test_export_reads_the_summary_name_recorded_by_the_run(tmp_path: Path):
    run_dir, _ = _write_run(tmp_path, schemes=("psic",), summary_name="pois.parquet")
    output = export_gis_run(
        run_dir,
        output_path=tmp_path / "elsewhere" / "metro_manila.parquet",
        reference_dir=tmp_path / "reference",
    )
    assert output == tmp_path / "elsewhere" / "metro_manila.parquet"
    assert output.exists()


def test_export_refuses_to_overwrite_classification_summary(tmp_path: Path):
    run_dir, _ = _write_run(tmp_path, schemes=("psic",))
    summary = run_dir / "classified_pois.parquet"

    with pytest.raises(GISExportError, match="cannot overwrite the classification summary"):
        export_gis_run(
            run_dir,
            output_path=summary,
            reference_dir=tmp_path / "reference",
        )


def test_export_can_include_status(tmp_path: Path):
    run_dir, _ = _write_run(tmp_path, schemes=("psic",))
    output = export_gis_run(
        run_dir, reference_dir=tmp_path / "reference", include_status=True
    )
    result = pd.read_parquet(output)
    assert result["psic_status"].tolist() == ["ASSIGNED", "ASSIGNED", "EMPTY"]


def test_export_drops_a_covering_that_points_at_a_removed_column(tmp_path: Path):
    covering = {
        "bbox": {
            "xmin": ["bbox", "xmin"],
            "ymin": ["bbox", "ymin"],
            "xmax": ["bbox", "xmax"],
            "ymax": ["bbox", "ymax"],
        }
    }
    run_dir, _ = _write_run(tmp_path, schemes=("psic",), geo=_geo_json(covering=covering))
    output = export_gis_run(run_dir, reference_dir=tmp_path / "reference")

    geo = json.loads(pq.read_schema(output).metadata[b"geo"])
    assert "covering" not in geo["columns"]["geometry"]
    assert geo["primary_column"] == "geometry"


def test_export_warns_when_the_source_records_no_crs(tmp_path: Path):
    run_dir, _ = _write_run(tmp_path, schemes=("psic",), geo=_geo_json(crs=None))
    with pytest.warns(RuntimeWarning, match="no CRS"):
        export_gis_run(run_dir, reference_dir=tmp_path / "reference")


def test_export_rejects_geometry_without_geoparquet_metadata(tmp_path: Path):
    run_dir, _ = _write_run(
        tmp_path,
        schemes=("psic",),
        geo=None,
        extra_outputs={"classified_pois_geo_metadata": "source_has_no_geo_metadata"},
    )
    _write_geo_source(
        run_dir / "classified_pois.parquet",
        pd.DataFrame(_rows(("psic",))),
        geo=None,
    )
    with pytest.raises(GISExportError, match="input was not GeoParquet"):
        export_gis_run(run_dir, reference_dir=tmp_path / "reference")


def test_export_rejects_classification_code_absent_from_taxonomy(tmp_path: Path):
    run_dir, _ = _write_run(tmp_path, schemes=("psic",))
    frame = pd.read_parquet(run_dir / "classified_pois.parquet")
    frame.loc[0, "psic_code"] = "99999"
    frame.loc[0, "psic_level"] = None
    _write_geo_source(run_dir / "classified_pois.parquet", frame, geo=_geo_json())
    with pytest.raises(GISExportError, match="99999"):
        export_gis_run(run_dir, reference_dir=tmp_path / "reference")


def test_export_rejects_a_taxonomy_that_changed_after_the_run(tmp_path: Path):
    run_dir, _ = _write_run(tmp_path, schemes=("psic",))
    manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    manifest["schemes"]["psic"]["taxonomy_fingerprint"] = "0123456789abcdef"
    (run_dir / "run.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(GISExportError, match="fingerprint"):
        export_gis_run(run_dir, reference_dir=tmp_path / "reference")
    assert export_gis_run(
        run_dir, reference_dir=tmp_path / "reference", check_taxonomy_fingerprint=False
    ).exists()


def test_export_rejects_a_level_that_disagrees_with_the_reference_tree(tmp_path: Path):
    run_dir, _ = _write_run(tmp_path, schemes=("psic",), fingerprints=False)
    frame = pd.read_parquet(run_dir / "classified_pois.parquet")
    frame.loc[0, "psic_level"] = "group"
    _write_geo_source(run_dir / "classified_pois.parquet", frame, geo=_geo_json())
    with pytest.raises(GISExportError, match="recorded at level"):
        export_gis_run(run_dir, reference_dir=tmp_path / "reference")


def test_cli_gis_export_writes_the_layer(tmp_path: Path):
    run_dir, _ = _write_run(tmp_path, schemes=("psic",))
    result = CliRunner().invoke(
        app,
        ["gis-export", str(run_dir), "--reference-dir", str(tmp_path / "reference")],
    )
    assert result.exit_code == 0, result.output
    assert (run_dir / "gis.parquet").exists()


def test_cli_gis_export_reports_an_export_error(tmp_path: Path):
    run_dir, _ = _write_run(tmp_path, schemes=("psic",))
    (run_dir / "run.json").unlink()
    result = CliRunner().invoke(
        app,
        ["gis-export", str(run_dir), "--reference-dir", str(tmp_path / "reference")],
    )
    assert result.exit_code != 0
    assert not (run_dir / "gis.parquet").exists()


def test_export_refuses_to_overwrite_the_run_manifest(tmp_path: Path):
    """Replacing run.json is worse than replacing any single table.

    The manifest carries the taxonomy versions and fingerprints, so a run whose manifest has
    been overwritten cannot be exported again at all.
    """
    run_dir, _ = _write_run(tmp_path, schemes=("psic",))
    before = (run_dir / "run.json").read_bytes()
    with pytest.raises(GISExportError, match="run manifest"):
        export_gis_run(
            run_dir,
            output_path=run_dir / "run.json",
            reference_dir=tmp_path / "reference",
        )
    assert (run_dir / "run.json").read_bytes() == before


def test_export_refuses_to_overwrite_another_run_output(tmp_path: Path):
    run_dir, _ = _write_run(
        tmp_path,
        schemes=("psic",),
        extra_outputs={"diagnostics": "diagnostics.csv"},
    )
    diagnostics = run_dir / "diagnostics.csv"
    diagnostics.write_text("metric,value\nrows,3\n", encoding="utf-8")
    with pytest.raises(GISExportError, match="run output"):
        export_gis_run(
            run_dir,
            output_path=diagnostics,
            reference_dir=tmp_path / "reference",
        )


def test_build_gis_export_refuses_to_overwrite_its_own_source(tmp_path: Path):
    run_dir, _ = _write_run(tmp_path, schemes=("psic",))
    summary = run_dir / "classified_pois.parquet"
    with pytest.raises(GISExportError, match="its own source"):
        build_gis_export(summary, {"psic": _taxonomies()["psic"]}, summary)


def test_export_reports_an_unreadable_run_manifest(tmp_path: Path):
    run_dir, _ = _write_run(tmp_path, schemes=("psic",))
    (run_dir / "run.json").write_bytes(b'{"schemes": {"psic": {"version": "\xb5"}}}')
    with pytest.raises(GISExportError, match="could not read"):
        export_gis_run(run_dir, reference_dir=tmp_path / "reference")


def test_export_records_which_run_and_taxonomy_produced_the_layer(tmp_path: Path):
    run_dir, _ = _write_run(tmp_path, schemes=("psic",), package_version="0.0-run")
    output = export_gis_run(run_dir, reference_dir=tmp_path / "reference")

    provenance = json.loads(pq.read_schema(output).metadata[b"placetype"])
    fingerprint = _taxonomies()["psic"].fingerprint
    assert provenance["run"] == run_dir.name
    assert provenance["run_package_version"] == "0.0-run"
    assert provenance["exporter_package_version"] == __version__
    assert provenance["taxonomy_fingerprint_check"] is True
    assert provenance["status_included"] is False
    assert provenance["schemes"] == {
        "psic": {
            "version": "rev5",
            "run_taxonomy_fingerprint": fingerprint,
            "export_taxonomy_fingerprint": fingerprint,
        }
    }


def test_provenance_preserves_run_fingerprint_when_override_is_used(tmp_path: Path):
    run_dir, _ = _write_run(tmp_path, schemes=("psic",))
    manifest_path = run_dir / "run.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schemes"]["psic"]["taxonomy_fingerprint"] = "0123456789abcdef"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    output = export_gis_run(
        run_dir,
        reference_dir=tmp_path / "reference",
        check_taxonomy_fingerprint=False,
    )
    provenance = json.loads(pq.read_schema(output).metadata[b"placetype"])
    psic = provenance["schemes"]["psic"]
    assert provenance["taxonomy_fingerprint_check"] is False
    assert psic["run_taxonomy_fingerprint"] == "0123456789abcdef"
    assert psic["export_taxonomy_fingerprint"] == _taxonomies()["psic"].fingerprint


def test_export_is_reproducible_for_an_unchanged_run(tmp_path: Path):
    # No export timestamp, so a second export of the same run is byte-identical and can be
    # compared directly against the first.
    run_dir, _ = _write_run(tmp_path, schemes=("psic",))
    first = export_gis_run(
        run_dir, output_path=tmp_path / "a.parquet", reference_dir=tmp_path / "reference"
    )
    second = export_gis_run(
        run_dir, output_path=tmp_path / "b.parquet", reference_dir=tmp_path / "reference"
    )
    assert first.read_bytes() == second.read_bytes()
