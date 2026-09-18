from __future__ import annotations

import json
import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from . import __version__
from .taxonomy import LEVEL_ORDER, Taxonomy

DEFAULT_SUMMARY_NAME = "classified_pois.parquet"

# Column suffix for each taxonomy level. The set of levels belongs to `taxonomy.LEVEL_ORDER`;
# this table only names columns, and `_level_columns` reads the order from LEVEL_ORDER so the
# two cannot drift apart. `tests/test_gis.py` asserts that every level has a suffix here.
LEVEL_SUFFIX: dict[str, dict[str, str]] = {
    "psic": {
        "section": "section",
        "division": "division",
        "group": "group",
        "class": "class",
        "subclass": "subclass",
    },
    "pcpc": {
        "section": "section",
        "division": "division",
        "group": "group",
        "class": "class",
        "subclass": "subclass",
        "item": "item",
    },
    "pscc": {
        "chapter": "chapter",
        "heading": "heading",
        "hs_subheading": "hs_subheading",
        "ahtn_subheading": "ahtn_subheading",
        "commodity": "commodity",
    },
}


class GISExportError(ValueError):
    pass


def _level_columns(scheme: str) -> tuple[tuple[str, str], ...]:
    """Return (column suffix, level) pairs, deepest level first.

    Order and membership both come from `LEVEL_ORDER`, so a level added to a scheme appears
    in the export automatically instead of being silently dropped.
    """
    suffixes = LEVEL_SUFFIX[scheme]
    missing = [level for level in LEVEL_ORDER[scheme] if level not in suffixes]
    if missing:
        raise GISExportError(f"no GIS column name defined for {scheme} level(s): {missing}")
    return tuple((suffixes[level], level) for level in reversed(LEVEL_ORDER[scheme]))


def _clean_code(value: object) -> str | None:
    """Normalize one stored code to a stripped string, or None when it is absent.

    Values arrive from Arrow rather than pandas, so a null is a real `None`. Floats are still
    handled because a summary written by an older pipeline may store codes as numbers.
    """
    if value is None:
        return None
    if isinstance(value, float):
        if value != value:  # NaN
            return None
        if value.is_integer():
            return str(int(value))
    text = str(value).strip()
    return text or None


def _hierarchy_values(
    taxonomy: Taxonomy, code: str, level_columns: Sequence[tuple[str, str]]
) -> dict[str, str | None]:
    if code not in taxonomy.nodes:
        raise GISExportError(
            f"classification code {code!r} is absent from {taxonomy.scheme} {taxonomy.version}"
        )
    by_level = {
        taxonomy.get(node_code).level: node_code for node_code in taxonomy.path_from_root(code)
    }
    return {
        f"{taxonomy.scheme}_{suffix}": by_level.get(level) for suffix, level in level_columns
    }


def _covering_columns(covering: object) -> set[str]:
    """Column names a GeoParquet 1.1 `covering` block points at."""
    if not isinstance(covering, dict):
        return set()
    referenced: set[str] = set()
    for reference in (covering.get("bbox") or {}).values():
        if isinstance(reference, (list, tuple)) and reference:
            referenced.add(str(reference[0]))
    return referenced


def _parse_geo(raw: bytes, source: Path) -> dict:
    try:
        geo = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GISExportError(f"{source} has unreadable GeoParquet metadata: {exc}") from exc
    if not isinstance(geo, dict) or not isinstance(geo.get("columns"), dict):
        raise GISExportError(f"{source} has GeoParquet metadata with no column description")
    return geo


