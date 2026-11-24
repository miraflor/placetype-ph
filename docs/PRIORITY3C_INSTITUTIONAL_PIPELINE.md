# Priority 3c: default ownership enrichment in the standard pipeline

Priority 3c wires the existing Priority 3b `resolve_institutional_ownership` into
`classify_openplaces`, **after** the existing per-taxonomy classification and
specialized evidence enrichment, and **before** the existing classified POI export.
It does not introduce any new eligibility or promotion rules.

## Enabled by default

A normal run reads the four packaged national registry snapshots. No environment
variable, installation of reference files, network request or separate download is
needed. The manifest and active file checksums are verified before use.

## Optional custom configuration

Create a JSON configuration *outside the repository*, for example:

```json
{
  "schema": "placetype-ph-institution-reference-config-v1",
  "references": {
    "beis": "C:\\Users\\YOUR_USER\\Downloads\\beis.csv",
    "unifast": "C:\\Users\\YOUR_USER\\Downloads\\unifast.csv",
    "hospitals": "C:\\Users\\YOUR_USER\\Downloads\\hospitals.csv",
    "official_records": "C:\\Users\\YOUR_USER\\Downloads\\official_records.csv",
    "verified_links": "C:\\Users\\YOUR_USER\\Downloads\\verified_links.json"
  }
}
```

Include **only** references you actually have. Each path must be absolute and
point to an existing file; at least one reference is required. Formats, schemas
and evidence promotion are **exactly those of Priority 3 and 3b**. A hospital
reference must be the validated fourteen-column Level 3 source format. An
`official_records` reference is the twelve-column format described in
[institution registry data and tools](INSTITUTION_REGISTRY_TOOLS.md). The command
`python -m placetype_ph.specialized install-references` writes such a
configuration for the snapshots bundled with the package.

Activate the explicit configuration in your current PowerShell session:

```powershell
$env:PLACETYPE_PH_INSTITUTION_REFERENCES = "$HOME\Downloads\institution_references.json"
# Run your existing PlaceType PH classification command here, unchanged.
# To restore default behavior:
Remove-Item Env:\PLACETYPE_PH_INSTITUTION_REFERENCES
```

An invalid or empty custom configuration **fails the classification run** before
publishing the classified summary. An absent environment variable selects the bundled references. Set the variable to the literal `off` only to explicitly disable enrichment.

When enabled, the existing `classified_pois.parquet` receives the seven
`institution_resolution_*` / institutional evidence columns documented for
Priority 3b. Existing PSIC, PCPC, PSCC, all previously exported fields, and
entity ordering remain unchanged; the resolver explicitly checks this.

## Scope and limitations

- Does not make a new PSIC determination, override reviewed classifications,
  assign an I/O sector, or use a new ownership heuristic.
- Provisional BEIS, UniFAST alone, name-only matches, publication-row identifiers, and bare
  source ownership remain non-promotable under Priority 3b rules.
- Reference files are local. Bundled snapshots and the import, build, convert and
  review commands are described in [institution registry data and tools](INSTITUTION_REGISTRY_TOOLS.md).
- Automatic institutional evidence enrichment is **enabled by default**; promotion guards are unchanged.
- The installer requires the merged Priority 3b modules and a fresh feature
  branch from the latest remote `main`, and fails closed on unexpected pipeline
  structure. The installer never commits, pushes, merges or deploys.

## Implementation boundary

The installer modifies **only** the existing `pipeline.py` integration point
and adds one module, one test module and this document. It does not change
`cli.py`, the taxonomy classifier, reference crosswalks, or rule files.

Installer note: v2 keeps the new institutional_pipeline import before specialized.integration to satisfy Ruff I001.

See [national registry site linkage](source/institutions.md) for the policy-scoped matching rules. Strict retains site matches as candidates; evidence-weighted and extended may resolve them.
