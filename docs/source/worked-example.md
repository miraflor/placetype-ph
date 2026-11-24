# One example, three outcomes

<p class="lede">The easiest way to understand the system is to run a few small, explicitly illustrative records through its decision semantics. We will keep the same PSIC Rev. 5 hierarchy and vary only the evidence.</p>

<div class="note"><strong>About these examples</strong>The cases reflect rules and regression tests in the package (including pharmacy, banking, OSM compounds, and administrative offices). They are instructional traces, not measurements of actual Philippine places or model-inferred probabilities. To reproduce them, load the matching official taxonomy and reviewed crosswalk.</div>

## Case A — Two compatible source categories

An OpenPlaces entity contains `overture_category = pharmacy` and `osm_category = shop=pharmacy`. Suppose the corresponding accepted category assertions refer to **PSIC 4772** in the loaded hierarchy.

<figure><img src="assets/diagrams/evidence-agree.svg" alt="OSM shop pharmacy and Overture pharmacy independently support the same PSIC group 4772."><figcaption>Figure 1 — Two independent providers can assert compatible taxonomic subtrees without needing a confidence-vote formula.</figcaption></figure>

1. The source adapter keeps OSM `key=value` and Overture label distinct; their dependency groups are independent unless provenance says otherwise.
2. The crosswalk looks up each source's category, match type, and PSIC version.
3. Fusion intersects the rooted subtrees. The surviving supported code is 4772; this is not an inferred deeper subclass.
4. The output records the code, level, source list, status, and accepted-rule flags.

When a single category supports only a division, the output must remain at that division unless another accepted assertion narrows it.

## Case B — Two sources, one lineage

Suppose the record has Foursquare evidence and an Overture label explicitly recorded as derived from Foursquare (`overture_has_foursquare_provenance = true`). Both may describe banking. They are not two independent confirmations.

<figure><img src="assets/diagrams/lineage.svg" alt="Foursquare and Overture share one upstream lineage and therefore one dependency group."><figcaption>Figure 2 — Dependence-aware corroboration. Two field values can constrain a subtree but are not two independent observations.</figcaption></figure>

If compatible, the records may narrow one another. If disjoint, the same-lineage conflict is flagged rather than converted into a fraudulent consensus. The worklist and run outputs preserve the provider identities.

## Case C — Disjoint economic activities

A canonical entity combines `osm_category = amenity=bank` and `overture_category = pharmacy`. When accepted PSIC mappings point into unrelated branches, there is no defensible single principal activity.

- A forced subclass would silently discard a source contradiction.
- A common high-level ancestor may be too broad to explain either activity.
- The production result conservatively reports `CONFLICT` with candidate codes, or `REVIEW_ENTITY_MATCH` if the input carries entity-match warning indicators.

This does **not** change OpenPlaces identity automatically; it asks the upstream matching process to be reviewed.

## Case D — Administrative function is not the service administered

`DepEd Schools Division Office` may be annotated with `amenity=school`. A narrow administrative name can support **84120**, rather than treating the office as a school delivering education. But this correction is guarded: a reviewed exact category, unrelated activity evidence, or disjoint named rules can block it into review.

A hospital office, a school itself, and a government agency may be related institutionally while carrying **different principal economic activities**. Public ownership is a *separate question*, and does not follow from PSIC 84120 alone.

## A practical comparison you can reproduce

```powershell
$places = "..\openplaces-ph\data\output\areas_iloilo\canonical_pois.parquet"
placetype classify $places --schemes psic --llm none --output-dir output\reviewed
if ($LASTEXITCODE -ne 0) { throw "Reviewed run failed" }
placetype classify $places --schemes psic --llm none --no-reviewed-psic --output-dir output\legacy
if ($LASTEXITCODE -ne 0) { throw "Legacy run failed" }
```

Compare `code`, `status`, `flags` and `candidate_codes` by `canonical_id`. More coded rows are not necessarily more correct; the adjudicated comparison in [evaluation](evaluation.html) is required to assess correctness.

**Implementation & tests:** [`test_reviewed_psic_transfer.py`](https://github.com/miraflor/placetype-ph/blob/main/tests/test_reviewed_psic_transfer.py) · [`classifier.py`](https://github.com/miraflor/placetype-ph/blob/main/src/placetype_ph/classifier.py).
