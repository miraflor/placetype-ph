# Glossary, notation and sources

<p class="lede">A compact vocabulary for distinguishing observational records, statistical code semantics, review states, and downstream industrial interpretations.</p>

## Glossary

| Term | Meaning |
| --- | --- |
| **Canonical entity** | One OpenPlaces record after upstream provider reconciliation, identified by `canonical_id`. |
| **Source evidence** | Typed provider category/name observation with its own provenance and dependency group. |
| **Dependency group** | A set of statements sharing an upstream lineage, not independent confirmations. |
| **Crosswalk** | Versioned source-field rule mapping to a code/subtree/union or terminal decision. |
| **`EXACT`** | Human-reviewed assertion of one specific code. |
| **`SUBTREE`** | Assertion that the true category is in a root's subtree; the root limits reported specificity. |
| **`UNION`** | Multiple admissible taxonomy branches. |
| **`NOT_ACTIVITY`** | Source feature is not an economic activity for PSIC. |
| **`UNCODEABLE`** | Source observation cannot support a taxonomy code; not the same as non-economic. |
| **Sibling backoff** | Specific policy of moving one level to an immediate common parent for compatible narrow PSIC disagreements. |
| **Peer recheck** | Bounded sensitivity test of first-pass candidate ranking using labels from other taxonomies. |
| **Reviewed rule** | Explicitly accepted source-to-taxonomy decision, distinct from a machine suggestion. |
| **Automatic unchecked** | Suggestion promoted under Express default despite no usable peer verdict. |
| **Taxonomy fingerprint** | Stable content identity of the normalized hierarchy. |
| **Crosswalk fingerprint** | Stable content identity of the active reviewed rules used in a run. |
| **I–O candidate set** | Possible corresponding industries under a derived PSIC/PSA I–O bridge; not multiple observed activities. |
| **Map-ready singleton** | One candidate code at a resolution, not an external truth certificate. |
| **Verified institutional link** | Separately adjudicated canonical-to-registry identity evidence with source and date. |
| **`run.json`** | Manifest recording classifier, source version, taxonomy, crosswalk, reference and output metadata. |

## Method references

The principal theoretical and data sources are **official statistical classification systems**, explicit tree ancestry and set operations, standard TF–IDF retrieval, and conventional hierarchical validation metrics. The package's thresholds, corpus rules, source-floor coverage, sibling backoff and promotion decisions are documented **implementation policies** rather than attributed to an unrelated paper.

- [Philippine Statistics Authority — PSIC classification](https://psa.gov.ph/classification/psic)
- [Philippine Statistics Authority — PCPC classification](https://psa.gov.ph/classification/pcpc)
- [Philippine Statistics Authority — PSCC classification](https://psa.gov.ph/classification/pscc)
- [PSA — 2018 input–output tables](https://psa.gov.ph/statistics/supply-and-use-input-output/tables)
- [PlaceType PH official source catalog and licensing](https://github.com/miraflor/placetype-ph/blob/main/DATA_SOURCES.md)
- [Source code and regression tests](https://github.com/miraflor/placetype-ph)
- [OpenPlaces PH upstream canonical entities](https://github.com/miraflor/openplaces-ph)

## Which page answers which question?

| Question | Read |
| --- | --- |
| Why does a place stop at a division? | [Fusion and backoff](fusion.html) |
| Why don't two sources count as two votes? | [Evidence triangulation](triangulation.html) |
| Can a peer PCPC code prove a PSIC activity? | [Cross-taxonomy recheck](joint-recheck.html) |
| Why is a government school office 84120? | [Classification](classification.html) and [Institutions](institutions.html) |
| Why do I–O mappings have multiple codes? | [I–O projection](io-projection.html) |
| How do I reproduce a run? | [Reproducibility](reproducibility.html) |
| How do I establish accuracy? | [Evaluation](evaluation.html) |

<div class="note"><strong>Documentation revision</strong>This guide describes the PlaceType PH **v0.1.2** source architecture verified on `main` after the reviewed PSIC integration and hardening PRs. When implementation semantics or official source revisions change, update the website and its tests together.</div>
