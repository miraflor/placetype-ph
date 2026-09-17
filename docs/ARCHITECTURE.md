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

## Joint crosswalk and cross-taxonomy recheck

The crosswalk is a **joint review object**, not a PSIC table with optional add-ons. One
OpenPlaces source/category value receives a stable `joint_key`, and that key groups the
PSIC, PCPC and PSCC mappings for the same observed category. Each mapping still points to
a node in its own full hierarchy; the joint suggestion output also records the complete
root-to-node path so review is not reduced to three flat labels.

`joint_key` is a digest of the normalised source, source field and source value only. It
deliberately excludes the scheme and the version, so re-running `crosswalk-init` for a
different set of schemes does not change any key, and a row can be matched across worklists.
One function, `ensure_joint_key`, defines it for every stage.

Suggestion is two-pass. The first pass retrieves each taxonomy independently and is the only
pass that writes `mapping_kind`, `codes` or `suggested_codes`. Only reviewed `codes` and
accepted `suggested_codes` may become peer context. Raw `candidate_codes` never cross a
taxonomy boundary, so a below-threshold retrieval hit cannot influence another scheme.

The second pass is a **controlled comparison**. For each row it issues the same retrieval
call twice: once on the row's own query (the control) and once on that query with the peer
schemes' selected labels appended (the treatment). Only the appended text differs, so a
change of top candidate is attributable to peer context and not to a different query, a
different branch restriction or a different score threshold. A shift is escalated to
`SHIFT` only when the control candidate leaves the treatment candidate list, or when the
treatment candidate leads it by at least `--min-margin`; otherwise it is recorded as
`SHIFT_WEAK` and left for the reviewer to read.

Peer evidence is bounded three ways. Only reviewed `codes` and accepted `suggested_codes`
cross a taxonomy boundary; a raw retrieval candidate that failed the acceptance threshold
never becomes evidence for another system. Two rows for two versions of one classification
system are alternatives rather than independent evidence, so at most one row speaks for each
system. Reviewed evidence outranks machine suggestions; within the same evidence class the
configured default version is preferred, and any remaining tie keeps worklist order. The audit
rendering is separate from the retrieval rendering: `peer_context` names the version and codes
for a reviewer, while `peer_query_context` carries titles only, because a bare code such as
`2106` is a numeral to a retriever and would perturb the treatment query without adding
evidence.

Separators do not collide. `|` keeps its existing meaning inside code lists, ` ; ` separates
classification systems, and ` + ` joins the members of one union, so splitting any of these
fields is unambiguous.

The base query is uniform within a group: `query_text` is used only when every taxonomy-backed
row in the group has one, otherwise every row falls back to `source_value`. For reviewed rows,
`crosswalk-suggest` reruns the same deterministic suggestion preparation/retrieval path only to
reconstruct `query_text` and `branch_codes`; the reviewed mapping itself is never overwritten.
This keeps reviewed and unreviewed rows comparable in both query text and branch scope.

Whether the first-pass code agrees with the control candidate is reported separately, in
`first_pass_status`. That column is a diagnostic on comparability between the two passes,
not a stability signal, and it is kept apart from `recheck_status` for that reason.
`selected_code_column` records whether the compared code came from a reviewed `codes` value
or from a machine suggestion, so a disagreement with a human decision can be told apart
from a disagreement between two machine passes.

Group status in `joint_status` is derived from the group, not from a fixed threshold:
`RECHECK` when any row shifted, `SINGLE_SCHEME` when fewer than two distinct classification
systems had a taxonomy, `NO_EVIDENCE` when no taxonomy-backed row in the group has an accepted
or reviewed code yet, `STABLE` when every taxonomy-backed row was compared and none shifted,
and `PARTIAL` otherwise. `NO_EVIDENCE` is the expected state of a freshly created worklist:
peer context exists only once something has been accepted or reviewed, so the recheck does
nothing until review is under way. Multi-code mappings retain every accepted code and
hierarchy path in the audit.

This peer-context pass is a consistency check, **not** an official concordance. PSIC classifies
activities, PCPC products/services, and PSCC traded commodities; disagreement can therefore be
legitimate. When official inter-classification concordance tables are added later, they should
augment this check rather than replacing the independent first pass.

`placetype classify` also defaults to `psic,pcpc,pscc`, making the joint architecture the
normal product path rather than a crosswalk-only feature. Users can still request a subset with
`--schemes`.

The first suggestion pass evaluates all three schemes independently from the same OpenPlaces
evidence, but evidence admissibility follows what each classification means. PSIC retains curated
activity-oriented query rewrites and branch constraints. PCPC uses normalized category text and
may propose a review-required potential product/service family when retrieval is strong and
separated. PSCC also receives normalized category retrieval, but a place category alone is not
commodity evidence: its hits remain candidates until a reviewer confirms a mapping or a later
workflow supplies explicit product/commodity evidence.

This is simultaneous classification without forcing false symmetry. PSCC remains present in the
joint worklist, receives peer-context rechecks, and reviewed PSCC codes can become peer evidence.
What is withheld is only automatic promotion of category-only PSCC hits. Such rows record
`guard:commodity_evidence_required` in `suggestion_source`.

For PSIC and PCPC, ordinary evidence-quality guards remain shared: compound categories, heuristic
store suffixes, ambiguous pet-shop evidence, and one-token queries stay candidate-only. Every
reason a hit was not promoted is named in `suggestion_source`.

Coverage separates review completion from coded coverage. Reviewed `NOT_ACTIVITY` or
`UNCODEABLE` decisions count as reviewed decisions but not as rows carrying a code. The coded
coverage percentage therefore uses only reviewed rows with nonempty `codes` plus fresh rows with
nonempty `suggested_codes`.
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
