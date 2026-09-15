# PlaceType-PH Deterministic PSIC Layer: V1 → V5 Evolution and Anti-Regression Notes

## Purpose

This document records how the deterministic PSIC suggestion layer evolved from V1 to V5 during stress testing on a combined Iloilo + Metro Manila OpenPlaces corpus.

It is written primarily for future coding agents and reviewers.

**Important:** do not interpret the current conservative behavior as an unfinished heuristic that should be made more aggressive. Several apparently “obvious” expansions were already tried, audited, and intentionally rolled back because they increased false confidence.

The current design principle is:

> **High-precision deterministic mappings first. Uncertain categories remain candidates rather than being forced into PSIC.**

V5 should be treated as the **freeze candidate for the deterministic layer**.

---

## 1. Test corpus and evaluation context

The deterministic layer was stress-tested on a combined OpenPlaces corpus built from Iloilo and Metro Manila using Foursquare OS Places, Overture Maps Places, and OpenStreetMap.

Combined corpus:

- **795,966 canonical POIs**
- **13,965 distinct `(source, source_value)` categories**
- **912,943 source-category occurrences**

The deterministic suggestion system emits four mapping kinds:

- `EXACT`
- `SUBTREE`
- `NOT_ACTIVITY`
- `UNCODEABLE`

Rows without a deterministic proposal remain review candidates.

The goal is **not maximum automatic coverage**. The goal is a deterministic layer whose retained automatic mappings are extremely safe, leaving ambiguous categories for later review or model-assisted classification.

---

## 2. V1 — initial combined-corpus baseline

### Behavior

V1 used the existing deterministic architecture:

- source-aware category normalization
- high-precision source rules
- hierarchical PSIC retrieval
- branch constraints
- score + margin acceptance rule
- explicit `NOT_ACTIVITY`
- explicit `UNCODEABLE`

Both curated/source-aware semantic routes and generic `raw_category` retrieval could become automatic proposals if retrieval confidence was high enough.

### V1 combined-corpus results

- **1,961 deterministic proposals**
- **256,913 proposed occurrences**
- **28.14% weighted deterministic coverage**

Breakdown:

- `EXACT`: 443 categories / 76,231 occurrences
- `SUBTREE`: 1,188 / 97,096
- `NOT_ACTIVITY`: 325 / 57,880
- `UNCODEABLE`: 5 / 25,706
- unresolved/review candidates: 12,004 categories / 656,030 occurrences

### What already worked

Many explicit mappings were useful and remain conceptually valid, including convenience stores, groceries, fuel stations, automotive repair, pharmacies, banks, schools, hospitals, hotels/lodging, non-activity objects such as roads and structures, and intentionally broad uncodeable buckets.

### Problem A — `spa` matched `space`

The FSQ beauty rule effectively contained:

```python
if "hair salon" in joined or "spa" in joined:
```

Because this was substring matching, `spa` also matched `space`, causing false beauty mappings for Coworking Space, Event Space, Outdoor Event Space, and similar labels.

### Problem B — specific sports categories collapsed to PSIC section `S`

Examples such as Basketball Court and Gym went through raw semantic retrieval. Generic sports language strongly matched:

```text
S = Arts, Sports and Recreation
```

This happened even when specific valid PSIC targets existed:

- Basketball Court → **93112**
- Gym / fitness center → **93111**

### V1 lesson

> Source-aware routing must protect obvious fine-grained categories from global lexical retrieval.

But this does **not** imply that every source hierarchy should be manually mapped.

---

## 3. V2 — fix confirmed SPA and sports routing bugs

V2 made targeted corrections.

### Change 1 — eliminate the `spa` / `space` collision

The beauty rule was changed away from unconstrained substring matching so that `Spa` would match but `Space` would not.

### Change 2 — explicit safe sports routes

Specific high-confidence FSQ sports categories received source-aware mappings:

