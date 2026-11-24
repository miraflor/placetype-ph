#!/usr/bin/env python3
"""Dependency-free, fail-fast validation of the checked-in documentation site."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent
SCOPES = {
    "index",
    "getting-started",
    "architecture",
    "worked-example",
    "taxonomies",
    "sources",
    "crosswalks",
    "triangulation",
    "fusion",
    "joint-recheck",
    "classification",
    "retrieval-llm",
    "institutions",
    "io-projection",
    "express",
    "outputs",
    "evaluation",
    "reproducibility",
    "api-cli",
    "decision-register",
    "limitations",
    "glossary",
}
NEEDED = {
    "assets/css/site.css",
    "assets/js/site.js",
    "assets/js/search-index.js",
    "assets/diagrams/favicon.svg",
}


class Parsed(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.targets: list[tuple[str, str]] = []
        self.title = False
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "title":
            self.title = True
        if "id" in attributes:
            if attributes["id"] in self.ids:
                self.errors.append(f"duplicate id: {attributes['id']}")
            self.ids.add(attributes["id"])
        if tag == "img" and not attributes.get("alt"):
            self.errors.append("image missing alt")
        if tag in ("a", "link", "script", "img"):
            field = "src" if tag in ("script", "img") else "href"
            if attributes.get(field):
                self.targets.append((tag, attributes[field]))


def check() -> tuple[int, int]:
    pages = sorted(ROOT.glob("*.html"))
    actual_pages = {f.stem for f in pages}
    if actual_pages != SCOPES:
        missing = SCOPES - actual_pages
        extra = actual_pages - SCOPES
        raise RuntimeError(f"unexpected page set: missing={missing}, extra={extra}")

    for name in NEEDED:
        path = ROOT / name
        if not path.is_file():
            raise RuntimeError(f"missing static dependency: {path}")
    if not (ROOT / ".nojekyll").is_file():
        raise RuntimeError("missing .nojekyll for branch-based GitHub Pages")

    parsed: dict[Path, Parsed] = {}
    for page in pages:
        content = page.read_text(encoding="utf-8")
        forbidden_parts = (
            "project" + "sigma",
            "src/" + "sigma",
            "s" + "igma explorer",
        )
        if any(part in content.casefold() for part in forbidden_parts):
            raise RuntimeError(f"private-project reference leaked into public docs: {page.name}")
        parser = Parsed()
        parser.feed(content)
        if parser.errors:
            raise RuntimeError(f"{page.name}: {'; '.join(parser.errors)}")
        if (
            not parser.title
            or '<main id="main"' not in content
            or 'class="sidebar-nav"' not in content
        ):
            raise RuntimeError(f"{page.name}: missing document structure")
        parsed[page] = parser

    targets = 0
    for page, parser in parsed.items():
        for _, target in parser.targets:
            parts = urlsplit(target)
            if parts.scheme or parts.netloc or target.startswith("//"):
                continue
            if parts.path.startswith("/") or "\\" in parts.path:
                raise RuntimeError(f"{page.name}: unsafe/nonportable absolute link {target}")
            if not parts.path and not parts.fragment:
                continue
            child = Path(unquote(parts.path)) if parts.path else Path(page.name)
            local_file = (page.parent / child).resolve()
            if not local_file.is_relative_to(ROOT.resolve()):
                raise RuntimeError(f"{page.name}: link escapes published root {target}")
            if local_file.is_dir():
                local_file = local_file / "index.html"
            if not local_file.is_file():
                raise RuntimeError(f"{page.name}: broken local asset/link {target}")
            if parts.fragment and local_file.suffix == ".html" and local_file in parsed:
                if unquote(parts.fragment) not in parsed[local_file].ids:
                    raise RuntimeError(f"{page.name}: broken fragment {target}")
            targets += 1

    diagrams = list((ROOT / "assets" / "diagrams").glob("*.svg"))
    if len(diagrams) < 8:
        raise RuntimeError("expected SVG worked diagrams")
    for diagram in diagrams:
        content = diagram.read_text(encoding="utf-8")
        if "<svg" not in content or "</svg>" not in content:
            raise RuntimeError(f"invalid diagram markup: {diagram.name}")
    if not (ROOT / "source" / "index.md").is_file():
        raise RuntimeError("missing editable Markdown documentation sources")
    if not (ROOT / "site_builder.py").is_file():
        raise RuntimeError("missing regenerating build script")
    return len(pages), targets


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        pages_checked, links_checked = check()
    except (OSError, UnicodeError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"FAIL: documentation validation: {exc}") from exc
    figures = len(list((ROOT / "assets" / "diagrams").glob("*.svg")))
    print(f"PASS: {pages_checked} generated pages, {links_checked} local links, {figures} SVGs")
