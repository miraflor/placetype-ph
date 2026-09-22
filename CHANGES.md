## PSA 2018 I-O industry mapping

- `classified_pois.parquet` gains PSA 2018 input-output industry sets (or explicitly provisional candidate sets) for every PSIC-classified place: `io16_codes`, `io80_codes`, `io240_codes` with names and confidence, plus `io_mapping_status`, `io_mapping_psic_level`, `io_mapping_psic_code`, `io_mapping_psic2019_codes`, `io_mapping_bridge_relation`, and `io_mapping_bridge_basis`.
- The concordance `psic_2019_to_io_2018.csv` is keyed on PSIC 2019, the basis of the 2018 I-O tables. The bridge `psic_rev5_to_psic_2019.csv` relates every Revision 5 section, division, and group to PSIC 2019, so changed titles, reused codes, and the section letters from J onward can be represented explicitly. Hand-written bridge rows remain `draft` until reviewed and therefore produce `PROVISIONAL_*` rather than ordinary `MAPPED_*` statuses.
- A bridge row applies only while its Revision 5 title matches the loaded taxonomy; a different tree maps at a coarser level or stays `UNMAPPED_NO_BRIDGE` instead of being mapped by code alone.
- A `draft` bridge row sets a `PROVISIONAL_*` status only when it changes the answer; when the deepest accepted ancestor row is an ordinary mapping with the same I-O sets, the accepted mapping is reported. A `no_counterpart` row, draft or accepted, never takes part in that replacement.
- Division 55 and section I also list PSIC 2019 group 799, because Revision 5 moved intermediation for accommodation into them; a test requires every mapped bridge row to lie inside its nearest ancestor row that lists codes.
- Both data files are validated when loaded: code formats, code and name alignment, confidence values, PSIC version, bridge relations, and bridge targets. The schema can also represent a reviewed category with no defensible PSIC 2019 counterpart without deleting its Revision 5 bridge row.
- `gis.parquet` carries I-O codes, confidence, and status, excludes I-O names, and records the SHA-256 hash of both data files in its `placetype` metadata.
- `run.json` records the SHA-256 hash and row count of both data files, bridge rows by basis and relation, and the number of places per `io_mapping_status`, so provisional use is auditable.
- I-O outputs now distinguish full candidate sets from GIS-ready singleton map codes. `io16_map_code`, `io80_map_code`, and `io240_map_code` are populated only when the corresponding candidate set contains exactly one industry; ambiguous sets such as `229; 230` remain intact in the audit columns and leave the map code blank. Map status, PSIC classification status/depth, bridge basis, candidate multiplicity, and concordance confidence are reported explicitly so coverage is not mistaken for validation.

## Batched joint-crosswalk retrieval

- `crosswalk-suggest` now prepares rows separately from retrieval and scores TF-IDF queries in bounded batches per taxonomy.
- Repeated query text is vectorised once per taxonomy, while row-specific branch restrictions and ranking semantics are preserved.
- `--batch-size` controls the number of unique query texts scored together; the default is 256.
- Five visible stages and Rich progress bars now show preparation, retrieval and recheck progress; `--no-progress` disables them.
- The V8 bounded peer rerank and PSCC commodity-evidence guard are unchanged.

# Unreleased

## Joint multi-taxonomy crosswalk

- The peer-context pass is now a bounded rerank of the source-only first-pass candidate set. Peer labels can reorder candidates but cannot introduce a new target-taxonomy code.
- Candidate-only reorders use `CANDIDATE_STABLE` / `CANDIDATE_SHIFT` and never escalate `joint_status`; only accepted suggestions or reviewed mappings can trigger group-level `RECHECK`.
- First-pass review status now distinguishes `REVIEW_MAPPING` from rule-based `REVIEW_DECISION`, while `REVIEW_CANDIDATES` remains candidate-only.
- Category-only PSCC remains candidate-level and continues to require commodity evidence before promotion.
- Recheck control results are reused from the first pass instead of repeating the same source-only retrieval.
- Hierarchical retrieval computes TF-IDF similarities once per query and reuses that score vector for coarse and fine ranking; repeated branch closures are cached.
- Reviewed rows retain their reconstructed source-only candidates for recheck without changing the reviewed mapping.
- `crosswalk-init` reports a legacy source only when it actually contains reviewed rows that were read.
- Joint worklists still preserve full PSIC/PCPC/PSCC hierarchy, reviewed legacy mappings, multi-code unions, one peer per classification system, and default three-scheme classification.

## QGIS export

- Added `placetype gis-export`, which writes a compact standalone GeoParquet next to a classification run for direct use in QGIS.
- Ancestor codes are read from the taxonomy tree, so a coarse assignment leaves deeper levels empty instead of implying a level the classifier never reached. The export covers every level the scheme defines, including PSIC/PCPC `section` and PCPC `item`.
- Column types are declared rather than inferred: a level that no row reached is still a string column, and `canonical_id` and `geometry` keep the Arrow types they had in the source.
- The copied GeoParquet `geo` block is rewritten to describe only the columns the export retains, so a bounding-box covering that pointed at a dropped column is removed rather than left dangling.
- The reference taxonomy is checked against the `taxonomy_fingerprint` recorded by the run, not only against scheme and version.
- The export refuses an `--output` path that points at the run's own manifest, its classification summary, or any other existing output file the manifest names, so a mistyped path cannot destroy the record the layer describes.
- The exported layer carries a `placetype` metadata key naming the run, the classification-time and export-time package versions, and both the run-recorded and export-time taxonomy fingerprints. This preserves provenance even when an old run is exported after an upgrade or with the fingerprint check explicitly disabled. No export timestamp is recorded, so re-exporting an unchanged run reproduces the same bytes.

# PlaceType PH 0.1.1

This patch is the first release driven by **live PSA workbooks**, not synthetic importer fixtures.

## Live-source corrections

- Added a PSIC Revision 5 matrix parser for the official `Section / Division / Group / Class / Sub-Class / Description` layout. A row may define several hierarchy nodes; all are now retained.
- Added a PSCC 2022 parser for the official `Heading / 2022 PSCC / DESCRIPTION` layout and `Chapter n` prose rows.
- PSCC now materializes omitted 2/4/6/8-digit prefix ancestors needed for hierarchical backoff. These nodes are explicitly marked as implicit source-structure nodes rather than presented as workbook titles.
- Repairs the workbook's numeric `98.1` heading to `9810` because it is stored as a number with Excel format `0.00` in a column that unambiguously represents 4-digit headings.
- Intermediate-level gaps are diagnostic warnings by default. The current official PSIC workbook contains three subclass rows whose 4-digit parent is absent, so rejecting every gap makes the official source unusable.
- The contested PSIC group count is no longer a hard structure gate: PSA's current web summary says 260 groups, while the current official July 2026 workbook contains 261 distinct group rows. Sections, divisions, classes and subclasses remain externally checked.

## Live results used for this patch

The current PSIC workbook parses to 22 sections, 88 divisions, 261 groups, 493 classes and 1,338 subclasses. It has no orphan/wrong-parent errors and three source-level gaps under group 523.

The current PSCC 2022 workbook parses to a complete five-level prefix tree with 98 chapters (including the reserved chapter), 1,245 headings, 5,702 HS subheadings, 11,522 AHTN subheadings and 16,049 11-digit commodities, with no structural errors or level gaps.
