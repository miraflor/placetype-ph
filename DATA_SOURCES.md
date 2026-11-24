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

`src/placetype_ph/data/registry/` holds local snapshots for the opt-in institution stage; `manifest.json` records the hash, row count, source address, identifier meaning and ownership basis of each file (see [institution registry data and tools](docs/INSTITUTION_REGISTRY_TOOLS.md)).

- `level3_hospitals_20260930.csv` (125 rows) and its source table `philhealth_level3_source_20260930.csv`: Level 3 rows of the PhilHealth list of accredited hospitals as of 2026-09-30, https://www.philhealth.gov.ph/partners/providers/facilities/accredited/HOSP_093026.pdf (PDF SHA-256 `9ef34056f893a5aa485619e71d8481f02da149ce61a1ca1d75686a2f4c81c0b4`). The PDF row number is a publication row, not a facility code.
- `unifast_hei_ra10931_20261104.csv.gz` (1,955 rows): UniFAST list of higher education institutions participating under RA 10931, https://unifast.gov.ph/hei-list.html, captured 2026-11-04. Participating institutions only; the record key is a local digest, not an official HEI code; location is the region only.
- `deped_schools_national.csv.gz` (30,364 rows): a cleaned extract prepared on 2026-11-04 from a Kaggle-hosted DepEd school masterlist (`schools_town_psgc_clean.csv`, Windows-1252 converted to UTF-8, 30,364 unique school IDs; `school_id`, `school_name`, `town_psgc`). The exact Kaggle listing, uploader, original snapshot date and license remain unconfirmed. Retained at the repository owner’s direction; local cleaning does not establish redistribution rights. Ownership from the school ID prefix is a hint and is never promoted without a verified link.
- `official_records_quezon_city_seed.csv` (80 rows): Quezon City records from the PhilHealth hospital list as of 2025-05-31, the DepEd NCR directory of public schools, the Quezon City list of government-owned hospitals and CHED CMO No. 02 s. 2025.

## Licensing

PSA website pages state that website data/content are CC BY 4.0 unless otherwise stated. Check
any file-specific notice before redistributing a downloaded workbook or a normalized derivative.
The I-O industry codes and names in `psic_2019_to_io_2018.csv` follow the PSA 2018 input-output release; attribute them to the Philippine Statistics Authority (CC BY 4.0).
The code in this repository is separately licensed under MIT.

## Published completeness checks used by PlaceType PH

For **PSIC Revision 5**, PlaceType PH checks the published 22 / 88 / 260 / 493 / 1,338
section-to-subclass structure after normalization.

For **PCPC 2002**, exact counts are not frozen in v0.1.1 because current PSA pages disagree on
the division/item totals. Diagnose the authenticated live API first and then freeze the observed
structure.

For **PSCC 2022**, the five hierarchy levels (chapter, heading, HS subheading, AHTN subheading,
commodity) are known, but exact category counts are not frozen in v0.1.1. Run
`placetype taxonomy diagnose pscc` against the current official workbook first; after a successful
live import, freeze the observed counts and a small source-structure fixture rather than guessing.
