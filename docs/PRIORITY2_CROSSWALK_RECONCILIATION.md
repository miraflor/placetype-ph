# Priority 2 — Reviewed crosswalk reconciliation

This package adds a **candidate-only**, **read-only** reconciliation tool for Philippine Standard Industrial Classification (PSIC) Rev. 5 source mappings. It compares an outside candidate catalogue against existing local source crosswalks and the installed specialized activity-rule catalogue. It does **not** import or approve rules, change source crosswalks, alter PSIC/PCPC/PSCC classifications, or infer a review decision from the name of any file.

## Scope and authority

1. An imported rule is always `UNREVIEWED`, even if its original file says it was reviewed elsewhere. Only PlaceType PH's own review process may approve it.
2. Local mapping rows are considered reviewed only when they explicitly say `review_status=REVIEWED` or `APPROVED`. `ACCEPTED` is tracked separately, and absent/unknown review status remains unverified.
3. `EXACT`, `SUBTREE`, `UNION`, `NOT_ACTIVITY`, and `UNCODEABLE` are distinct mapping types. A code match with a different mapping type is **not** treated as equivalent.
4. A regex mapping is not equated to an exact literal, even if the regex appears similar. No unreviewed regex is executed against establishments.
5. Code sets are compared without regard to order. Candidate narrowing/widening is determined **only** against an explicit, verified PSIC Rev. 5 `code,parent_code` taxonomy supplied by the user; numeric-prefix guessing is not accepted as proof.
6. Rule parsing rejects malformed CSV, duplicate incoming source keys, invalid regex syntax, duplicate codes, unrecognized nonblank mapping types, and incompatible coded/terminal mappings. The local master crosswalk may contain blank mapping and code cells representing *unreviewed placeholders*: these remain indexed and are never treated as approved. Partly blank mappings and incoming blank kinds are rejected. No report is published when input parsing fails.
7. The installed 178-rule activity catalogue is compared separately as *suggestion coverage*, not as a source of reviewed authority. It is not modified.

## Supported input schemas

**External candidate CSV:** `source,source_value,mapping_kind,codes`, with optional `source_field` (default `category`), `match_type` (`exact` or `regex`, default `exact`), `scheme` (default `psic`), `version` (default `rev5`), and `notes`.

**Existing local CSV:** the same core source-rule columns. It may also contain `scheme`, `version`, and `review_status`. For joint crosswalks, non-PSIC or non-Rev. 5 rows are excluded **before** parsing PSIC-specific mapping types. An existing row with both `mapping_kind` and `codes` empty is retained as an `UNREVIEWED` placeholder (not silently dropped); an explicit `REVIEWED`, `APPROVED`, or `ACCEPTED` marker on a blank mapping is inconsistent and fails closed. Unknown nonblank kinds or matching modes fail with their exact values and line numbers.

**Taxonomy CSV, optional but strongly recommended:** `code,parent_code`. The file must contain a complete acyclic parent relation for every included node, retain string identifiers, and correspond to the exact PSIC revision being reconciled. This tool does not fetch or attest to the official taxonomy; operators must verify the source. If omitted, coded mappings are marked `TAXONOMY_NOT_SUPPLIED` and remain review-required even when their local mapping appears identical.

Code sets may be written as a semicolon-separated list (`641;642`), a pipe-separated list (`641|642`), or a JSON array of code strings (`["641","642"]`). Do not silently split on commas.

`source_value` comparisons use casefolded whitespace-normalized keys for exact rules; regex patterns are kept literally. Only the same source, field, match type, and normalized value are directly comparable.

## Run a read-only audit

First locate and inspect the actual existing local PSIC reviewed crosswalk file(s). **Do not pass a placeholder or unrelated crosswalk.** Your current repository layout determines the correct paths.

In the project Conda environment, from the repository root:

```powershell
python -m placetype_ph.specialized.crosswalk_reconcile `
  --incoming "$HOME\Downloads\placetype_ph_review_candidates_20261120.csv" `
  --existing "C:\path\to\existing\reviewed_psic.csv" `
  --taxonomy-nodes "C:\path\to\verified\psic_rev5_nodes.csv" `
  --output-dir "$HOME\Downloads\placetype_ph_priority2_audit_01" `
  --dry-run
