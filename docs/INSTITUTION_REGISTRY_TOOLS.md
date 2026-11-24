# Institution registry data and tools

This page covers the registry snapshots bundled with PlaceType PH and the commands that prepare, check and review institution references. All commands are in `python -m placetype_ph.specialized`. None of them changes a classification. The institution stage uses the bundled national references by default, with an optional custom configuration ([Priority 3c](PRIORITY3C_INSTITUTIONAL_PIPELINE.md)).

## Bundled snapshots

The snapshots are in `src/placetype_ph/data/registry/`. `manifest.json` records the SHA-256 hash, row count, source, source address, identifier meaning and ownership basis of each file.

| File | Reference type | Rows | Source | Identifier |
|---|---|---|---|---|
| `philhealth_hospitals_20260930.csv` | `hospitals` | 1,338 | PhilHealth Levels 1–3, 2026-09-30 | publication row, not facility code |
| `ched_heida_eligible_20261104.csv` | `heida` | 2,424 | CHED HEIDA directory | official HEI code; not site verification |
| `unifast_hei_ra10931_20261104.csv.gz` | `unifast` | 1,955 | UniFAST list of participating higher education institutions, captured 2026-11-04 | local digest of region and name, not an official HEI code |
| `deped_schools_national.csv.gz` | `beis` | 30,364 | project-cleaned extract of [Masterlist of DepEd Schools](https://www.kaggle.com/datasets/joshuagilodlsu/masterlist-of-deped-schools) by Kaggle uploader `joshuagilodlsu` (processed 2026-11-04) | DepEd school ID, treated as unverified; ownership from the ID prefix is a hint only |

The DepEd extract was prepared on 2026-11-04 from [Masterlist of DepEd Schools](https://www.kaggle.com/datasets/joshuagilodlsu/masterlist-of-deped-schools) by Kaggle uploader `joshuagilodlsu`, via `schools_town_psgc_clean.csv` (Windows-1252 converted to UTF-8; 30,364 unique IDs). The original Kaggle listing and uploader are identified; the source snapshot date, downloaded version and license remain unverified. The owner directed that it remain bundled; local cleaning does not establish redistribution rights. The institution stage is enabled by default.

## Optional: copy snapshots for a custom configuration

Normal classification requires no installation step.

```powershell
python -m placetype_ph.specialized verify-registry-data
if ($LASTEXITCODE -ne 0) { throw "Bundled registry data failed its check" }
python -m placetype_ph.specialized install-references --target "$HOME\placetype-references"
if ($LASTEXITCODE -ne 0) { throw "Reference installation failed" }
$env:PLACETYPE_PH_INSTITUTION_REFERENCES = "$HOME\placetype-references\institution_references.json"
```

`install-references` checks every bundled file against the manifest, copies the chosen types (`--include hospitals heida unifast beis`, all by default) and an optional `--verified-links` file, and writes `institution_references.json` with absolute paths. It refuses to overwrite existing files.

## Optional custom official records

No city-specific official-record dataset ships with the package.

Reference type `official_records` reads a twelve-column CSV: `source`, `source_id`, `name`, `domain`, `ownership`, `city`, `address`, `aliases`, `source_url`, `as_of`, `id_kind`, `evidence_type`. Each source decides which official HTTPS hosts, domain, side, identifier kind and evidence type are allowed:

| Source | Hosts | Domain | Side | Identifier | Evidence type |
|---|---|---|---|---|---|
| `doh_nhfr` | nhfr.doh.gov.ph, hfsrb.doh.gov.ph | health | either | `official_code` | `explicit_sector` |
| `deped_masterlist` | deped.gov.ph, depedncr.com.ph (with or without www) | education | either | `official_code` | `explicit_sector` |
| `deped_public` | depedncr.com.ph | education | public | `official_code` | `official_public_directory` |
| `ched_cmo` | ched.gov.ph, legacy.ched.gov.ph | education | private | `publication_row` | `official_private_list` |
| `philhealth_hospitals` | philhealth.gov.ph | health | either | `publication_row` | `explicit_sector` |
| `qc_government` | quezoncity.gov.ph | either | public | `publication_row` | `official_public_directory` |

A row without a city is refused. An `official_code` that equals the place's own external ID (`deped_school_id`, `nhfr_code`, `ref:deped=`, `ref:nhfr=`), with agreeing name and city, gives `EXACT_EXTERNAL_ID`, which can promote ownership. A name-and-city match stays `PROVISIONAL`. A DepEd school ID on a place is compared with all three DepEd lists.

## Import an official export

```powershell
python -m placetype_ph.specialized import-registry-export --input nhfr_export.xlsx --source doh_nhfr `
  --source-url "https://nhfr.doh.gov.ph/..." --as-of 2026-11-01 --output official_records.csv
```

DOH NHFR exports keep hospitals only (the facility type must contain "hospital"). DepEd masterlist exports need an explicit sector column (`Sector`, `School Type`, `Public/Private Classification`, `Ownership` or `Classification`). A row without an explicit public or private sector is left out, never guessed. `--city` keeps one city; without it all cities are kept. Duplicate identifiers stop the import.

## Build a unified hospital reference

```powershell
python -m placetype_ph.specialized build-hospitals --input philhealth_source.csv `
  --source philhealth_hospitals --source-url "https://www.philhealth.gov.ph/.../HOSP_093026.pdf" `
  --as-of 2026-09-30 --output philhealth_hospitals.csv
```

A PDF must first be transcribed to CSV with its original row numbers. Levels 1–3 are kept in one CSV, with an explicit `G` or `P` sector for every row. All three levels must be present unless `--allow-partial` is given. The legacy Level 3 command remains available for custom references. `--check` validates without writing.

## Convert a verified-link file in the registry layout

Some verified-link files record `registry_id` and `source_url` instead of `source` and `source_id`. `convert-links` converts such a file to the documented format `placetype-ph-verified-institution-links-v1`:

```powershell
python -m placetype_ph.specialized convert-links --input links.json --schema "<schema identifier in the file>" --output verified_links.json
```

The schema identifier written in the input file must be given, so that an unrelated file is not converted by mistake. The source family is taken from the official host of `source_url` (an unknown host is refused), and the output must pass the normal verified-link checks.

## Review queue

```powershell
python -m placetype_ph.specialized review-institutions --classified out\classified_pois.parquet `
  --places canonical_pois.parquet --references "$HOME\placetype-references\institution_references.json" `
  --output-dir review
```

For every place matched to a registry record, `institution_review_queue.csv` gives the registered fields, a relationship hypothesis (`SAME_LOCALITY_SITE_CANDIDATE`, `CROSS_LOCALITY_BRANCH_OR_AFFILIATE_CANDIDATE`, `INSTITUTIONAL_AFFILIATION_CANDIDATE`, `IDENTITY_CONFLICT_REVIEW`), the ownership opportunity compared with the resolved IO80 code, evidence checks (official ID, explicit source ownership, raw source names, locality, coordinates, branch or subunit words) and a priority (`HIGH_CONFLICT`, `HIGH_UNASSIGNED`, `BLOCKED_MISSING_PLACE`, `REVIEW`). `institution_review_by_reference.csv` groups the rows by registry record. `promotion_allowed` and `identity_verified` are always `false`: the queue is for people to review, and it never creates a verified link. Cells that start with `=`, `+`, `-` or `@` are prefixed with `'` so that spreadsheet programs do not run them as formulas.

## Add user-supplied places

```powershell
python -m placetype_ph.specialized augment-places --places canonical_pois.parquet --input my_places.csv `
  --source my_list --output canonical_pois_with_my_list.parquet
```

The user file needs `source_id`, `name`, `lon` and `lat`. A row with a `canonical_id` adds codes and columns to that existing place; any other row becomes a new place `user-<source>-<source_id>`. Optional columns: `explicit_psic_code`, `explicit_io16_code`, `explicit_io80_code`, `explicit_io240_code` (or `psic_code`, `io16`, `io80`, `io240`), source fields such as `osm_category`, and any other column, kept as `aug_<source>__<column>`. Places are never matched by distance or name; spatial reconciliation belongs to the canonical-layer build. A conflicting explicit code, a bad coordinate, a duplicate `source_id`, repeated target `canonical_id`, conflicting source evidence or a source label already present stops the command and nothing is written. Source fields on an existing place may fill an empty field or agree with existing evidence; they never overwrite conflicting evidence. The supplied ID, name and coordinates are retained in `aug_<source>__source_id`, `aug_<source>__name`, `aug_<source>__lon` and `aug_<source>__lat`; canonical coordinates are unchanged. Express fingerprints the selected manifest or custom config and all referenced file contents, including verified links.
