# System architecture

<p class="lede">The core architecture is a separation of responsibilities: canonical place identity, statistical classification, auxiliary institutional evidence, and economic concordance are different layers with different proof obligations.</p>

## System boundary

<figure><img src="assets/diagrams/pipeline.svg" alt="PlaceType PH pipeline from an already reconciled OpenPlaces entity to classification and GIS outputs."><figcaption>Figure 1 — Classification begins with canonical entities. Upstream matching and downstream economic interpretation are not part of the primary PSIC code decision.</figcaption></figure>

**Upstream:** OpenPlaces PH supplies canonical IDs, linked provider names/categories, provenance, coordinates, and matching diagnostics. PlaceType PH neither merges nor splits those upstream entities. It may flag suspicious records (`REVIEW_ENTITY_MATCH`) when independent semantic evidence disagrees and matching-quality indicators are present.

**Classification core:** `openplaces.py` produces typed evidence, `crosswalk.py` matches reviewed rules, `fusion.py` reasons over actual taxonomy parent edges, `classifier.py` applies eligibility and policy guards, and `retrieval.py`/`traversal.py` supply an optional model fallback. `taxonomy.py` holds the normalized official hierarchy.

**Joint review workflow:** `joint_crosswalk.py` and `suggest.py` create worklists, preserve first-pass independence, and perform a bounded peer-context rerank. A peer recheck proposes no new code outside the first-pass candidate set. It is not a vote in the production `fuse()` algorithm.

**Additive enrichment:** specialized activity, administrative, institutional and ownership modules attach review candidates and provenance. Opt-in institutional identity may refine a *separate* I–O candidate projection where stringent gates are satisfied. It does not silently replace primary PSIC.

**Publication:** `pipeline.py` produces audit-level and map-level Parquet, `io_mapping.py` attaches I–O sets, `gis.py` exports compact GeoParquet, and `run.json` records versions, fingerprints, and status counts.

## Modules and responsibilities

| Component | Main modules | Contract |
| --- | --- | --- |
| Official taxonomy | `taxonomy.py`, `taxonomy_import.py` | Nodes have explicit parents, levels, and versioned fingerprints. |
| Canonical input | `openplaces.py` | Read canonical IDs and typed source fields; no new entity linkage. |
| Reviewed decisions | `crosswalk.py`, `reviewed_psic.py` | Version-scoped exact/contains/regex rules; bundled 328 PSIC Rev. 5 rows. |
| Evidence fusion | `fusion.py`, `eligibility.py` | Hierarchy intersection, dependency grouping, conflict or abstention. |
| Optional decision fallback | `retrieval.py`, `traversal.py`, `cache.py` | Local TF-IDF, bounded taxonomy traversal, deterministic cache identity. |
| Joint review | `joint_crosswalk.py`, `suggest.py` | Candidate worklist, first-pass control, diagnostic rerank. |
| Institutional audit | `specialized/*` | Activity candidates, verified identity, conditional ownership enrichment. |
| Economic candidate sets | `io_mapping.py`, `io80_resolver.py` | Rev. 5 → 2019 PSIC → 2018 I–O; never silently choose among candidates. |
| Data products | `pipeline.py`, `gis.py`, `express.py` | Parquet, GeoParquet, manifest, cache-aware workflow. |

## Three separate kinds of uncertainty

1. **Source uncertainty:** two observed labels can be independent, correlated, broad, or contradictory. The source adapter and dependency groups make these distinctions explicit.
2. **Taxonomy uncertainty:** one source label may support a subtree, a union of subtrees, or no category. The classifier stops at an ancestor or abstains where no unique defensible code exists.
3. **Economic correspondence uncertainty:** even a valid PSIC code may map to more than one I–O industry, and bridging across statistical revisions may be provisional. Candidate sets and provenance are retained.

These uncertainties cannot be collapsed into one confidence score without additional evidence and calibration.

## Decisions are typed; audits are additive

`ClassificationResult` contains `code`, `level`, `status`, `deciding_component`, `candidate_codes`, `evidence_sources`, `flags`, `audit`, and depth metadata. Specialized enrichment attaches separate columns rather than silently modifying those fields. A `SourceEvidence` record stores `source`, `category`, `name`, `dependency_group`, and an optional reviewed `mapping`.

The core distinction is **accepted rule vs candidate**. A high lexical score, registry name similarity, or recheck stability is not equivalent to human review or verified identity.

## Method lineage and traceability

The taxonomy, hierarchical classifiers, TF-IDF retrieval, and constrained traversal use established structures/algorithms. *Source-specific precision floors, independent sibling backoff, status vocabulary, promotion defaults, institutional matching gates, and I–O candidate narrowing* are **project policy**. Their existence is documented in the [design register](decision-register.html), not presented as canonical statistical algorithms.

**Implementation:** [source tree](https://github.com/miraflor/placetype-ph/tree/main/src/placetype_ph) · [architecture specification](https://github.com/miraflor/placetype-ph/blob/main/docs/ARCHITECTURE.md).
