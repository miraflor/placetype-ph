from __future__ import annotations

import hashlib
import json
import re
import warnings
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import pandas as pd
import requests

from .models import TaxonomyNode
from .taxonomy import LEVEL_ORDER, Taxonomy, TaxonomyError
from .text import clean_literal

OFFICIAL_FILES = {
    ("psic", "rev5"): (
        "https://psa.gov.ph/sites/default/files/scd/"
        "PSIC_Revision_5_Detailed_Structure_30July2026.xlsx"
    ),
    ("pscc", "2022"): "https://psa.gov.ph/system/files?file=scd/2022%20PSCC_11292023.xlsx",
}

PSA_API = {
    "pcpc": "https://classification.psa.gov.ph/pcpc",
    "psic": "https://classification.psa.gov.ph/psic",
    "pscc": "https://classification.psa.gov.ph/pscc",
}

LEVELS = {
    "psic": {"section": None, "division": 2, "group": 3, "class": 4, "subclass": 5},
    "pcpc": {"section": 1, "division": 2, "group": 3, "class": 4, "subclass": 5, "item": 6},
    "pscc": {"chapter": 2, "heading": 4, "hs_subheading": 6, "ahtn_subheading": 8, "commodity": 11},
}

# Shortest code that can be valid in each scheme. Anything shorter is not an unknown
# level; it is a corrupted code, almost always a spreadsheet column stored as numbers so
# that leading zeroes were lost (PSIC division "01" arriving as "1"). PCPC sections really
# are one digit, so the floor there is 1.
MIN_CODE_DIGITS = {"psic": 2, "pcpc": 1, "pscc": 2}

# PSIC Revision 5 has 22 sections, A through V. Recognising only A-U drops the last
# section and, because section membership is tracked in workbook order, silently
# re-parents its divisions under the previous section without any structural finding.
PSIC_SECTION_LETTERS = r"[A-V]"

# Published level counts for official sources, used as a post-import sanity check.
# PSIC's current web summary says 260 groups while the current official July 2026
# workbook contains 261 distinct group rows. Until PSA reconciles that discrepancy,
# group count is deliberately not a hard external gate.
EXPECTED_STRUCTURE: dict[tuple[str, str], dict[str, int]] = {
    ("psic", "rev5"): {
        "section": 22,
        "division": 88,
        "class": 493,
        "subclass": 1338,
    },
}

LEVEL_ALIASES = {
    "section": "section", "sections": "section",
    "division": "division", "divisions": "division",
    "group": "group", "groups": "group",
    "class": "class", "classes": "class",
    "subclass": "subclass", "sub class": "subclass", "sub-class": "subclass",
    "subclasses": "subclass",
    "item": "item", "items": "item",
    "chapter": "chapter", "chapters": "chapter",
    "heading": "heading", "headings": "heading",
    "hs subheading": "hs_subheading", "hs_subheading": "hs_subheading",
    "ahtn subheading": "ahtn_subheading", "ahtn_subheading": "ahtn_subheading",
    "commodity": "commodity", "commodities": "commodity",
}

ALIASES = {
    # Tuples are intentional: exact-match precedence must be deterministic when a
    # workbook contains both, for example, "Title" and "Description" columns.
    "code": ("code", "classification code", "psic code", "pcpc code", "pscc code"),
    "title": (
        "title",
        "classification description",
        "activity description",
        "commodity description",
        "item description",
        "description",
    ),
    "includes": ("includes", "include", "inclusions", "this class includes", "inclusion"),
    "excludes": ("excludes", "exclude", "exclusions", "this class excludes", "exclusion"),
    "notes": ("notes", "note", "explanatory notes", "explanatory note"),
}

# A true direct-parent column is different from a repeated PSIC section-membership column.
# Treating `Section` as the direct parent for every row flattens group/class/subclass nodes
# under the section and can still look superficially valid.
PARENT_ALIASES = ("parent", "parent code", "parent_code")
SECTION_ALIASES = ("section", "section code")
LEVEL_COLUMN_ALIASES = ("level", "hierarchy level", "hierarchy", "classification level")

_MAX_API_PAGES = 200


@dataclass(slots=True)
class ImportReport:
    """What the importer had to throw away.

    Silently dropping rows turns a malformed workbook into a plausible partial taxonomy
    whose only visible symptom is a smaller node count. Every discard is counted here so
    the caller can see it.
    """

    rows_seen: int = 0
    nodes_built: int = 0
    missing_code_or_title: int = 0
    missing_examples: list[str] = field(default_factory=list)
    short_codes: list[str] = field(default_factory=list)
    invalid_code_formats: list[str] = field(default_factory=list)
    level_mismatches: list[str] = field(default_factory=list)
    invalid_parent_codes: list[str] = field(default_factory=list)
    title_variants: list[str] = field(default_factory=list)
    unrecognized_lengths: dict[int, int] = field(default_factory=dict)
    unrecognized_examples: list[str] = field(default_factory=list)
    duplicate_codes: int = 0
    duplicate_conflicts: list[str] = field(default_factory=list)
    sheet_details: list[str] = field(default_factory=list)

    @property
    def skipped(self) -> int:
        return (
            self.missing_code_or_title
            + len(self.short_codes)
            + len(self.invalid_code_formats)
            + len(self.level_mismatches)
            + len(self.invalid_parent_codes)
            + sum(self.unrecognized_lengths.values())
            + len(self.duplicate_conflicts)
        )

    def messages(self) -> list[str]:
        out: list[str] = []
        if self.missing_code_or_title:
            out.append(f"{self.missing_code_or_title} row(s) had no usable code or title")
        if self.invalid_code_formats:
            out.append(f"{len(self.invalid_code_formats)} code(s) had invalid syntax")
        if self.unrecognized_lengths:
            detail = ", ".join(
                f"{count} code(s) of {length} digit(s)"
                for length, count in sorted(self.unrecognized_lengths.items())
            )
            out.append(f"no level matches {detail}")
        if self.level_mismatches:
            out.append(
                f"{len(self.level_mismatches)} code(s) do not match their declared level"
            )
        if self.invalid_parent_codes:
            out.append(f"{len(self.invalid_parent_codes)} invalid explicit parent code(s)")
        if self.duplicate_conflicts:
            out.append(f"{len(self.duplicate_conflicts)} conflicting duplicate definition(s)")
        return out

    def merge_messages(self) -> list[str]:
        out: list[str] = []
        if self.title_variants:
            out.append(
                f"{len(self.title_variants)} repeated code(s) had title variants; "
                "the fuller wording was kept"
            )
        if self.duplicate_codes:
            out.append(f"{self.duplicate_codes} duplicate definition(s) merged")
        return out


