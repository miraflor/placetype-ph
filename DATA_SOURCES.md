# Reference data sources

The project is designed around versioned Philippine Statistics Authority classifications.

## PSIC Revision 5

Official detailed-structure workbook used by `placetype taxonomy fetch psic`:

https://psa.gov.ph/sites/default/files/scd/PSIC_Revision_5_Detailed_Structure_30July2026.xlsx

Official Board Resolution annex with the Revision 5 detailed structure and explanatory notes:

https://psa.gov.ph/system/files/psa-board/Annex-BR-09-20260521-01.pdf

Classification browser:

https://psa.gov.ph/classification/psic

The browser currently lags the Revision 5 annex in some section labels. Import validation uses the
official Revision 5 source structure, not the stale browser rendering.

## PSA 2018 I-O concordance and PSIC version bridge

`src/placetype_ph/data/psic_2019_to_io_2018.csv` is a derived analytical concordance from PSIC 2019 (official title: 2019 Updates to the 2009 PSIC) to the industries of the PSA 2018 input-output (I-O) tables at the 16-, 80-, and 240-industry levels. It covers every PSIC 2019 section, division, and group.

This is **not an official PSA PSIC-to-I-O correspondence table**. One PSIC category can correspond to several I-O industries, so the package keeps I-O codes as sets instead of choosing one industry. Where no single IO240 line could be identified, a row lists every IO240 industry inside its IO80 industry, with confidence `Low`.

PSA defined the 2018 I-O sectors on the 2019 PSIC, while PlaceType PH classifies against PSIC Revision 5. `src/placetype_ph/data/psic_rev5_to_psic_2019.csv` bridges the two versions. It has one row for every Revision 5 section, division, and group, lists the PSIC 2019 code or codes that hold the same activities, and records:

- `relation`: `same` (same code and title), `recoded` (same activities under another code, for example the section letters from J onward), `renamed` (same code, changed title, activities treated as unchanged), `changed_scope` (the listed PSIC 2019 codes together cover the activities, so the I-O set can be wider than the Revision 5 scope), `new` (no PSIC 2019 category of its own; the listed codes held the activities), or `no_counterpart` (review found no defensible PSIC 2019 bridge; `psic2019_codes` is blank).
- `basis`: `code_title_match` (generated where code and normalized title agree), `draft` (written by hand and not yet reviewed), or `reviewed`. A `draft` row remains usable as a candidate bridge. Its result is reported as `PROVISIONAL_*` unless the deepest accepted ancestor row is an ordinary mapping (not `no_counterpart`) with the same I-O sets, in which case the accepted mapping is reported.

A Revision 5 code maps through its deepest section, division, or group ancestor that has a bridge row whose Revision 5 title still matches the loaded taxonomy. Its I-O sets are the union of the concordance rows of the listed PSIC 2019 codes. A `code_title_match` or `reviewed` bridge produces an ordinary `MAPPED_*` status; a `draft` bridge exposes the same candidate sets with a `PROVISIONAL_*` status (or `PROVISIONAL_NO_IO_CORRESPONDENCE` when the candidate has a historical counterpart but no I-O line). A `no_counterpart` row instead reports `NO_PSIC2019_COUNTERPART` when reviewed or `PROVISIONAL_NO_PSIC2019_COUNTERPART` while draft. If the taxonomy tree changes, rows whose title no longer matches stop applying, and `io_mapping_status` records the coarser level used or `UNMAPPED_NO_BRIDGE`.

Generated `code_title_match` rows assume that an unchanged code and title mean an unchanged scope. Division 55 and section I are exceptions: Revision 5 added intermediation for accommodation (group 554), which was part of PSIC 2019 group 799, so both rows are hand-written `changed_scope` rows that also list 799. A test requires the PSIC 2019 codes of every mapped bridge row to lie inside those of its nearest ancestor row that lists codes; a `no_counterpart` row lists none, so its children are checked against the next ancestor up. An empty target set means 'no defensible bridge', not 'the activity did not exist'. A reviewed scope audit can promote a generated row by changing its basis to `reviewed` without changing its relation.

`MAPPED_*` is an operational status, not a claim that PSA published or independently verified the PSIC-to-I-O correspondence. A `code_title_match` bridge is generated evidence and remains distinguishable from a human-reviewed bridge through `io_mapping_bridge_basis`; `draft` remains provisional. Likewise, a single I-O code means that this derived concordance currently has one candidate at that resolution, not that the code has been externally validated. The per-resolution confidence fields describe the concordance evidence and must be considered separately from bridge basis.

### Correction of PSIC 2019 division 24 and groups 242 and 243 (November 2026)

