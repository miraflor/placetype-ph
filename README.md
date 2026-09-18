# placetype-ph

A downstream economic-semantic layer for [`openplaces-ph`](https://github.com/miraflor/openplaces-ph).
It classifies canonical OpenPlaces entities against Philippine statistical taxonomies while
preserving uncertainty, hierarchy depth, source provenance, and an audit trail.

The design rule is simple:

> Return the deepest code supported by the evidence. If the evidence cannot distinguish
> children, stop at the parent. Never force a leaf.

## Scope

The package supports three taxonomies with different semantics:

| Scheme | Unit being classified | Policy |
|---|---|---|
| **PSIC Revision 5** | principal economic activity of an establishment | primary OpenPlaces classification |
| **PCPC 2002** | goods/services supplied | OpenPlaces-only results are *potential product/service families*, not a complete product basket |
| **PSCC 2022** | traded commodities | requires explicit product/commodity evidence or a confirmed crosswalk; never inferred from a place name alone |

The taxonomy engine is generic. Backoff follows the actual parent graph rather than chopping
characters from codes.

## Pipeline

```text
OSM + Overture + Foursquare
            |
            v
      openplaces-ph
            |
            v
 canonical_pois.parquet
            |
            v
 source-category crosswalks      <- deterministic, reviewed once per distinct category
            |
            v
 dependency-aware tree fusion    <- Overture/FSQ provenance is not double-counted
            |
       +----+----+
       |         |
    resolved   unresolved
       |         |
       |     lexical taxonomy retrieval (local)
       |                |
       |     constrained Qwen traversal (optional)
       |       CHILD | STOP_HERE | INSUFFICIENT
       |         |
       +----+----+
            v
 deepest defensible taxonomy node
            |
            v
 entity_classifications.parquet
 classified_pois.parquet
```

Semantic conflicts can also flag a suspicious OpenPlaces entity cluster. The classifier never
silently rewrites OpenPlaces entity resolution.

## Installation

### Conda

```powershell
conda env create -f environment.yml
conda activate placetype-ph
python -m pip install -e .
placetype version
```

For development:

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
```

The deterministic classifier does not require an LLM client. If you install with pip and want
Hugging Face or Ollama inference, add the optional client:

```powershell
python -m pip install -e ".[llm]"          # from a source checkout
python -m pip install "placetype-ph[llm]"   # from a released package
```

The supplied Conda environment already includes the LLM client.

## 1. Diagnose, then build the reference taxonomies

The repository stores normalized taxonomies as small Parquet trees, but generated copies are
not committed by default. This makes the source version explicit and avoids silently freezing
an old classification revision.

**For the first live run, diagnose before fetching.** Diagnosis parses the real source as far as
possible, prints every finding it can collect, and writes no normalized taxonomy file. It reports
sheet/parser resolution, discard buckets and examples, level histograms, structural findings,
published-count deviations where verified, and whether a normal official fetch would save or
refuse the tree.

```powershell
placetype taxonomy diagnose psic
placetype taxonomy diagnose pscc

$env:PSA_CLASSIFICATION_TOKEN="your_token"
placetype taxonomy diagnose pcpc
```

For PSIC Revision 5, the published structure is 22 sections, 88 divisions, 260 groups, 493
classes and 1,338 sub-classes. Exact PCPC 2002 and PSCC 2022 counts are **not** hard-coded in
v0.1.1. PSA's current PCPC pages disagree on the division/item totals, and the PSCC pages expose
the hierarchy but not one unambiguous count table. The first live diagnoses should establish the
source/API counts before either scheme is frozen as a regression fixture.

### PSIC Revision 5

```powershell
placetype taxonomy fetch psic
```

This uses the official PSA **PSIC Revision 5 Detailed Structure** spreadsheet rather than the
currently stale PSIC API documentation, which still documents the 2019 version.

Expected output:

```text
reference/psic_rev5/nodes.parquet
```

### PSCC 2022

```powershell
placetype taxonomy fetch pscc
```

Expected output:

```text
reference/pscc_2022/nodes.parquet
```

### PCPC 2002

The PSA PCPC API requires a token. Set it once:

```powershell
$env:PSA_CLASSIFICATION_TOKEN="your_token"
placetype taxonomy fetch pcpc
```

Expected output:

```text
reference/pcpc_2002/nodes.parquet
```

Validate any normalized taxonomy with:

```powershell
placetype taxonomy validate reference/psic_rev5/nodes.parquet
```

Validation checks the shape of the tree, not only its internal consistency, and separates two
different findings:

| Finding | Full official `fetch` | Partial/manual import | Meaning |
|---|---|---|---|
| **structural error** | fatal | fatal unless explicitly allowed | a node lost its parent, points sideways/upward, or attaches to an incompatible code branch |
| **level gap** | fatal | warning by default | an intermediate level is absent, so a node attaches to an ancestor |

A level gap is legitimate in a deliberately partial extract, but it still removes an available
backoff level. Therefore `taxonomy fetch`, whose job is to build the full official taxonomy, is
strict by default and also requires every expected hierarchy level to be present. Manual
`import-xlsx` remains gap-tolerant unless `--strict-levels` is requested.

```powershell
# the workbook has an explicit parent or section column
placetype taxonomy import-xlsx psic.xlsx --scheme psic --version rev5 `
  --output reference\psic_rev5\nodes.parquet --section-column "Section"

# require a partial/manual import to contain every hierarchy level too
placetype taxonomy import-xlsx psic.xlsx --scheme psic --version rev5 `
  --output reference\psic_rev5\nodes.parquet --strict-levels

# deliberately accept a gapped official source (normally do not do this)
placetype taxonomy fetch psic --no-strict-levels

# override a known published category-count mismatch (normally do not do this)
placetype taxonomy fetch psic --allow-structure-deviation

# accept a tree with lost parents deliberately
placetype taxonomy fetch psic --allow-orphan-nodes
```

`placetype taxonomy validate` reports the same findings. Use `--strict-levels` when validating a
full official tree; leave it off when intentionally validating a partial extract.

### Codes that lost their leading zeroes

A spreadsheet column stored as numbers turns PSIC division `01` into `1` and group `011` into
`11`. The consequence is worse than a dropped row: `11` is then read as a division, so the whole
branch moves up one level while every structural check above still passes. The importer therefore
refuses any code shorter than the scheme's minimum width, naming the offending values, rather than
guessing a padding it cannot justify. PCPC sections are genuinely one digit, so the floor is per
scheme.

There are two ways forward. Re-export the code column as text, or tell the importer which level
each row is on, which makes the intended width unambiguous and repairs the codes:

```powershell
placetype taxonomy import-xlsx psic.xlsx --scheme psic --version rev5 `
  --output reference\psic_rev5\nodes.parquet --level-column "Level"
```

Accepted level names are the scheme's own (`Section`, `Division`, `Group`, `Class`, `Sub-class`,
`Item`, `Chapter`, `Heading`, and so on). When a level column is in use the flat-table parse is
authoritative and the visual-structure fallback scanner is skipped, so it cannot re-add the same
nodes at their unpadded codes.