def _rewrite_geo_metadata(raw: bytes, output_columns: Sequence[str], source: Path) -> bytes:
    """Copy the source `geo` metadata, keeping only what the exported file still contains.

    Copying the block unchanged can leave it describing columns this export drops, for example
    a GeoParquet 1.1 bounding-box covering column. A reader that trusts the metadata then looks
    for a column that is not there, which is exactly the class of file this module refuses to
    accept as input.
    """
    geo = _parse_geo(raw, source)
    present = set(output_columns)
    kept = {
        name: dict(spec)
        for name, spec in geo["columns"].items()
        if name in present and isinstance(spec, dict)
    }
    primary = geo.get("primary_column")
    if primary not in kept:
        raise GISExportError(
            f"{source} names {primary!r} as its primary geometry column, "
            "but that column is not part of the export"
        )
    for name, spec in kept.items():
        if not _covering_columns(spec.get("covering")) <= present:
            spec.pop("covering", None)
        if "crs" in spec and spec["crs"] is None:
            warnings.warn(
                f"{source} records no CRS for geometry column {name!r}; "
                "QGIS will ask for one when the layer is opened",
                RuntimeWarning,
                stacklevel=3,
            )
    geo["columns"] = kept
    return json.dumps(geo, separators=(",", ":")).encode("utf-8")


def _missing_geo_metadata_message(source: Path, geo_metadata_status: str | None) -> str:
    base = (
        f"{source} has a geometry column but no GeoParquet metadata; "
        "cannot guarantee that QGIS will recognize it as a spatial layer"
    )
    if geo_metadata_status == "source_has_no_geo_metadata":
        return f"{base}. The run recorded that its OpenPlaces input was not GeoParquet"
    if geo_metadata_status and geo_metadata_status.startswith("failed:"):
        return f"{base}. The run recorded metadata preservation as {geo_metadata_status}"
    return base


def build_gis_export(
    classified_pois_path: str | Path,
    taxonomies: Mapping[str, Taxonomy],
    output_path: str | Path,
    *,
    include_status: bool = False,
    geo_metadata_status: str | None = None,
    provenance: Mapping[str, object] | None = None,
) -> Path:
    """Write a compact, standalone GeoParquet for direct inspection in QGIS.

    The export keeps only `canonical_id`, `geometry`, the assigned code for each available
    scheme, and one column per taxonomy level. Ancestors come from the taxonomy tree rather
    than string slicing, so a coarse classification leaves deeper columns null.

    Every column type is declared rather than inferred. A level that no row reached is still a
    string column, and `canonical_id` and `geometry` keep the exact Arrow types they had in the
    source, so two runs of the same pipeline produce files with the same schema.
    """
    classified_pois_path = Path(classified_pois_path)
    output_path = Path(output_path)
    if not classified_pois_path.exists():
        raise GISExportError(f"missing classification summary {classified_pois_path}")
    # `export_gis_run` guards every file the run owns; this guards the one file this function
    # can destroy on its own, so a direct library call cannot consume and replace one path.
    if output_path.resolve() == classified_pois_path.resolve():
        raise GISExportError(f"GIS output cannot overwrite its own source {classified_pois_path}")

    schema = pq.read_schema(classified_pois_path)
    names = set(schema.names)
    missing = {"canonical_id", "geometry"} - names
    if missing:
        raise GISExportError(
            f"{classified_pois_path} is missing required GIS column(s): {sorted(missing)}"
        )
    geo_metadata = (schema.metadata or {}).get(b"geo")
    if geo_metadata is None:
        raise GISExportError(
            _missing_geo_metadata_message(classified_pois_path, geo_metadata_status)
        )

    available: list[str] = []
    for scheme in LEVEL_ORDER:
        if f"{scheme}_code" not in names:
            continue
        taxonomy = taxonomies.get(scheme)
        if taxonomy is None:
            raise GISExportError(
                f"{scheme}_code is present but no {scheme} taxonomy was supplied"
            )
        if taxonomy.scheme != scheme:
            raise GISExportError(
                f"taxonomy supplied for {scheme} contains scheme {taxonomy.scheme!r}"
            )
        available.append(scheme)
    if not available:
        raise GISExportError("no psic_code, pcpc_code, or pscc_code column is present")

    read_columns = ["canonical_id", "geometry"]
    for scheme in available:
        read_columns.append(f"{scheme}_code")
        if f"{scheme}_level" in names:
            read_columns.append(f"{scheme}_level")
        if include_status and f"{scheme}_status" in names:
            read_columns.append(f"{scheme}_status")
    source = pq.read_table(classified_pois_path, columns=read_columns)

    carried = ["canonical_id", "geometry"]
    fields = [source.schema.field(name) for name in carried]
    arrays = [source.column(name) for name in carried]

    for scheme in available:
        taxonomy = taxonomies[scheme]
        level_columns = _level_columns(scheme)
        codes = [_clean_code(value) for value in source.column(f"{scheme}_code").to_pylist()]
        expanded = {
            code: _hierarchy_values(taxonomy, code, level_columns)
            for code in sorted({code for code in codes if code is not None})
        }
        if f"{scheme}_level" in read_columns:
            _check_recorded_levels(
                scheme, taxonomy, codes, source.column(f"{scheme}_level").to_pylist()
            )

        fields.append(pa.field(f"{scheme}_code", pa.string()))
        arrays.append(pa.array(codes, type=pa.string()))
        for suffix, _level in level_columns:
            column = f"{scheme}_{suffix}"
            fields.append(pa.field(column, pa.string()))
            arrays.append(
                pa.array(
                    [None if code is None else expanded[code][column] for code in codes],
                    type=pa.string(),
                )
            )
        if include_status and f"{scheme}_status" in read_columns:
            status = [_clean_code(v) for v in source.column(f"{scheme}_status").to_pylist()]
            fields.append(pa.field(f"{scheme}_status", pa.string()))
            arrays.append(pa.array(status, type=pa.string()))

    table_columns = [field.name for field in fields]
    geo = _rewrite_geo_metadata(
        geo_metadata, table_columns, classified_pois_path
    )
    metadata: dict[bytes, bytes] = {b"geo": geo}
    if provenance is not None:
        # The layer will outlive this shell session. Nothing else in the file records which
        # run and which taxonomy trees produced it.
        metadata[b"placetype"] = json.dumps(
            dict(provenance), separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    table = pa.Table.from_arrays(arrays, schema=pa.schema(fields, metadata=metadata))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, output_path, compression="zstd")
    return output_path


