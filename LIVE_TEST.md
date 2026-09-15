# First live test: PlaceType PH 0.1.1

Use PowerShell on Windows. Keep the Iloilo OpenPlaces output where it already is; PlaceType PH
accepts an absolute or relative path and does not need the file copied into this repository.

## 1. Put the repo under GitHub and create its environment

Extract the release ZIP under:

```text
C:\Users\James Matthew\Documents\Github\
```

The ZIP contains one top-level folder named `placetype-ph`, so the result should be:

```text
C:\Users\James Matthew\Documents\Github\placetype-ph
```

Then:

```powershell
cd "C:\Users\James Matthew\Documents\Github\placetype-ph"
conda env create -f environment.yml
conda activate placetype-ph
python -m pip install -e .
placetype version
python -m ruff check .
python -m pytest -q
```

`placetype version` should print `0.1.1`.

If the Conda environment already exists, update it instead:

```powershell
conda env update -f environment.yml --prune
conda activate placetype-ph
python -m pip install -e .
```

## 2. Diagnose the live PSA sources before fetching

Create a diagnostics folder and capture complete console output:

```powershell
New-Item -ItemType Directory -Force diagnostics | Out-Null

placetype taxonomy diagnose psic 2>&1 |
  Tee-Object -FilePath diagnostics\psic.txt

placetype taxonomy diagnose pscc 2>&1 |
  Tee-Object -FilePath diagnostics\pscc.txt
```

PCPC requires a PSA classification API token. If you have one:

```powershell
$env:PSA_CLASSIFICATION_TOKEN="YOUR_TOKEN"
placetype taxonomy diagnose pcpc 2>&1 |
  Tee-Object -FilePath diagnostics\pcpc.txt
```

Diagnosis may download/cache the official source workbook under `reference/downloads`, but it does
**not** save a normalized `nodes.parquet` taxonomy.

**Stop here on the first run and review the diagnostic files.** In particular, look for the final
line saying that a normal official fetch would be eligible to save. If it says the fetch would
refuse, do not override the gate yet; inspect the reported workbook/API shape first.

## 3. Fetch the reference taxonomies after diagnosis is clean

```powershell
placetype taxonomy fetch psic
placetype taxonomy fetch pscc
```

If PCPC diagnosis was clean and the token is set:

```powershell
placetype taxonomy fetch pcpc
```

Validate the saved trees:

```powershell
placetype taxonomy validate reference\psic_rev5\nodes.parquet `
  --strict-levels --strict-structure

placetype taxonomy validate reference\pscc_2022\nodes.parquet --strict-levels

# once PCPC has been fetched
placetype taxonomy validate reference\pcpc_2002\nodes.parquet --strict-levels
```

PSIC has a verified published-count gate. PCPC and PSCC exact counts are intentionally not frozen
in 0.1.1; the live source/API diagnosis is what should establish the first regression baseline.

## 4. Point PlaceType PH at the existing Iloilo OpenPlaces output

Assuming the existing OpenPlaces repo is a sibling under the same GitHub directory:

```powershell
$iloilo = "C:\Users\James Matthew\Documents\Github\openplaces-ph\data\output\areas_iloilo\canonical_pois.parquet"
Test-Path $iloilo
placetype inspect $iloilo --top 30
```

`Test-Path` must return `True`.

## 5. Create the PSIC crosswalk worklist

```powershell
placetype crosswalk-init $iloilo --scheme psic
```

This creates:

```text
reference\crosswalks\psic_rev5.csv
```

Do not try to review every row immediately. Start with the high-frequency categories at the top.
The classifier is designed so a reviewed coarse mapping is preferable to an invented subclass.

## 6. Smoke-test the deterministic path

```powershell
placetype classify $iloilo `
  --schemes psic `
  --llm none `
  --limit 100 `
  --output-dir output\iloilo-smoke
```

An unreviewed crosswalk will leave many rows unresolved. That is expected. This run tests input,
taxonomy loading, classification plumbing and Parquet output, not accuracy.

## 7. Smoke-test the Qwen fallback

### Hosted Hugging Face

```powershell
$env:HF_TOKEN="hf_..."
placetype classify $iloilo `
  --schemes psic `
  --llm hf `
  --limit 50 `
  --output-dir output\iloilo-qwen-pilot
```

### Or local Ollama

```powershell
ollama pull qwen3:4b
placetype classify $iloilo `
  --schemes psic `
  --llm ollama `
  --model qwen3:4b `
  --limit 50 `
  --output-dir output\iloilo-qwen-pilot
```

The first 50 rows are a plumbing/model test, **not** an accuracy estimate.

## 8. What to bring back for the first empirical review

For the very first live pass, the most useful files are:

```text
diagnostics\psic.txt
diagnostics\pscc.txt
diagnostics\pcpc.txt              # if available
reference\crosswalks\psic_rev5.csv
output\iloilo-qwen-pilot\entity_classifications.parquet
output\iloilo-qwen-pilot\run.json
```

After those work, draw an adjudicated sample stratified by `status` and `deciding_component`, with
explicit `NOT_CODEABLE` and `NON_ECONOMIC_POI` cases. That sample, rather than another static code
review, should determine the next classifier change.
