# Unreleased

## Joint multi-taxonomy crosswalk

- `crosswalk-init` creates one joint PSIC/PCPC/PSCC review worklist by default, keyed by a stable `joint_key`, while preserving each scheme's full hierarchy.
- Reviewed rows are never stranded: existing per-scheme crosswalks are adopted when the joint worklist is first created, and reviewed rows whose category no longer occurs are carried forward with `row_count` and `row_share` of zero.
- Suggestion stays independent on the first pass. PSIC and PCPC may produce review-required category-based suggestions when retrieval is strong and separated; PSCC category-only hits remain candidates because a place category is not itself commodity evidence.
- PSCC remains fully present in the joint workflow: it receives candidates and peer-context rechecks, and reviewed PSCC mappings can become peer evidence. Category-only PSCC rows record `guard:commodity_evidence_required`.
- Every refusal to promote a hit is named in `suggestion_source` as `guard:<reason>`, including `guard:short_query` for one-token PSIC/PCPC queries.
- Per-scheme reporting separates reviewed decisions from coded coverage. `NOT_ACTIVITY` and `UNCODEABLE` can be reviewed decisions without being counted as rows carrying a code.
- Reviewed rows reconstruct the same query and branch inputs used before review, so the recheck compares reviewed and unreviewed rows on the same retrieval basis without changing the reviewed mapping.
- A controlled peer-context recheck compares two identical retrieval calls, one with and one without the accepted labels of the other classification systems, and never overwrites a reviewed or first-pass decision.
- Only reviewed codes and accepted suggestions become cross-taxonomy evidence; raw retrieval candidates do not. At most one row speaks for each classification system, so two versions of one system are treated as alternatives rather than as independent peers; reviewed evidence wins first, then the configured default version.
- Multi-code mappings keep every accepted code and every hierarchy path in the recheck audit. Peer context is recorded twice: `peer_context` for reading, with versions and codes, and `peer_query_context` for retrieval, with titles only.
- `placetype classify` defaults to PSIC, PCPC and PSCC together; `--schemes` still selects a subset.

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
