# Specialized place evidence and classification review

## Purpose

This module adds domain-specific **evidence and review candidates** to an existing
place classification workflow. It is an independent, general-purpose component.
It does not infer products or traded commodities from place types, does not
change any reviewed mapping, and does not assign a place to a model-specific
industry partition.

## Active workflow integration

The installer inserts a guarded hook immediately before writing
`classified_pois.parquet`. It preserves every original column and row, and
appends the following columns:

- `specialized_status`, `specialized_candidate_codes`, `specialized_reason`, `specialized_flags`;
- `administrative_status`, `administrative_candidate_code`, `administrative_reason`;
- `ownership_candidate`, `ownership_status`, `ownership_reason`;
- `registry_status`, `registry_reference`, `registry_basis`;
- `specialized_audit` (canonical, deterministic JSON decision evidence).

The existing PSIC/PCPC/PSCC first passes, reviewed mappings, output classification
rows, and joint review semantics are unchanged. In particular, these fields are
**proposals and audit data**; they are not certified corrected classifications.
This conservatism is intentional: an unknown target checkout is not grounds for
rewriting its taxonomy codes or interpreting a second taxonomy as production proof.

## Evidence policy

1. Keep OSM, Overture and Foursquare independently attributed. Never turn two
   categories from the same underlying source into independent votes.
2. Parse OSM tags as exact key-value assertions. Auxiliary brand, operator,
   cuisine, payment, and contact keys do not create a separate primary activity.
3. Eight validated compatible OSM pairs are included as examples, not an exhaustive list.
4. Categorize category rules by precision. Precise name/category differences
   are reviewable, not silently overridden. Broad categories do not automatically
   supersede names.
5. The catalog includes 178 source-category and high-precision name rules at
   reasonably supported PSIC ancestors, never invented product/commodity leaves.
6. Maintain separate NOT_PSIC_ACTIVITY and UNCODEABLE source states. Genuine
   activity/nonactivity conflicts are flagged for review.
7. Distinguish government administrative offices from frontline health/education
   establishments. A narrow administrative name can propose code 84120; it does
   not establish ownership.
8. School and hospital public/private name conventions are **provisional only**.
   Saint/Holy/Our-Lady prefixes are not private-ownership evidence.
9. Registry name+city/region matches are **identity candidates**, not verified
   physical-site links. An external-ID+name+locality match may support a
   stronger assertion but can never override a conflicting heuristic silently.
10. Preserve review-required, union, unresolved, conflict, and candidate-only
    outputs. No tool here modifies existing PSIC, PCPC or PSCC decisions.

## Apply safely

From the extracted archive directory, after activating the target Python environment:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\apply.ps1 `
  -Root "$HOME\Documents\GitHub\placetype-ph" -Check
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\apply.ps1 `
  -Root "$HOME\Documents\GitHub\placetype-ph" -Apply
```

The installer validates the Git repository identity, requires a clean checkout,
checks every overlay file against the manifest, checks exact compatible hook
anchors and Python syntax, and aborts before changing anything on a mismatch.
Applying the overlay is transactional: it executes the rule smoke test, targeted
tests and entire target pytest suite. Any failure triggers rollback of both
new files and the original pipeline. **It never commits, pushes or merges.**

## Use the new standalone commands

```powershell
python -m placetype_ph.specialized validate-rules
python -m placetype_ph.specialized export-rule-worklist `
  --output reference\placetype_ph\rule_review_worklist.csv
python -m placetype_ph.specialized migrate-proposals `
  --source C:\path\to\psic_rev5_reviewed.csv `
  --existing reference\crosswalks\joint.csv `
  --output reference\placetype_ph\external_review_worklist.csv
python -m placetype_ph.specialized enrich `
  --places C:\path\to\openplaces.parquet `
  --classified C:\path\to\classified_pois.parquet `
  --registry reference\placetype_ph\my_registry.csv `
  --output C:\path\to\classified_pois_enriched.parquet `
  --report C:\path\to\specialized_report.json
```

`--existing` is optional. Every migration emits a **review-required** worklist;
imported rules are never silently promoted into authoritative source mappings.
The separate `enrich` command is useful for a registry-backed audit; default
pipeline enrichment does not use private or external registry files.

## Optional industry candidate projection

`placetype_ph.specialized.projection.IndustryReferences` accepts four separately
supplied, validated CSV references:

- version-to-version activity bridge (`rev5_code`, `psic2019_codes`, `basis`);
- activity-to-industry concordance (`psic_code`, `io16_codes`, `io80_codes`, `io240_codes`);
- class/subclass refinement (`rev5_code`, `rev5_title`, `io240_codes`, `basis`);
- exactly 240 fine-industry parent entries (`io240_code`, `io80_code`).

It returns candidate sets, provisional status and blocked-refinement diagnostics.
It does **not** assign residual sectors or infer that a place belongs to a particular
industry solely because the analytical sector contains an "Other" code.
This optional API is deliberately not a default classification pipeline stage.

## Source/reference safety

Bundled rules contain no institutional registries and no external institution
records. Registry data must have independently verified provenance and appropriate
redistribution permission. The empty registry template is a schema, not evidence.
Never publish user-supplied or third-party restricted registry snapshots without
source and licensing checks.

## Known limits and next steps

- The exact target repository version has not been independently inspected in this
  build environment; installation therefore demands its expected interface anchors.
- Automatic overwrite of existing classification codes is **not included**. After
  adjudicating candidate mappings, use the project's existing reviewed-crosswalk
  workflow. A later code-changing integration requires a verified target checkout.
- The three-taxonomy joint recheck is preserved but not reimplemented here.
- Official-ID identity still requires verifying site/branch semantics; the generic
  CSV adapter is intentionally more restrictive than a national registry connector.
- Rule catalog proposals need a reviewed sample measuring precision and false
  positives, not merely a coverage increase.
