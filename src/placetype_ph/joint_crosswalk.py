from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping

import pandas as pd

from .retrieval import TaxonomyRetriever
from .taxonomy import Taxonomy

JOINT_KEY_COLUMN = "joint_key"
PEER_CONTEXT_MARKER = "related classification context:"
FIRST_PASS_CODE_COLUMNS = ("codes", "suggested_codes")

# Separators. The project uses "|" inside code lists (``codes``, ``candidate_codes``), so
# neither of these may contain it: a reviewer splitting a field must never get an ambiguous
# result. " ; " separates classification systems, " + " joins the members of one union.
PEER_SEPARATOR = " ; "
UNION_SEPARATOR = " + "

# Columns the recheck reads from the worklist. ``crosswalk-suggest`` carries these through
# for reviewed rows instead of blanking them, so a reviewed row is searched from the same
# inputs as an unreviewed one.
RECHECK_INPUT_COLUMNS = ("query_text", "branch_codes")

JOINT_RECHECK_COLUMNS = (
    "selected_codes",
    "selected_code_column",
    "selected_path",
    "peer_context",
    "peer_query_context",
    "control_query",
    "control_top_code",
    "control_top_path",
    "recheck_query",
    "recheck_candidate_codes",
    "recheck_candidate_titles",
    "recheck_candidate_scores",
    "recheck_top_code",
    "recheck_top_path",
    "recheck_margin",
    "recheck_status",
    "first_pass_status",
    "joint_status",
)

_EMPTY_RESULT: dict[str, str] = {name: "" for name in JOINT_RECHECK_COLUMNS}


def make_joint_key(source: str, source_value: str, source_field: str = "category") -> str:
    """Stable identifier joining one OpenPlaces value across classification schemes.

    The key deliberately excludes the scheme and the version, so the PSIC, PCPC and PSCC
    rows for one observed category share it. It is a digest of the normalised source,
    source field and source value only.
    """
    raw = "\x1f".join(
        (
            str(source).strip().casefold(),
            str(source_field or "category").strip().casefold(),
            str(source_value).strip(),
        )
    )
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def ensure_joint_key(frame: pd.DataFrame, *, overwrite: bool = False) -> pd.DataFrame:
    """Add ``joint_key`` to ``frame`` in place when it is absent, then return ``frame``.

    One definition is used by every caller so the three worklist stages cannot drift apart.
    """
    if JOINT_KEY_COLUMN in frame.columns and not overwrite:
        return frame
    frame[JOINT_KEY_COLUMN] = [
        make_joint_key(
            str(record.get("source", "")),
            str(record.get("source_value", "")),
            str(record.get("source_field", "category") or "category"),
        )
        for record in frame.to_dict("records")
    ]
    return frame


def split_codes(value: object) -> list[str]:
    """Split a pipe-separated code field into a list, dropping empty parts."""
    return [part.strip() for part in str(value or "").split("|") if part.strip()]


def first_pass_codes(row: Mapping[str, object]) -> tuple[list[str], str]:
    """Return every code already chosen for this row and the column it came from.

    Only reviewed codes and accepted first-pass suggestions count as selections. Raw retrieval
    candidates are deliberately excluded: a below-threshold candidate must not become evidence
    for another taxonomy. The whole accepted list is returned, not only its first element.
    """
    for column in FIRST_PASS_CODE_COLUMNS:
        codes = split_codes(row.get(column, ""))
        if codes:
            return codes, column
    return [], ""


def hierarchy_path(taxonomy: Taxonomy | None, code: str | None) -> str:
    """Readable root-to-node path for one code, or an empty string when unknown."""
    if taxonomy is None or not code or code not in taxonomy.nodes:
        return ""
    parts: list[str] = []
    for member in taxonomy.path_from_root(code):
        node = taxonomy.get(member)
        parts.append(f"{node.level}:{node.code} {node.title}")
    return " > ".join(parts)


def hierarchy_paths(taxonomy: Taxonomy | None, codes: list[str]) -> str:
    """All valid root-to-node paths for a possibly multi-code mapping."""
    paths = [hierarchy_path(taxonomy, code) for code in codes]
    return UNION_SEPARATOR.join(path for path in paths if path)


def peer_labels(
    row: Mapping[str, object], taxonomy: Taxonomy, scope: tuple[str, str]
) -> tuple[str, str]:
    """Audit text and retrieval text for one peer row, from accepted mappings only.

    The two differ on purpose. The audit text names the version and the codes so a reviewer
    can see exactly which mapping supplied the context. The retrieval text carries titles
    only: a bare code such as ``2106`` is a numeral to a retriever, not a meaning, and adding
    it perturbs the treatment query without adding evidence.
    """
    codes, _ = first_pass_codes(row)
    entries: list[str] = []
    titles: list[str] = []
    for code in codes:
        if code in taxonomy.nodes:
            node = taxonomy.get(code)
            entries.append(f"{node.code} {node.title}")
            titles.append(node.title)
    if not entries:
        return "", ""
    scheme, version = scope
    audit = f"[{scheme} {version}] " + UNION_SEPARATOR.join(entries)
    query = f"[{scheme}] " + UNION_SEPARATOR.join(titles)
    return audit, query


