# CLI and Python API reference

<p class="lede">The public interface has a few stable entry points and a larger set of specialized review commands. Use the CLI for reproducible runs and import the core classes for controlled programmatic classification.</p>

## Primary CLI commands

| Command | Responsibility |
| --- | --- |
| `placetype version` | Print installed package version. |
| `placetype inspect PATH --top 30` | Inspect observed source vocabulary and frequency. |
| `placetype taxonomy diagnose psic` | Diagnose official source before accepting a normalized reference. |
| `placetype taxonomy fetch psic` | Obtain/reuse official reference and write normalized taxonomy. |
| `placetype taxonomy validate PATH` | Check structure and hierarchy integrity. |
| `placetype crosswalk-init PATH --scheme psic` | Enumerate category worklist. |
| `placetype crosswalk-suggest PATH` | Suggest taxonomy candidates and run joint recheck where configured. |
| `placetype classify PATH --schemes psic --llm none` | Apply accepted crosswalks and deterministic policies. |
| `placetype express PATH` | Orchestrate joint classification and GIS export. |
| `placetype gis-export RUN_DIR` | Export compact QGIS GeoParquet. |
| `placetype evaluate PREDICTIONS GOLD --scheme psic` | Compare to independently adjudicated codes. |

Use `placetype <command> --help` to inspect the precise options for your installed version. Flag names and defaults here describe the verified **v0.1.2** source tree.

## Important classification switches

| Option | Default | Effect |
| --- | --- | --- |
| `--schemes` | `psic,pcpc,pscc` | Comma-separated independent taxonomies. |
| `--reviewed-psic / --no-reviewed-psic` | Enabled | Bundled accepted Rev. 5 policy or controlled legacy mode. |
| `--crosswalk PATH` | None | Repeat to add explicit user mappings. |
| `--llm` | `none` | `none`, `hf`, or `ollama` traversal backend. |
| `--passes` | 3 | Traversal consensus passes (when LLM enabled). |
| `--product-column FIELD` | None | Optional explicit product/commodity text for PCPC/PSCC. |
| `--workers` | 1 | Deterministic spawn-based parallelism (no live LLM). |
| `--fail-on-ambiguous-crosswalk` | Disabled | Treat observed scanned-rule conflicts as fatal. |
| `--limit` | None | Pilot on first N rows; not a random evaluation sample. |

## Python API: reviewed PSIC

```python
from placetype_ph.classifier import EntityClassifier
from placetype_ph.crosswalk import Crosswalk
from placetype_ph.reviewed_psic import bundled_psic_crosswalk_path
from placetype_ph.taxonomy import Taxonomy

psic_taxonomy = Taxonomy.load("reference/psic_rev5/nodes.parquet")
if psic_taxonomy.scheme != "psic" or psic_taxonomy.version != "rev5":
    raise ValueError("Expected a validated PSIC Rev. 5 taxonomy")
crosswalk = Crosswalk.load([bundled_psic_crosswalk_path()])
classifier = EntityClassifier(psic_taxonomy, crosswalk)
result = classifier.classify_row({
    "canonical_id": "illustrative-id",
    "overture_category": "pharmacy",
})
print(result.code, result.status, result.flags)
```

The snippet loads the normalized taxonomy file from the repository reference directory; verify it independently with `placetype taxonomy validate` before using it. For a legacy test, use `EntityClassifier(psic_taxonomy, crosswalk, reviewed_psic_policy=False)` and pass only the explicit crosswalks you wish to retain.

## Python API: data pipeline

`classify_openplaces(input_path, classifiers, output_dir, *, product_column=None, limit=None, workers=1, role_crosswalk=None)` processes canonical Parquet input and returns the two produced paths. For a multi-scheme run, supply a dictionary of `scheme → EntityClassifier` objects built from their own matching taxonomies; never reuse a PSIC taxonomy for PCPC or PSCC.

## Specialized inspection and review

The modules under `placetype_ph.specialized` provide validation, evidence extraction, candidate-only rule reconciliation, institutional preview and optional enrichment. See the repository's [specialized evidence guide](https://github.com/miraflor/placetype-ph/blob/main/docs/SPECIALIZED_EVIDENCE.md) and [Priority 2 reconciliation instructions](https://github.com/miraflor/placetype-ph/blob/main/docs/PRIORITY2_CROSSWALK_RECONCILIATION.md) before running those commands with external references.

## Input and output contracts

For exact columns, see [Source evidence](sources.html) and [Outputs](outputs.html). A missing `canonical_id`, invalid gold codes, conflicting exact crosswalk keys, an incomplete taxonomy, or an invalid opt-in institutional configuration should fail rather than yielding a plausible-looking partial classification.

**Source:** [`cli.py`](https://github.com/miraflor/placetype-ph/blob/main/src/placetype_ph/cli.py) · [`pipeline.py`](https://github.com/miraflor/placetype-ph/blob/main/src/placetype_ph/pipeline.py).
