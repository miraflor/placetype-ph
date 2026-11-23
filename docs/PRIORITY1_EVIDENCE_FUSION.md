# Specialized source evidence and conservative fusion — Priority 1

This extension adds source-aware activity proposals and conflict detection to the
existing *additive* specialized evidence layer. It does **not** change any PSIC,
PCPC, or PSCC decisions, the 178-row activity-rule catalogue, or reviewed crosswalks.

## Evidence semantics

- OSM multi-value `key=value` categories are parsed into individual assertions.
  Auxiliary tags do not become activity rules merely because they exist.
- Overture `primary`, `alternate`, and `basic` category fields are represented
  independently in the audit, but all share the **same** dependency group.
  Repeated categories across combined/structured fields do not multiply votes.
  Basic categories can generate broad, never precise, proposals.
- Foursquare categories remain source-typed. This release does not invent any
  FSQ-specific activity mappings; these require reviewed crosswalk coverage.
- Contradictory precise and broad codes in one dependent source group produce
  `REVIEW`, rather than choosing a preferred tag or the longest code.
- Independent sources must have compatible hierarchical candidates or abstain.
  When an official ancestry callback is supplied, independent sibling evidence
  can produce a common-ancestor **candidate** (`BACKOFF_CANDIDATE`).
- Without an authoritative hierarchy callback, only numeric prefix nesting is
  used provisionally. Section letters are **not** related to numeric codes
  through guessed prefixes; uncertain ancestry produces `REVIEW`.
- Non-activity or uncodeable evidence concurrent with a positive activity
  proposal produces `REVIEW` even if the positive proposal is broad.
- Specific administrative-function evidence cannot clear an existing review.

## Optional authoritative hierarchy

Pure assessments accept the existing taxonomy's ancestry accessor:

```python
assessment = assess(row, ancestors=official_psic_taxonomy.ancestors)
```

The callback accepts a code and returns its ancestor codes, not including the
code itself. It must be from the verified, matching PSIC revision. Exceptions
or invalid taxonomy data fail rather than invent a result. The default pipeline
still produces conservative candidate evidence without loading an external
PSIC hierarchy. A later, separately reviewed integration can supply the
canonical taxonomy to the whole batch path.

## No promotion

All outputs remain under existing `specialized_*` audit fields. Results such
as `SUPPORTING_CANDIDATE`, `BROAD_CANDIDATE`, `BACKOFF_CANDIDATE`, and `REVIEW`
are not authoritative PSIC codes. Original reviewed codes, PCPC/PSCC results,
entity counts, and row order are retained.

## Validation

The fail-fast installer verifies a pinned feature commit, a clean checkout,
original function ASTs, its own payload hashes, all required tooling, the
packaged rule fingerprint, Ruff, full pytest, and that only expected files
changed. It rolls back the source and created files on any post-write failure.
Commit, push, and merge remain manual and require successful CI checks.
