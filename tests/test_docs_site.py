"""Keep the checked-in GitHub Pages content internally linked and isolated.

stdlib-only: existing package CI requires no additional runtime documentation tools.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path


def test_static_site_links_and_structure():
    root = Path(__file__).resolve().parents[1]
    path = root / 'docs' / 'check_site.py'
    assert path.is_file(), 'Missing documentation link checker'
    spec = importlib.util.spec_from_file_location('placetype_site_check', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pages, links = module.check()
    assert pages == 22
    assert links > 100


def test_public_site_contains_no_private_project_mentions():
    docs = Path(__file__).resolve().parents[1] / 'docs'
    for path in [*docs.glob('*.html'), *docs.glob('source/*.md')]:
        text = path.read_text(encoding='utf-8').casefold()
        for forbidden in ('project' + 'sigma', 'src/' + 'sigma', 's' + 'igma explorer'):
            assert forbidden not in text, path


def test_site_makes_critical_policy_boundaries_explicit():
    docs = Path(__file__).resolve().parents[1] / 'docs'
    assert '328' in (docs / 'crosswalks.html').read_text(encoding='utf-8')
    assert 'AUTO_ACCEPTED_UNCHECKED' in (docs / 'express.html').read_text(encoding='utf-8')
    assert 'EXACT' in (docs / 'fusion.html').read_text(encoding='utf-8')
    assert 'not an official' in (docs / 'io-projection.html').read_text(encoding='utf-8').lower()
    assert 'gold' in (docs / 'evaluation.html').read_text(encoding='utf-8').lower()


def test_light_theme_readability_and_text_contrast():
    """The default theme's small-text colors meet WCAG AA contrast against panels."""
    import re

    css = (Path(__file__).resolve().parents[1] / "docs/assets/css/site.css").read_text(
        encoding="utf-8"
    )
    light = css.split('html[data-theme="dark"]', maxsplit=1)[0]
    colors = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", light))

    def luminance(value: str) -> float:
        channels = [int(value[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in channels]
        return sum(x * y for x, y in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))

    def contrast(first: str, second: str) -> float:
        bright, dark = sorted((luminance(colors[first]), luminance(colors[second])), reverse=True)
        return (bright + 0.05) / (dark + 0.05)

    for foreground, background in (
        ("ink", "paper"),
        ("dim", "paper"),
        ("subtle", "paper"),
        ("sideText", "side"),
        ("accent", "paper"),
        ("accent", "accent2"),
    ):
        assert contrast(foreground, background) >= 4.5, (foreground, background)
    assert "font-size: 16.5px" in css  # intentionally larger reading text
    assert "@media (max-width: 580px)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css


def test_theme_control_is_in_every_header():
    """Keep the visible toggle in the top bar and its persisted preference wired."""
    docs = Path(__file__).resolve().parents[1] / "docs"
    for page in docs.glob("*.html"):
        source = page.read_text(encoding="utf-8")
        assert source.count('id="theme-toggle"') == 1, page
        header = source.split('class="topbar"', 1)[1].split('class="reading-layout"', 1)[0]
        assert 'id="theme-toggle"' in header, page
        assert 'aria-label="Switch to dark mode"' in header, page
        assert 'class="theme-moon"' in header and 'class="theme-sun"' in header, page
        assert source.index('localStorage.getItem') < source.index('assets/css/site.css'), page
    script = (docs / "assets/js/site.js").read_text(encoding="utf-8")
    assert "localStorage.setItem(themeKey, next)" in script
    assert "applyTheme(stored === 'dark' ? 'dark' : 'light')" in script
    assert "Switch to ${target} mode" in script