Code syntax is conservative too. PSIC and PCPC accept integral digit codes (plus PSIC section
letters); an Excel-style trailing zero decimal such as `10.0` is safely normalized to `10`. Other
decimal/sign syntax such as `1.5` or `-11` is rejected instead of stripping punctuation and
turning it into a plausible code. PSCC additionally allows its legitimate dot/hyphen/space display
separators.

### Repeated codes

An official workbook repeats codes: PSA publishes a Summary of Classification Scheme beside the
Detailed Classification, so the same code can appear more than once and the wording of its title
may differ. That is a wording variant. Repeated definitions are sent through one global merge
whether they occur on the same worksheet or different worksheets. The importer preserves the
explicit parent plus the fuller title and richer description/include/exclude fields independently,
so choosing a terse summary row cannot discard detailed explanatory notes.

A repeated code at a different **level**, or under a different explicit **parent**, is a real
structural contradiction rather than a wording difference, and it still fails the import instead of
becoming first-wins by sheet order.

### Checking against the published structure

PSIC Revision 5 has an unambiguous published category count: 22 sections, 88 divisions, 260
groups, 493 classes and 1,338 sub-classes. After any PSIC import, and on every `placetype taxonomy
validate`, those counts are compared with the built tree and deviations are reported. PCPC 2002
and PSCC 2022 exact counts are deliberately left unfrozen in v0.1.1 until the current API/workbook
has been diagnosed live.

For a manual `import-xlsx` this comparison is advisory, because a deliberate partial extract is
legitimate. For `taxonomy fetch`, which claims to create the complete official reference tree, a
known published-count mismatch is a gate by default: the incomplete tree is not saved. Use
`--allow-structure-deviation` only when you deliberately want to override that protection.
`taxonomy validate --strict-structure` applies the same count gate to an already saved tree.

