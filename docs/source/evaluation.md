# Validation, gold labels and accuracy

<p class="lede">Passing software tests confirms invariants and expected decisions on fixtures. It does not establish that a source-category rule is correct for the general Philippine establishment population.</p>

## Four different validation questions

| Question | Evidence required | Already covered? |
| --- | --- | --- |
| Does the code execute consistently? | CI and deterministic regression tests. | Yes, for tested paths and Python versions. |
| Does the taxonomy preserve its published hierarchy? | Official-source counts and import validation. | PSIC Rev. 5 has specific structural gates; PCPC/PSCC require source-specific checks. |
| Does the rule assign the correct category for a real place? | Independent adjudicated establishment labels and identity verification. | **Not established by CI alone.** |
| Does a PSIC or I–O singleton correspond to actual economic output? | Verified concordance and relevant external economic evidence. | **Not established by a mapping alone.** |

## Construct an independent gold set

Sample canonical entities across source combinations, rule types, classification depths, statuses (`SINGLE`, `INTERSECT`, `UNION`, `CONFLICT`, `UNCODEABLE`, `NOT_PSIC_ACTIVITY`), location, establishment size/category, and higher-risk entity-match indicators. Preserve a random component for population-rate estimation; oversampling difficult cases without weights is useful for diagnostics but cannot directly estimate overall population accuracy.

Adjudicators should verify the actual site identity and principal economic activity against the relevant **official taxonomy revision**, independently of the classifier's proposed code. Label negative cases explicitly instead of silently dropping them from evaluation.

## Gold file schema

At minimum supply `canonical_id`, `scheme`, and `gold_code`, with optional `gold_status`:

| `gold_status` | `gold_code` | Meaning |
| --- | --- | --- |
| `CODED` | Valid code | Activity/product/commodity reference established. |
| `NOT_CODEABLE` | Blank | Evidence insufficient for an admissible taxonomy code. |
| `NOT_PSIC_ACTIVITY` | Blank | Physical feature is not a PSIC economic activity. |
| `NON_ECONOMIC_POI` | Blank | Legacy alias accepted by evaluator. |

Invalid codes, missing required labels and contradictory negative statuses are fatal rather than being removed from the denominator.

## Run the evaluator

```powershell
placetype evaluate output\iloilo-reviewed\entity_classifications.parquet `
  gold.csv --scheme psic --output-dir output\evaluation\reviewed
if ($LASTEXITCODE -ne 0) { throw "Evaluation failed" }
```

The evaluator reports per-depth **production rate** and **accuracy**, Cohen's kappa, exact-code accuracy, hierarchy-compatible accuracy (including honest ancestor backoff), over-specificity, wrong-branch outcomes, mean tree distance, unknown prediction codes, and code-avoidance on negative gold cases. One division-level classification counts as produced at its supported depth, not as an invented subclass.

## Compare reviewed and legacy policies

Use the *same* OpenPlaces input and verified taxonomy. Run reviewed deterministic classification and legacy `--no-reviewed-psic` to separate the reviewed rule-policy change. Compare exact/compatible accuracy, abstention, false-positive code rate, coverage, source-specific error patterns and status distribution. A coverage gain that lowers precision is not automatically a scientific improvement.

For Express, **stratify by review source**: `REVIEWED`, `AUTO_ACCEPTED`, `AUTO_ACCEPTED_INCONCLUSIVE`, and `AUTO_ACCEPTED_UNCHECKED` must be reported separately. Default Express promotion does not create independently validated labels.

## Evaluation limitations

A convenience 50- or 100-row CLI slice is a smoke test, not a representative evaluation. Human label agreement, ambiguous PSIC scope, entity-linkage failures, selected cities versus national coverage and temporal changes in source categories are all material uncertainties. Record sampling scheme, adjudication source/date, disagreements, and negative labels so the benchmark is reproducible without redistributing restricted raw registry data.

**Implementation:** [`evaluate.py`](https://github.com/miraflor/placetype-ph/blob/main/src/placetype_ph/evaluate.py) · [`tests/test_evaluate.py`](https://github.com/miraflor/placetype-ph/blob/main/tests/test_evaluate.py).