def _check_recorded_levels(
    scheme: str,
    taxonomy: Taxonomy,
    codes: Sequence[str | None],
    recorded: Sequence[object],
) -> None:
    """Compare the level the run recorded with the level the reference tree reports.

    A disagreement means the taxonomy on disk is not the one that produced the run, so the
    ancestor columns would describe a different tree than the classification did.
    """
    seen: dict[str, str] = {}
    for code, level in zip(codes, recorded, strict=True):
        if code is None:
            continue
        recorded_level = _clean_code(level)
        if recorded_level is None or seen.get(code) == recorded_level:
            continue
        seen[code] = recorded_level
        actual = taxonomy.get(code).level
        if actual != recorded_level:
            raise GISExportError(
                f"{scheme} code {code!r} is recorded at level {recorded_level!r} in the run "
                f"but sits at level {actual!r} in {taxonomy.scheme} {taxonomy.version}"
            )


def _load_run_taxonomies(
    manifest: Mapping[str, object],
    manifest_path: Path,
    reference_dir: Path,
    *,
    check_taxonomy_fingerprint: bool,
) -> dict[str, Taxonomy]:
    scheme_meta = manifest.get("schemes")
    if not isinstance(scheme_meta, dict) or not scheme_meta:
        raise GISExportError(f"{manifest_path} has no scheme metadata")

    taxonomies: dict[str, Taxonomy] = {}
    for scheme in LEVEL_ORDER:
        details = scheme_meta.get(scheme)
        if not isinstance(details, dict):
            continue
        version = str(details.get("version") or "").strip()
        if not version:
            raise GISExportError(f"{manifest_path} has no version for {scheme}")
        taxonomy_path = reference_dir / f"{scheme}_{version}" / "nodes.parquet"
        if not taxonomy_path.exists():
            raise GISExportError(
                f"missing taxonomy {taxonomy_path}; "
                f"run `placetype taxonomy fetch {scheme}` first"
            )
        taxonomy = Taxonomy.load(taxonomy_path)
        if (taxonomy.scheme, taxonomy.version) != (scheme, version):
            raise GISExportError(
                f"{taxonomy_path} contains {taxonomy.scheme} {taxonomy.version}, "
                f"but the run used {scheme} {version}"
            )
        # Scheme and version alone do not identify a tree: a corrected import or a repaired
        # parent edge keeps the official version string. The run records a content
        # fingerprint for exactly this reason.
        recorded = str(details.get("taxonomy_fingerprint") or "").strip()
        if check_taxonomy_fingerprint and recorded and recorded != taxonomy.fingerprint:
            raise GISExportError(
                f"{taxonomy_path} has fingerprint {taxonomy.fingerprint}, but the run used "
                f"{recorded}. The reference tree changed after the run, so exported ancestor "
                "codes would not match the classification. Re-run the classification, or pass "
                "--no-check-taxonomy-fingerprint to export anyway"
            )
        taxonomies[scheme] = taxonomy
    return taxonomies