- Basketball/volleyball/badminton/tennis/pickleball court → **93112**
- Gym → **93111**

### Change 3 — attempted generic sports branch

A broader rule was initially added for other FSQ Sports and Recreation categories, routing them toward PSIC `93`.

This later proved too aggressive.

### What V2 fixed

Correctly:

- Coworking Space stopped mapping to beauty
- Event Space stopped mapping to beauty
- Basketball Court mapped to 93112
- Gym mapped to 93111

### New problems discovered

The first SPA fix became too strict for multi-category FSQ values such as `Spa, Massage Clinic`, and the blanket Sports → `93` fallback created unjustified proposals for categories such as Swimming Pool, Dance Studio, Yoga Studio, Sauna, and Recreation Center.

### V2 lesson

> Fix confirmed systematic routing errors, but do not generalize from a few valid leaf mappings into a whole-hierarchy mapping rule.

---

## 4. V3 — conservative sports fallback + token-safe SPA matching

V3 corrected the overreach introduced in V2.

### Change 1 — token-aware SPA detection

SPA recognition was made token-aware so that `Spa` and `Spa, Massage Clinic` remain valid while `Coworking Space` and `Event Space` do not match.

### Change 2 — remove blanket Sports → `93`

The generic sports fallback was removed.

Only explicit safe routes remained:

- Basketball Court → 93112
- Gym → 93111

Generic sports categories returned to raw retrieval / candidate behavior.

### V3 results

Approximate deterministic coverage:

- **251,898 occurrences**
- **27.6% weighted coverage**

Counts:

- `EXACT`: 526
- `SUBTREE`: 966
- `NOT_ACTIVITY`: 325
- `UNCODEABLE`: 5
- review candidates: 12,143

### New problem discovered

Removing the explicit Sports → 93 rule was not enough. Raw semantic retrieval still confidently promoted many sports categories to the one-letter PSIC section `S`.

This was not just a sports issue. Raw retrieval also produced one-letter section proposals such as:

- `F` — Construction
- `O` — Administrative and Support Service Activities
- `C` — Manufacturing

Diagnostic:

- **305 raw-category automatic mappings**
- **5,386 occurrences**
- terminating at one-letter PSIC sections

### V3 lesson

> A high semantic score can still reflect only broad topical similarity. Retrieval confidence is not classification confidence.

---

## 5. V4 — prevent raw retrieval from auto-promoting one-letter PSIC sections

V4 introduced a general granularity guard.

### Rule

Raw semantic retrieval could still generate candidates, but it could no longer automatically propose a one-letter PSIC section.

Conceptually:

```python
and not (
    plan.rule == "raw_category"
    and len(codes[0]) == 1
)
```

Explicit source-aware rules were unaffected.

### Why this was better than more sports exceptions

The same failure mode appeared across multiple PSIC sections, so the correct abstraction was broader:

> **Raw lexical retrieval must not convert a source label directly into an entire PSIC section-level classification.**

### V4 results

- **1,517 deterministic proposals**
- **246,512 proposed occurrences**
- **27.0% weighted coverage**
- review candidates: 12,448

Breakdown:

- `EXACT`: 526 / 80,765 occurrences
- `SUBTREE`: 661 / 82,161
- `NOT_ACTIVITY`: 325 / 57,880
- `UNCODEABLE`: 5 / 25,706

The guard worked exactly as intended:

```text
raw one-letter automatic suggestions:
Categories: 0
Occurrences: 0
```

V3 → V4 changed exactly the previously diagnosed set:

- **305 categories**
- **5,386 occurrences**

---

## 6. Formal V4 human audit

A formal audit sample was generated instead of continuing example-driven tuning.

### Audit design

The sample included:

- top high-frequency deterministic proposals
- all `UNCODEABLE`
- stratification by mapping kind, source, and frequency band
- random low-frequency tail

Final audit:

- **315 rows**
- representing **242,501 occurrences**