def _copy_report(target: ImportReport, source: ImportReport) -> None:
    target.rows_seen = source.rows_seen
    target.nodes_built = source.nodes_built
    target.missing_code_or_title = source.missing_code_or_title
    target.missing_examples = list(source.missing_examples)
    target.short_codes = list(source.short_codes)
    target.invalid_code_formats = list(source.invalid_code_formats)
    target.level_mismatches = list(source.level_mismatches)
    target.invalid_parent_codes = list(source.invalid_parent_codes)
    target.title_variants = list(source.title_variants)
    target.unrecognized_lengths = dict(source.unrecognized_lengths)
    target.unrecognized_examples = list(source.unrecognized_examples)
    target.duplicate_codes = source.duplicate_codes
    target.duplicate_conflicts = list(source.duplicate_conflicts)
    target.sheet_details = list(source.sheet_details)


def _declared_level(scheme: str, value: object) -> str | None:
    text = clean_literal(value)
    if not text:
        return None
    level = LEVEL_ALIASES.get(re.sub(r"\s+", " ", text.strip().casefold()))
    return level if level in LEVELS[scheme] else None


def _pad_to_level(scheme: str, code: str, level: str) -> str:
    width = LEVELS[scheme].get(level)
    if width is None or not code.isdigit() or len(code) >= width:
        return code
    return code.zfill(width)


def _code_matches_level(scheme: str, code: str, level: str) -> bool:
    """Whether a canonical code has the exact shape required by a declared level."""
    if scheme == "psic" and level == "section":
        return bool(re.fullmatch(PSIC_SECTION_LETTERS, code))
    width = LEVELS[scheme].get(level)
    return width is not None and code.isdigit() and len(code) == width


def _canonical_for_level(
    scheme: str, raw_code: object, level: str, *, repair_short: bool
) -> str:
    """Canonicalize a code whose hierarchy level is already known.

    A declared level can safely repair a lost leading zero because it supplies the
    intended width. It must never bless a code that is *longer* than that width or has
    the wrong representation; otherwise a bad level column can create a structurally
    plausible but semantically impossible tree.
    """
    code = canonical_code(scheme, raw_code)
    if repair_short:
        code = _pad_to_level(scheme, code, level)
    if not _code_matches_level(scheme, code, level):
        raise TaxonomyError(
            f"code {raw_code!r} normalizes to {code!r}, which is not valid for "
            f"{scheme} level {level!r}"
        )
    return code


def _record_unusable(report: ImportReport, scheme: str, code: str, level: str | None) -> bool:
    """True when the code is usable; otherwise record why it is not."""
    if not code:
        report.missing_code_or_title += 1
        if len(report.missing_examples) < 10:
            report.missing_examples.append("blank or unusable code")
        return False
    if code.isdigit() and len(code) < MIN_CODE_DIGITS[scheme]:
        report.short_codes.append(code)
        return False
    if level is None:
        length = len(code)
        report.unrecognized_lengths[length] = report.unrecognized_lengths.get(length, 0) + 1
        if len(report.unrecognized_examples) < 10:
            report.unrecognized_examples.append(code)
        return False
    return True


def _norm_header(value: object) -> str:
    text = (clean_literal(value) or "").casefold().replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", text)


def _digits(code: str) -> str:
    return re.sub(r"\D", "", code)


def _code_text(code: object) -> str:
    """Normalize spreadsheet-like integral code text without inventing digits.

    Excel readers can expose an integer-looking code as ``"10.0"``. A trailing
    all-zero decimal is safe to remove. Other decimal/sign syntax is not: blindly
    stripping punctuation would turn malformed ``"1.5"`` into the valid-looking code
    ``"15"``.
    """
    raw = clean_literal(code) or ""
    if re.fullmatch(r"\d+\.0+", raw):
        raw = raw.split(".", 1)[0]
    return raw


def _valid_code_syntax(scheme: str, code: object) -> bool:
    raw = _code_text(code)
    scheme = scheme.casefold()
    if scheme == "psic" and re.fullmatch(PSIC_SECTION_LETTERS, raw.upper()):
        return True
    if scheme in {"psic", "pcpc"}:
        return bool(re.fullmatch(r"\d+", raw))
    if scheme == "pscc":
        # PSCC/HS/AHTN displays may contain separators, but a code must start and end
        # with a digit. This accepts e.g. 10.06 / 1006-30 while rejecting signs,
        # decimal-like fragments with letters, and arbitrary prose containing digits.
        return bool(re.fullmatch(r"\d(?:[\d.\-\s]*\d)?", raw))
    return False


def canonical_code(scheme: str, code: object) -> str:
    raw = _code_text(code)
    if not _valid_code_syntax(scheme, raw):
        return ""
    if scheme == "psic" and re.fullmatch(PSIC_SECTION_LETTERS, raw.upper()):
        return raw.upper()
    return _digits(raw)


def infer_level(scheme: str, code: str) -> str | None:
    scheme = scheme.casefold()
    canonical = canonical_code(scheme, code)
    if scheme == "psic" and re.fullmatch(PSIC_SECTION_LETTERS, canonical):
        return "section"
    if not canonical or not canonical.isdigit():
        return None
    for level, length in LEVELS[scheme].items():
        if length == len(canonical):
            return level
    return None


# Headers naming a different role. A column whose name contains one of these is never
# a fuzzy match for another role, which is what made generic `code` select `Parent Code`.
_ROLE_MARKERS = ("parent", "section", "level")


def _find_column(
    columns: Iterable[object],
    aliases: Iterable[str],
    fuzzy: bool = True,
    avoid: Iterable[str] | None = None,
) -> object | None:
    """Resolve one role to a column header.

    Fuzzy matching uses every alias, including short ones such as `code`, because real
    headers are qualified ("Code (PSIC Rev. 5)", "Industry Code"). The false positive
    that has to be prevented is a header belonging to a *different* role, so those are
    excluded by name rather than by dropping short aliases and losing ordinary columns.
    The exclusion is derived from the alias set itself, so it protects any caller rather
    than only the ones that remember to pass it.
    """
    aliases = tuple(aliases)
    if avoid is None:
        avoid = tuple(
            marker for marker in _ROLE_MARKERS if not any(marker in alias for alias in aliases)
        )
    else:
        avoid = tuple(avoid)
    normalized = {_norm_header(c): c for c in columns}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    if not fuzzy:
        return None
    for norm, original in normalized.items():
        if any(marker in norm for marker in avoid):
            continue
        if any(alias in norm for alias in aliases):
            return original
    return None