In the official PSA 2018 Input-Output Industry Classification, IO240 124 (casting/foundry of iron and steel), 125 (basic precious and non-ferrous metals) and 126 (non-ferrous metal casting) belong to IO80 42 (fabricated metal products, except machinery and equipment), not to IO80 41 (basic metals), although their names suggest 41. The concordance now maps division 24 to IO80 41 and 42, and groups 242 and 243 to IO80 42. This assignment should be checked against the published 2018 industry classification and aggregation tables.

### Official I-O hierarchy

`src/placetype_ph/data/io_hierarchy_2018.csv` lists every IO240 industry with its IO80 and IO16 parents. This is a derived analytical reference, not a verbatim PSA release; review the parent basis and notes before distribution. PlaceType PH uses the file to check that every concordance row nests (each IO240 code's parent is in the row's IO80 set, and each IO80 code's parent in its IO16 set) and to derive parent codes for resolved and explicit I-O codes.

### Draft subclass narrowing

`src/placetype_ph/data/psic_rev5_io240_refinement.csv` (280 draft rows) narrows the IO240 set of a Revision 5 class or subclass inside its group's set. Every row has basis `draft`. A row applies only when its Revision 5 title matches the loaded taxonomy and its narrowed set lies inside the group's sets; its result is written only to the `io*_subgroup_codes` columns.

### Direct tag rules

`src/placetype_ph/data/direct_io_rules.csv` holds 14 curated analytical rules, one row per tag term. Each rule names an IO80 industry for explicit OSM tags (for example `amenity=bank` → 66, banking institutions). These are analytical rules, not PSA correspondences. They are used only as direct evidence next to the PSIC-based sets, as described in the README section on resolved codes.

PSA sources:

- 2018 I-O release: https://psa.gov.ph/content/psa-releases-2018-input-output-tables
- Technical notes, which state that the I-O sectors are based on the 2019 PSIC: https://psa.gov.ph/statistics/supply-and-use-input-output/technical-notes
- 2018 Input-Output Industry Classification workbook (16, 80, and 240 industries), on the statistical tables page: https://psa.gov.ph/statistics/supply-and-use-input-output/tables

## PCPC 2002

Classification browser:

https://psa.gov.ph/classification/pcpc

API documentation:

https://psa.gov.ph/classifications-api/pcpc

## PSCC 2022

Official workbook used by `placetype taxonomy fetch pscc`:

https://psa.gov.ph/system/files?file=scd/2022%20PSCC_11292023.xlsx

Classification browser:

https://psa.gov.ph/classification/pscc

API documentation:

https://psa.gov.ph/classifications-api/pscc

## Institution registry snapshots

`src/placetype_ph/data/registry/` holds bundled snapshots for the default institution stage; `manifest.json` records the hash, row count, source address, identifier meaning and ownership basis of each file (see [institution registry data and tools](docs/INSTITUTION_REGISTRY_TOOLS.md)).

- `philhealth_hospitals_20260930.csv` (1,338 rows): PhilHealth accredited hospitals, Levels 1–3, as of 2026-09-30, https://www.philhealth.gov.ph/partners/providers/facilities/accredited/HOSP_093026.pdf. The manifest preserves the PDF hash, row and page provenance, level and ownership counts. Publication row IDs are not facility codes.
- `unifast_hei_ra10931_20261104.csv.gz` (1,955 rows): UniFAST list of higher education institutions participating under RA 10931, https://unifast.gov.ph/hei-list.html, captured 2026-11-04. Participating institutions only; the record key is a local digest, not an official HEI code; location is the region only.
- `deped_schools_national.csv.gz` (30,364 rows): a cleaned extract prepared on 2026-11-04 from [Masterlist of DepEd Schools](https://www.kaggle.com/datasets/joshuagilodlsu/masterlist-of-deped-schools) by Kaggle uploader `joshuagilodlsu` (`schools_town_psgc_clean.csv`, Windows-1252 converted to UTF-8, 30,364 unique school IDs; `school_id`, `school_name`, `town_psgc`). The Kaggle listing (text supplied by the repository owner on 2026-11-25) declares MIT and acknowledges BEIS data derived on 2026-01-30, scraped into DuckDB and exported to CSV. This is distinct from the project cleaning date. The exact downloaded version, its applicable notices and any necessary underlying source permissions remain unconfirmed. Ownership from the school ID prefix is a hint and is never promoted without a verified link.
- `ched_heida_eligible_20261104.csv` (2,424 rows): CHED HEIDA directory, https://heida.ched.gov.ph/hei-directory, captured 2026-11-04; 321 ambiguous or unsupported entries excluded. Official HEI codes do not independently verify canonical site identity. See the source-specific rights assessment below for CHED and PhilHealth.

## Licensing

Rights review: 2026-11-25. The MIT license applies to the project's own software, not a blanket relicensing of third-party data. The registry manifest records the source-specific findings and evidence URLs. This is a documented rights assessment, not an agency authorization or legal opinion.

### Philippine government and statistical data

[Republic Act No. 8293](https://lawphil.net/statutes/repacts/ra1997/ra_8293_1997.html), sections 171.11, 173, 175 and 176, distinguishes factual data, original compilations and qualifying works created by government personnel in their official duties. Government works generally do not carry copyright under section 176.1, but the provision requires agency approval for exploitation for profit. Publication by a government body does not extinguish third-party copyrights (section 176.3). These provisions are not a blanket license for every hosted file.

The [PSA terms of use](https://psa.gov.ph/terms-of-use) permit sharing and adaptation, including commercial use, under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), unless a source states otherwise. Attribute PSA, identify transformations, preserve relevant technical notes, and avoid suggesting endorsement. This applies to PSA-origin codes, names and other eligible material in the derived PSIC/I-O references. Check the exact downloaded workbook or publication for more specific terms; some PSA publications carry separate terms. The project's analytical correspondences are not official PSA concordances.

### National institutional snapshots

| Source | Rights evidence | Remaining conditions or questions |
| --- | --- | --- |
| PhilHealth | [Website disclaimer](https://www.philhealth.gov.ph/disclaimer/): educational/informative reproduction without written permission. | Respect stated use limits and any file-specific terms; not an unrestricted commercial grant. |
| UniFAST | [Source directory](https://unifast.gov.ph/hei-list.html): public-domain notice unless otherwise stated. | Check exceptions and the statutory government-work requirements where applicable. |
| CHED HEIDA | [Public directory](https://heida.ched.gov.ph/hei-directory); factual-data and government-work provisions of RA 8293. | No snapshot-specific license located; confirm with CHED for unrestricted redistribution and exploitation for profit. |
| DepEd via Kaggle | [Original listing](https://www.kaggle.com/datasets/joshuagilodlsu/masterlist-of-deped-schools); uploader `joshuagilodlsu`. | Uploader declares [MIT](https://www.mit.edu/~amini/LICENSE.md) and reports BEIS data derived on 2026-01-30 (listing text supplied by repository owner). Exact downloaded version, applicable copyright notices and underlying permissions remain unconfirmed. [DepEd's own school masterlist terms](https://www.deped.gov.ph/masterlist-of-elementary-schools/) require attribution, accuracy, same-license distribution of derivatives, and approval for modifications beyond editorial changes. They cannot be assumed to apply to the Kaggle copy. |

All four datasets remain unchanged. The registry manifest preserves their checksums, row counts, provenance, and source dates. Presence in the package does not establish permission for downstream commercial use. No endorsement by the source agencies is implied. No personal contact fields have been added in this update.

**Release clearance:** before further public distribution of the Kaggle-derived extract, confirm the downloaded version and retain its applicable MIT copyright and permission notices; establish any necessary underlying DepEd permissions, or replace it with an authorized national extract. Obtain source-agency confirmation where required, including for exploitation for profit. A pre-release designation does not resolve licensing questions. Existing publication of these files is not proof of clearance. Do not advertise all bundled data as MIT-licensed, CC0, or unconditionally commercially reusable.

## Published completeness checks used by PlaceType PH

For **PSIC Revision 5**, PlaceType PH checks 22 sections, 88 divisions, 493 classes and
1,338 subclasses. The published summary reports 260 groups; the July 2026 workbook
contains 261. The disputed group count is not a hard external gate.

For **PCPC 2002**, exact counts are not frozen in v0.1.2 because current PSA pages disagree on
the division/item totals. Diagnose the authenticated live API first and then freeze the observed
structure.

For **PSCC 2022**, the five hierarchy levels (chapter, heading, HS subheading, AHTN subheading,
commodity) are known, but exact category counts are not frozen in v0.1.2. Run
`placetype taxonomy diagnose pscc` against the current official workbook first; after a successful
live import, freeze the observed counts and a small source-structure fixture rather than guessing.

### Expanded national snapshots

- CHED HEIDA (2026-11-04): 2,424 eligible records recovered from the versioned national reference; 321 ambiguous or unsupported entries excluded from the package; original institution codes and addresses preserved. Source: https://heida.ched.gov.ph/hei-directory . No contact details added. The specific redistribution terms remain unverified; see the rights assessment above.
- PhilHealth (2026-09-30): 781 Level 1, 432 Level 2 and 125 Level 3 hospital records, consolidated into one 1,338-row runtime dataset. All 75 pages were processed, 596 infirmaries/dispensaries excluded; publication row IDs are not facility codes. The PDF hash and sector totals are recorded in `manifest.json`.

The repository's software license does not grant rights over third-party source data. Classification reads the four bundled national datasets by default, with no environment configuration or network access. Separate review-only and hospital source tables are not bundled.