The purpose is to catch silent structural loss that internal tree rules cannot see. An unrecognised
section letter can remove a whole branch while leaving every surviving node internally consistent.
PSIC Revision 5 section letters therefore run A to V, not A to U.

### What the import discarded — and what it merged

Both import paths count every source row they could not use and report it: rows with no usable
code or title, codes whose digit length matches no level, codes contradicting a declared level,
invalid explicit parents, and code cells whose syntax is not safely interpretable. **Invalid code
syntax is a visible non-fatal discard** during workbook parsing; it is never rewritten into a
plausible code. For official fetches, structural and published-count gates decide whether those
discards actually made the resulting taxonomy incomplete. A lost-leading-zero signature remains
fatal because it can construct a coherent but wrong tree.

Repeated codes are different: they are merged, not counted as discarded rows. Title variants and
benign duplicate definitions are reported separately, while contradictory levels or explicit
parents remain fatal in a normal import. Library callers can inspect the same diagnostics through
`ImportReport`.

If PSA changes a workbook layout and automatic normalization cannot identify its columns, the
original workbook is retained under `reference/downloads/`; use `taxonomy import-xlsx` with
explicit column names. Supplying any explicit column also disables the visual-structure fallback
scanner, since the caller has stated the schema and the scanner would reconstruct a different one.
Explicit column names are authoritative: if one is misspelled or absent,
the import fails instead of letting the heuristic raw-layout scanner reinterpret the workbook.

`--allow-orphan-nodes` relaxes only orphan/wrong-parent validation. It does **not** disable
`--strict-levels` and it never turns malformed or leading-zero-corrupted codes into warnings.
An explicit parent is authoritative too: an invalid parent is reported rather than silently
replaced by a prefix-derived parent.

Taxonomy text and POI text deliberately use different missing-value semantics. Generic source
labels such as `other`, `unknown` and `n/a` can be treated as missing classification evidence in
OpenPlaces fields, while official taxonomy titles are preserved literally. This matters because
valid classification nodes can themselves be titled **Other**. Pandas `NaN`/`pd.NA` values are
recognized before string conversion in both paths.

For PCPC, pagination follows an API-provided `next` cursor when present; otherwise it continues
page-number requests until an empty page (with repeat detection for servers that ignore `page`).
The official fetch refuses individual API rows that have no usable code or title rather than
silently returning a partial taxonomy.

## 2. Inspect an OpenPlaces build

The OpenPlaces output does **not** need to be copied into this repository. Point PlaceType PH at
the existing Parquet file by relative or absolute path. For the Iloilo build, when the two repos
are sibling folders:

```powershell
$iloilo = "..\openplaces-ph\data\output\areas_iloilo\canonical_pois.parquet"
Test-Path $iloilo
placetype inspect $iloilo --top 30
```

This reports the distinct FSQ, Overture and OSM category vocabularies and their frequencies.

## Express: OpenPlaces straight to QGIS

For the normal zero-intervention path, point `express` directly at an OpenPlaces
`canonical_pois.parquet`:

```powershell
placetype express `
  ..\openplaces-ph\data\output\areas_metro_manila\canonical_pois.parquet
