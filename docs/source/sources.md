# Source evidence and provenance

<p class="lede">A canonical place can combine provider annotations from OSM, Overture and Foursquare. PlaceType PH turns those annotations into typed evidence without erasing provider identity, key/value meaning, or common origin.</p>

## Input contract

`read_openplaces()` reads Parquet, requires unique, non-null, non-blank `canonical_id`, and at least one supported source semantic field. Typical fields include `osm_name`, `osm_category`, `overture_name`, `overture_category`, `fsq_name`, `fsq_category`, and structured Overture primary/alternate/basic categories. Input coordinates and geometry are carried to the spatial summary as available; classification does not change them.

## Three provider streams

| Source | Category handling | Dependence / warning |
| --- | --- | --- |
| **OSM** | Parse `key=value` primary assertions, not substring matches; ignore ancillary attributes for principal activity. | Multiple substantive tags can describe disjoint activities and trigger review. |
| **Overture** | Preserve primary and alternate labels as dependent source statements; basic category is advisory context in the operational PSIC adapter. | An Overture record may itself derive from Foursquare. |
| **Foursquare** | Preserve source-specific category hierarchy strings, rather than naively splitting category path text. | Foursquare and FSQ-derived Overture are a single lineage. |

For example, `shop=car` and `shop=carpet` are different observed strings. An `atm=yes` auxiliary tag at a bank does not create a second principal banking/ATM activity. `overture_basic_category = pharmacy` alone is not accepted as a precise operational PSIC rule.

## Typed evidence is not a vote

Every `SourceEvidence` contains a `dependency_group`. With explicit FSQ-derived provenance, Foursquare and Overture share `fsq-lineage`; otherwise independent providers have separate groups. Categories and name rules from one provider remain dependent on the same upstream record. Agreement does not produce an invented confidence value, and multiple fields from one provider cannot be counted as independent corroboration.

## Name and category evidence

A reviewed `source_field=category` mapping is a reusable category-to-hierarchy rule. A reviewed `source_field=name` mapping may be a high-precision chain or institution-specific assertion. If they are compatible they constrain the same root; if contradictory, the name precedence logic can preserve the narrower reviewed name rule while visibly flagging `CROSSWALK_NAME_RULE_OVERRODE_CATEGORY`. This is package policy, not a generic theorem about business names.

When a precise reviewed name rule conflicts with a trusted category floor, the operational classifier retains counterevidence rather than manufacturing a single definitive code.

## Identity conflicts are returned upstream for review

If independent categories disagree and the canonical record has match-risk indicators (transitive completion, large pair distance or low match score), the classifier can emit `REVIEW_ENTITY_MATCH`. It **does not** re-run the OpenPlaces matching algorithm or silently split one canonical ID into two. That distinction is critical: mismatched POI identity is an upstream data problem, not a reason to assume one establishment performs several activities.

## Untrusted input boundary

Business names, descriptions, and provider tags are **data**, not instructions to a model. The optional traversal prompt explicitly tells the LLM not to obey embedded instructions. Provider normalization strips missing labels for matching but keeps official taxonomy titles intact.

**Implementation:** [`openplaces.py`](https://github.com/miraflor/placetype-ph/blob/main/src/placetype_ph/openplaces.py) · [`reviewed_psic.py`](https://github.com/miraflor/placetype-ph/blob/main/src/placetype_ph/reviewed_psic.py).
