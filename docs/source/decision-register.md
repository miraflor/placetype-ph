# Method lineage and design decision register

<p class="lede">The purpose of this register is to state *why* each computational choice exists, what the implementation actually does, what alternative was rejected, and what evidence would be needed to validate it.</p>

## Foundations versus package policy

| Component | Foundation | PlaceType PH implementation choice / trade-off |
| --- | --- | --- |
| Statistical classification | PSA's official PSIC, PCPC and PSCC classifications | Use versioned explicit parent graphs, strict import diagnostics and source fingerprints instead of numeric prefix assumptions. |
| Source annotations | Provider-typed observational records | Keep OSM `key=value`, Overture structured categories, FSQ category hierarchy, and their provenance groups. |
| Crosswalk matching | Deterministic label-to-category rules | Exact, contains and regex with validation; preserve `EXACT` vs `SUBTREE` vs `UNION` vs terminal policy. The present fusion engine still treats `EXACT` as a rooted constraint. |
| Hierarchical fusion | Intersections of rooted subtrees | Fuse declared roots without leaf expansion; same-lineage dependencies cannot create independent votes. |
| Conflict backoff | Tree ancestry / least common ancestor | Only one-level immediate sibling backoff below section for specific PSIC independent discrepancies; otherwise abstain. |
| Category suggestions | Lexical TF–IDF | Combine word and character similarity (0.6/0.4), coarse branch selection, bounded batches; retrieval score is not truth probability. |
| Joint taxonomy review | Controlled reranking | Restrict peer rerank to first-pass candidate set; peer titles only, never new cross-taxonomy code discovery. |
| Automatic adoption | Convenience workflow choice | Express promotes no-verdict suggestions by default *with audit status*; conservative operators can hold them. |
| Optional LLM | Hierarchical classification and constrained decoding | Legal-child traversal, `STOP_HERE`, `INSUFFICIENT`, multi-pass consensus and cached prompt identity. |
| Administrative function | PSIC Rev. 5 activity distinction | Narrow 84120 office correction with exact/unrelated evidence vetoes; ownership separately adjudicated. |
| Institution identity | Verified linking and source attribution | Four national references load by default; strict accepts verified links and qualified official site IDs; narrowly matched registry sites are candidates in strict and eligible in evidence-weighted/extended. |
| I–O concordance | PSA 2018 I–O industrial system | Derived PSIC 2019 concordance and separate Rev. 5 bridge; candidate sets with provisional status and confidence. |
| Map publication | GeoParquet standard and taxonomy hierarchy | Preserve geometry and actual ancestor paths; fail on schema/taxonomy drift. |
| Evaluation | Hierarchical classification metrics | Report code-production rate separately from exact/hierarchy-compatible correctness and negative code avoidance. |
| Reproducibility | Content fingerprints and deterministic cache recipes | Version-scoped rule hashes; cheaper size/mtime for large input; not full publication-directory atomicity yet. |

## Design constraints and what they rule out

### Why no single numeric classifier confidence?

Source crosswalk status, model path agreement, I–O concordance confidence and external identity evidence have **different meanings**. A single number would conflate independent and dependent sources, product versus activity semantics, and calibrated versus uncalibrated assertions.

### Why not classify products from the establishment category?

An establishment may sell many products. PCPC permits only a *potential* family without direct product evidence; PSCC is more restrictive and requires explicit commodity text or accepted mapping. Inventing a product basket from a shop category would encode an unsupported economic model.

### Why no arbitrary cross-taxonomy voting?

PSIC, PCPC, and PSCC answer different official classification questions. Joint peer recheck tests the **sensitivity of an already retrieved candidate set**, not the correctness of a third taxonomy's code. The bounded design avoids introducing entirely new candidates from contextual labels.

### Why preserve I–O ambiguity?

A code set describes **alternatives** in a derived correspondence. A map-ready singleton merely says the candidate set currently has one value; there is no observed local buyer–supplier transaction evidence here. Users can accept only those candidate statuses appropriate for their downstream research.

### Why separate data publication from code reproducibility?

A complete `run.json` and unchanged fingerprints identify computation. They do not validate individual establishments or grant permission to redistribute third-party registry data. The public documentation site contains only synthetic examples and code/algorithm exposition.

## Explicit remaining engineering trade-offs

The full input/result materialization limits RAM; default-enabled institutional enrichment is not publication-atomic; Express's large input identity is size/mtime; a final published accuracy claim requires a representative adjudicated benchmark; official PCPC/PSCC normalization counts require source-specific verification. See [Limits](limitations.html).

**Read with:** [Architecture](architecture.html) · [Reproducibility](reproducibility.html) · [Evaluation](evaluation.html).
