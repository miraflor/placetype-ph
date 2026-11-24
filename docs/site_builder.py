#!/usr/bin/env python3
"""Build the checked-in static PlaceType PH Pages site from Markdown source.

Only used by documentation authors, not by the runtime package.
Requires markdown-it-py >=3 (pip install -r docs/requirements.txt).
The published site contains plain HTML/CSS/JS and has no build dependency.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

try:
    from markdown_it import MarkdownIt
except ImportError as exc:
    raise SystemExit(
        "Install the documentation build dependency: pip install -r docs/requirements.txt"
    ) from exc

ROOT = Path(__file__).resolve().parent
SITE_TITLE = "PlaceType PH"
REPO = "https://github.com/miraflor/placetype-ph"
NAV = [
    ("Start here", [
        ("index", "Overview"), ("getting-started", "Quickstart"),
        ("architecture", "Architecture"), ("worked-example", "Worked example"),
    ]),
    ("Methods", [
        ("taxonomies", "Official taxonomies"), ("sources", "Source evidence"),
        ("crosswalks", "Reviewed crosswalks"), ("triangulation", "Evidence triangulation"),
        ("fusion", "Tree fusion & backoff"), ("joint-recheck", "Cross-taxonomy recheck"),
        ("classification", "Classification decisions"),
        ("retrieval-llm", "Retrieval & optional LLM"),
    ]),
    ("Enrichment & delivery", [
        ("institutions", "Institutions & ownership"), ("io-projection", "PSIC to I–O"),
        ("express", "Express workflow"), ("outputs", "Outputs & GIS"),
    ]),
    ("Reference & assurance", [
        ("evaluation", "Validation & evaluation"), ("reproducibility", "Reproducibility"),
        ("api-cli", "CLI & Python API"), ("decision-register", "Design decisions"),
        ("limitations", "Limits & interpretation"), ("glossary", "Glossary & references"),
    ]),
]
ALL = [p for _, entries in NAV for p in entries]
LINKS = {slug: name for slug, name in ALL}


def slugify(value: str) -> str:
    s = re.sub(r"<[^>]*>", "", value)
    s = html.unescape(s).lower().replace("–", "-").replace("—", "-")
    s = re.sub(r"[^a-z0-9 -]+", "", s)
    return re.sub(r"[-\s]+", "-", s).strip("-") or "section"


def headings(rendered: str) -> tuple[str, list[tuple[str, str, str]]]:
    found: list[tuple[str, str, str]] = []
    seen = set()

    def change(m):
        level, text = m.group(1), m.group(2)
        title = html.unescape(re.sub(r"<[^>]*>", "", text))
        ident = slugify(title)
        original = ident
        idx = 2
        while ident in seen:
            ident = f"{original}-{idx}"
            idx += 1
        seen.add(ident)
        if level in ("2", "3"):
            found.append((ident, title, level))
        return f'<h{level} id="{ident}">{text}</h{level}>'

    return re.sub(r"<h([1-4])>(.*?)</h\1>", change, rendered), found


def shell(slug: str, title: str, body: str, outline: list[tuple[str, str, str]]) -> str:
    nav = []
    for group, items in NAV:
        nav.append(f'<div class="nav-heading">{html.escape(group)}</div>')
        for key, name in items:
            active = ' aria-current="page" class="active"' if key == slug else ""
            nav.append(f'<a href="{key}.html"{active}>{html.escape(name)}</a>')
    on_this = '<div class="toc-title">On this page</div>' + ''.join(
        f'<a class="toc-level-{lev}" href="#{ident}">{html.escape(name)}</a>'
        for ident, name, lev in outline[:28]
    )
    prev, next_ = "", ""
    slugs = [x for x, _ in ALL]
    idx = slugs.index(slug)
    if idx > 0:
        prev_name = LINKS[slugs[idx - 1]]
        prev = (
            f'<a class="page-step previous" href="{slugs[idx - 1]}.html">'
            f'<span>← Previous</span><strong>{html.escape(prev_name)}</strong></a>'
        )
    if idx < len(slugs) - 1:
        next_name = LINKS[slugs[idx + 1]]
        next_ = (
            f'<a class="page-step next" href="{slugs[idx + 1]}.html">'
            f'<span>Next →</span><strong>{html.escape(next_name)}</strong></a>'
        )
    return (
        '<!doctype html>\n'
        '<html lang="en">\n'
        '<head>\n'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<meta name="theme-color" content="#ffffff"><meta name="description" '
        'content="PlaceType PH — an evidence-first guide to hierarchical Philippine economic '
        'classification, source triangulation, conservative decisions, '
        'and reproducible outputs.">\n'
        f'<title>{html.escape(title)} | {SITE_TITLE} documentation</title>\n'
        '<link rel="icon" href="assets/diagrams/favicon.svg" type="image/svg+xml">\n'
        '<script>try{const t=localStorage.getItem("placetype-ph-docs-theme");'
        'if(t==="dark"||t==="light")document.documentElement.dataset.theme=t;'
        '}catch(e){}</script>\n'
        '<link rel="stylesheet" href="assets/css/site.css">\n'
        '<script defer src="assets/js/search-index.js"></script>\n'
        '<script defer src="assets/js/site.js"></script>\n'
        '</head>\n'
        f'<body data-page="{slug}">\n'
        '<button class="menu-toggle" type="button" aria-label="Open documentation navigation" '
        'aria-controls="site-sidebar" aria-expanded="false">☰</button>\n'
        '<div class="nav-scrim" aria-hidden="true"></div>\n'
        '<aside class="sidebar" id="site-sidebar" aria-label="Documentation sidebar">\n'
        '<a href="index.html" class="brand"><span class="brand-icon">P<span>H</span></span>'
        '<span><strong>PlaceType <em>PH</em></strong>'
        '<small>Economic classification · v0.1.2</small></span></a>\n'
        '<div class="search-area"><label class="sr-only" for="doc-search">'
        'Search documentation</label><input type="search" id="doc-search" '
        'placeholder="Search the documentation…" autocomplete="off"><kbd>/</kbd>'
        '<div id="search-results" hidden></div></div>\n'
        f'<nav class="sidebar-nav" aria-label="Chapters">{"".join(nav)}</nav>\n'
        f'<div class="sidebar-foot"><a href="{REPO}">GitHub repository ↗</a>'
        f'<a href="{REPO}/blob/main/DATA_SOURCES.md">Data sources ↗</a></div>\n'
        '</aside>\n'
        '<div class="content-frame"><div class="topbar">'
        f'<span>Documentation <b>/</b> {html.escape(title)}</span>'
        '<div class="topbar-actions">'
        '<button type="button" id="theme-toggle" aria-label="Switch to dark mode" '
        'title="Switch to dark mode">'
        '<svg class="theme-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M20.9 13.3A9 9 0 0 1 10.7 3.1 9 9 0 1 0 20.9 13.3Z"/></svg>'
        '<svg class="theme-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.8" stroke-linecap="round" aria-hidden="true">'
        '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2'
        'M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'
        '</svg><span class="theme-label">Dark mode</span></button>'
        f'<a href="{REPO}">View source ↗</a></div></div>\n'
        '<div class="reading-layout"><main id="main" class="prose" tabindex="-1">'
        f'<article>{body}</article>\n'
        f'<nav class="page-pagination" aria-label="Document pages">{prev}{next_}</nav>'
        '<footer class="article-footer"><span>PlaceType PH · MIT-licensed package</span>'
        '<span>Documentation describes the v0.1.2 implementation; classification is not '
        'validation of truth.</span></footer></main>\n'
        f'<aside class="toc" aria-label="On this page">{on_this}'
        f'<a class="toc-repo" href="{REPO}/tree/main/docs">Edit on GitHub ↗</a>'
        '</aside></div></div>\n'
        '</body></html>'
    )


def build(check: bool) -> int:
    md = MarkdownIt("commonmark", {"html": True, "linkify": True, "typographer": True})
    md.enable("table")
    generated = {}
    index = []
    for slug, title in ALL:
        path = ROOT / "source" / f"{slug}.md"
        if not path.is_file():
            raise RuntimeError(f"missing source chapter: {path}")
        raw = path.read_text(encoding="utf-8")
        rendered, outline = headings(md.render(raw))
        result = shell(slug, title, rendered, outline)
        target = ROOT / f"{slug}.html"
        if check:
            if not target.is_file() or target.read_text(encoding="utf-8") != result:
                raise RuntimeError(
                    f"stale generated page: {target.name}; run python docs/site_builder.py"
                )
        else:
            target.write_text(result, encoding="utf-8", newline="\n")
        generated[slug] = result
        clean = re.sub(r"<[^>]+>", " ", rendered)
        clean = re.sub(r"\s+", " ", html.unescape(clean)).strip()
        index.append(
            {"slug": slug, "title": title, "text": clean[:12000],
             "headings": [name for _, name, _ in outline]}
        )
    data = (
        "window.PLACETYPE_DOCS_SEARCH = "
        + json.dumps(index, ensure_ascii=False, separators=(",", ":"))
        + ";\n"
    )
    search = ROOT / "assets" / "js" / "search-index.js"
    if check:
        if not search.is_file() or search.read_text(encoding="utf-8") != data:
            raise RuntimeError("stale search index; rebuild documentation")
    else:
        search.write_text(data, encoding="utf-8")
    print(f"PASS: {len(generated)} pages {'match' if check else 'built'} generated source")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    raise SystemExit(build(args.check))