### Audit composition

Suggested kind:

- `SUBTREE`: 124
- `EXACT`: 121
- `NOT_ACTIVITY`: 65
- `UNCODEABLE`: 5

Source:

- FSQ: 205
- OSM: 60
- Overture: 50

### Human adjudication

| Decision | Rows | Weighted occurrences |
|---|---:|---:|
| CORRECT | 183 | 216,473 |
| TOO_BROAD | 3 | 16,342 |
| TOO_NARROW | 86 | 7,919 |
| WRONG_CODE | 18 | 1,480 |
| WRONG_KIND | 25 | 287 |

Strict weighted correctness:

```text
89.3%
```

Semantically safe weighted precision (`CORRECT + TOO_BROAD`):

```text
96.0%
```

The three `TOO_BROAD` cases were principally safe beauty/hairdressing branch mappings and were not considered blocking failures.

---

## 7. What the V4 audit revealed

The remaining failures were structural, not isolated examples.

### Failure family 1 — raw retrieval still auto-promoted finer codes

The V4 one-letter guard solved broad sections, but `raw_category` retrieval could still automatically propose 2-, 3-, or 5-digit codes.

This produced high-confidence but semantically wrong mappings such as patterns equivalent to:

- insurance agency → one particular insurance class
- water park → water transport
- animal shelter → animal production
- employment law → employment activities
- bowling-related labels → manufacturing of bowling equipment

The problem was not score-threshold calibration. Some wrong mappings had strong scores and margins.

**Conclusion:**

> **Raw-category retrieval must be candidate-only at every PSIC level.**

Do not restore automatic classification from `raw_category`, even with higher thresholds.

### Failure family 2 — compound source labels collapsed to one narrow activity

Examples include Overture categories with explicit alternatives/conjunctions, plus FSQ/OSM values containing multiple meaningful labels.

Examples:

```text
bank_or_credit_union
flowers_and_gifts_store
corporate_or_business_office
building_or_construction_service
```

**Conclusion:**

> Compound source categories must remain candidates unless all meaningful components are demonstrably compatible.

### Failure family 3 — `NOT_ACTIVITY` precedence over mixed values

Mixed values could contain patterns such as:

```text
Structure + Hardware Store
Apartment + Veterinarian
Structure + Coworking Space
```

and become `NOT_ACTIVITY` merely because a non-activity token was present.

**Conclusion:**

> `NOT_ACTIVITY` requires unanimity: no conflicting activity-bearing component may be present.

### Failure family 4 — generic Overture `*_store` over-specialization

The generic suffix rewrite was useful for candidate generation but not sufficiently reliable for automatic classification.

Examples included problematic categories such as:

- `flowers_and_gifts_store`
- `pet_store`

**Conclusion:**

> Overture store-suffix inference remains a candidate-generation heuristic, not an automatic mapping rule.

### Failure family 5 — ambiguous OSM pet-shop semantics

`shop=pet` and related tags may represent live animal retail, pet supplies, grooming, or mixed pet services.

**Conclusion:**

> Ambiguous pet-shop categories remain candidates.

---

## 8. V5 — structural safety guards

V5 intentionally reduced automatic coverage in exchange for very high precision.

### V5 Rule 1 — raw retrieval is candidate-only

If:

```python
plan.rule == "raw_category"
```

semantic retrieval may rank PSIC nodes, but it cannot become an automatic `REVIEW_REQUIRED` proposal.

**Do not revert this.**

Do not assume that high score + high margin makes raw retrieval safe. The V4 audit disproved that.

### V5 Rule 2 — compound source categories are candidate-only

V5 detects multiple independently meaningful components.

#### Foursquare

Multiple labels are recognized only when another known FSQ root begins after a delimiter. This avoids incorrectly splitting internal commas in labels such as:

```text
Cafe, Coffee, and Tea House
```

But this is compound:

```text
Retail > Convenience Store,
Retail > Market
```