def _title_key(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _merge_duplicate_nodes(
    nodes: Iterable[TaxonomyNode],
    report: ImportReport | None = None,
    *,
    tolerate_conflicts: bool = False,
) -> list[TaxonomyNode]:
    """Deduplicate repeated taxonomy codes without silently hiding contradictions.

    Official workbooks repeat a section, and PSA publishes a Summary of Classification
    Scheme alongside the Detailed Classification, so the same code legitimately appears
    twice with differently worded titles. That is a wording variant: keep the fuller row
    and record it. A repeated code at another *level* or under a different explicit
    *parent* is a real structural contradiction and still fails, rather than becoming
    first-wins by sheet order.
    """
    report = report if report is not None else ImportReport()
    unique: dict[str, TaxonomyNode] = {}
    for node in nodes:
        previous = unique.get(node.code)
        if previous is None:
            unique[node.code] = node
            continue
        if previous.level != node.level:
            message = (
                f"conflicting duplicate code {node.code!r}: levels "
                f"{previous.level!r} and {node.level!r}"
            )
            if tolerate_conflicts:
                report.duplicate_conflicts.append(message)
                continue
            raise TaxonomyError(message)
        if (
            previous.parent_code is not None
            and node.parent_code is not None
            and previous.parent_code != node.parent_code
        ):
            message = (
                f"conflicting duplicate code {node.code!r}: parents "
                f"{previous.parent_code!r} and {node.parent_code!r}"
            )
            if tolerate_conflicts:
                report.duplicate_conflicts.append(message)
                continue
            raise TaxonomyError(message)
        if _title_key(previous.title) != _title_key(node.title):
            report.title_variants.append(
                f"{node.code}: {previous.title!r} / {node.title!r}"
            )

        report.duplicate_codes += 1

        # Merge complementary information field-by-field. Choosing one whole row can
        # accidentally keep a terse summary merely because it carries an explicit parent
        # while discarding richer notes from the detailed occurrence. Parent identity is
        # structural; explanatory text is descriptive, so preserve the strongest value
        # from each independently.
        parent = previous.parent_code or node.parent_code

        def fuller(left: str, right: str) -> str:
            if not left:
                return right
            if not right:
                return left
            return right if len(right.strip()) > len(left.strip()) else left

        unique[node.code] = replace(
            previous,
            parent_code=parent,
            title=fuller(previous.title, node.title),
            description=fuller(previous.description, node.description),
            includes=fuller(previous.includes, node.includes),
            excludes=fuller(previous.excludes, node.excludes),
            source_url=previous.source_url or node.source_url,
        )
    return list(unique.values())


def _derive_parents(nodes: list[TaxonomyNode]) -> list[TaxonomyNode]:
    """Attach parents by explicit value, then by numeric prefix, then by section order.

    The prefix search used to compare every node against every other node. It now walks
    decreasing prefix lengths against a dict, which is linear in code length per node.
    """
    by_code = {n.code: n for n in nodes}
    scheme = nodes[0].scheme if nodes else ""
    out: list[TaxonomyNode] = []
    current_section: str | None = None

    for node in nodes:
        if scheme == "psic" and node.level == "section":
            current_section = node.code
            out.append(node)
            continue
        parent = node.parent_code
        # A supplied parent is authoritative. Do not silently throw away an invalid
        # reference and replace it with a prefix-derived parent; Taxonomy will report
        # the missing parent explicitly instead.
        if parent is None and node.code.isdigit():
            for length in range(len(node.code) - 1, 0, -1):
                candidate = node.code[:length]
                if candidate in by_code and candidate != node.code:
                    parent = candidate
                    break
        if parent is None and scheme == "psic" and node.level == "division":
            if current_section in by_code:
                parent = current_section
        out.append(replace(node, parent_code=parent))
    return out


def _build(
    nodes: list[TaxonomyNode], strict: bool = True, strict_levels: bool = False
) -> Taxonomy:
    """Build the tree and enforce its shape.

    Two levels of enforcement. A node that lost its parent is always an error under
    `strict`, because that silently deletes a whole level from backoff. A missing
    intermediate level is reported but tolerated unless `strict_levels` is set: partial
    PSA extracts legitimately skip a level, and failing the whole import for that leaves
    no way to keep orphan detection while accepting a gap.
    """
    taxonomy = Taxonomy(_derive_parents(nodes))
    report = taxonomy.structural_report()
    # `strict` controls orphan/wrong-parent findings only. `strict_levels` is an
    # independent gate and must still work when callers deliberately allow orphans.
    problems = list(report.errors) if strict else []
    if strict_levels:
        problems.extend(report.level_gaps)
        expected = LEVEL_ORDER.get(taxonomy.scheme, ())
        present = {node.level for node in taxonomy.nodes.values()}
        problems.extend(
            f"taxonomy has no nodes at expected {level} level"
            for level in expected
            if level not in present
        )
    if not problems:
        return taxonomy
    shown = "\n  ".join(problems[:10])
    more = f"\n  ... and {len(problems) - 10} more" if len(problems) > 10 else ""
    if strict and report.errors:
        remedy = (
            "The workbook probably lists parents after their children, or in a separate "
            "sheet. Re-run `placetype taxonomy import-xlsx` with --parent-column or "
            "--section-column as appropriate, or pass --allow-orphan-nodes to accept the "
            "tree as-is."
        )
    else:
        remedy = (
            "These are missing intermediate levels, not lost parents. Drop --strict-levels "
            "to accept them, or supply the missing rows."
        )
    raise TaxonomyError(
        f"{len(problems)} node(s) do not fit the {taxonomy.scheme} level order:\n  "
        f"{shown}{more}\n{remedy}"
    )


def normalize_frame(
    frame: pd.DataFrame,
    scheme: str,
    version: str,
    source_url: str = "",
    code_column: str | None = None,
    title_column: str | None = None,
    parent_column: str | None = None,
    section_column: str | None = None,
    level_column: str | None = None,
    report: ImportReport | None = None,
) -> list[TaxonomyNode]:
    """Return raw nodes. Parent derivation happens once, after all sheets are merged."""
    scheme = scheme.casefold()
    report = report if report is not None else ImportReport()

    # User-supplied column names are an explicit schema contract. A typo must not
    # quietly fall through to auto-detection or the raw-layout scanner, because that
    # can reconstruct a different tree than the caller asked us to import.
    supplied_columns = {
        "code": code_column,
        "title": title_column,
        "parent": parent_column,
        "section": section_column,
        "level": level_column,
    }
    missing_supplied = [
        f"{label}={column!r}"
        for label, column in supplied_columns.items()
        if column is not None and column not in frame.columns
    ]
    if missing_supplied:
        raise TaxonomyError(
            "explicit column(s) not found in this sheet: " + ", ".join(missing_supplied)
        )

    if code_column is None:
        code_column = _find_column(frame.columns, ALIASES["code"])
    if title_column is None:
        title_column = _find_column(frame.columns, ALIASES["title"])
    if code_column is None or title_column is None:
        raise TaxonomyError("could not identify code/title columns; supply them explicitly")
    if parent_column is None:
        parent_column = _find_column(frame.columns, PARENT_ALIASES, fuzzy=False)
    if section_column is None and scheme == "psic":
        section_column = _find_column(frame.columns, SECTION_ALIASES, fuzzy=False)
    if level_column is None:
        level_column = _find_column(frame.columns, LEVEL_COLUMN_ALIASES, fuzzy=False)

    includes_col = _find_column(frame.columns, ALIASES["includes"])
    excludes_col = _find_column(frame.columns, ALIASES["excludes"])
    notes_col = _find_column(frame.columns, ALIASES["notes"])

    nodes: list[TaxonomyNode] = []
    for row in frame.to_dict("records"):
        report.rows_seen += 1
        raw_code = clean_literal(row.get(code_column))
        title = clean_literal(row.get(title_column))
        if not raw_code or not title:
            report.missing_code_or_title += 1
            if len(report.missing_examples) < 10:
                report.missing_examples.append(
                    f"code={raw_code!r}, title={title!r}"
                )
            continue
        declared = _declared_level(scheme, row.get(level_column)) if level_column else None
        if not _valid_code_syntax(scheme, raw_code):
            report.invalid_code_formats.append(str(raw_code))
            continue
        code = canonical_code(scheme, raw_code)
        if declared is not None:
            # An explicit level makes the intended width unambiguous, so a lost leading
            # zero can be repaired rather than only detected. A code wider than the
            # declared level is corruption, not something to reinterpret.
            try:
                code = _canonical_for_level(scheme, raw_code, declared, repair_short=True)
            except TaxonomyError:
                report.level_mismatches.append(f"{raw_code!r} as {declared}")
                continue
        level = declared or infer_level(scheme, code)
        if not _record_unusable(report, scheme, code, level):
            continue
        # Do not deduplicate inside a sheet. Repeated definitions must reach the
        # global merge so title variants, conflicting explicit parents, and richer
        # explanatory notes are audited consistently whether they occur within one
        # worksheet or across several worksheets.
        notes = clean_literal(row.get(notes_col)) if notes_col else None
        raw_parent = clean_literal(row.get(parent_column)) if parent_column else None
        if raw_parent is None and scheme == "psic" and level == "division" and section_column:
            raw_parent = clean_literal(row.get(section_column))
        parent = canonical_code(scheme, raw_parent) if raw_parent else None
        if raw_parent and not parent:
            report.invalid_parent_codes.append(f"{code!r}->{raw_parent!r}")
            continue
        nodes.append(
            TaxonomyNode(
                scheme=scheme,
                version=version,
                code=code,
                level=level,
                title=title,
                parent_code=parent or None,
                description=notes or "",
                includes=(clean_literal(row.get(includes_col)) or "") if includes_col else "",
                excludes=(clean_literal(row.get(excludes_col)) or "") if excludes_col else "",
                source_url=source_url,
            )
        )
    report.nodes_built += len(nodes)
    if not nodes:
        raise TaxonomyError("no taxonomy nodes recognized")
    return nodes


def _detect_header(raw: pd.DataFrame, max_rows: int = 40) -> int | None:
    needles = {x for values in ALIASES.values() for x in values}
    best: tuple[int, int] | None = None
    for i in range(min(max_rows, len(raw))):
        cells = [_norm_header(x) for x in raw.iloc[i].tolist() if clean_literal(x)]
        score = sum(any(n in cell for n in needles) for cell in cells)
        if best is None or score > best[1]:
            best = (i, score)
    return best[0] if best and best[1] >= 2 else None


def _is_short_code_cell(scheme: str, value: object) -> bool:
    """A bare single digit where the scheme needs at least two.

    The scanner's code test rejects such a cell outright, so without this the corruption
    is invisible on the fallback path: only the deeper codes survive, one level too high.
    """
    if MIN_CODE_DIGITS[scheme] < 2:
        return False
    return bool(re.fullmatch(r"\d", clean_literal(value) or ""))


def _looks_like_code(scheme: str, value: object) -> bool:
    text = clean_literal(value) or ""
    if scheme == "psic":
        return bool(re.fullmatch(rf"{PSIC_SECTION_LETTERS}|\d{{2,5}}", text.upper()))
    if scheme == "pcpc":
        return bool(re.fullmatch(r"\d{1,6}", text))
    if scheme == "pscc":
        digits = _digits(text)
        return len(digits) in {2, 4, 6, 8, 11} and bool(re.fullmatch(r"[\d.\-\s]+", text))
    return False


def _split_explanatory_text(text: str) -> tuple[str, str, str]:
    """Best-effort split when PSA puts title/includes/excludes in one spreadsheet cell."""
    normalized = re.sub(r"\s+", " ", text).strip()
    prefix = r"\b(?:this (?:class|group|division|subclass|item) )?"
    include_match = re.search(prefix + r"includes?\s*:\s*", normalized, flags=re.I)
    exclude_match = re.search(prefix + r"excludes?\s*:\s*", normalized, flags=re.I)
    title_end = min([m.start() for m in (include_match, exclude_match) if m] or [len(normalized)])
    title = normalized[:title_end].strip(" ;:-")
    includes = ""
    excludes = ""
    if include_match:
        end = (
            exclude_match.start()
            if exclude_match and exclude_match.start() > include_match.end()
            else len(normalized)
        )
        includes = normalized[include_match.end() : end].strip()
    if exclude_match:
        excludes = normalized[exclude_match.end() :].strip()
    return title or normalized, includes, excludes



def _find_header_map(
    raw: pd.DataFrame, required: tuple[str, ...], max_rows: int = 12
) -> tuple[int, dict[str, int]] | None:
    """Find a compact official-table header row by normalized exact labels."""
    required_set = set(required)
    for row_idx in range(min(max_rows, len(raw))):
        mapping: dict[str, int] = {}
        for col_idx, value in enumerate(raw.iloc[row_idx].tolist()):
            label = _norm_header(value)
            if label:
                mapping[label] = col_idx
        if required_set.issubset(mapping):
            return row_idx, mapping
    return None


def _parse_psic_official_matrix(
    sheets: dict[str, pd.DataFrame],
    version: str,
    source_url: str,
) -> tuple[list[TaxonomyNode], ImportReport] | None:
    """Parse PSA's Rev. 5 matrix where one row may define several hierarchy nodes.

    The official workbook has separate Section/Division/Group/Class/Sub-Class columns.
    Rows such as ``0113`` + ``01130`` intentionally define both a class and its
    subclass with the same description. A generic one-node-per-row scanner necessarily
    drops the shallower node, so this layout needs a column-aware parser.
    """
    required = ("section", "division", "group", "class", "sub class", "description")
    level_columns = (
        ("section", "section"),
        ("division", "division"),
        ("group", "group"),
        ("class", "class"),
        ("sub class", "subclass"),
    )
    for sheet_name, raw in sheets.items():
        found = _find_header_map(raw, required)
        if found is None:
            continue
        header_idx, columns = found
        report = ImportReport()
        nodes: list[TaxonomyNode] = []
        for row_idx in range(header_idx + 1, len(raw)):
            cells = raw.iloc[row_idx].tolist()
            if not any(clean_literal(cell) for cell in cells):
                continue
            title = clean_literal(cells[columns["description"]]) or ""
            raw_codes = [
                clean_literal(cells[columns[column_name]])
                for column_name, _ in level_columns
            ]
            if not any(raw_codes):
                # Non-taxonomy prose belongs to another layout, not this matrix.
                continue
            report.rows_seen += 1
            if not title:
                report.missing_code_or_title += 1
                if len(report.missing_examples) < 10:
                    report.missing_examples.append(
                        " | ".join(x for x in raw_codes if x)
                    )
                continue
            for (_column_name, level), raw_code in zip(level_columns, raw_codes, strict=True):
                if not raw_code:
                    continue
                try:
                    code = _canonical_for_level("psic", raw_code, level, repair_short=True)
                except TaxonomyError:
                    report.level_mismatches.append(f"{raw_code!r} as {level}")
                    continue
                nodes.append(
                    TaxonomyNode(
                        scheme="psic",
                        version=version,
                        code=code,
                        level=level,
                        title=title,
                        source_url=source_url,
                    )
                )
        report.nodes_built = len(nodes)
        report.sheet_details.append(
            f"{sheet_name}: parser=psic-level-columns header_row={header_idx + 1} "
            f"nodes={len(nodes)}"
        )
        return nodes, report
    return None


def _implicit_pscc_title(level: str, code: str, child_titles: list[str]) -> str:
    clean_titles = [title.strip() for title in child_titles if title and title.strip()]
    if len(clean_titles) == 1:
        return clean_titles[0]
    label = {
        "chapter": "Chapter",
        "heading": "Heading",
        "hs_subheading": "HS subheading",
        "ahtn_subheading": "AHTN subheading",
    }.get(level, level.replace("_", " ").title())
    return f"{label} {code}"


def _complete_pscc_prefix_hierarchy(
    nodes: list[TaxonomyNode], version: str, source_url: str
) -> list[TaxonomyNode]:
    """Add omitted PSCC/HS/AHTN ancestors that are unambiguous code prefixes.

    The PSA workbook often prints only the deepest category when an HS/AHTN level has
    no independent split. The hierarchy itself is still encoded by the 2/4/6/8/11-digit
    prefixes. Missing prefix nodes are therefore structural nodes, not guessed commodity
    assignments. They are explicitly marked in ``description``.
    """
    by_code: dict[str, TaxonomyNode] = {node.code: node for node in nodes}
    level_for_width = {2: "chapter", 4: "heading", 6: "hs_subheading", 8: "ahtn_subheading"}
    needed: set[str] = set()
    for node in list(by_code.values()):
        if not node.code.isdigit():
            continue
        for width in (2, 4, 6, 8):
            if len(node.code) > width:
                needed.add(node.code[:width])
    for code in sorted(needed, key=lambda value: (len(value), value)):
        if code in by_code:
            continue
        level = level_for_width[len(code)]
        # Use an explicit descendant title only when this missing node has one immediate
        # branch. Otherwise a generic code label is safer than inventing a semantic title.
        next_width = {2: 4, 4: 6, 6: 8, 8: 11}[len(code)]
        child_titles = [
            node.title
            for child_code, node in by_code.items()
            if len(child_code) == next_width and child_code.startswith(code)
        ]
        by_code[code] = TaxonomyNode(
            scheme="pscc",
            version=version,
            code=code,
            level=level,
            title=_implicit_pscc_title(level, code, child_titles),
            description=(
                "Implicit hierarchy node derived from the official 2022 PSCC code prefix; "
                "the workbook does not label this intermediate level separately."
            ),
            source_url=source_url,
        )
    return list(by_code.values())


def _parse_pscc_official_matrix(
    sheets: dict[str, pd.DataFrame],
    version: str,
    source_url: str,
) -> tuple[list[TaxonomyNode], ImportReport] | None:
    """Parse the PSA 2022 PSCC workbook's Heading / PSCC / Description layout."""
    required = ("heading", "2022 pscc", "description")
    for sheet_name, raw in sheets.items():
        found = _find_header_map(raw, required)
        if found is None:
            continue
        header_idx, columns = found
        report = ImportReport()
        nodes: list[TaxonomyNode] = []
        for row_idx in range(header_idx + 1, len(raw)):
            cells = raw.iloc[row_idx].tolist()
            if not any(clean_literal(cell) for cell in cells):
                continue
            desc = clean_literal(cells[columns["description"]]) or ""
            heading_raw = clean_literal(cells[columns["heading"]])
            pscc_raw = clean_literal(cells[columns["2022 pscc"]])

            chapter_match = re.match(r"^Chapter\s+(\d+)\s*-\s*(.+)$", desc, flags=re.I)
            parsed_any = False
            if chapter_match:
                code = chapter_match.group(1).zfill(2)
                nodes.append(
                    TaxonomyNode(
                        scheme="pscc",
                        version=version,
                        code=code,
                        level="chapter",
                        title=chapter_match.group(2).strip(),
                        source_url=source_url,
                    )
                )
                parsed_any = True

            for raw_code, expected_level in ((heading_raw, "heading"), (pscc_raw, None)):
                if not raw_code:
                    continue
                normalized_raw = raw_code
                if expected_level == "heading":
                    # One cell in the live PSA workbook is stored numerically as 98.1
                    # with Excel format 0.00, i.e. the displayed heading is 98.10.
                    # A declared Heading column makes this right-padding unambiguous.
                    match = re.fullmatch(r"(\d{1,2})\.(\d)", raw_code)
                    if match:
                        normalized_raw = f"{match.group(1).zfill(2)}.{match.group(2)}0"
                code = canonical_code("pscc", normalized_raw)
                level = infer_level("pscc", normalized_raw)
                if not code or level is None:
                    report.invalid_code_formats.append(raw_code)
                    continue
                if expected_level is not None and level != expected_level:
                    report.level_mismatches.append(f"{raw_code!r} as {expected_level}")
                    continue
                if not desc:
                    report.missing_code_or_title += 1
                    if len(report.missing_examples) < 10:
                        report.missing_examples.append(raw_code)
                    continue
                nodes.append(
                    TaxonomyNode(
                        scheme="pscc",
                        version=version,
                        code=code,
                        level=level,
                        title=desc,
                        source_url=source_url,
                    )
                )
                parsed_any = True

            # Section banners and dash-only descriptive grouping rows are presentation
            # context, not discarded taxonomy nodes, so they do not inflate diagnostics.
            if parsed_any:
                report.rows_seen += 1

        nodes = _merge_duplicate_nodes(nodes, report, tolerate_conflicts=False)
        nodes = _complete_pscc_prefix_hierarchy(nodes, version, source_url)
        report.nodes_built = len(nodes)
        report.sheet_details.append(
            f"{sheet_name}: parser=pscc-code-columns header_row={header_idx + 1} "
            f"nodes={len(nodes)}"
        )
        return nodes, report
    return None

def _scan_raw_sheets(
    sheets: dict[str, pd.DataFrame],
    scheme: str,
    version: str,
    source_url: str,
    report: ImportReport | None = None,
) -> list[TaxonomyNode]:
    """Best-effort scanner for visual hierarchy sheets.

    A row in a visual taxonomy normally represents one node even when ancestor codes are
    repeated in columns to its left. Choose the rightmost code that has a textual label to
    its right. Unlike older versions, do not deduplicate across rows here: repeated node
    definitions must reach the global merger so richer wording/notes can be reconciled in
    one place.
    """
    report = report if report is not None else ImportReport()
    nodes: list[TaxonomyNode] = []
    for sheet_name, raw in sheets.items():
        before_sheet = len(nodes)
        for _, row in raw.iterrows():
            cells = row.tolist()
            if not any(clean_literal(cell) for cell in cells):
                continue
            report.rows_seen += 1

            candidates: list[tuple[int, object, str]] = []
            for i, cell in enumerate(cells):
                if not _looks_like_code(scheme, cell):
                    continue
                title: str | None = None
                for candidate in cells[i + 1 :]:
                    candidate_text = clean_literal(candidate)
                    if not candidate_text or _looks_like_code(scheme, candidate_text):
                        continue
                    if len(candidate_text) >= 2 and re.search(r"[A-Za-z]", candidate_text):
                        title = candidate_text
                        break
                if title:
                    candidates.append((i, cell, title))

            if not candidates:
                # Preserve the leading-zero corruption detector on the scanner path.
                short = next(
                    (
                        clean_literal(cell)
                        for i, cell in enumerate(cells)
                        if _is_short_code_cell(scheme, cell)
                        and any(clean_literal(x) for x in cells[i + 1 :])
                    ),
                    None,
                )
                if short:
                    report.short_codes.append(short)
                else:
                    # Code-like but unparseable cells are visible diagnostics, not fatal
                    # by themselves. This catches summary ranges such as 01-03 or 46, 47.
                    bad = next(
                        (
                            clean_literal(cell)
                            for i, cell in enumerate(cells)
                            if clean_literal(cell)
                            and re.match(r"^\s*\d", clean_literal(cell) or "")
                            and any(clean_literal(x) for x in cells[i + 1 :])
                        ),
                        None,
                    )
                    if bad and not _valid_code_syntax(scheme, bad):
                        report.invalid_code_formats.append(bad)
                    else:
                        report.missing_code_or_title += 1
                        if len(report.missing_examples) < 10:
                            preview = [clean_literal(x) for x in cells if clean_literal(x)]
                            report.missing_examples.append(" | ".join(preview[:4]))
                continue

            i, cell, title = max(candidates, key=lambda item: item[0])
            raw_code = clean_literal(cell) or ""
            code = canonical_code(scheme, raw_code)
            level = infer_level(scheme, raw_code)
            if not _record_unusable(report, scheme, code, level):
                continue
            title_text, includes, excludes = _split_explanatory_text(title)
            nodes.append(
                TaxonomyNode(
                    scheme=scheme,
                    version=version,
                    code=code,
                    level=level,
                    title=title_text,
                    includes=includes,
                    excludes=excludes,
                    source_url=source_url,
                )
            )
        report.sheet_details.append(
            f"{sheet_name}: parser=visual header_row=None nodes={len(nodes) - before_sheet}"
        )
    report.nodes_built += len(nodes)
    return nodes


def import_excel(
    path: str | Path,
    scheme: str,
    version: str,
    source_url: str = "",
    code_column: str | None = None,
    title_column: str | None = None,
    parent_column: str | None = None,
    section_column: str | None = None,
    strict: bool = True,
    strict_levels: bool = False,
    level_column: str | None = None,
    report: ImportReport | None = None,
    diagnostic: bool = False,
) -> Taxonomy:
    path = Path(path)
    scheme = scheme.casefold()
    report = report if report is not None else ImportReport()
    flat_report = ImportReport()
    sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=str)

    # The current official PSIC and PSCC workbooks are not generic two-column
    # code/title tables. Parse their documented matrix layouts before falling back to
    # heuristics. This was established against the live PSA workbooks in September 2026.
    special: tuple[list[TaxonomyNode], ImportReport] | None = None
    if scheme == "psic":
        special = _parse_psic_official_matrix(sheets, version, source_url)
    elif scheme == "pscc":
        special = _parse_pscc_official_matrix(sheets, version, source_url)
    if special is not None:
        special_nodes, special_report = special
        merged_nodes = _merge_duplicate_nodes(
            special_nodes, special_report, tolerate_conflicts=diagnostic
        )
        _copy_report(report, special_report)
        if not diagnostic:
            _check_report(report, scheme)
        report.nodes_built = len(merged_nodes)
        if diagnostic:
            known_codes = {node.code for node in merged_nodes}
            repaired: list[TaxonomyNode] = []
            for node in merged_nodes:
                parent = node.parent_code
                if parent is not None and parent not in known_codes:
                    finding = f"{node.code}->{parent} (parent node absent)"
                    if finding not in report.invalid_parent_codes:
                        report.invalid_parent_codes.append(finding)
                    repaired.append(replace(node, parent_code=None))
                else:
                    repaired.append(node)
            merged_nodes = repaired
        return _build(merged_nodes, strict, strict_levels)

    errors: list[str] = []
    all_nodes: list[TaxonomyNode] = []
    header_rows: dict[str, int] = {}
    for sheet_name, raw in sheets.items():
        header_idx = _detect_header(raw)
        if header_idx is None:
            continue
        header_rows[sheet_name] = header_idx
        frame = pd.read_excel(path, sheet_name=sheet_name, header=header_idx, dtype=str)
        resolved_code = code_column or _find_column(frame.columns, ALIASES["code"])
        resolved_title = title_column or _find_column(frame.columns, ALIASES["title"])
        resolved_parent = parent_column or _find_column(
            frame.columns, PARENT_ALIASES, fuzzy=False
        )
        resolved_section = section_column
        if resolved_section is None and scheme == "psic":
            resolved_section = _find_column(frame.columns, SECTION_ALIASES, fuzzy=False)
        resolved_level = level_column or _find_column(
            frame.columns, LEVEL_COLUMN_ALIASES, fuzzy=False
        )
        before = len(all_nodes)
        try:
            all_nodes.extend(
                normalize_frame(
                    frame,
                    scheme,
                    version,
                    source_url,
                    code_column,
                    title_column,
                    parent_column,
                    section_column,
                    level_column,
                    flat_report,
                )
            )
        except TaxonomyError as exc:
            errors.append(f"{sheet_name}: {exc}")
        flat_report.sheet_details.append(
            f"{sheet_name}: parser=flat header_row={header_idx + 1} "
            f"code={resolved_code!r} title={resolved_title!r} parent={resolved_parent!r} "
            f"section={resolved_section!r} level={resolved_level!r} "
            f"nodes={len(all_nodes) - before}"
        )

    # If a workbook is formatted as a visual detailed structure rather than a flat
    # table, fall back to scanning code/title pairs row by row. Skip it when a level
    # column made the flat parse authoritative: the scanner cannot see declared levels,
    # so it would re-add the same nodes at their unpadded codes and defeat the repair.
    used_level_column = bool(level_column) or any(
        _find_column(
            pd.read_excel(path, sheet_name=name, header=idx, dtype=str, nrows=0).columns,
            LEVEL_COLUMN_ALIASES,
            fuzzy=False,
        )
        for name, idx in header_rows.items()
    )
    used_explicit_columns = any(
        value is not None
        for value in (
            code_column,
            title_column,
            parent_column,
            section_column,
            level_column,
        )
    )
    chosen_report = flat_report
    if (
        len({n.code for n in all_nodes}) < 50
        and not used_level_column
        and not used_explicit_columns
    ):
        scan_report = ImportReport()
        scanned = _scan_raw_sheets(sheets, scheme, version, source_url, scan_report)
        existing_codes = {n.code for n in all_nodes}
        new_scanned = [node for node in scanned if node.code not in existing_codes]
        if new_scanned or not all_nodes:
            all_nodes.extend(new_scanned if all_nodes else scanned)
            # These two parsers inspect the same source rows. Do not add their counters
            # together and claim the workbook had twice as many rows; use the scanner's
            # source-row accounting when it materially contributed coverage, while
            # preserving fatal diagnostics discovered by the structured parse.
            chosen_report = scan_report
            chosen_report.sheet_details = [
                *flat_report.sheet_details,
                *scan_report.sheet_details,
            ]
            chosen_report.short_codes = list(
                dict.fromkeys([*flat_report.short_codes, *scan_report.short_codes])
            )
            chosen_report.invalid_code_formats = list(
                dict.fromkeys(
                    [*flat_report.invalid_code_formats, *scan_report.invalid_code_formats]
                )
            )
            chosen_report.level_mismatches = list(
                dict.fromkeys(
                    [*flat_report.level_mismatches, *scan_report.level_mismatches]
                )
            )
            chosen_report.invalid_parent_codes = list(
                dict.fromkeys(
                    [*flat_report.invalid_parent_codes, *scan_report.invalid_parent_codes]
                )
            )
            chosen_report.missing_examples = list(
                dict.fromkeys([*flat_report.missing_examples, *scan_report.missing_examples])
            )[:10]
            chosen_report.unrecognized_examples = list(
                dict.fromkeys(
                    [*flat_report.unrecognized_examples, *scan_report.unrecognized_examples]
                )
            )[:10]

    _copy_report(report, chosen_report)

    # Deduplicate while preserving workbook order; parents are derived exactly once.
    # Repeated definitions must agree on hierarchy identity rather than silently
    # becoming first-wins by sheet order.
    merged_nodes = _merge_duplicate_nodes(
        all_nodes, report, tolerate_conflicts=diagnostic
    )
    if not merged_nodes:
        detail = "; ".join(errors) or "no recognizable table found"
        raise TaxonomyError(f"unable to normalize workbook: {detail}")
    if not diagnostic:
        _check_report(report, scheme)
    report.nodes_built = len(merged_nodes)

    # Diagnosis is intentionally best-effort: an explicit parent that is absent from
    # the parsed node set is still a fatal error during a normal import, but should not
    # prevent diagnosis from reporting the rest of the workbook. Record it and detach
    # the edge only in diagnostic mode so structural/coverage findings can still run.
    if diagnostic:
        known_codes = {node.code for node in merged_nodes}
        diagnostic_nodes: list[TaxonomyNode] = []
        for node in merged_nodes:
            parent = node.parent_code
            if parent is not None and parent not in known_codes:
                finding = f"{node.code}->{parent} (parent node absent)"
                if finding not in report.invalid_parent_codes:
                    report.invalid_parent_codes.append(finding)
                diagnostic_nodes.append(replace(node, parent_code=None))
            else:
                diagnostic_nodes.append(node)
        merged_nodes = diagnostic_nodes

    return _build(merged_nodes, strict, strict_levels)