```

By default Express uses the current joint **PSIC + PCPC + PSCC** workflow and writes:

```text
output\areas_metro_manila_placetype.parquet
```

The upstream OpenPlaces file is read only. Express writes its temporary work under
`data/work/express/` and its reusable classification run under `output/express/`.

Express is an orchestration layer over the normal commands. It:

1. reuses each normalized taxonomy when its `nodes.parquet` already exists;
2. if a tree is missing, first rebuilds it from an already-downloaded PSA workbook;
3. only if neither exists does it use the normal taxonomy fetch path;
4. creates one joint PSIC/PCPC/PSCC worklist for the current OpenPlaces input;
5. seeds that worklist from `reference/crosswalks/joint.csv` when available, otherwise
   retaining the legacy per-scheme migration behavior;
6. runs the current batched first-pass suggestion and bounded peer-context recheck;
7. promotes a first-pass suggestion only when the peer recheck did not contradict it;
8. leaves candidate-only and contradicted suggestions unresolved rather than
   manufacturing a taxonomy code;
9. runs deterministic POI classification with the resulting joint crosswalk; and
10. writes the normal compact QGIS GeoParquet with status columns by default.

Reviewed mappings always outrank automatic suggestions. The recheck remains diagnostic:
Express never turns `recheck_top_code` into a new mapping.

### What the recheck can and cannot tell you

The peer recheck compares a suggestion against the same retrieval call with peer labels
from the other taxonomies appended. It can only do that when the peer rows in the same
joint group already carry a code. On a cold start almost none of them do, so most rows
come back `NO_PEERS`: the comparison never ran. `NO_CONTROL_HIT`, `NO_RECHECK_HIT` and
`MISSING_TAXONOMY` mean the same thing for a different reason.

Express therefore separates four outcomes, and reports them separately:

| Recheck outcome | `recheck_status` | Express |
| --- | --- | --- |
| Cleared | `STABLE` | `AUTO_ACCEPTED` |
| Contradicted | `SHIFT` | `HELD_RECHECK` |
| Inconclusive | `SHIFT_WEAK` | `AUTO_ACCEPTED_INCONCLUSIVE`, or `HELD_INCONCLUSIVE` under `--hold-inconclusive` or `--no-promote-unchecked` |
| No verdict / candidate diagnostic | `CANDIDATE_STABLE`, `CANDIDATE_SHIFT`, `NO_PEERS`, `NO_CONTROL_HIT`, `NO_RECHECK_HIT`, `MISSING_TAXONOMY`, anything unrecognized | candidate-only rows stay `UNRESOLVED`; otherwise `AUTO_ACCEPTED_UNCHECKED`, or `HELD_NO_VERDICT` under `--no-promote-unchecked` |

Inconclusive and no verdict are kept apart because they are different states. `SHIFT_WEAK`
means the comparison ran and the top candidate moved, just by less than `min_margin`.
`NO_PEERS` means no comparison happened. Both are promoted by default, but only the first
is evidence, and only the first can be held on its own:

```powershell
placetype express canonical_pois.parquet --hold-inconclusive
```

That holds suggestions the recheck argued against while still promoting the ones it never
saw, which is usually what you want once part of the crosswalk is reviewed.

One consequence worth knowing. The comparison runs over first-pass codes, so a suggestion
that assigns no code, such as `NOT_ACTIVITY`, cannot reach `STABLE` through the peer
recheck. Under `--no-promote-unchecked` those rows stay held until reviewed directly or
unchecked promotion is allowed. Express reports how many held rows are in that position.

Only `STABLE` and `SHIFT` are decisive for Express. Candidate-only statuses describe rows
that did not yet carry an accepted first-pass code, so they stay diagnostic rather than
entering the promotion-policy verdict. `SHIFT_WEAK` is different: the comparison ran but
did not meet the configured margin, so Express records it as `INCONCLUSIVE`.

By default a row with no verdict is still promoted, because otherwise a first run against
an unreviewed input would produce nothing. It is recorded as `AUTO_ACCEPTED_UNCHECKED` in
`express_status`, and the run prints how many mappings rest on no evidence. To accept only
suggestions the recheck actually cleared:

```powershell
placetype express canonical_pois.parquet --no-promote-unchecked
```

Selecting a single scheme removes the peer comparison entirely, since a joint group then
holds one row and there is no peer taxonomy to compare against. Express says so before it
starts.

The workflow is resumable. Its cache key includes the OpenPlaces file state, selected
schemes, package version, taxonomy fingerprints, the reviewed crosswalk state, the
product column, the promotion policy, and every threshold Express passes to
`crosswalk-suggest` and `classify`. Changing any of them starts a new run rather than
reusing an old one. Small inputs are keyed by content, so copying or re-cloning the
repository does not invalidate a valid run. An unchanged automatic crosswalk and
completed classification run are reused.

Advanced users can still select schemes explicitly:

```powershell
placetype express canonical_pois.parquet --schemes psic
```

For PCPC/PSCC, explicit product or commodity text can be passed through to `classify`:

```powershell
placetype express enriched_places.parquet `
  --product-column product_text
```

Express deliberately uses deterministic `--llm none`. The product column therefore does
not by itself infer a PCPC or PSCC code; it supplies product evidence to the underlying
scheme policy. Use the lower-level `classify` command with a traversal backend when you
want free product text to drive taxonomy retrieval and classification.

If PCPC must be fetched because both its normalized tree and retained local workbook are
absent, set `PSA_CLASSIFICATION_TOKEN` or pass `--token`.

## 3. Create the crosswalk worklist

The expensive unit is the **distinct category value**, not the POI row.

```powershell
placetype crosswalk-init `
  ..\openplaces-ph\data\output\areas_iloilo\canonical_pois.parquet `
  --output reference\crosswalks\psic_rev5.csv `
  --scheme psic
