# Reproducibility, caching and safe publication

<p class="lede">A reproducible classification is one whose code revision, taxonomy revision, reviewed mappings, source evidence, decision policy, and output can be identified. Reproducing the computation is distinct from showing that its assumptions are true.</p>

## Inputs that define a run

- `canonical_pois.parquet`: canonical IDs and observed provider evidence; geometry and upstream matching uncertainty remain part of the source contract.
- Official taxonomy: scheme, version, normalized node content, explicit ancestry and fingerprint.
- Crosswalk: version-scoped reviewed rules and any explicit user paths, recorded with a stable content-based fingerprint.
- Policy: reviewed PSIC operational mode, deterministic/traversal settings, accepted source-floor behavior, administrative gates, and eligible specialized enrichment.
- I–O references: packaged concordance/bridge hashes, provisional versus reviewed relation and mapping statuses.
- Optional model: provider/base URL, model name, prompt fingerprint, pass count, input/restriction identity.

## `run.json` versus cache identity

The classifier's run manifest records source/input path, package version, taxonomy fingerprints, active crosswalk fingerprint, LLM metadata, classification counts and I–O provenance. Express has a **separate work-cache identity** with the key semantic parameters; a cache hit means the requested computation's recorded dependencies match the cache recipe, not that the resulting economic interpretation has been independently validated.

The current Express cache version is `joint-v9.3-reviewed-psic-rev5`. Its pipeline state includes the SHA-256-derived *file state* of the packaged reviewed CSV. Earlier pre-integration cache keys are invalidated without deleting historical outputs.

For small files, `file_state` hashes contents; large input files above 8 MiB use size/mtime. The latter is not a tamper-proof content identity: a same-size/same-timestamp replacement can escape detection. A scientific archive should retain independent source hashes for any input that must be authenticated byte-for-byte.

## Reviewed rules versus suggestions

`REVIEWED` and accepted bundled mappings have stronger authority than `AUTO_ACCEPTED_UNCHECKED`. A crosswalk fingerprint proves **which rules were loaded**, not that the rules' empirical precision is high. The specialized rule catalog has its own fingerprint: v0.1.2 currently reports 178 advisory rules, separate from the 328 accepted PSIC inventory.

## Publication is not fully transactional

The current classification pipeline writes `entity_classifications.parquet` before every optional enrichment and validation step has completed. If later institutional reference processing fails, the output directory can contain intermediate files that are **not a complete successful run**. Avoid treating the presence of Parquet files alone as an acceptance certificate. Check command success, a complete `run.json` with the declared outputs, and any required schema/provenance values.

`express` checks key output names and schema before reuse, but publication-directory atomicity remains an explicit limitation. The docs website itself is just static content; deploying it does not publish any establishment dataset or optional institution register.

## Deterministic parallelism

With `--llm none`, the pipeline supports spawn-based worker processes, merges worker diagnostics, and preserves result order. Chunked dictionary conversion reduces per-row Python overhead, but the full DataFrame and results are still materialized. Backend-driven traversal is not guaranteed deterministic across model deployments, even if code and prompts are identical.

## Deployment and branch safety

The project's `main` is protected by required Python 3.11/3.12/3.13 checks, no force-push and a PR requirement. Documentation contributions should also pass link/build checks before merge. GitHub Pages deployment is a separate publication step: enable **Deploy from a branch → main → /docs** in Settings → Pages after the reviewed PR merges. A private institution dataset is never an input to this static build.

## Evidence lineage and references

The [decision register](decision-register.html) lists exactly which parts derive from a known statistical hierarchy or algorithm and which are package policy. Read the [reference data source guide](https://github.com/miraflor/placetype-ph/blob/main/DATA_SOURCES.md) before republishing PSA source material or institutional extracts; the website intentionally ships no third-party raw registry data.

**Implementation:** [`express.py`](https://github.com/miraflor/placetype-ph/blob/main/src/placetype_ph/express.py) · [`pipeline.py`](https://github.com/miraflor/placetype-ph/blob/main/src/placetype_ph/pipeline.py).