#### OSM

Multiple OpenPlaces OSM category tags joined by:

```text
 | 
```

are compound.

#### Overture

Explicit conjunction/disjunction labels such as `_and_` and `_or_` are compound.

Examples:

```text
bank_or_credit_union
building_or_construction_service
corporate_or_business_office
party_and_event_planning
```

These remain review candidates even when a semantic route exists.

**Do not revert this by selecting the “best” component.**

### V5 Rule 3 — `NOT_ACTIVITY` requires a non-compound value

Conceptually:

```python
if not compound_source and _is_non_activity(...):
    ...
```

Thus a value like:

```text
Structure + Hardware Store
```

cannot automatically become `NOT_ACTIVITY`.

**Do not restore “any non-activity token wins” behavior.**

### V5 Rule 4 — Overture store-suffix retrieval is candidate-only

The generic `*_store` rewrite remains useful for retrieval but is not a curated high-confidence ontology mapping.

Therefore `overture:store_suffix` cannot by itself become an automatic deterministic proposal.

Explicit curated Overture mappings such as `convenience_store`, `grocery_store`, `hardware_store`, and `clothing_store` are different and remain valid.

### V5 Rule 5 — ambiguous OSM pet shops remain candidates

Atomic ambiguous OSM pet labels are explicitly prevented from auto-classification.

They remain candidate-generating evidence.

---

## 9. V5 validation results

V5 results:

- **13,965 total categories**
- **117 automatic deterministic proposals**
- **225,791 proposed occurrences**
- **24.73% weighted deterministic coverage**
- **13,848 review candidates**

Mapping counts:

- `EXACT`: 36
- `SUBTREE`: 47
- `NOT_ACTIVITY`: 29
- `UNCODEABLE`: 5

Structural assertions all passed:

```text
raw_category auto proposals: 0
heuristic store-suffix auto proposals: 0
OSM atomic pet-shop auto proposals: 0
```

---

## 10. V5 against the human V4 audit

Of the original 315-row adjudicated V4 audit, V5 retained automatically:

- **86 audit rows**
- representing **225,214 occurrences**

Human decision among retained V5 mappings:

| Decision | Rows | Occurrences |
|---|---:|---:|
| CORRECT | 83 | 208,872 |
| TOO_BROAD | 3 | 16,342 |
| unsafe categories | 0 | 0 |

Strict weighted correctness:

```text
92.744%
```

Semantically safe weighted precision:

```text
100.000%
```

Unsafe retained rows:

```text
0
```

Unsafe retained occurrences:

```text
0
```

This met the pre-specified V5 acceptance criterion.

---

## 11. Why coverage falling from 28.14% to 24.73% is intentional

A future agent may compare:

```text
V1: 28.14%
V5: 24.73%
```

and attempt to “recover” the lost coverage.

That would be a mistake.

The lost coverage disproportionately consisted of categories for which deterministic confidence was not justified.

The deterministic layer is **not intended to classify every category**.

The remaining review candidates are explicitly reserved for:

- human review
- model-assisted classification
- later principled agreement rules
- future curated source mappings supported by evidence

A lower-coverage but auditable deterministic foundation is preferred to a higher-coverage layer that embeds false certainty.

---

## 12. Anti-regression rules for future agents

Treat the following as **design invariants** unless new empirical evidence from a comparable audit demonstrates that they can safely change.

### MUST NOT: restore raw-category auto-promotion

Bad:

```python
if score >= threshold:
    automatically_map(raw_category)
```

Even if the score and margin are high, raw semantic retrieval is candidate generation only.

### MUST NOT: blanket-map FSQ Sports and Recreation

Do not restore:

```text
FSQ Sports and Recreation → PSIC 93
```

or:

```text
FSQ Sports and Recreation → PSIC S
```

Specific safe mappings such as Basketball Court → 93112 and Gym → 93111 are valid because they were individually grounded.

