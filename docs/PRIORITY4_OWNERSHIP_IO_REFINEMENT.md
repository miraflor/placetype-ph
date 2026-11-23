# Priority 4 — Verified ownership-aware I/O candidate refinement

## Scope

Adds a **downstream, additive refinement** of the built-in `IOCrosswalk` candidate
sets after Priority 3c institutional ownership resolution, without a new
classification engine or changing PSIC, PCPC, PSCC, IO80 name resolution, or the
base I/O sets. No extra environment variable is needed: when
`PLACETYPE_PH_INSTITUTION_REFERENCES` is unset, Priority 3c returns unchanged
and this stage is exactly a no-op.

The existing `src/placetype_ph/specialized/projection.py` is an *optional*,
externally supplied refinement catalog. It is not a replacement for the bundled
`IOCrosswalk`, and this change neither imports nor changes it.

## Evidence rule

Only `institution_resolution_status=RESOLVED` with ownership `public`/`private`,
a `VERIFIED_LINK` or `EXACT_EXTERNAL_ID` evidence status, and nonempty verified
reference and basis qualify. Furthermore:

* The primary PSIC classification must have `psic_status=SINGLE`.
* The built-in crosswalk must have `MAPPED_GROUP` or `MAPPED_DIVISION`, an
  accepted bridge basis (`reviewed` or `code_title_match`), and a matching
  PSIC division 85 (education) or 86 (human health) in both the authoritative
  PSIC code and mapped ancestor.
* Both the original IO80 and IO240 statuses must be `AMBIGUOUS_SET`, with no
  existing IO80/IO240 map code and **exactly** the appropriate public/private
  two-element code pair. The IO16 result must be the expected singleton.
* The bundled PSIC 2019/PSA 2018 concordance is checked at runtime for both
  numeric codes and public/private labels. Reference drift stops execution.
* A provisional bridge, partial candidate set, conflicting code, extra industry,
  incompatible activity, missing provenance, or uncertain institutional evidence
  produces abstention or a schema error, never a forced leaf.

### Reference-backed pairs

| Domain | PSIC2019 division | Public IO80 / IO240 | Private IO80 / IO240 | IO16 |
|---|---|---|---|---|
| Education | 85 | 74 / 229 | 75 / 230 | 14 |
| Human health | 86 | 76 / 231 | 77 / 232 | 15 |

These are **checked against the packaged concordance**, not accepted solely
because they appear in code. They are not applicable to residential care,
social work, section-only or other broad/ambiguous economic activity.

## Output

Original `io16_codes`, `io80_codes`, `io240_codes`, `io80_map_code`,
`io240_map_code`, and every other preexisting field remain unchanged. When
Priority 3c is enabled, the following nine *new* fields are appended:

- `io_ownership_refinement_status`: `REFINED` or `ABSTAIN`
- `io_ownership_refinement_reason`: exact policy gate or confirmation reason
- `io_ownership_refinement_basis`: verified institutional basis when refined
- `io_ownership_refinement_reference`: source reference when refined
- `io16_refined_codes`: unchanged original IO16 candidate set
- `io80_refined_codes`: selected singleton only on eligible rows; otherwise original set
- `io240_refined_codes`: selected singleton only on eligible rows; otherwise original set
- `io80_refined_map_code`, `io240_refined_map_code`: narrowed singleton code or existing original field

`run.json` gains `io_mapping.ownership_refinement` only when enrichment is
active. It contains the policy identifier, per-status counts, refined row count,
and reference validation mode. Existing `io_mapping` concordance/bridge SHA-256
fingerprints remain the authoritative provenance of the numeric mappings.

## No-op semantics and safety

- Feature disabled: returns the *same DataFrame object*, adds no fields, and
  does not change the run metadata.
- Feature enabled: preserves all original columns and row order, rejects
  duplicate canonical IDs, incomplete schema, malformed candidate sets, and
  attempts to overwrite the nine new fields.
- It does not access live registries, apply name heuristics, overwrite base
  I/O classifications, or change the previously audited `io80-v4` policy.
- This layer is a **candidate refinement**, not proof of an establishment's
  true input-output production technology or a new empirically validated mapping.

## Verification

Run Ruff on the entire checkout, all pytest tests, and the specialized
`validate-rules` command. Confirm unchanged fingerprint
`c90516ce39f4ffaa126b6e2344a49e074bcc772be75a196b7d5399a1a91ef22c`.
Install only on a clean feature branch based exactly on the audited merged
`main` commit. A failed installer must restore the original pipeline bytes
and remove newly created files; never commit or push after failure.

## Known limitation

The preexisting `classify_openplaces` function writes
`entity_classifications.parquet` before institutional/reference validation.
This Priority 4 change does **not** retrofit full run-directory atomicity.
That operational hardening should be addressed separately rather than
represented as already resolved by this narrowly scoped projection.