```

The resulting CSV is ordered by row coverage. Fill these fields for reviewed rows:

```text
mapping_kind = EXACT | SUBTREE | UNION | NOT_ACTIVITY | UNCODEABLE
codes        = one code, or code1|code2|... for UNION
match_type   = exact | contains | regex
source_field = category | name
```

`CLASS` is also accepted as an alias for `EXACT` for compatibility with the original PSIC
pipeline terminology. Unreviewed rows may remain blank; the loader skips them.

`source_field` decides which OpenPlaces field the row is keyed on. `category` is the default.
Use `name` together with `match_type = regex` for chain or brand rules, for example a row that
maps a known bakery chain name straight to a class.
If both a category rule and a name rule match the same source, both are treated as dependent
evidence: a specific reviewed name rule may refine a broad category rule without becoming a second
independent vote. When the two assert disjoint subtrees the name rule wins, because the category
rule is a bulk mapping and the name rule is a specific reviewed judgment; the row is flagged
`CROSSWALK_NAME_RULE_OVERRODE_CATEGORY`.

Overlapping `contains`/`regex` rules that disagree are never resolved by CSV row order. By default
the affected field yields no mapping, the row is flagged `CROSSWALK_AMBIGUOUS:<source>` and left for
review, and the run continues; the offending rule sets are printed at the end and recorded in
`run.json`. Whether two scanned rules overlap depends on the data, so this can only be discovered
mid-run, and losing a whole build to one bad category value is the wrong trade. Pass
`--fail-on-ambiguous-crosswalk` to stop instead.

Rows are validated at load: `source`, `source_value`, `scheme`, `version` and `mapping_kind` must be
present; `source` must be `fsq`, `overture` or `osm`; `match_type` must be `exact`, `contains` or
`regex`; `confidence` must be a number between 0 and 1; `EXACT` and `SUBTREE` need exactly one code;
`UNION` needs at least two **distinct, non-redundant** subtree roots; `NOT_ACTIVITY` and `UNCODEABLE`
must carry none. Blank match values are rejected, including blank regular expressions that would
otherwise match every name. Duplicate exact rows are allowed only when they make the same decision.

Without `--output`, the worklist is written to `reference/crosswalks/<scheme>_<version>.csv`.
Re-running `crosswalk-init` on the same output path is safe. Reviewed rows are matched by
`(scheme, version, source, source_field, source_value)` and carried over, row counts are refreshed,
new categories are added, and a reviewed category that no longer appears in the build is kept with
a row count of zero. Rows for other schemes/versions and reviewed name rules are carried over
unchanged rather than copied into the new category worklist.

A coarse but correct mapping is allowed and encouraged. For example, if a source category
supports only a division, map it to that division rather than guessing a subclass.

### First Iloilo smoke test

Before reviewing the full crosswalk, verify the end-to-end file path on a small slice:

```powershell
placetype classify $iloilo --schemes psic --llm none --limit 100 `
  --output-dir output\iloilo-smoke
```

With an empty/unreviewed crosswalk this will intentionally leave many rows unresolved; the point is
to verify taxonomy loading, OpenPlaces ingestion and output writing. For a model-path pilot, use a
small sample first:

```powershell
$env:HF_TOKEN="hf_..."
placetype classify $iloilo --schemes psic --llm hf --limit 50 `
  --output-dir output\iloilo-qwen-pilot
```

Do not interpret this 50-row convenience slice as an accuracy estimate. The first real evaluation
should be a stratified adjudicated sample by `status` and `deciding_component`.

## 4. Run deterministic classification first

```powershell
placetype classify `
  ..\openplaces-ph\data\output\areas_iloilo\canonical_pois.parquet `
  --crosswalk reference\crosswalks\psic_rev5.csv `
  --llm none `
  --output-dir output\iloilo
```

This gives you the production baseline before any model is introduced.

## 5. Add the hosted open-source LLM fallback

The default hosted model is `Qwen/Qwen3-4B-Instruct-2507` through Hugging Face Inference
Providers. No model weights need to be downloaded.

```powershell
$env:HF_TOKEN="hf_..."

placetype classify `
  ..\openplaces-ph\data\output\areas_iloilo\canonical_pois.parquet `
  --crosswalk reference\crosswalks\psic_rev5.csv `
  --llm hf `
  --passes 3 `
  --output-dir output\iloilo
```

For a cheap pilot:

```powershell
placetype classify canonical_pois.parquet --llm hf --limit 100
```

