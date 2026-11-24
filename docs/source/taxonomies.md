# Official taxonomies & hierarchy integrity

<p class="lede">A taxonomy is not a bag of codes. PlaceType PH represents it as a verified rooted forest whose parent relationships, statistical revision, source metadata, and level ordering define every allowed classification and backoff.</p>

## The three separate hierarchies

| Taxonomy | Official hierarchy | Interpretation |
| --- | --- | --- |
| PSIC Rev. 5 | Section → division → group → class → subclass | Principal economic activity of an establishment. |
| PCPC 2002 | Section → division → group → class → subclass → item | A candidate product/service family based on place evidence. |
| PSCC 2022 | Chapter → heading → HS subheading → AHTN subheading → commodity | Traded good; requires commodity evidence or confirmed crosswalk. |

The official PSIC Rev. 5 published structure is **22 sections, 88 divisions, 260 groups, 493 classes and 1,338 subclasses**. PCPC and PSCC count baselines are deliberately not hard-coded without validating the actual official source.

## A tree, not numeric string truncation

<figure><img src="assets/diagrams/taxonomy.svg" alt="Explicit PSIC parent edges from section G through division 47 to group 4772 and more specific descendants."><figcaption>Figure 1 — Parent relationships govern ancestry. Section letters and cross-version recodings make numerical prefix slicing unsafe as a general algorithm.</figcaption></figure>

The functions `taxonomy.ancestors(code)`, `taxonomy.children(code)`, `taxonomy.lca(codes)` and `taxonomy.path_from_root(code)` use stored parent edges. A coarse classification `SUBTREE(47)` does not become a subclass just because one descendant is present in a partial extract; the evidence asserts the original root.

## Official sources and acquisition

`placetype taxonomy diagnose <scheme>` inspects the statistical source before normalization; `placetype taxonomy fetch <scheme>` obtains or reuses the source and saves normalized `nodes.parquet`. PSIC Rev. 5 and PSCC 2022 use PSA spreadsheets. PCPC supports a PSA API requiring a token in relevant configurations. Refer to [DATA_SOURCES.md](https://github.com/miraflor/placetype-ph/blob/main/DATA_SOURCES.md) for exact cited source URLs, licensing and recoding boundaries.

The importer preserves `code`, `level`, `title`, `parent_code`, description/inclusions/exclusions and source identifiers. It rejects conflicting explicit parents and duplicate code definitions with incompatible identity. Source text for an official node titled “Other” is retained literally, even when “other” in a noisy POI label may be treated as missing evidence.

## Structural and completeness gates

- **Missing top-level and parent nodes:** strict normalization rejects a hierarchy that has the wrong branch attachment.
- **Leading zero / invalid code morphology:** report missing or malformed identifiers; do not silently coerce them into a plausible code.
- **Explicit workbook schema:** if a named column does not exist, fail rather than switch to a different parser interpretation.
- **Duplicates:** merge only consistent code identities; retain diagnostic counts for discarded or invalid rows.
- **Hierarchy levels:** `--strict-levels` checks completeness by level. `--strict-structure` checks known official structure when supported; `--allow-orphan-nodes` is a separate relaxation, not an override of everything.
- **Version:** a code under Rev. 5 cannot be silently interpreted as equivalent to the same string under PSIC 2019.

A taxonomy fingerprint is recorded so an export can verify it is using the same classification tree as the original run.

## Reproducible commands

```powershell
placetype taxonomy diagnose psic
if ($LASTEXITCODE -ne 0) { throw "Diagnosis failed" }
placetype taxonomy fetch psic
if ($LASTEXITCODE -ne 0) { throw "Taxonomy fetch failed" }
placetype taxonomy validate reference\psic_rev5\nodes.parquet --strict-levels --strict-structure
if ($LASTEXITCODE -ne 0) { throw "Taxonomy validation failed" }
```

**Implementation:** [`taxonomy.py`](https://github.com/miraflor/placetype-ph/blob/main/src/placetype_ph/taxonomy.py) · [`taxonomy_import.py`](https://github.com/miraflor/placetype-ph/blob/main/src/placetype_ph/taxonomy_import.py).