if ($LASTEXITCODE -ne 0) { throw "Crosswalk dry run failed" }
```

Remove `--dry-run` to publish results once the inputs and summary are satisfactory. **Output directory must not exist.** The tool writes exactly these three files to a newly created directory: `reconciliation.csv`, `review_worklist.csv`, `summary.json`. Failures during report preparation remove pending files. A subsequent run never overwrites the previous report.

The review worklist deliberately leaves authoritative `mapping_kind` and `codes` **blank**;
source proposals appear only in `proposed_mapping_kind` and `proposed_codes`.
This prevents the worklist from being mistaken for an already-approved crosswalk.

The `summary.json` contains source hashes, taxonomy presence/hash, counts by reconciliation status, counts by validation status, counts comparing incoming rules with specialized rules, and an explicit `automatic_promotion: false` marker.

## Interpreting results

- `MATCH_REVIEWED`: identical mapping kind and code set, same source/field/matching semantics, and explicit local reviewed/approved status. Only clears manual review when the taxonomy validates coded identifiers (terminal mappings need no code validation).
- `MATCH_ACCEPTED` and `MATCH_LOCAL_UNVERIFIED`: mapping matches, but review authority is incomplete. Still flagged for review.
- `LOCAL_UNREVIEWED_MAPPING`: a local source category exists but has **no approved mapping yet**. This is distinct from `ABSENT_LOCAL_MAPPING` and always requires review. Its existing authoritative fields stay blank in the exported worklist.
- `ABSENT_LOCAL_MAPPING`: no local rule under that exact source/field/match-type/value identity. It is *not* evidence that the proposed rule is correct.
- `CONFLICT_WITH_REVIEWED`: imported candidate differs from explicitly reviewed local mapping. The local mapping is never rewritten.
- `CONFLICT_WITH_ACCEPTED`, `CONFLICT_WITH_LOCAL_UNVERIFIED`, `MULTIPLE_LOCAL_MATCHES`: require human adjudication, not automatic selection.
- `MATCH_TYPE_DISAGREEMENT`: related literal source key has a different matching mode; comparison cannot establish equivalence.
- `INCOMING_NARROWER`, `INCOMING_BROADER`: hierarchy-based relationship given a supplied taxonomy. This is a diagnostic, not approval.
- `DIFFERENT_MAPPING_KIND`: different meaning of exact/subtree/union/terminal mapping, even when code strings match.
- `SPECIALIZED_SAME_CODE` compares **code identifiers only**: it does not imply the `EXACT`, `SUBTREE`, and `UNION` mapping scopes are identical to an advisory activity rule. This is never local review approval.
- `SPECIALIZED_INCOMING_WITHIN_ROOT` and `SPECIALIZED_ROOT_WITHIN_INCOMING` are verified ancestor relations of code roots, not semantic equivalence; `SPECIALIZED_PARTIAL_HIERARCHY_OVERLAP` and `SPECIALIZED_OVERLAPPING_ROOTS` remain review-only.
- `SPECIALIZED_TERMINAL_CODE_CONFLICT` detects non-activity/uncodeable proposals conflicting with positive specialized activity. `SPECIALIZED_DIFFERENT_BRANCHES` requires review, while `SPECIALIZED_HIERARCHY_UNVERIFIED` indicates no authoritative hierarchy was supplied.
- `SPECIALIZED_CODE_NOT_IN_TAXONOMY` flags specialist references outside the supplied node set; `SPECIALIZED_ABSENT` and `SPECIALIZED_NOT_COMPARABLE_REGEX` remain distinct.
- Comparison of category proposals against name-pattern rules is **not** evidence that those expressions are empirically safe. Actual name matches require false-positive evaluation and explicit human adjudication.

## Known boundaries

- The tool does not infer official review status from the external catalogue or the filename of a local file.
- It does not automatically discover the authoritative local crosswalk. Selecting the correct existing files is a required operator responsibility.
- It compares source rules, not row-level joint-classification worklists or case-by-case human adjudications. A separate reviewed workflow remains responsible for approval.
- It does not silently migrate or overwrite any PSIC, PCPC, PSCC, or activity-rule data.
- When taxonomy inputs are absent or unverified, it does not claim that a candidate's code exists or that one code is a child of another.
- This is an evidence reconciliation tool, not an empirical accuracy or coverage benchmark.

## Installation and verification

The guarded installer operates only on a clean feature branch with the expected PlaceType PH package, previously installed Priority 1, and the unchanged activity-rule catalogue. It performs baseline Ruff and pytest checks before writing; adds the reconciliation module, regression tests, and this document; then runs Ruff and the full pytest suite. It validates the complete Git status and rolls back on any error. It never stages, commits, pushes, merges, publishes classifications, or contacts remote services.

## v1.2 schema compatibility repair

The v1.1 reconciler incorrectly rejected wholly blank local mappings on the first row it encountered. The v1.2 repair preserves those rows as unreviewed placeholders and reports other unsupported field values precisely. The repair updates only the three previously installed, untracked Priority 2 files. It validates the v1.1 baseline using same-interpreter AST comparison for code (insensitive to Ruff formatting) and exact normalized documentation bytes, runs the full test suite before and after application, and rolls back byte-for-byte on any failed check. The existing review catalogues are never written.

## v1.3 taxonomy-aware advisory comparison

This patch fixes two diagnostic limitations without changing a single mapping or
classification decision:

1. Specialized-rule code roots are compared using **actual PSIC Rev. 5 ancestry**
   when `--taxonomy-nodes` is supplied. A descendant code no longer appears
   incorrectly as disjoint merely because its identifier is a different string.
   Without a taxonomy the relationship remains unverified; no string-prefix guess
   is substituted. The `specialized_comparison_counts` summary therefore changes.
2. Combining source-specific and wildcard specialist matches now copies records
   instead of mutating the shared rule index. Results must be independent of
   candidate iteration order.

Established quality requirements remain in force: broad retail and accommodation
roots may straddle distinct economic sectors; finer code membership does not
establish the correct establishment activity; category/name regex patterns require
frequency-weighted audits and negative fixtures; ambiguous categories stay
unreviewed; and any future product, commodity, or economic-sector projection
must remain separate from primary PSIC classification.

This tool is a **rule-inventory audit**, not an empirically calibrated classifier.
The presence of a candidate in the source catalogue does not prove category
precision or recall. Production crosswalk approval is deliberately out of scope.