LLM decisions are cached in `cache/decisions.sqlite`, keyed by taxonomy version **and taxonomy
content fingerprint**, inference provider/base URL, model, prompt fingerprint, evidence, restriction
set and pass count. Re-running the same unresolved case does not spend another API call. The prompt
fingerprint covers the system prompt, the
variant hints, the prompt template and the temperature, so editing any of them invalidates the
affected entries instead of silently reusing decisions made under the old wording.

### Local Qwen with Ollama

After installing/pulling Qwen in Ollama:

```powershell
placetype classify canonical_pois.parquet --llm ollama --model qwen3:4b
```

The classifier uses Ollama's OpenAI-compatible local endpoint. The rest of the code is unchanged.

## 6. PCPC and PSCC

You can run multiple taxonomies with the same engine:

```powershell
placetype classify canonical_pois.parquet `
  --schemes psic,pcpc `
  --llm hf
```

For product-level PCPC or PSCC classification, supply a column containing explicit product or
commodity text:

```powershell
placetype classify enriched_places.parquet `
  --schemes pcpc,pscc `
  --product-column product_text `
  --llm hf
```

Without a product column, PSCC returns `NO_PRODUCT_EVIDENCE` unless a confirmed source crosswalk
already supplies a PSCC mapping.

## Outputs

### `entity_classifications.parquet`

One row per entity × requested scheme. Important fields include:

- `canonical_id`
- `scheme`, `version`
- `code`, `level`
- `status`
- `deciding_component` (`CROSSWALK`, `FUSION`, `LLM`, `POLICY`)
- `candidate_codes`
- `evidence_sources`
- `flags`
- `traversal_agreement`
- `model`
- `audit`
- `classification_depth`, `branch_max_depth`, `max_depth`

Lists and audit objects are JSON-serialized inside the compact Parquet table.

> **Memory note:** row conversion is chunked for lower per-row overhead, but the current pipeline still
> materializes the full input DataFrame and the full classification result set before writing. A true
> Philippines-wide bounded-memory path will require batch Parquet read/write and is still open.

### `classified_pois.parquet`

A convenience spatial table retaining `canonical_id`, display name, coordinates/geometry and
compact scheme summaries such as `psic_code`, `psic_level`, and `psic_status`. If the input carries
GeoParquet metadata, the pipeline attempts to preserve it. A failure is now warned rather than
silently swallowed, and `run.json` records `classified_pois_geo_metadata` as `preserved`,
`source_has_no_geo_metadata`, or `failed:<ExceptionType>`.

### `gis.parquet`

An optional compact layer for QGIS. It is written on request, not by `classify`:

```powershell
placetype gis-export .\output\metro-manila-workers-8
```

It keeps `canonical_id`, `geometry`, each scheme's assigned code, and one column for every level
of that scheme, named by the taxonomy level rather than by code width:
`psic_section`, `psic_division`, `psic_group`, `psic_class`, `psic_subclass`;
`pcpc_section`, `pcpc_division`, `pcpc_group`, `pcpc_class`, `pcpc_subclass`, `pcpc_item`; and
`pscc_chapter`, `pscc_heading`, `pscc_hs_subheading`, `pscc_ahtn_subheading`,
`pscc_commodity`. Ancestor codes come from the taxonomy tree recorded in `run.json`, not from
truncating the assigned code, so a class-level PSIC assignment leaves `psic_subclass` empty rather
than presenting a subclass the classifier never chose. The separate `psic_code`, `pcpc_code`, and
`pscc_code` columns remain the actual assigned classifications and may therefore stop at any level.

Names, coordinates, taxonomy titles, candidate codes and audit fields are left out. `geometry` is
kept because the file is meant to be a standalone layer rather than a join table. Add
`--with-status` to carry `<scheme>_status`, which distinguishes an unclassified row from an
ineligible one. Output is Zstandard-compressed, and every code column is declared as a string, so
two runs of the same pipeline produce the same schema even when one of them resolved fewer levels.

The file records where it came from. Its Parquet metadata carries a `placetype` key naming the
run, the package version that created the classification, the package version that performed the
export, and both the run-recorded and export-time taxonomy fingerprints. This distinction matters
when an older run is exported after an upgrade or with the fingerprint check disabled. No export
timestamp is stored, so exporting an unchanged run twice produces the same bytes.

The export stops rather than writing a file it cannot vouch for. `canonical_id` and `geometry`
must be present; the summary must carry GeoParquet metadata; every non-empty code must exist in
the exact taxonomy version the run used; and the reference tree must still match the
`taxonomy_fingerprint` that `run.json` recorded. Pass `--no-check-taxonomy-fingerprint` to export
against a reference tree that was rebuilt after the run. The output path may not be `run.json`,
the classification summary, or any other existing output file the manifest names.

## 7. Evaluate the cascade

Keep a quarantined gold file with at least `canonical_id`, `scheme`, and `gold_code`, then report
accuracy and production rate separately at every taxonomy depth. Invalid or unknown gold codes are
errors; they are never silently dropped from the denominator.

```powershell
placetype evaluate output\iloilo\entity_classifications.parquet gold.csv --scheme psic
```

This writes `*_depth_metrics.csv` and `*_summary.json`. A row that stops honestly at a division
counts as produced at section/division depth but not at deeper group/class/subclass depths. The
summary also separates coded predictions into `exact`, `ancestor_backoff`, `over_specific`, and
`wrong_branch`. Its denominator is every coded row, including any prediction whose code is not in
the taxonomy, so it is comparable with `exact_code_accuracy` rather than flattering a run that
emitted unknown codes. `hierarchy_compatible_accuracy` counts exact predictions plus honest ancestor
backoff, so stopping at a defensible parent is no longer summarized as if it were a wrong branch.
Overall coverage, exact-code accuracy, invalid prediction-code counts and mean tree distance are
reported separately.

Gold rows that should receive **no taxonomy code** can be evaluated explicitly with an optional
`gold_status` column. Use `CODED` for ordinary rows, `NOT_CODEABLE` when the evidence supports no
code, or `NON_ECONOMIC_POI` for the PSIC eligibility case; leave `gold_code` blank for the latter
two. The summary reports their false-positive code count and `not_codeable_code_avoidance` rather
than quietly excluding them from evaluation. Rows with a blank/invalid code and no explicit
not-codeable status fail validation so a typo cannot improve the reported score.

`entity_classifications.parquet` also stores three depth fields on every result, so rollout
reports can measure how much of the dataset reaches each useful level without conflating coverage
with accuracy. `classification_depth` is the depth of the assigned code. `branch_max_depth` is the
deepest level reachable under that code, which is the right denominator when asking how far a
result could have gone. `max_depth` is the deepest level in the whole tree.

## Statuses and flags

Every result carries exactly one `status`. The full set:

| Status | Meaning |
|---|---|
| `SINGLE` | one mapped source, resolved in taxonomy space |
| `NESTED` | compatible mapped evidence agreed and one mapping refined another |
| `INTERSECT` | compatible mapped evidence agreed on an overlapping subtree |
| `UNION` | a deliberately broad mapping that no evidence narrowed |
| `EMPTY` | no source mapping applied and no model was configured |
| `CONFLICT` | independent source mappings occupy disjoint branches |
| `FUSION_BACKOFF` | the model reached no decision, so the deterministic code stands |
| `LLM_PARTIAL` | model stopped honestly above a leaf |
| `LLM_FULL` | model reached a leaf |
| `REVIEW` | no code from either the deterministic layer or the model |
| `REVIEW_ENTITY_MATCH` | semantic disagreement coincides with entity-resolution warnings |
| `NON_ECONOMIC_POI` | every source mapping says this is not an economic activity |
| `NO_PRODUCT_EVIDENCE` | PSCC was requested without commodity-level evidence |
| `POTENTIAL_PRODUCT_FAMILY_PARTIAL` / `_FULL` | the PCPC form of a partial or full result |

PCPC never uses the general status names. Whichever component decided it, a PCPC result is a
potential product/service family, so the label is the same for a crosswalk-resolved row and a
model-resolved row.

Flags are additive and any number may appear:

| Flag | Meaning |
|---|---|
| `DEPENDENT_CONFLICT:fsq-lineage` | FSQ and an Overture record carrying FSQ provenance disagree |
| `NO_COMMON_ANCESTOR` | the admissible set spans several roots |
| `LLM_NOT_CONFIGURED` | the row was unresolved and no model was available |
| `LLM_FELL_BACK_TO_FUSION` | the model produced nothing, so the crosswalk code was kept |
| `NO_CONSENSUS` | the passes disagreed |
| `INSUFFICIENT_AT_ROOT` | every pass refused at the root, which is agreement, not disagreement |
| `INSUFFICIENT_BACKOFF` | a pass descended, then rejected that node as unsupported and backed off to its parent |
| `UNBALANCED_PASS_VARIANTS` | `--passes` is not a multiple of the number of prompt variants |
| `CROSSWALK_AMBIGUOUS:<source>` | overlapping scanned rules disagreed, so that field supplied no mapping |
| `CROSSWALK_NAME_RULE_OVERRODE_CATEGORY` | a reviewed name rule contradicted the category rule and won |
| `PSCC_REQUIRES_PRODUCT_EVIDENCE` | PSCC policy blocked inference from a place name |
| `ENTITY_MATCH_SUSPECT_TRANSITIVE` | the cluster was completed transitively |
| `ENTITY_MATCH_SUSPECT_DISTANCE` | cluster members are at least 80 m apart |
| `ENTITY_MATCH_SUSPECT_LOW_MATCH_SCORE` | the weakest pair match in the cluster is below 0.85 |

A model is never allowed to remove a code the reviewed deterministic layer already supports. If traversal
ends without a decision and crosswalk fusion had a defensible code, that code is returned with
status `FUSION_BACKOFF`.

## Why the LLM is small

The model is not asked to memorize PSIC, PCPC or PSCC. For unresolved cases, a local word/character
TF-IDF retriever first finds up to 20 plausible taxonomy nodes using titles, descriptions and
inclusions (never exclusions). This is only a high-recall prompt restriction; it never overrides
a deterministic mapping. At each hierarchy node the model then receives only legal children
consistent with those candidates and the relevant inclusion/exclusion notes. It may return only:

```text
<one supplied child code>
STOP_HERE
INSUFFICIENT
```

Three differently worded precision/boundary/evidence passes are combined by majority path
consensus. They are **not** treated as independent annotators.

There are three prompt variants, so `--passes 3` is balanced. A pass count that is not a multiple
of three gives the earlier framings more weight in the vote; those results carry the
`UNBALANCED_PASS_VARIANTS` flag.

## Current PSA source versions

Every command takes `--version` and `classify` refuses to run if the tree stored at
`reference/<scheme>_<version>/nodes.parquet` does not declare the scheme and version that were
asked for. `classify` also warns when no loaded crosswalk row targets the scheme being run, which
is what a version string typo looks like from the inside.

The initial defaults are deliberately versioned:

- PSIC: **Revision 5 (2026)**
- PCPC: **2002**
- PSCC: **2022**

Source material comes from the Philippine Statistics Authority. The software is MIT licensed;
reference data retain the terms of their official sources. See `reference/README.md`.

## What this baseline intentionally does not do

This first implementation does not yet include SetFit, encoder fine-tuning, Dawid-Skene, synthetic
seeding, or conformal calibration. Those are evaluation-driven additions, not prerequisites.
The code first establishes the simpler measurable baseline:

1. official taxonomy trees;
2. reviewed distinct-category crosswalks;
3. provenance-aware fusion;
4. honest hierarchical backoff;
5. constrained LLM fallback;
6. auditable outputs.

Add complexity only when a held-out gold set shows where this baseline fails.

## Suggesting crosswalk mappings

After `crosswalk-init`, use the local taxonomy retriever to prepare a review file. This
never changes `mapping_kind` or `codes`; suggestions remain inert until reviewed.

```powershell
placetype crosswalk-suggest reference/crosswalks/psic_rev5.csv
```

The default output is `reference/crosswalks/psic_rev5_suggested.csv`. It adds
`query_text`, `branch_codes`, `branch_titles`, `suggested_kind`, `suggested_codes`,
candidate codes/titles/scores, `suggestion_source`, and `review_status`.

For PSIC, the review helper uses source semantics before lexical retrieval. Examples:
OSM `shop=*` is constrained to retail unless a more specific rule says otherwise;
`amenity=school` is constrained to education; restaurant/cafe categories stay inside
food-service branches; automotive repair stays inside motor-vehicle repair; and lodging
is rewritten as accommodation so it cannot retrieve logging merely by spelling. FSQ's
parent path and common Overture categories provide the same kind of coarse branch signal.
Unknown categories fall back to a shallow-to-deep hierarchical retrieval beam.

The suggester is intentionally conservative: high-precision physical-place categories may
be proposed as `NOT_ACTIVITY`; strong, well-separated matches may be proposed as
`SUBTREE`; ambiguous retrievals only receive ranked candidates for human review. Branch
constraints are review evidence, not accepted mappings. The command never changes
`mapping_kind` or `codes`, and it does not automatically infer `UNION` or `UNCODEABLE`
from TF-IDF similarity alone.
