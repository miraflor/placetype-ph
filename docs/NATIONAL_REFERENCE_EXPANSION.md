# Additional national institutional references

The bundled registry is enabled by default. Four national datasets are installed with the package: `hospitals`, `heida`, `unifast`, and `beis`. Custom configurations can still use explicit official records, verified links or legacy level-specific hospital files. No network access occurs during classification.

## CHED HEIDA

CSV header: `source_id,name,hei_type,ownership,address,source_url,as_of`. Source: https://heida.ched.gov.ph/hei-directory . The recovered November 4, 2026 snapshot has 2,424 eligible entries (664 public and 1,760 private), from a 2,745-row directory. The 321 entries with unsupported types or unreliable identifiers are excluded from the package. Preserve string IDs including leading zeros and campus suffixes. Only explicit reviewed HEI types and unique non-placeholder codes are accepted. No municipality is inferred from an address. HEI codes identify an institution, not independently verified canonical sites; even an exact HEI code alone cannot promote ownership. Independently verified canonical links remain eligible under existing guards. Overlapping names from different sources are reviewed, not treated as identical sites. Redistribution permission is not independently established; the repository owner directed inclusion. Source terms remain applicable.

## PhilHealth Levels 1, 2 and 3

Use the September 30, 2026 accredited hospital list: https://www.philhealth.gov.ph/partners/providers/facilities/accredited/HOSP_093026.pdf . Official totals: Level 1, 781 (326 public, 455 private); Level 2, 432 (79 public, 353 private); Level 3, 125 (63 public, 62 private). All three levels share `philhealth_hospitals_20260930.csv` with a `facility_level` column. `hospital_rows` builds a unified reference from an official transcription; the old Level 3 builder remains available for custom references. The manifest preserves the source PDF hash and page mapping for every original publication row. Infirmaries and dispensaries are excluded. Publication row IDs do not identify a site independently. Retain source URL, snapshot date, row identifier and explicit G/P sector. Never infer ownership or municipality from names. See https://www.philhealth.gov.ph/partners/providers/facilities/status.php for source totals.

No configuration or download is required for a normal run. Set `PLACETYPE_PH_INSTITUTION_REFERENCES` only to override the bundled selection with an absolute configuration path, or set it to `off` to disable this stage explicitly. Express fingerprints the selected manifest/configuration and all active files. These data sources expand coverage; they do not guarantee a resolved I-O code for every institution.

See [national registry site linkage](source/institutions.md) for the policy-scoped matching rules. Strict retains site matches as candidates; evidence-weighted and extended may resolve them.
