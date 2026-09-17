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

The crosswalk is one **joint review object** over PSIC, PCPC and PSCC. One OpenPlaces
source/category value receives a stable `joint_key`; rows under that key remain separate
taxonomy mappings with their full hierarchy intact.

The first pass is independent by construction. Each scheme retrieves only from the original
OpenPlaces evidence. Reviewed `codes` and accepted `suggested_codes` may become peer evidence;
raw `candidate_codes` never cross a taxonomy boundary. At most one version of each other
classification system is allowed to speak as a peer: reviewed evidence outranks machine
suggestions, then the configured default version is preferred.

Evidence admissibility follows the meaning of each taxonomy. PSIC can use curated
activity-oriented rewrites and branch constraints. PCPC can make reviewable category-based
product/service-family proposals when retrieval is strong and separated. PSCC still receives
candidates and participates in the joint workflow, but category text alone is not commodity
evidence, so category-only PSCC hits remain candidates and record
`guard:commodity_evidence_required`.

Review status distinguishes what the reviewer is being asked to do. A strong code proposal is
`REVIEW_MAPPING`. Rule conclusions such as `NOT_ACTIVITY` and `UNCODEABLE` are
`REVIEW_DECISION`. Candidate-only rows remain `REVIEW_CANDIDATES`; already reviewed rows are
`REVIEWED`.

The peer-context pass is a **bounded rerank**, not a second discovery search. The source-only
first-pass ranking is the control. Peer labels are appended to the same base query, but the
retriever is restricted to the first-pass candidate codes. Therefore peer context can reorder
already plausible candidates but can never introduce a code that the source evidence did not
retrieve. This is especially important for PSCC, where unrestricted activity/service words can
otherwise pull unrelated commodity codes into the result.

Candidate-only rows may report `CANDIDATE_STABLE` or `CANDIDATE_SHIFT`. Those are diagnostics
only and cannot escalate the group to `RECHECK`. A group-level `RECHECK` requires a sufficiently
large shift on a reviewed mapping or an accepted first-pass suggestion. `SHIFT_WEAK` records a
smaller reorder without escalation.

`joint_status` is derived from accepted evidence: `RECHECK` when an accepted/reviewed row
shifts strongly, `SINGLE_SCHEME` when fewer than two classification systems are available,
`NO_EVIDENCE` when no accepted/reviewed code exists, `STABLE` when every accepted/reviewed row
with taxonomy support was compared and none shifted, and `PARTIAL` otherwise.

The audit keeps peer context separate from retrieval context. `peer_context` contains scheme,
version, codes and titles for a reviewer. `peer_query_context` contains titles only, avoiding
numerical code tokens that would perturb lexical retrieval. Multi-code mappings retain every
accepted code and hierarchy path. `|` remains the code-list separator, ` ; ` separates
classification systems, and ` + ` joins union members.

Reviewed rows reconstruct their source-only candidate set without altering the human mapping.
The recheck reuses that candidate set as its control, so it no longer repeats the same control
retrieval. `TaxonomyRetriever.search_hierarchical` also computes the TF-IDF score vector only
once per query and reuses it for coarse-branch selection and fine ranking. Repeated branch
closures are cached. These changes reduce the dominant first-pass cost without process fan-out
or duplicated retriever matrices.

Coverage reporting separates review completion from coded coverage. Reviewed `NOT_ACTIVITY` or
`UNCODEABLE` decisions count as reviewed decisions but not as rows carrying a code. The coded
coverage percentage uses only reviewed rows with nonempty `codes` plus fresh rows with nonempty
`suggested_codes`.

This recheck is an advisory consistency diagnostic, not an official concordance. PSIC
classifies activities, PCPC products/services, and PSCC traded commodities; disagreement can be
legitimate. Official inter-classification concordances, when added, should augment rather than
replace the independent first pass.

`placetype classify` defaults to `psic,pcpc,pscc`; `--schemes` still selects a subset.

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

### Batched retrieval and progress

`crosswalk-suggest` separates deterministic source preparation from taxonomy scoring. First-pass
TF-IDF queries are grouped by taxonomy and scored in bounded batches; repeated query text within
a taxonomy is vectorised once even when several worklist rows use it. Ranking still applies each
row's own branch restriction, so batching changes throughput rather than retrieval semantics.
The default batch size is 256 unique query texts and can be changed with `--batch-size`.

The command reports five stages: loading, preparation, batched retrieval, peer-context recheck,
and output writing. Rich progress bars are enabled by default and can be disabled with
`--no-progress`. The peer-context pass remains the V8 bounded rerank and cannot introduce codes
outside the first-pass candidate set.