def default_scope(row: Mapping[str, object]) -> tuple[str, str]:
    """Scheme and version of one worklist row, normalised for dictionary lookup."""
    return (
        str(row.get("scheme", "")).strip().casefold(),
        str(row.get("version", "")).strip(),
    )


def _choose_peer_rows(
    position: int,
    scope: tuple[str, str],
    positions: list[int],
    scopes: list[tuple[str, str]],
    records: list[dict],
    taxonomies: Mapping[tuple[str, str], Taxonomy],
    preferred_versions: Mapping[str, str] | None = None,
) -> list[tuple[tuple[str, str], dict]]:
    """At most one peer row per other classification system, in scheme-name order.

    Two rows for two versions of one system are alternatives, not independent evidence.
    Reviewed codes outrank machine suggestions. Within the same evidence class, a configured
    preferred version outranks the others; remaining ties keep worklist order. No lexical
    comparison of version strings is used.
    """
    preferred = preferred_versions or {}
    by_scheme: dict[str, list[tuple[int, int, int, tuple[str, str], dict]]] = {}
    order = 0
    for other_position, other_scope, other_record in zip(
        positions, scopes, records, strict=True
    ):
        order += 1
        if other_position == position or other_scope[0] == scope[0]:
            continue
        if taxonomies.get(other_scope) is None:
            continue
        codes, column = first_pass_codes(other_record)
        if not codes:
            continue
        evidence_rank = 0 if column == "codes" else 1
        version_rank = 0 if preferred.get(other_scope[0]) == other_scope[1] else 1
        by_scheme.setdefault(other_scope[0], []).append(
            (evidence_rank, version_rank, order, other_scope, other_record)
        )

    chosen: list[tuple[tuple[str, str], dict]] = []
    for scheme_name in sorted(by_scheme):
        candidates = sorted(by_scheme[scheme_name], key=lambda item: item[:3])
        chosen.append((candidates[0][3], candidates[0][4]))
    return chosen