### MUST NOT: use substring `"spa" in joined`

This reintroduces:

```text
Coworking Space → beauty
Event Space → beauty
```

SPA matching must remain token/segment aware.

### MUST NOT: classify compound values from one component

Bad logic:

```text
value contains A + B
A maps strongly to X
therefore whole value = X
```

Compound values remain candidates unless an explicit agreement mechanism shows that all meaningful components converge.

### MUST NOT: let `NOT_ACTIVITY` override mixed activity labels

Bad:

```text
Structure + Hardware Store → NOT_ACTIVITY
```

Presence of one non-activity category is insufficient when another component represents an activity.

### MUST NOT: treat generic `*_store` inference as equivalent to curated rules

Explicit curated rules and generic lexical suffix heuristics are not the same thing.

### MUST NOT: tune only on hand-picked examples

Changes after V5 should be justified by:

1. a defined error family,
2. a sufficiently large audit sample,
3. weighted occurrence impact,
4. regression tests,
5. before/after precision and coverage.

---

## 13. Safe future extensions

### A. Agreement-based compound resolution

A future system may safely auto-map compound values if each independently parsed component maps to the same PSIC branch.

For example:

```text
component A → 4772
component B → 4772
```

could potentially justify a common mapping.

But this must be implemented as an explicit agreement rule. Do not simply choose the highest-scoring component.

### B. Additional curated source mappings

New deterministic mappings may be added when:

- source semantics are unambiguous,
- PSIC target semantics are unambiguous,
- mapping is supported by actual source ontology meaning,
- regression tests are added,
- audit evidence supports the rule.

### C. Model-assisted candidate classification

The remaining review candidates are the correct place for:

- an LLM
- embedding reranking
- constrained classification
- human-in-the-loop review

The model-assisted layer should sit **after** deterministic V5 rather than replacing its safety guarantees.

---

## 14. Current conceptual architecture

```text
OpenPlaces canonical POIs
        |
        v
distinct source-category worklist
        |
        v
+--------------------------------------+
| V5 deterministic high-precision layer|
+--------------------------------------+
        |
        +--> explicit source rules
        |
        +--> exact safe activity routes
        |
        +--> NOT_ACTIVITY
        |
        +--> UNCODEABLE
        |
        +--> guarded semantic retrieval
                    |
                    +--> automatic only when allowed
                    |
                    +--> otherwise REVIEW_CANDIDATES
                                      |
                                      v
                         model / human review layer
```

The most important architectural separation is:

```text
candidate generation != deterministic classification
```

Retrieval can be permissive. Automatic classification must be conservative.

---

## 15. V5 freeze state

At the end of V5:

```text
pytest: PASS
ruff: PASS
```

Human-audited retained deterministic layer:

```text
semantic weighted safety: 100%
unsafe retained audit rows: 0
unsafe retained audit occurrences: 0
```

This is the baseline against which future deterministic-layer changes should be evaluated.

If a proposed change increases automatic coverage but reduces this safety profile, the burden of proof is on the proposed change.

---

## 16. Short instructions to an agent

If you are an agent modifying `placetype-ph`, read this before changing deterministic PSIC suggestion behavior.

1. **Do not optimize for automatic coverage.**
2. **Do not auto-promote raw semantic retrieval.**
3. **Do not collapse compound source categories without agreement.**
4. **Do not let one non-activity component dominate a mixed value.**
5. **Do not restore generic Sports → 93/S routing.**
6. **Keep SPA matching token-aware.**
7. **Distinguish curated ontology rules from lexical heuristics.**
8. **Preserve uncertain cases as review candidates.**
9. **Any relaxation of V5 guards requires an empirical before/after audit.**
10. **Regression tests must accompany every new deterministic rule.**

The intended optimization objective is:

> **maximize useful deterministic coverage subject to a very high precision constraint, not maximize coverage subject to a score threshold.**
