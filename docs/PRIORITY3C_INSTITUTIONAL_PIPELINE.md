# Priority 3c: opt-in ownership enrichment in the standard pipeline

Priority 3c wires the existing Priority 3b `resolve_institutional_ownership` into
`classify_openplaces`, **after** the existing per-taxonomy classification and
specialized evidence enrichment, and **before** the existing classified POI export.
It does not introduce any new eligibility or promotion rules.

## Disabled by default

When `PLACETYPE_PH_INSTITUTION_REFERENCES` is absent, the stage returns the exact
existing in-memory classification frame unchanged and adds **no columns**.
Nothing is downloaded, no references are loaded, and the current workflow and
outputs remain unchanged.

## Explicit opt-in

Create a JSON configuration *outside the repository*, for example:

```json
{
  "schema": "placetype-ph-institution-reference-config-v1",
  "references": {
    "beis": "C:\\Users\\YOUR_USER\\Downloads\\beis.csv",
    "unifast": "C:\\Users\\YOUR_USER\\Downloads\\unifast.csv",
    "hospitals": "C:\\Users\\YOUR_USER\\Downloads\\hospitals.csv",
    "verified_links": "C:\\Users\\YOUR_USER\\Downloads\\verified_links.json"
  }
}
```

Include **only** references you actually have. Each path must be absolute and
point to an existing file; at least one reference is required. Formats, schemas
and evidence promotion are **exactly those of Priority 3 and 3b**. A hospital
reference must be the validated fourteen-column Level 3 source format.

Activate the explicit configuration in your current PowerShell session:

```powershell
$env:PLACETYPE_PH_INSTITUTION_REFERENCES = "$HOME\Downloads\institution_references.json"
# Run your existing PlaceType PH classification command here, unchanged.
# To restore default behavior:
Remove-Item Env:\PLACETYPE_PH_INSTITUTION_REFERENCES
```

An invalid or empty opt-in configuration **fails the classification run** before
publishing the classified summary. A missing environment variable is a no-op.

When enabled, the existing `classified_pois.parquet` receives the seven
`institution_resolution_*` / institutional evidence columns documented for
Priority 3b. Existing PSIC, PCPC, PSCC, all previously exported fields, and
entity ordering remain unchanged; the resolver explicitly checks this.

## Scope and limitations

- Does not make a new PSIC determination, override reviewed classifications,
  assign an I/O sector, or use a new ownership heuristic.
- Provisional BEIS, UniFAST, name matches, publication-row identifiers, and bare
  source ownership remain non-promotable under Priority 3b rules.
- Reference files are local and user-supplied; this integration is not a
  reference-data acquisition or validation study.
- Automatic ownership enrichment is **opt-in**, not on by default.
- The installer requires the merged Priority 3b modules and a fresh feature
  branch from the latest remote `main`, and fails closed on unexpected pipeline
  structure. The installer never commits, pushes, merges or deploys.

## Implementation boundary

The installer modifies **only** the existing `pipeline.py` integration point
and adds one module, one test module and this document. It does not change
`cli.py`, the taxonomy classifier, reference crosswalks, or rule files.

Installer note: v2 keeps the new institutional_pipeline import before specialized.integration to satisfy Ruff I001.