def _check_report(report: ImportReport, scheme: str) -> None:
    """Refuse code-shape corruption; warn about non-fatal discarded rows.

    A code shorter than the scheme allows is the signature of a spreadsheet column stored
    as numbers, which does not merely drop those rows: PSIC group "011" arrives as "11"
    and is then read as a division, so the whole branch shifts up one level while every
    structural check still passes. Guessing the padding is not safe without a declared
    level, so this stops and says what to do.
    """
    # Unparseable cells are discarded rather than canonicalized. Official fetches are
    # protected by published-structure checks, while manual imports report the loss.
    # Short numeric codes remain fatal below because they can shift a whole branch to
    # the wrong hierarchy level rather than merely dropping one non-node row.
    if report.short_codes:
        sample = ", ".join(sorted(set(report.short_codes))[:10])
        raise TaxonomyError(
            f"{len(report.short_codes)} code(s) are shorter than the minimum "
            f"{MIN_CODE_DIGITS[scheme]} digit(s) for {scheme}: {sample}.\n"
            "The code column was almost certainly stored as numbers, so leading zeroes "
            "were lost and deeper codes now read as shallower levels. Re-export the code "
            "column as text, or pass --level-column so the intended width is explicit."
        )
    if report.level_mismatches:
        sample = ", ".join(report.level_mismatches[:10])
        raise TaxonomyError(
            f"{len(report.level_mismatches)} code(s) contradict their declared hierarchy "
            f"level: {sample}. Fix the level/code columns instead of importing a shifted tree."
        )
    if report.invalid_parent_codes:
        sample = ", ".join(report.invalid_parent_codes[:10])
        raise TaxonomyError(
            f"{len(report.invalid_parent_codes)} explicit parent code(s) are invalid: {sample}. "
            "Fix the parent column instead of silently deriving a replacement parent."
        )
    messages = report.messages()
    if messages:
        warnings.warn(
            f"import discarded {report.skipped} of {report.rows_seen} row(s): "
            + "; ".join(messages),
            RuntimeWarning,
            stacklevel=2,
        )