def _check_output_target(
    output: Path, run_dir: Path, manifest_path: Path, summary_path: Path, outputs: Mapping
) -> None:
    """Refuse an output path that would destroy one of the run's own files.

    The manifest matters most: it holds the taxonomy versions and fingerprints, so replacing it
    makes the run unexportable afterwards rather than merely losing one file.
    """
    protected = {manifest_path.resolve(): f"run manifest {manifest_path}"}
    for name in outputs.values():
        if not isinstance(name, str):
            continue
        candidate = run_dir / name
        if candidate.exists() and candidate.is_file():
            protected[candidate.resolve()] = f"run output {candidate}"
    protected[summary_path.resolve()] = f"classification summary {summary_path}"
    owner = protected.get(output.resolve())
    if owner is not None:
        raise GISExportError(f"GIS output cannot overwrite the {owner}")


def export_gis_run(
    run_dir: str | Path,
    *,
    output_path: str | Path | None = None,
    reference_dir: str | Path = "reference",
    include_status: bool = False,
    check_taxonomy_fingerprint: bool = True,
) -> Path:
    """Export one PlaceType run using the exact taxonomy versions recorded in run.json."""
    run_dir = Path(run_dir)
    manifest_path = run_dir / "run.json"
    if not manifest_path.exists():
        raise GISExportError(f"missing run manifest {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GISExportError(f"could not read {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise GISExportError(f"{manifest_path} is not a run manifest")

    # The manifest names its own outputs; do not assume the default file name.
    outputs = manifest.get("outputs")
    outputs = outputs if isinstance(outputs, dict) else {}
    summary_name = str(outputs.get("classified_pois") or DEFAULT_SUMMARY_NAME)
    geo_metadata_status = outputs.get("classified_pois_geo_metadata")
    summary_path = run_dir / summary_name
    output = Path(output_path) if output_path is not None else run_dir / "gis.parquet"
    # Checked before the taxonomies are loaded: a mistyped --output should not cost a full
    # reference tree read before it is refused.
    _check_output_target(output, run_dir, manifest_path, summary_path, outputs)

    taxonomies = _load_run_taxonomies(
        manifest,
        manifest_path,
        Path(reference_dir),
        check_taxonomy_fingerprint=check_taxonomy_fingerprint,
    )

    created_at = manifest.get("created_at")
    run_package_version = manifest.get("package_version")
    scheme_meta = manifest.get("schemes")
    scheme_meta = scheme_meta if isinstance(scheme_meta, dict) else {}
    provenance: dict[str, object] = {
        "run": run_dir.name,
        "run_package_version": (
            run_package_version if isinstance(run_package_version, str) else None
        ),
        "exporter_package_version": __version__,
        "taxonomy_fingerprint_check": check_taxonomy_fingerprint,
        "status_included": include_status,
        "schemes": {
            scheme: {
                "version": taxonomy.version,
                "run_taxonomy_fingerprint": (
                    str(scheme_meta[scheme].get("taxonomy_fingerprint") or "") or None
                ),
                "export_taxonomy_fingerprint": taxonomy.fingerprint,
            }
            for scheme, taxonomy in sorted(taxonomies.items())
        },
    }
    if isinstance(created_at, str):
        provenance["run_created_at"] = created_at

    return build_gis_export(
        summary_path,
        taxonomies,
        output,
        include_status=include_status,
        geo_metadata_status=(
            geo_metadata_status if isinstance(geo_metadata_status, str) else None
        ),
        provenance=provenance,
    )
