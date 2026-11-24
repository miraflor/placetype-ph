# Evidence triangulation

<p class="lede">Triangulation here means comparing typed observations while respecting source dependence. More labels do not automatically mean more independent evidence.</p>

## Start with the provenance graph

<figure><img src="assets/diagrams/lineage.svg" alt="Independent OSM annotation and an Overture annotation that may share Foursquare provenance."><figcaption>Figure 1 — A provider record is not necessarily an independent observation. Overture can reuse Foursquare-origin information.</figcaption></figure>

A naive rule would count every provider field as another supporting vote. PlaceType PH instead assigns a **dependency group** to each `SourceEvidence` item and reasons over the admissible *taxonomic subtrees*. Foursquare and FSQ-derived Overture are one lineage; two categories from one Overture record are also dependent. Independent OSM and Overture statements can corroborate or contradict each other.

## Explore three controlled scenarios

<div class="scenario" data-scenario-panel>
<div><strong>Worked source-evidence outcomes</strong><p class="scenario-caption">Select a case. This is an explanatory illustration of documented policy, not a live classifier or an accuracy estimate.</p></div>
<div class="scenario-tabs"><button type="button" class="scenario-tab selected" data-scenario="agree" aria-pressed="true">Compatible</button><button type="button" class="scenario-tab" data-scenario="dependent" aria-pressed="false">Same lineage</button><button type="button" class="scenario-tab" data-scenario="conflict" aria-pressed="false">Conflict</button></div>
<div class="scenario-evidence"><div data-evidence-a><small data-field>OSM</small><strong data-field>shop=pharmacy</strong><em data-field>Independent OSM lineage</em></div><div data-evidence-b><small data-field>Overture</small><strong data-field>pharmacy</strong><em data-field>Independent Overture lineage</em></div></div>
<div class="scenario-result"><strong data-result-title>Compatible — one supported PSIC branch</strong><p data-result-detail>Compatible reviewed mappings to the same subtree can support one code without a numerical confidence vote.</p></div>
</div>

## The order of operations

1. **Parse** source semantics without losing source identity (`osm` key/value vs Overture primary/alternate/basic vs complete Foursquare hierarchy).
2. **Attach** reviewed mapping objects or narrower trusted policy floors where permitted for PSIC Rev. 5.
3. **Group** mapped evidence by provider lineage; dependent assertions may intersect or be flagged if disjoint.
4. **Intersect** the admissible subtrees across independent groups. Compatible evidence supports the deepest *asserted* surviving root, not an inferred leaf.
5. **Report** sibling-only backoff, unresolved union, semantic conflict, or eligibility conflict explicitly. Optional record-linkage warning fields turn some conflicts into `REVIEW_ENTITY_MATCH`.

## Triangulation does not mean majority voting

Suppose FSQ and FSQ-derived Overture both suggest one activity while independent OSM asserts a disjoint activity. It is *not* two votes against one: FSQ and Overture are one dependency group. Counting them separately would manufacture false confidence. The classification engine can return `CONFLICT` rather than choosing the most popular label.

The dependency grouping also explains why an Overture primary category and alternate category cannot be treated as two establishments. Their disagreement is potential **within-source ambiguity**, not independent evidence for the LCA.

## Name rules versus category rules

A source's reviewed name rule and its reviewed category rule are dependent. A specific name rule can override a coarse incompatible source category, with a visible audit flag, but not by silently adding an extra independent vote. A reviewed exact category or an unrelated activity can block an administrative name-based correction into review. The [classification chapter](classification.html) details these precedence policies.

## Distinguish a second kind of triangulation

The joint PSIC/PCPC/PSCC **peer-context recheck** is a separate workflow from source fusion. It compares candidate rankings across independently prepared taxonomy proposals, but does **not** allow those other taxonomies to vote a PSIC code into existence. See [cross-taxonomy recheck](joint-recheck.html).

**Implementation:** [`fusion.py`](https://github.com/miraflor/placetype-ph/blob/main/src/placetype_ph/fusion.py) · [`specialized/fusion.py`](https://github.com/miraflor/placetype-ph/blob/main/src/placetype_ph/specialized/fusion.py) · [source-aware evidence note](https://github.com/miraflor/placetype-ph/blob/main/docs/PRIORITY1_EVIDENCE_FUSION.md).
