# Scope, limitations and interpretation

<p class="lede">The package makes defensible, reproducible classification decisions under its explicit rules. It does not turn incomplete source metadata into observed economic transactions, site-verified institutional identity, or population-level accuracy guarantees.</p>

## Scientific interpretation limits

1. **Principal activity is not revenue share.** PSIC classification identifies an activity category, not turnover, jobs, gross value added, production functions, prices, or transactions.
2. **A source category is not an audited establishment record.** OSM, Overture and FSQ differ in completeness, taxonomy vocabulary and update cadence; matching/lineage errors can persist in canonical records.
3. **Dependent labels are not independent confirmations.** FSQ-derived Overture tags belong to the same source lineage; exact repeated labels do not establish probability or certainty.
4. **PCPC is not a complete production basket.** Place-only PCPC family assignments require additional product data for stronger claims.
5. **PSCC is not an inference from a shop name.** Traded commodities demand specific evidence; missing commodity text often implies abstention.
6. **I–O codes are candidates.** A derived concordance and a singleton candidate are not proof of actual local supply-chain relationships.
7. **Institutional identity is site-specific.** Name/city similarity, participation/accreditation, administrative jurisdiction, and religious name patterns do not prove legal ownership or physical branch equivalence.
8. **Cross-taxonomy recheck is not a gold label.** A stable lexical rerank from peer titles is a diagnostic, not an independent statistical verification.

## Computational limits

- The input and result DataFrames are held in memory even though row conversion is chunked; true bounded-memory national-scale streaming remains open.
- Reviewed `EXACT` and `SUBTREE` have different stated rule meanings, but the current fusion implementation represents both as rooted constraints; exact singleton semantics are not globally enforced during fusion. This should be considered when auditing mixed evidence.
- The default deterministic path is reproducible under fixed inputs and rule code; an optional external LLM can introduce backend nondeterminism despite cached prompt identity.
- `classification_run_ready` requires manifests and output files/schema, but the pipeline may leave an intermediate classification Parquet after a later institutional enrichment error. No all-or-nothing publication guarantee yet.
- Express hashes small input contents but uses size and modification time for large files. That cache identity is a performance optimization, not an archival integrity proof.
- GitHub Pages is **documentation only**. It does not deploy Python classification, user-supplied data, institution registries or OpenPlaces output.

## Data provenance and licensing

Official PSA statistical definitions are cited in the repository's [source guide](https://github.com/miraflor/placetype-ph/blob/main/DATA_SOURCES.md); normalized/derived tables may carry separate attribution and scope requirements. The package's MIT license does not grant rights over third-party institutional extracts. The [source-specific rights assessment](https://github.com/miraflor/placetype-ph/blob/main/DATA_SOURCES.md#licensing) identifies affirmative source notices and unresolved permissions; it is not blanket clearance. This documentation includes no institutional source records or proprietary downstream datasets.

## What still needs empirical work

A stratified, independent, version-aware adjudication set should estimate precision, false positives, coverage, correct ancestor backoff, and error by source/provider and decision path. Comparisons must distinguish reviewed mappings from automatically promoted suggestions and preserve sampling weights where difficult cases were oversampled.

A strong engineering result is still only *an implementation result*. The research claim becomes stronger when tested against ground truth independent of the classifier.

## Not part of PlaceType PH

Road networks, shortest-path routing, network HDBSCAN, network Voronoi, economic centrality and geometric Delaunay triangulation are different algorithms. PlaceType PH's **triangulation** refers to semantic evidence comparison and bounded peer-taxonomy review. No geometric partition or clustering stage is hidden in this package.

**Next:** [Evaluation](evaluation.html) · [Reference sources](glossary.html).