def structure_deviations(taxonomy: Taxonomy) -> list[str]:
    """Compare a built tree against the published counts for that official source.

    A silent structural loss, such as an unrecognised section letter taking its whole
    branch with it, leaves a tree that passes every internal check. Published counts are
    the only external reference available without the workbook itself.
    """
    expected = EXPECTED_STRUCTURE.get((taxonomy.scheme, taxonomy.version))
    if not expected:
        return []
    actual: dict[str, int] = {}
    for node in taxonomy.nodes.values():
        actual[node.level] = actual.get(node.level, 0) + 1
    out: list[str] = []
    for level, count in expected.items():
        found = actual.get(level, 0)
        if found != count:
            out.append(f"{level}: found {found:,}, official structure has {count:,}")
    return out


def download_official(scheme: str, version: str, destination: str | Path) -> Path:
    key = (scheme.casefold(), version)
    if key not in OFFICIAL_FILES:
        raise ValueError(f"no direct official file configured for {scheme} {version}")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(
        OFFICIAL_FILES[key],
        headers={"User-Agent": "placetype-ph/0.1.1"},
        timeout=120,
        stream=True,
    ) as response:
        response.raise_for_status()
        with destination.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 16):
                handle.write(chunk)
    return destination


@dataclass(frozen=True, slots=True)
class ApiLevel:
    endpoint: str
    level: str
    code_keys: tuple[str, ...]
    title_keys: tuple[str, ...]
    alternate_endpoints: tuple[str, ...] = ()


