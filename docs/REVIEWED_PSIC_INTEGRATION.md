# Reviewed PSIC Rev. 5 classification integration

## Scope

The installed `src/placetype_ph/data/psic_rev5_reviewed.csv` is a packaged,
version-scoped, audited set of **346 accepted OSM and Overture PSIC mappings**.
It contains 249 `SUBTREE`, 46 `EXACT`, four `UNION`, 17 `NOT_ACTIVITY`, and
30 `UNCODEABLE` decisions. The first 328 rows are the transferred reviewed set;
18 rows (notes beginning "reviewed title-search result" or naming the corrected
search result) were added for categories that only a PSIC title search used to code. This is the reviewed mapping source for PSIC Rev. 5;
it is not the 1,993-row unreviewed category worklist, and is not a machine
suggestion catalogue.

In `placetype classify`, the reviewed file is loaded by default for PSIC Rev. 5
alongside any explicitly supplied crosswalk CSV. Other scheme versions and
PCPC/PSCC continue using their separate classifiers. The flag
`--no-reviewed-psic` disables this default for reproducible legacy comparisons.

For direct API use, opt in with:

```python
from placetype_ph.classifier import EntityClassifier
from placetype_ph.crosswalk import Crosswalk
from placetype_ph.reviewed_psic import bundled_psic_crosswalk_path

crosswalk = Crosswalk.load([bundled_psic_crosswalk_path()])
classifier = EntityClassifier(psic_taxonomy, crosswalk)
```

Explicit user rules with a conflicting exact key are rejected by the existing
crosswalk validator rather than overwriting accepted mappings. Unreviewed
specialized suggestions remain advisory. Only accepted reviewed source rules
and curated ontology floors can result in default deterministic PSIC codes.

## Source evidence

PSIC source interpretation preserves OSM `key=value` assertions and separates
auxiliary attributes, including an on-premises ATM. It also respects Overture
primary/alternate/basic categories as dependent statements from the same
source, preserves FSQ hierarchy strings, and reuses FSQ-derived provenance
from the canonical OpenPlaces input. An OSM `shop=carpet` assertion cannot
match a reviewed `shop=car` exact key.

A genuinely incompatible set of accepted source mappings returns an explicit
conflict or entity-match review rather than collapsing several activities into
an unsupported single code. An administrative office may receive a specific
public-administration PSIC classification only for narrow function-name
patterns when existing accepted activity evidence does not contraindicate it.
Verified institutional ownership remains independent of economic activity.

`NOT_PSIC_ACTIVITY`, `UNCODEABLE`, `UNRESOLVED`, `UNION`, and `CONFLICT` are
not interchangeable. Candidate codes remain visible in the classification
result. Every run also records a deterministic SHA256 fingerprint of the
active accepted crosswalk entries in `run.json`. Specialized candidates never automatically overwrite accepted codes.

## Reproducibility and limitations

The reviewed CSV is installed from one pinned, verified revision of a separate
classification-rule baseline. The library makes **no network calls** at runtime,
and the imported file is packaged independently with the wheel. Source-specific
mapping evidence can be inspected in the installed CSV. The resulting runtime
requires neither the source project nor its filesystem.

This integration does **not** prove empirical classification accuracy on a
representative Philippine evaluation sample. It does not automatically accept
any of the remaining unreviewed candidate mappings, enable LLMs, infer
commodities from business type, or guarantee that historical classified datasets
are regenerated. Existing optional institution-reference loading remains
explicit, and I/O industry projection stays downstream of primary PSIC.

## Legacy comparisons, version scope and Express caches

`--no-reviewed-psic` disables **all new operational PSIC policy** introduced
by the reviewed integration, including expanded structured source evidence,
trusted ontology floors and automatic administrative classification. Existing
explicit crosswalks and earlier classification rules continue to work.
It does not change PCPC/PSCC source handling. The reviewed integration is
restricted to PSIC Rev. 5; other PSIC versions do not inherit the new floors.

`placetype express` always uses the reviewed Rev. 5 default unless explicitly
reconfigured in code. Its cache key includes a new semantic cache version,
the active classifier policy and the content fingerprint of the packaged
reviewed CSV. Completed Express runs using an older key are not silently
reused. No historical output is deleted or overwritten by the key change.

The package version is 0.1.2. These changes do not imply that every PSIC
mapping has been empirically validated on a representative evaluation set.

The current pipeline writes intermediate classification outputs before all
optional validation and enrichment completes. Publication-directory atomicity
remains a separate, disclosed operational limitation. The overlay installer,
however, is fail-fast and restores original files if any test/check fails.
