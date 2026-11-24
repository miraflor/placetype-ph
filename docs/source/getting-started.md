# Quickstart

<p class="lede">Install the package, verify taxonomy inputs, classify an existing OpenPlaces Parquet, and inspect the outputs. No hosted model is required for the deterministic baseline.</p>

## Prerequisites

- Python 3.11–3.13 is covered by the repository's current CI matrix; the project declares Python 3.11 or newer.
- A valid [OpenPlaces PH](https://github.com/miraflor/openplaces-ph) `canonical_pois.parquet`, containing unique non-null `canonical_id` and at least one supported semantic source field.
- Normalized taxonomy files under `reference/` or source workbooks/API access to create them. The repository includes normalized taxonomy assets, but the operator should validate versions before analytical use.

## Install and confirm the environment

```powershell
$ErrorActionPreference = "Stop"
cd "$HOME\Documents\GitHub\placetype-ph"
conda env update -f environment.yml --prune
if ($LASTEXITCODE -ne 0) { throw "Conda environment update failed" }
conda activate placetype-ph
python -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) { throw "Editable install failed" }
placetype version
if ($LASTEXITCODE -ne 0) { throw "Version check failed" }
python -m ruff check .
if ($LASTEXITCODE -ne 0) { throw "Lint failed" }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed" }
```

Expected package version for this guide: **0.1.2**. The official taxonomy files need their own validation; a successful package installation does not guarantee the supplied statistical references are complete.

## Inspect the inputs first

```powershell
$places = "..\openplaces-ph\data\output\areas_iloilo\canonical_pois.parquet"
if (-not (Test-Path -LiteralPath $places -PathType Leaf)) { throw "OpenPlaces input missing" }
placetype inspect $places --top 30
if ($LASTEXITCODE -ne 0) { throw "Inspect failed" }
placetype taxonomy validate reference\psic_rev5\nodes.parquet --strict-levels --strict-structure
if ($LASTEXITCODE -ne 0) { throw "Taxonomy validation failed" }
```

If a taxonomy is absent, use `placetype taxonomy diagnose psic` before `placetype taxonomy fetch psic`; PCPC's official API may require a PSA token. See [Taxonomy integrity](taxonomies.html).

## Run the reviewed PSIC baseline

```powershell
placetype classify $places `
  --schemes psic --llm none `
  --output-dir output\iloilo-reviewed
if ($LASTEXITCODE -ne 0) { throw "Reviewed classification failed" }
```

The default PSIC Rev. 5 run includes the **328 bundled accepted mappings** and the conservative operational rules. It does not call an LLM. A custom reviewed crosswalk may be supplied with repeated `--crosswalk` arguments. Conflicting exact keys are validation errors; they do not silently override the packaged rules.

## Make a controlled legacy comparison

```powershell
placetype classify $places `
  --schemes psic --llm none --no-reviewed-psic `
  --output-dir output\iloilo-legacy
if ($LASTEXITCODE -ne 0) { throw "Legacy comparison failed" }
```

This disables the newly introduced reviewed-PSIC policy and packaged mappings **without** disabling explicitly supplied crosswalks or earlier classifier rules. Keep the taxonomy, input file, and optional user crosswalk constant for a meaningful comparison; the result is not a causal estimate of classification quality.

## Or use the complete Express workflow

```powershell
placetype express $places --workers 1
if ($LASTEXITCODE -ne 0) { throw "Express run failed" }
```

Express builds a joint crosswalk, runs bounded cross-taxonomy rechecks, classifies selected schemes, and exports a QGIS-ready GeoParquet. **Important:** Express promotes unchecked machine suggestions by default and labels them `AUTO_ACCEPTED_UNCHECKED`. For a conservative gate, add `--no-promote-unchecked`; this can result in little coverage on a cold start. See [Express](express.html).

## Verify the outputs

A `classify` run writes `entity_classifications.parquet`, `classified_pois.parquet`, and `run.json`. The GeoParquet output of `express` is a separate compact layer; it is not a replacement for the audit-rich classification table. Check [Outputs](outputs.html) for the schema and interpretation rules.

To evaluate classifications you need **independently adjudicated gold labels**; see [Evaluation](evaluation.html). A large number of assigned codes, by itself, is only *coverage*.
