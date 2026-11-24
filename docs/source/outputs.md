# Artifacts, schemas and GIS

<p class="lede">PlaceType PH publishes both an audit-rich result table and a compact map-ready view. Neither should be confused with ground truth: every code remains scoped to a scheme, statistical version, and evidence policy.</p>

## `entity_classifications.parquet`

One row per `canonical_id` × requested taxonomy. Core columns include `scheme`, `version`, `code`, `level`, `status`, `deciding_component`, `candidate_codes`, `evidence_sources`, `flags`, `audit`, `model`, `traversal_agreement`, `classification_depth`, `branch_max_depth`, and `max_depth`. List/audit fields are JSON strings in the Parquet table.

This is the primary dataset for studying a classifier's *decisions*. A missing code may be principled (`NOT_PSIC_ACTIVITY`, `UNCODEABLE`, `CONFLICT`) or simply uncovered (`EMPTY`); do not collapse all blank codes into an error category.

## `classified_pois.parquet`

One row per canonical place, preserving available geometry/location and convenience fields (`psic_code`, `psic_level`, `psic_status`, and equivalents for other requested schemes). It additionally holds derived I–O industry candidate sets, role evidence, and optional specialized/institutional fields. When GeoParquet metadata exists, the pipeline attempts to preserve it; the manifest reports preservation or failure explicitly.

Important: `io80_codes = "74; 75"` means **one of two** candidate industries, not two simultaneously observed industries. A singleton map code or a labelled resolver decision is distinct from a policy-resolved code. See [PSIC to I–O](io-projection.html).

The detailed classification table includes JSON `admissible_roots`; the place summary includes `psic_admissible_roots`, `io_mapping_draft_levels`, draft subgroup sets, direct I–O evidence, ownership inferences and `io*_resolved_code`, `io*_resolved_status`, `io*_resolved_source`. These resolved columns are also available in GIS export. Candidate, refined, inferred and resolved values are not interchangeable. Two review groups are added: `psic_search_*` (PSIC title search for places without a code; never sets `psic_code`) and `institution_psic_proposal_*` (the public or private PSIC subtree implied by a known ownership side; diagnostic only).

## `run.json` manifest

`io_mapping.resolution` records the selected policy, implementation policy, rule fingerprint and per-level coded-row, status and source counts. Coverage is not a measure of classification accuracy.

Each run identifies package version, source path, selected schemes and versions, taxonomy fingerprints, active reviewed crosswalk fingerprints, mapping ambiguity counts, LLM prompt/model metadata when applicable, I–O reference provenance and status counts, and output file names. Input reference hashes allow some material changes to be detected; they do not attest the source's factual accuracy.

## GIS export

The separate `placetype gis-export` command produces a compact GeoParquet file for QGIS. It exports string-typed assigned codes and the **actual official ancestor codes** for each level: `psic_section`, `psic_division`, `psic_group`, `psic_class`, `psic_subclass`; analogous level columns exist for PCPC and PSCC.

A code assigned at division or group level leaves more-specific fields **blank**, rather than filling them with an invented subclass. The output can also carry I–O code sets, singleton map fields, map status and provenance—but omits bulky name/audit fields by design.

```powershell
placetype gis-export output\iloilo-reviewed
if ($LASTEXITCODE -ne 0) { throw "GIS export failed" }
```

### GeoParquet and fingerprint safeguards

The exporter rejects an invalid/missing geometry, invalid classification codes, or taxonomy fingerprint drift by default. It rewrites the GeoParquet metadata so references to omitted covering/bounding-box columns are not left dangling, and records both source-run and export-time provenance. Do not disable a taxonomy fingerprint check as an ordinary convenience; this override changes the interpretation contract.

## Geospatial accuracy is inherited, not generated

The coordinates, canonical entity geometry, source lineage and record-linkage uncertainty originate in OpenPlaces. PlaceType PH does **not** improve geocoding, triangulate points, route roads, or create network partitions. Its map export is a representation of existing classification results.

## Performance profile

For deterministic `--llm none`, the pipeline supports `--workers N` spawn-safe process workers and chunked row dictionary conversion. It still materializes the entire input DataFrame and entire result table: **this is not bounded-memory streaming Parquet I/O**. Large national runs may need enough RAM or a future streaming implementation. Parallel worker count must not be used with a live LLM backend in the current CLI.

**Implementation:** [`pipeline.py`](https://github.com/miraflor/placetype-ph/blob/main/src/placetype_ph/pipeline.py) · [`gis.py`](https://github.com/miraflor/placetype-ph/blob/main/src/placetype_ph/gis.py).
