# PlaceType PH · GitHub Pages documentation

This folder contains the complete, standalone static documentation site (22 HTML chapters, diagrams, CSS/JS, editable Markdown sources). It is **documentation only**: no Python package runs in the browser, and no establishment or institutional source datasets are published.

## Activate GitHub Pages (once, after merging the PR)

1. Open `https://github.com/miraflor/placetype-ph/settings/pages`.
2. Under **Build and deployment**, choose **Deploy from a branch**.
3. Select branch **main** and folder **/docs**, then click **Save**.
4. After GitHub reports a successful Pages deployment, open `https://miraflor.github.io/placetype-ph/`.

This site uses **pre-rendered** static HTML, so no Pages build workflow, tokens, hosted JavaScript dependencies, CDN fonts, or external search provider is required. The 22 HTML pages and static assets are ready for GitHub Pages' branch-based hosting.

## Preview locally

From the repository root:

```powershell
python -m http.server 8000 --directory docs
```

Then open `http://localhost:8000/` in your browser. The navigation, full-site search, scenario illustration, theme toggle, and code-copy actions work without network access.

## Edit and rebuild the documentation

Write in `docs/source/<chapter>.md`, then render the checked-in HTML with the **optional documentation build dependency**:

```powershell
python -m pip install -r docs/requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Docs dependency install failed" }
python docs/site_builder.py
if ($LASTEXITCODE -ne 0) { throw "Docs build failed" }
python docs/site_builder.py --check
if ($LASTEXITCODE -ne 0) { throw "Docs source mismatch" }
python docs/check_site.py
if ($LASTEXITCODE -ne 0) { throw "Site link check failed" }
```

The docs build dependency is **not** needed to use PlaceType PH. The existing package CI tests check that the pages and relative links remain valid. If a maintainer changes a source chapter, regenerate the corresponding checked-in HTML and search index before committing.

## Layout

- `source/*.md` — canonical editable chapter source.
- `*.html` — generated standalone pages, served verbatim by GitHub Pages.
- `assets/css/site.css` — responsive light-sage theme, optional dark theme, typography/contrast tokens, and print stylesheet.
- `assets/js/site.js` — navigation, local search, theme toggle, illustrative scenario, code copy.
- `assets/js/search-index.js` — generated plain-text search index. **No external search server**.
- `assets/diagrams/*.svg` — editable, synthetic diagrams; no provider map tiles or user datasets.
- `site_builder.py` — optional Markdown authoring build (requires `markdown-it-py`).
- `check_site.py` — dependency-free link and scope validation.
- `.nojekyll` — branch-based static site marker.

## Documentation approach

The website documents the package as actually implemented: inputs, transformations, explicit rule meaning, source dependence, exact subtree fusion, bounded recheck, model boundaries, conditional institution enrichment, I–O correspondence, outputs, reproducibility, and limitations. Source-code references are public PlaceType PH links. It contains no private-project references, private paths, proprietary datasets, or claims of empirically established accuracy.

## Publication safeguards

Follow normal protected-main PR workflow. Do not merge until all required Python CI checks complete successfully. If you decide to add a Docs Pages deployment workflow in the future, confirm that Pages is enabled and credentials/permissions work before making it required. **Do not copy external institutional registry extracts or upstream OpenPlaces output into `docs/`.**

## Readability and visual design

The default theme uses a **white reading panel on a pale sage background**, with a light
sidebar, forest-green interactive elements, and dark text. Its palette and panel treatment
are informed by the light documentation presentation of `net-distance-ph`, without changing
the source site framework. The default theme does not download any fonts or contact a CDN.

The stylesheet groups its palette under `:root` and its optional dark-mode overrides under
`html[data-theme="dark"]`. Change those variables rather than hard-coding colors in each
component. The article's reading measure is intentionally restricted; large figures and
tables can span the available article width. Body text, captions, code, navigation, tables
and side notes are all larger than the initial high-density draft.

Readability is tested via contrast-ratio spot checks, responsive CSS parsing, and the existing
static site validator. Browser screenshots should still be inspected by a maintainer before
public release; a static validator is not a substitute for checking real rendering.
