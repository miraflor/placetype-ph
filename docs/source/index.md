<section class="hero">
<p class="eyebrow">A visual algorithm guide · version 0.1.2</p>
<h1>From place observations to defensible economic categories.</h1>
<p>PlaceType PH classifies canonical establishments using Philippine statistical taxonomies. This guide shows exactly how source labels, reviewed rules, hierarchy constraints, uncertainty, institution evidence, and input–output correspondence become auditable results.</p>
<div class="hero-actions"><a class="hero-button" href="worked-example.html">Follow a worked example →</a><a class="hero-button ghost" href="getting-started.html">Run the package</a></div>
</section>

<div class="metric-grid">
<div class="metric"><b>3</b><span>distinct statistical classification systems</span></div>
<div class="metric"><b>346</b><span>bundled reviewed PSIC Rev. 5 rules</span></div>
<div class="metric"><b>178</b><span>specialized advisory activity rules</span></div>
</div>

## What the package does

PlaceType PH sits **after** [OpenPlaces PH](https://github.com/miraflor/openplaces-ph), which supplies one canonical record per reconciled place. PlaceType PH does not repeat the geometric or name-based record linkage upstream. It interprets the source evidence attached to those records and maps it to the official hierarchy, stopping at a defensible level rather than inventing a more specific category.

<figure><img src="assets/diagrams/pipeline.svg" alt="OpenPlaces entities pass through source evidence, reviewed mappings, dependency-aware fusion, optional traversal, and classification and GIS output."><figcaption>Figure 1 — Conceptual pipeline. Source and institutional evidence inform decisions, but candidate-only information does not silently become an accepted classification.</figcaption></figure>

The three taxonomies answer **different questions**:

| Scheme | What is classified | Boundary |
| --- | --- | --- |
| **PSIC Rev. 5** | The establishment's principal economic activity | A parent code may be the best supported answer. |
| **PCPC 2002** | Product and service categories | A place-only label is a *potential product/service family*, not its complete output basket. |
| **PSCC 2022** | Traded commodities | A shop name is not proof of a particular traded commodity. Explicit commodity evidence or a confirmed mapping is required. |

The source is not a probability model. A match to a reviewed category is a rule assertion; two matching labels may derive from one provider; a code set is not proof of a specific production technology. These distinctions are preserved in outputs and audit fields.

## Read this like an implementation specification

The documentation follows the two complementary styles of a visual worked derivation and a system-design manual: start with a small example, then inspect every transformation, input contract, fallback, status, implementation file, and limiting assumption. It is organized into chapters rather than one unsearchable README.

<div class="chapter-grid">
<a class="chapter-card" href="worked-example.html"><span class="chapter-symbol">01</span><strong>Worked example</strong><small>Trace a pharmacy, a disputed place, and an administrative office through actual decision rules.</small></a>
<a class="chapter-card" href="triangulation.html"><span class="chapter-symbol">02</span><strong>Evidence triangulation</strong><small>Why source dependence, corroboration, and contradictory annotations matter.</small></a>
<a class="chapter-card" href="fusion.html"><span class="chapter-symbol">03</span><strong>Hierarchy & fusion</strong><small>Exact subtree intersections, sibling backoff, unions and conflicts without forced leaves.</small></a>
<a class="chapter-card" href="joint-recheck.html"><span class="chapter-symbol">04</span><strong>Cross-taxonomy review</strong><small>Learn what the bounded peer-context recheck can and cannot legitimately infer.</small></a>
<a class="chapter-card" href="institutions.html"><span class="chapter-symbol">05</span><strong>Institutional evidence</strong><small>Separate an institution's activity from ownership and from derived I–O industries.</small></a>
<a class="chapter-card" href="reproducibility.html"><span class="chapter-symbol">06</span><strong>Reproducibility</strong><small>Taxonomy versions, packaged rules, fingerprints, caches, and selective invalidation.</small></a>
</div>

## What “triangulation” means here

PlaceType PH does **not** use geometric triangulation (Delaunay or otherwise). Its triangulation is **semantic**: comparing source annotations with dependency-aware fusion and separately checking proposals against other taxonomies in a bounded diagnostic pass. The latter is advisory, not an official statistical concordance. See [source triangulation](triangulation.html) and [cross-taxonomy rechecking](joint-recheck.html).

<div class="note warning"><strong>Not an accuracy certificate</strong>All tests passing, a complete reference hierarchy, a reviewed rule inventory, or a singleton I–O candidate set do not establish ground-truth classification accuracy. The [evaluation](evaluation.html) chapter explains the necessary adjudicated benchmark and what the current package reports.</div>

## How to navigate

Start with [Quickstart](getting-started.html) for an executable example; then read [Architecture](architecture.html), [Source evidence](sources.html), and [Tree fusion](fusion.html) to understand the decision engine. The [Design decision register](decision-register.html) distinguishes standard statistical ideas from package-specific choices and known fallbacks. The [API and CLI](api-cli.html) and [Outputs](outputs.html) chapters are references for implementation and downstream work.

**Source:** [Repository](https://github.com/miraflor/placetype-ph) · [README](https://github.com/miraflor/placetype-ph/blob/main/README.md) · [architecture note](https://github.com/miraflor/placetype-ph/blob/main/docs/ARCHITECTURE.md).
