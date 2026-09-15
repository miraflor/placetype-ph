# Architecture

`placetype-ph` is downstream of `openplaces-ph`. It never changes OpenPlaces
entity resolution; semantic contradictions can only *flag* a cluster for review.

```text
OpenPlaces canonical_pois.parquet
             |
             v
      source evidence
  FSQ / Overture / OSM
             |
             v
  confirmed source crosswalks
             |
             v
  dependency-aware tree fusion
             |
      +------+------+
      |             |
   resolved      unresolved
      |             |
      |       constrained traversal
      |       (optional Qwen LLM)
      |             |
      +------+------+
             v
     deepest defensible node
             |
             v
 entity_classifications.parquet
```

## Classification policies

### PSIC

One principal establishment activity. The classifier may stop at section, division,
group, class, or subclass. A shallower correct code is preferred over an invented
leaf.

### PCPC

An establishment may supply many products. An OpenPlaces-only PCPC result is therefore
labelled as a **potential product/service family**, not a complete product basket.
The label does not depend on which component decided the code: a crosswalk-resolved PCPC
row is no more a complete basket than a model-resolved one. For product-level work, pass
an explicit product description column.

### PSCC

PSCC is a traded-commodity classification. Place names/categories alone are not enough.
LLM inference is disabled for PSCC unless explicit product/commodity text is supplied;
confirmed crosswalk mappings remain allowed.

## Evidence dependence

If `overture_has_foursquare_provenance=true`, FSQ and Overture share the same dependency
group. They can constrain the code more deeply when compatible, but agreement does not
count as two independent votes.

A source's own category rule and name rule are dependent for the same reason. When they are
compatible, fusion intersects them and the name rule refines the category. When they are
disjoint, the name rule takes precedence: the category mapping is a bulk decision over a
whole vocabulary and the name rule is a specific reviewed judgment about one establishment,
so widening to their common ancestor would discard the more informed of the two.

## Backoff

The taxonomy is represented as parent/child nodes rather than by string truncation.
This matters for PSIC sections and makes the same engine usable for PCPC and PSCC.

Backoff is monotone with respect to **reviewed deterministic evidence**. Adding the optional
model can make an unresolved result deeper or leave an already-supported deterministic code
unchanged; a failed model call cannot erase a reviewed crosswalk/fusion result. If traversal
reaches no decision, the deterministic code stands with status `FUSION_BACKOFF`.

Fusion operates on the rooted subtrees explicitly asserted by each mapping. It does **not**
expand a coarse `SUBTREE` mapping to all leaves and then take an LCA: on a unary branch that
would invent specificity merely because only one descendant happens to exist.

The tree itself is checked against the scheme's level order after import. A tree whose
divisions never attached to a section, or whose numeric child points into the wrong code branch,
is rejected rather than accepted as a wider forest. A missing intermediate level is a different
finding: it may be accepted for a deliberately partial manual extract, but it still removes a
backoff level. Full official `taxonomy fetch` is therefore strict by default and also requires
every expected hierarchy level to be present; manual imports may opt into or out of that check.
The orphan and level-completeness controls are independent. Malformed code shape is a separate
source-integrity error and is not relaxed by either control unless a declared hierarchy level
provides enough information to repair a lost leading zero unambiguously.

Explicit schema fields are authoritative. A supplied parent is never silently replaced by an
inferred prefix parent, and explicitly named workbook columns disable heuristic fallback if that
schema cannot be found. Duplicate taxonomy codes may be merged only when their hierarchy identity
agrees.

The package also distinguishes **literal taxonomy text** from **noisy POI evidence**. Official
node titles such as `Other` are preserved; generic OpenPlaces labels such as `other` may still be
treated as non-evidence. Pandas missing scalars are removed before either kind of text is embedded
or matched.

## LLM boundary

The LLM never sees the whole taxonomy as an unconstrained generation problem. At each
node it receives only legal children and may answer one child, `STOP_HERE`, or
`INSUFFICIENT`. Exclusions are shown as veto/boundary evidence but are not indexed as
positive retrieval text. Business names, categories and product descriptions are explicitly
treated as untrusted data in the system prompt, so instructions embedded in a place name are not
followed. Cache identity includes the provider/base URL as well as the model name.
