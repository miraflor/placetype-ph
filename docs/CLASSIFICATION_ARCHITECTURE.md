# Classification and I-O resolution architecture

PlaceType PH classifies establishment activity under PSIC, then projects admissible
activity subtrees into the PSA 2018 I-O industry hierarchy. Reviewed rules and
curated category floors remain distinct from heuristic suggestions.

## Source evidence

OSM primary tags describe activity. Ownership, operator, religion, emergency,
access and contact attributes are supplementary; their raw values remain available
for institutional evidence. Hospital and healthcare hospital tags are compatible.
Unreviewed mixtures of distinct primary activities remain subject to conflict checks.
Whole-string reviewed rules are checked before splitting.

## Accepted subtrees and candidate sets

`psic_code` can represent the common ancestor of a restricted union. The detailed
classification table stores `admissible_roots` as a JSON array, with an audit mirror
for older readers. I-O projection uses the accepted members, not excluded siblings.
A known UNION with missing, malformed or inconsistent roots is invalid. If any
member is unmapped at any I-O level, the entire union mapping abstains conservatively.

## Resolution and provenance

Candidate sets, draft subgroup refinements, direct tag evidence, verified ownership
and heuristic ownership remain separate. The resolved codes prioritize explicit
input, verified institution evidence, reviewed PSIC mapping, then direct tag rules.
Direct agreement from a shared tag is consistency, not independent corroboration.
The official hierarchy determines parent codes; conflicts at equal authority retain
the coarser constraint and clear the incompatible finer code. Explicit code input
is a manual override, not independently verified source evidence.

Draft refinements and ownership hints remain advisory under the strict default.
The four bundled national registries load by default; use an explicit custom configuration
or `PLACETYPE_PH_INSTITUTION_REFERENCES=off` to override or disable enrichment.
Registry site matches may resolve ownership only in evidence-weighted/extended modes.
Review source-specific distribution rights and attribution in [DATA_SOURCES.md](../DATA_SOURCES.md#licensing).

See the README and DATA_SOURCES.md for schemas and reference provenance.

## Selectable resolution policy (CLI and Python API)

`classify_openplaces(..., io_resolution_policy="strict")` and
`resolve_io(..., policy="strict")` default to reviewed mappings, explicit input
and verified ownership. Direct tag evidence is still emitted but cannot fill
an unresolved code. `evidence-weighted` additionally allows direct evidence
inside a candidate set and explicit source ownership, but not standalone direct
fallback or name-only ownership. `extended` permits direct fallback, labelled
ownership hints and singleton draft subgroup narrowing. These options do not
alter the original candidate columns. All resolved codes carry a source and status;
non-strict policies do not make heuristic evidence verified. The selected policy is
recorded in `run.json`. Both `placetype classify` and `placetype express`
accept `--io-resolution-policy strict|evidence-weighted|extended` (choose one).
Strict is the default; it is conservative evidence acceptance, not proof of
accuracy. Express keys its cache by policy, implementation version and the five
I-O reference file hashes; its console reports resolved coverage separately from
map-ready candidate coverage. Use distinct output paths for policy comparisons.

Draft bridge rows are handled per I-O level. `io_mapping_draft_levels` lists the
levels (`io16`, `io80`, `io240`) whose candidate set depends on a draft
Revision 5 → PSIC 2019 bridge row, meaning the deepest accepted ancestor row gives
a different set at that level, or no accepted ancestor exists. In strict and
evidence-weighted mode a code at a listed level is not adopted; a level that the
draft row does not change is resolved normally. For example, PSIC 492 (passenger
land transport by buses) depends on a draft row only at IO240, so its IO16 and
IO80 codes resolve. When a single candidate is withheld for this reason the status
is `PROVISIONAL_BRIDGE_NOT_RESOLVED`, not `PSIC_AMBIGUOUS`. In extended mode the
code is adopted with status `PROVISIONAL_PSIC_MAP` and source `draft_bridge`.
An explicit `none` means a provisional row was compared and no level depends on
a draft. Missing/blank metadata on a provisional row is treated as draft at every
level (including older summaries). Unknown, duplicate or inconsistent level tokens
raise a validation error. This validates the metadata contract; it is not an
independent re-derivation from the original taxonomy for externally edited summaries.

A provisional ownership hint does not veto independently verified ownership;
its original value remains in the summary and the promotion reason records the
conflict. Untyped and stronger competing claims still require review. A public
operator is not proof of public ownership; verified private ownership is retained
with an audit reason where the only competing evidence is a government operator.

## Title search and reviewed rows

The reviewed crosswalk decides PSIC codes. A search of the PSIC titles (the query plan, guards and thresholds of `placetype suggest`) is reported in `psic_search_*` columns for places left with status `EMPTY`, and for name-only places as `NAME_ONLY_CANDIDATES`. It never sets `psic_code`, because a strong title match can still be wrong: before review, the search coded `water_park` as water transport and `bowling_alley` as bowling equipment manufacture. A category is coded once a reviewed row exists; 18 such rows were added after review of the search results, seven of them correcting the search.

## Public or private PSIC subtree

PSIC Revision 5 names the ownership side in the leaf titles of several education and health subtrees (for example 8611 public and 8612 private hospital activities). When a place has an ownership side, `institution_psic_proposal_*` reports the subtree of that side and checks that its I-O mapping, including the draft class and subclass narrowing, is exactly the expected public or private member of the IO80 and IO240 pair. When some leaves do not name a side (for example division 85), no PSIC subtree is proposed, and only the I-O side is proposed if both the row's candidate sets and the mapping of the unchanged PSIC code are exactly the pair. The side comes, in order, from verified institution resolution, institution evidence, or a labelled inference; `institution_psic_proposal_side_basis` says which. The columns are diagnostic and change no code.

See [national registry site linkage](source/institutions.md) for the policy-scoped matching rules. Strict retains site matches as candidates; evidence-weighted and extended may resolve them.