_PCPC_LEVELS = (
    ApiLevel(
        "sections", "section", ("section", "sectioncode"),
        ("title", "sectiondesc", "description"),
    ),
    ApiLevel(
        "divisions", "division", ("division", "divisioncode"),
        ("title", "divisiondesc", "description"),
    ),
    ApiLevel(
        "groups", "group", ("group", "groupcode"),
        ("title", "groupdesc", "description"),
    ),
    ApiLevel(
        "classes", "class", ("class_code", "classcode", "class"),
        ("title", "classdesc", "description"),
    ),
    ApiLevel(
        "sub-classes", "subclass", ("subclasscode", "sub_class", "subclass"),
        ("title", "subclassdesc", "description"),
    ),
    ApiLevel(
        "item",
        "item",
        ("itemcode", "item_code", "item"),
        ("title", "itemdesc", "description"),
        ("items",),
    ),
)


def _first(row: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = clean_literal(row.get(key))
        if value:
            return value
    return None


def _api_get_all(url: str, token: str) -> list[dict]:
    """Fetch every page from a PSA classification endpoint.

    PSA documents `page`/`page_size`, while its examples return plain JSON lists rather
    than a DRF-style object with a `next` cursor. A list response therefore cannot be
    treated as "one page only": continue until the endpoint returns an empty page. Some
    servers cap `page_size` below the requested value, so a short page is not a safe stop
    signal. Repeated page content is an error rather than a silent truncation signal.
    """
    rows: list[dict] = []
    page = 1
    page_size = 1000
    seen_pages: set[str] = set()
    base = urlparse(url)
    request_url = url
    request_params: dict[str, object] | None = {
        "token": token,
        "page": page,
        "page_size": page_size,
    }

    while page <= _MAX_API_PAGES:
        response = requests.get(
            request_url,
            params=request_params,
            headers={"User-Agent": "placetype-ph/0.1.1"},
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            batch = data
            next_url = None
            has_next_field = False
        elif isinstance(data, dict):
            batch = data.get("results", [])
            next_url = data.get("next")
            has_next_field = "next" in data
        else:
            raise TaxonomyError(
                f"{url} returned unsupported JSON type {type(data).__name__} on page {page}"
            )

        if not isinstance(batch, list):
            raise TaxonomyError(f"{url} returned a non-list result batch on page {page}")
        if not batch:
            break

        # Detect an endpoint that ignores `page`. Hashing a deterministic JSON encoding is
        # cheap at <=1000 rows/page and safer than silently keeping only the first page.
        signature = hashlib.sha256(
            json.dumps(batch, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest()
        if signature in seen_pages:
            raise TaxonomyError(
                f"{url} repeated page content at page {page}; pagination may be ignored"
            )
        seen_pages.add(signature)
        rows.extend(batch)

        # Cursor-bearing responses are authoritative. Follow the supplied URL rather
        # than assuming its cursor can be reproduced by incrementing `page`; validate
        # same-origin first so an upstream response cannot send the API token elsewhere.
        if has_next_field:
            if not next_url:
                break
            next_absolute = urljoin(request_url, str(next_url))
            parsed = urlparse(next_absolute)
            if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
                raise TaxonomyError(f"{url} returned a cross-origin next URL: {next_absolute}")
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            query.setdefault("token", token)
            request_url = urlunparse(parsed._replace(query=urlencode(query)))
            request_params = None
            page += 1
            continue

        # Plain-list/page-number responses do not tell us the final page. Keep asking
        # until the endpoint returns empty or repeats; a short page may only mean the
        # server capped `page_size` below our request.
        page += 1
        request_url = url
        request_params = {"token": token, "page": page, "page_size": page_size}
    else:
        raise TaxonomyError(f"{url} did not terminate within {_MAX_API_PAGES} pages")
    return rows


def fetch_pcpc_api(
    token: str,
    version: str = "2002",
    strict: bool = True,
    strict_levels: bool = True,
    *,
    diagnostic: bool = False,
    malformed_rows_out: list[str] | None = None,
) -> Taxonomy:
    nodes: list[TaxonomyNode] = []
    malformed_rows: list[str] = []
    for spec in _PCPC_LEVELS:
        endpoint_rows: list[dict] | None = None
        url = ""
        endpoints = (spec.endpoint, *spec.alternate_endpoints)
        for index, endpoint in enumerate(endpoints):
            url = f"{PSA_API['pcpc']}/{version}/{endpoint}"
            try:
                endpoint_rows = _api_get_all(url, token)
                break
            except requests.HTTPError as exc:
                status = getattr(exc.response, "status_code", None)
                if status == 404 and index + 1 < len(endpoints):
                    continue
                raise
        if endpoint_rows is None:
            raise TaxonomyError(f"no working PCPC endpoint found for {spec.level}")
        for row_number, row in enumerate(endpoint_rows, start=1):
            raw_code = _first(row, spec.code_keys)
            title = _first(row, spec.title_keys)
            if not raw_code or not title:
                row_id = clean_literal(row.get("id")) or f"row {row_number}"
                missing = "code" if not raw_code else "title"
                malformed_rows.append(f"{spec.level}:{row_id} missing {missing}")
                continue
            try:
                code = _canonical_for_level(
                    "pcpc", raw_code, spec.level, repair_short=True
                )
            except TaxonomyError as exc:
                row_id = clean_literal(row.get("id")) or f"row {row_number}"
                malformed_rows.append(f"{spec.level}:{row_id} {exc}")
                continue
            nodes.append(
                TaxonomyNode(
                    scheme="pcpc",
                    version=version,
                    code=code,
                    level=spec.level,
                    title=title,
                    description=clean_literal(row.get("description")) or "",
                    source_url=url,
                )
            )
    if malformed_rows_out is not None:
        malformed_rows_out.extend(malformed_rows)
    if malformed_rows and not diagnostic:
        sample = ", ".join(malformed_rows[:10])
        more = f"; ... and {len(malformed_rows) - 10} more" if len(malformed_rows) > 10 else ""
        raise TaxonomyError(
            f"PCPC API returned {len(malformed_rows)} row(s) without a usable code/title: "
            f"{sample}{more}. Refusing a silently incomplete official taxonomy."
        )

    # API responses are naturally level-grouped rather than official sequence;
    # PCPC parents are strictly decimal prefixes, so prefix derivation is sufficient.
    merged_nodes = _merge_duplicate_nodes(nodes)
    if not merged_nodes:
        raise TaxonomyError("PCPC API returned no usable rows")
    return _build(merged_nodes, strict, strict_levels)
