# Priority 3b — Verified institutional ownership post-classification stage

This is an **native PlaceType PH post-classification integration, enabled by default** of Priority 3 institutional identity decisions. The stage adds resolved institutional ownership fields for independently reviewed links, qualified official site IDs, and narrowly qualified national registry site matches. It does not alter PSIC, PCPC, PSCC, reviewed crosswalks, candidate sets, or any original classification column. It does not use any other project's code at runtime.

## Eligibility and unchanged semantics

Only `VERIFIED_LINK` and `EXACT_EXTERNAL_ID` (all policies), or `REGISTRY_SITE_MATCH` (evidence-weighted and extended only) decisions with `promotion_eligible=True` may populate `institution_resolved_ownership`. All require compatible PSIC education/health activity, no blocked/uncertain activity status, source-domain eligibility, no subunit or incompatible source categories, and no conflict with existing ownership evidence. A genuine external facility ID also requires the Priority 3 resolver's site, name, locality, official code, provenance, and ownership checks. Verified canonical links must have been independently reviewed, with provenance.

Name-only matches, BEIS-format records with unverified provenance, UniFAST local digests, PhilHealth PDF publication rows, OSM/Overture ownership claims by themselves, operator names, and conflicting signals **cannot produce a verified owner**. They remain candidate or review evidence in separate fields. The `institution_resolution_status=RESOLVED` value describes the new institutional ownership attribute only—not PSIC, PCPC, PSCC, IO80, IO240, or IO16.

This deliberately does **not** use a less strict name/ownership hint to choose an industry or silently clear an existing stronger classification. The optional source-ownership promotion policy and any IO-specific pairing rules require explicit separate review of the current target pipeline and mapping.

## CLI

Inputs must be the actual classified POI output and the original source-record table, both carrying unique `canonical_id` with matching entity sets. The independent classified table is authoritative for `psic_code` and `psic_status`. Sources supply names, categories, locality, and registered IDs. The main pipeline uses four validated bundled references by default. The standalone diagnostic CLI below takes explicit local files; no network access is used.

```powershell
python -m placetype_ph.specialized.institutional_promotion `
  --classified "$HOME\Downloads\classified_pois.parquet" `
  --places "$HOME\Downloads\openplaces_pois.parquet" `
  --hospitals "$HOME\Downloads\institution_national_v1.csv" `
  --verified-links "$HOME\Downloads\verified_institution_links.json" `
  --output-dir "$HOME\Downloads\institution_resolution_v1" `
  --dry-run
if ($LASTEXITCODE -ne 0) { throw "Institution resolution validation failed." }
```

Remove `--dry-run` to create `classified_with_institutions.parquet` and `institution_resolution_summary.json` in a **new** directory outside the checkout. Omit reference inputs not available, but supply at least one reference. CSV inputs produce CSV outputs; Parquet inputs produce Parquet outputs. The output directory must not exist and its parent directory must exist. A failed output operation leaves no partially published result.

## New fields

| Field | Description |
|---|---|
| `institution_resolution_status` | `RESOLVED`, `REVIEW`, `ABSTAIN`, `PROVISIONAL`, `EXPLICIT_SOURCE_CLAIM`, `NO_EVIDENCE`, or another original evidence status |
| `institution_resolved_ownership` | `public`/`private` **only** for eligible source-backed identity evidence |
| `institution_resolution_basis` | Official-ID, verified-link or registry-site evidence basis, otherwise advisory basis |
| `institution_resolution_reference` | Registry source identifiers, preserving original provenance |
| `institution_resolution_reason` | Explanation for promotion, review, or abstention |
| `institution_candidate_ownership` | Candidate owner, never conflated with verified ownership |
| `institution_evidence_status` | Original Priority 3 resolver outcome |

`canonical_id` count/order, all existing columns and dtypes in memory are preserved. No output is written to the existing source files. The complete output is another classified table with these seven additive fields.

## Limitations and next parity checkpoint

The main `placetype classify` and `placetype express` workflows now invoke institutional enrichment by default. Set `PLACETYPE_PH_INSTITUTION_REFERENCES=off` to disable it. The standalone command remains available for diagnostic use.

See [national registry site linkage](source/institutions.md) for the policy-scoped matching rules. Strict retains site matches as candidates; evidence-weighted and extended may resolve them.