def recheck_joint_worklist(
    frame: pd.DataFrame,
    taxonomies: Mapping[tuple[str, str], Taxonomy],
    retrievers: Mapping[tuple[str, str], TaxonomyRetriever],
    *,
    top_n: int = 5,
    min_margin: float = 0.12,
    scope_of: Callable[[Mapping[str, object]], tuple[str, str]] | None = None,
    preferred_versions: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Controlled second-pass cross-taxonomy recheck for a joint crosswalk worklist.

    For every row this runs the same retrieval call twice. The control call uses the row's
    own query. The treatment call uses that query with the titles selected by the peer
    systems appended. Only the appended text differs, so a change of top candidate is
    attributable to peer context rather than to a different query, a different branch
    restriction or a different score threshold. The first-pass code is reported separately,
    in ``first_pass_status``, as a diagnostic on whether the two passes are comparable at all.

    The base query is built the same way for every row of one group: ``query_text`` is used
    only when every taxonomy-backed row in the group has one, otherwise every row falls back
    to ``source_value``. ``crosswalk-suggest`` prepares the same query and branch inputs for
    reviewed and unreviewed rows before this function runs.

    The pass never writes ``mapping_kind``, ``codes`` or ``suggested_codes``. It is a review
    signal, not a concordance: PSIC, PCPC and PSCC classify different objects, so peer
    context can mark a suspicious choice but must not force one taxonomy to agree with another.

    ``recheck_status`` takes one of: ``MISSING_TAXONOMY``, ``NO_PEERS``, ``NO_CONTROL_HIT``,
    ``NO_RECHECK_HIT``, ``STABLE``, ``SHIFT_WEAK``, ``SHIFT``. ``SHIFT`` is written only when
    the control top candidate leaves the treatment candidate list, or when the treatment top
    candidate leads it by at least ``min_margin``.

    ``joint_status`` takes one of: ``RECHECK`` when any row in the group shifted,
    ``SINGLE_SCHEME`` when fewer than two distinct classification systems had a taxonomy,
    ``NO_EVIDENCE`` when no taxonomy-backed row in the group has an accepted or reviewed code
    yet, ``STABLE`` when every taxonomy-backed row was compared and none shifted, and
    ``PARTIAL`` otherwise.
    """
    scope_for = scope_of or default_scope
    original_index = frame.index
    out = frame.reset_index(drop=True)
    ensure_joint_key(out)

    results: list[dict[str, str]] = [dict(_EMPTY_RESULT) for _ in range(len(out))]

    for _joint_key, group in out.groupby(JOINT_KEY_COLUMN, sort=False, dropna=False):
        positions = list(group.index)
        records = group.to_dict("records")
        scopes = [scope_for(record) for record in records]
        selections = [first_pass_codes(record) for record in records]

        backed = [
            (record, codes)
            for record, scope, (codes, _) in zip(records, scopes, selections, strict=True)
            if taxonomies.get(scope) is not None
        ]
        group_has_evidence = any(codes for _, codes in backed)
        use_query_text = bool(backed) and all(
            str(record.get("query_text", "") or "").strip() for record, _ in backed
        )

        peers_by_position: dict[int, tuple[list[str], list[str]]] = {}
        for position, scope in zip(positions, scopes, strict=True):
            audit: list[str] = []
            query: list[str] = []
            for peer_scope, peer_record in _choose_peer_rows(
                position,
                scope,
                positions,
                scopes,
                records,
                taxonomies,
                preferred_versions,
            ):
                audit_label, query_label = peer_labels(
                    peer_record, taxonomies[peer_scope], peer_scope
                )
                if audit_label:
                    audit.append(audit_label)
                    query.append(query_label)
            peers_by_position[position] = (audit, query)

        rows_with_taxonomy = 0
        schemes_with_taxonomy: set[str] = set()
        compared = 0
        shifted = 0

        for position, scope, selection, record in zip(
            positions, scopes, selections, records, strict=True
        ):
            result = results[position]
            codes, code_column = selection
            taxonomy = taxonomies.get(scope)
            retriever = retrievers.get(scope)

            result["selected_codes"] = "|".join(codes)
            result["selected_code_column"] = code_column
            result["selected_path"] = hierarchy_paths(taxonomy, codes)
            audit_peers, query_peers = peers_by_position[position]
            result["peer_context"] = PEER_SEPARATOR.join(audit_peers)
            result["peer_query_context"] = PEER_SEPARATOR.join(query_peers)

            if taxonomy is None or retriever is None:
                result["recheck_status"] = "MISSING_TAXONOMY"
                continue
            rows_with_taxonomy += 1
            schemes_with_taxonomy.add(scope[0])
            if not audit_peers:
                result["recheck_status"] = "NO_PEERS"
                continue

            base_query = str(
                record.get("query_text", "") if use_query_text else record.get("source_value", "")
            ).strip()
            branches = tuple(
                code
                for code in split_codes(record.get("branch_codes", ""))
                if code in taxonomy.nodes
            )

            control_hits = list(
                retriever.search_hierarchical(base_query, top_n=top_n, branch_roots=branches)
            )
            result["control_query"] = base_query
            if not control_hits:
                result["recheck_status"] = "NO_CONTROL_HIT"
                continue
            control_top = control_hits[0].code
            control_codes = [hit.code for hit in control_hits]
            result["control_top_code"] = control_top
            result["control_top_path"] = hierarchy_path(taxonomy, control_top)

            if not codes:
                result["first_pass_status"] = "NO_FIRST_PASS"
            elif control_top in codes:
                result["first_pass_status"] = "AGREES"
            elif any(code in control_codes for code in codes):
                result["first_pass_status"] = "IN_CONTROL_TOPN"
            else:
                result["first_pass_status"] = "OUTSIDE_CONTROL_TOPN"

            recheck_query = (
                f"{base_query}{PEER_SEPARATOR}{PEER_CONTEXT_MARKER} "
                f"{result['peer_query_context']}"
            )
            hits = list(
                retriever.search_hierarchical(recheck_query, top_n=top_n, branch_roots=branches)
            )
            result["recheck_query"] = recheck_query
            result["recheck_candidate_codes"] = "|".join(hit.code for hit in hits)
            result["recheck_candidate_titles"] = "|".join(
                taxonomy.get(hit.code).title if hit.code in taxonomy.nodes else "" for hit in hits
            )
            result["recheck_candidate_scores"] = "|".join(f"{float(hit.score):.6f}" for hit in hits)
            if not hits:
                result["recheck_status"] = "NO_RECHECK_HIT"
                continue

            top = hits[0]
            result["recheck_top_code"] = top.code
            result["recheck_top_path"] = hierarchy_path(taxonomy, top.code)
            scores = {hit.code: float(hit.score) for hit in hits}
            compared += 1

            if top.code == control_top:
                result["recheck_margin"] = f"{0.0:.6f}"
                result["recheck_status"] = "STABLE"
            elif control_top not in scores:
                result["recheck_margin"] = ""
                result["recheck_status"] = "SHIFT"
                shifted += 1
            else:
                margin = float(top.score) - scores[control_top]
                result["recheck_margin"] = f"{margin:.6f}"
                if margin >= min_margin:
                    result["recheck_status"] = "SHIFT"
                    shifted += 1
                else:
                    result["recheck_status"] = "SHIFT_WEAK"

        if shifted:
            group_status = "RECHECK"
        elif len(schemes_with_taxonomy) < 2:
            group_status = "SINGLE_SCHEME"
        elif not group_has_evidence:
            group_status = "NO_EVIDENCE"
        elif compared == rows_with_taxonomy:
            group_status = "STABLE"
        else:
            group_status = "PARTIAL"
        for position in positions:
            results[position]["joint_status"] = group_status

    addition = pd.DataFrame(results, columns=list(JOINT_RECHECK_COLUMNS), index=out.index)
    out = out.drop(columns=[name for name in JOINT_RECHECK_COLUMNS if name in out.columns])
    out = pd.concat([out, addition], axis=1)
    out.index = original_index
    return out
