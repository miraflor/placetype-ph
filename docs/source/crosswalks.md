# Reviewed mappings and review worklists

<p class="lede">A crosswalk row is a versioned, source-specific assertion about admissible taxonomy codes—not an opaque classification prediction.</p>

## Five mutually distinct mapping kinds

| Mapping | Meaning | Codes |
| --- | --- | --- |
| `EXACT` | The reviewed label supports this exact code. | Exactly one. |
| `SUBTREE` | The label supports this code or an unspecified descendant. | Exactly one root. |
| `UNION` | Admissible alternatives lie in multiple distinct subtrees. | At least two nonredundant roots. |
| `NOT_ACTIVITY` | Source denotes a feature that is not a PSIC economic activity. | None. |
| `UNCODEABLE` | Source category is too broad/semantically weak to code. | None. |

**Important:** `NOT_ACTIVITY` means something different from an unknown or ambiguous business. `UNCODEABLE` is not an assertion that an economic activity does not exist. Neither may be converted to a code by a blind LCA.

## Bundled reviewed inventory

PlaceType PH 0.1.2 carries **346 reviewed PSIC Rev. 5 entries**, consisting of 249 `SUBTREE`, 45 `EXACT`, four `UNION`, 17 `NOT_ACTIVITY`, and 31 `UNCODEABLE` rows: 201 Overture and 145 OSM. The first 328 are the transferred reviewed set; eighteen were added after review of PSIC title-search results, and seven of those correct the search result. The 178-rule specialized advisory catalog is a **different** resource with a different authority level. The older 1,993-row worklist is not 1,993 accepted decisions.

These counts describe reviewed *rules*, not the number of establishments correctly classified in a benchmark.

## A rule has scope

A crosswalk identity includes `source`, `source_value`, `source_field`, `scheme`, and `version`. Matching uses `match_type = exact | contains | regex`. Normalization is performed consistently for literal rules; regex is compiled and validated separately. Conflicting duplicate exact rules fail validation. If overlapping contains/regex rules imply different decisions, the observed field is flagged as ambiguous and excluded from that field's accepted evidence (or made fatal with `--fail-on-ambiguous-crosswalk`).

| Column | Example | Meaning |
| --- | --- | --- |
| `source` | `osm` | Source namespace. |
| `source_value` | `shop=pharmacy` | Observed category label. |
| `source_field` | `category` | Category or name matching. |
| `scheme` / `version` | `psic` / `rev5` | Statistical scope. |
| `mapping_kind` | `SUBTREE` | Meaning of the assertion. |
| `codes` | `4772` | The asserted root(s); `|` for multiple. |
| `match_type` | `exact` | Literal/substring/regex semantics. |

The illustrative row above is not a recommendation to add a duplicate to the bundled rules: the package already loads reviewed PSIC Rev. 5 mappings by default.

## Worklist → suggestion → human review → accepted rule

The `crosswalk-init` command enumerates observed vocabulary and frequency. `crosswalk-suggest` uses local lexical retrieval and review-status policies to produce candidate categories with reasons and scores. A suggestion is not a reviewed mapping; it must be promoted under the appropriate Express policy or explicitly adjudicated. The read-only reconciliation tool compares outside rule candidates to local reviewed states, reportable hierarchy relations, and specialized advisory evidence but never silently imports unreviewed authority.

```powershell
placetype crosswalk-init path\to\canonical_pois.parquet --scheme psic
if ($LASTEXITCODE -ne 0) { throw "Worklist creation failed" }
```

The worklist normally orders category values by coverage so a reviewer can address frequently occurring observed labels first. Repeated runs preserve reviewed rows and refresh frequencies; absent reviewed categories are retained as zero-observation rows.

## Why review remains essential

One OSM or Overture category can mean an activity, a physical structure, a product line, a facility name, or a broad venue. High lexical similarity between a source label and an official taxonomy title does **not** prove equivalence. The review process must choose the defensible subtree or abstain.

**Implementation:** [`crosswalk.py`](https://github.com/miraflor/placetype-ph/blob/main/src/placetype_ph/crosswalk.py) · [reviewed integration](https://github.com/miraflor/placetype-ph/blob/main/docs/REVIEWED_PSIC_INTEGRATION.md) · [reconciliation note](https://github.com/miraflor/placetype-ph/blob/main/docs/PRIORITY2_CROSSWALK_RECONCILIATION.md).
