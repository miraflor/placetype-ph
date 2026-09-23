"""IO80 ambiguity resolver, policy io80-v4.

For rows whose PSIC -> I-O concordance returned an audited two-code IO80 candidate set, the
resolver selects one candidate when every same-run singleton record with the same normalized
name carries one globally unanimous IO80 code, that code belongs to the audited pair, and at least
IO80_NAME_MIN_SUPPORT distinct establishments support it. Evidence outside the pair can therefore
veto a resolution. Upstream PSIC classification may itself use establishment names, so this is
cross-row consistency evidence, not statistically independent truth.
The candidate set itself (io80_codes) is never changed. The 55/56 model remains disabled.

Changes from io80-v2:
- validation also requires the stored fields to equal what this policy produces (fixed point),
  so a row that should have been resolved but was not is now detected;
- validation reports every failing rule with its row count and first index labels;
- reference rows without a canonical_id no longer count as support by default, so support is
  always a count of known establishments (IO80_MISSING_ENTITY_ID_POLICY);
- a non-unique index is rejected before any assignment;
- public functions no longer accept a min_support override, so output and provenance cannot
  describe different policies;
- provenance carries a hash of this file and the Unicode database version;
- abstention reasons are available per row and counted in the run metadata;
- contradictions are checked against all singleton IO80 codes, not only the target pair;
- metadata explicitly records same-run composition dependence and non-independence of name evidence.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

IO80_RESOLUTION_COLUMNS = (
    "io80_resolution_method",
    "io80_resolution_support",
    "io80_resolution_score",
)
IO80_MAP_FIELDS = ("io80_map_code", "io80_map_status", *IO80_RESOLUTION_COLUMNS)
IO80_NAME_MIN_SUPPORT = 2
IO80_NAME_METHOD = "global_unanimous_name_v4"
IO80_MODEL_METHOD = "char_ngram_logistic_frozen_v1"
IO80_NAME_PAIRS = (("55", "56"), ("66", "67"))
IO80_MODEL_PAIR = ("55", "56")
IO80_MODEL_THRESHOLD = 0.95
IO80_MODEL_ENABLED = False
IO80_RESOLVED_STATUSES = frozenset({"NAME_MATCH_RESOLVED", "MODEL_RESOLVED"})

# Treatment of evidence rows whose canonical_id is missing or blank:
#   "exclude"    - the row contributes no support (default: support counts known establishments)
#   "count_rows" - each such row counts as a separate establishment (io80-v2 behaviour)
#   "fail"       - resolution stops with an error
# Editing this constant changes the policy fingerprint.
IO80_MISSING_ENTITY_ID_POLICY = "exclude"
IO80_MISSING_ENTITY_ID_POLICIES = ("exclude", "count_rows", "fail")

# Why an AMBIGUOUS_SET row stays ambiguous under this policy.
IO80_ABSTENTION_REASONS = (
    "pair_not_audited",  # the candidate set is not exactly one of IO80_NAME_PAIRS
    "blank_name",  # the normalized name is empty
    "contradictory_name",  # singleton evidence for this name supports more than one IO80 code
    "support_below_min",  # unanimous evidence, but fewer than IO80_NAME_MIN_SUPPORT establishments
    "no_reference",  # no usable singleton evidence for this name within the pair
)

_POLICY = {
    "version": "io80-v4",
    "normalization": "unicode_nfkc_casefold_alnum_v1",
    "unicode_data_version": unicodedata.unidata_version,
    "name_method": IO80_NAME_METHOD,
    "name_min_support": IO80_NAME_MIN_SUPPORT,
    "name_pairs": [list(pair) for pair in IO80_NAME_PAIRS],
    "support_unit": "distinct_canonical_id",
    "missing_entity_id_policy": IO80_MISSING_ENTITY_ID_POLICY,
    "contradictory_name_policy": "abstain",
    "contradiction_scope": "all_singleton_io80_codes",
    "evidence_semantics": "cross_row_same_name_consistency_not_independent_truth",
    "reference_stability": "same_run_composition_dependent",
    "model": {
        "enabled": IO80_MODEL_ENABLED,
        "pair": list(IO80_MODEL_PAIR),
        "method": IO80_MODEL_METHOD,
        "threshold": IO80_MODEL_THRESHOLD,
        "artifact_policy": "frozen_audited_artifact_required",
        "score_semantics": "model_score_not_concordance_confidence",
    },
}
IO80_RESOLVER_FINGERPRINT = hashlib.sha256(
    json.dumps(_POLICY, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


def _implementation_sha256() -> str:
    """Hash of this file, so that a code change also changes cache keys and provenance."""
    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:  # e.g. loaded from an archive without a readable source file
        return "unavailable"


IO80_IMPLEMENTATION_SHA256 = _implementation_sha256()


@dataclass(frozen=True, slots=True)
class NameEvidence:
    """Same-run singleton name evidence for one audited pair, with global contradiction checks."""

    references: dict[str, tuple[str, int]]
    contradictory: frozenset[str]
    below_min_support: frozenset[str]
    unique_names: int
    supporting_entities: int
    clean_supporting_entities: int
    rows_without_entity_id: int
    digest: str


@dataclass(frozen=True, slots=True)
class _Resolution:
    fields: pd.DataFrame
    parsed: pd.Series
    names: pd.Series
    evidence: dict[tuple[str, str], NameEvidence]
    reasons: pd.Series


def normalize_io80_name(value: object) -> str:
    """Conservative Unicode-preserving normalization for establishment-name evidence."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = "".join(character if character.isalnum() else " " for character in text)
    return " ".join(text.split())


def parse_candidate_codes(value: object) -> tuple[str, ...]:
    """Parse a semicolon-delimited candidate set without changing the stored field."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ()
    return tuple(part.strip() for part in str(value).split(";") if part.strip())


def _flags(values: pd.Series) -> pd.Series:
    """Plain boolean flags (nullable booleans become False where missing)."""
    return values.fillna(False).astype(bool)


def _text(values: pd.Series) -> pd.Series:
    return values.astype("string").fillna("").str.strip()


def _check_preconditions(summary: pd.DataFrame) -> None:
    if not summary.index.is_unique:
        raise ValueError("io80 resolver requires a unique index; reset the index before resolving")


def _missing_id_policy() -> str:
    policy = IO80_MISSING_ENTITY_ID_POLICY
    if policy not in IO80_MISSING_ENTITY_ID_POLICIES:
        raise ValueError(f"unknown IO80_MISSING_ENTITY_ID_POLICY {policy!r}")
    return policy


def _normalized_names(summary: pd.DataFrame) -> pd.Series:
    if "canonical_name" not in summary.columns:
        return pd.Series("", index=summary.index, dtype="string")
    return summary["canonical_name"].map(normalize_io80_name).astype("string")


def _entity_keys(summary: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Return (key, has_id): canonical_id where present, else a per-row fallback key.

    Real ids are stripped and non-empty, so a fallback key that starts with a space can never
    equal a real id. (Keys must not contain NUL: pandas 2.x string hashing stops at NUL.)
    """
    if "canonical_id" in summary.columns:
        ids = _text(summary["canonical_id"])
    else:
        ids = pd.Series("", index=summary.index, dtype="string")
    has_id = _flags(ids.ne(""))
    fallback = pd.Series(
        [f" row:{position}" for position in range(len(summary))],
        index=summary.index,
        dtype="string",
    )
    return ids.where(has_id, fallback), has_id


def _evidence_rows(
    summary: pd.DataFrame,
    names: pd.Series,
    parsed: pd.Series,
    codes: tuple[str, ...] | None,
) -> tuple[pd.DataFrame, int]:
    """Singleton rows usable as evidence: one row per (name, label, entity).

    Also returns the number of otherwise usable rows that have no canonical_id.
    """
    labels = parsed.map(lambda value: value[0] if len(value) == 1 else "")
    usable = _flags(labels.ne("") & names.ne(""))
    if codes is not None:
        usable &= labels.isin(codes)
    keys, has_id = _entity_keys(summary)
    without_id = usable & ~has_id
    rows_without_id = int(without_id.sum())
    policy = _missing_id_policy()
    if rows_without_id and policy == "fail":
        raise ValueError(
            f"{rows_without_id} io80 evidence row(s) have no canonical_id "
            "(IO80_MISSING_ENTITY_ID_POLICY is 'fail')"
        )
    if policy == "exclude":
        usable &= has_id
    frame = pd.DataFrame(
        {
            "name": names[usable],
            "label": labels[usable].astype("string"),
            "entity": keys[usable],
        }
    )
    return frame.drop_duplicates(["name", "label", "entity"]).reset_index(
        drop=True
    ), rows_without_id


def _build_evidence(
    summary: pd.DataFrame,
    names: pd.Series,
    parsed: pd.Series,
    pair: tuple[str, str],
    min_support: int,
) -> NameEvidence:
    """Build pair references, vetoing a name if any singleton IO80 evidence disagrees globally."""
    pair_refs, rows_without_id = _evidence_rows(summary, names, parsed, pair)
    if pair_refs.empty:
        empty = hashlib.sha256(b"[]").hexdigest()
        return NameEvidence({}, frozenset(), frozenset(), 0, 0, 0, rows_without_id, empty)

    all_refs, _ = _evidence_rows(summary, names, parsed, None)
    pair_names = frozenset(pair_refs["name"].astype(str))
    relevant_all = all_refs[all_refs["name"].astype(str).isin(pair_names)].copy()

    pair_counts = (
        pair_refs.groupby(["name", "label"])["entity"]
        .nunique()
        .rename("support")
        .reset_index()
    )
    pair_support = pair_counts.groupby("name")["support"].sum()
    global_labels = relevant_all.groupby("name")["label"].nunique()

    contradictory = frozenset(global_labels[global_labels.gt(1)].index.astype(str))
    globally_unanimous = global_labels[global_labels.eq(1)].index
    eligible_support = pair_support.reindex(globally_unanimous).dropna().astype(int)
    below = frozenset(eligible_support[eligible_support.lt(min_support)].index.astype(str))
    clean_support = eligible_support[eligible_support.ge(min_support)]

    global_label = (
        relevant_all.sort_values(["name", "label"])
        .drop_duplicates("name")
        .set_index("name")["label"]
        .astype(str)
    )
    references = {
        str(name): (str(global_label.loc[name]), int(support))
        for name, support in clean_support.items()
        if str(global_label.loc[name]) in pair
    }

    digest_payload = [
        (str(row["name"]), str(row["label"]), str(row["entity"]))
        for row in relevant_all.sort_values(["name", "label", "entity"]).to_dict("records")
    ]
    digest = hashlib.sha256(
        json.dumps(digest_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    clean_entities = pair_refs[pair_refs["name"].astype(str).isin(references)].drop_duplicates(
        ["name", "entity"]
    )
    return NameEvidence(
        references=references,
        contradictory=contradictory,
        below_min_support=below,
        unique_names=len(pair_names),
        supporting_entities=int(pair_refs["entity"].nunique()),
        clean_supporting_entities=int(clean_entities.shape[0]),
        rows_without_entity_id=rows_without_id,
        digest=digest,
    )


def build_name_evidence(
    summary: pd.DataFrame,
    normalized_names: pd.Series,
    pair: tuple[str, str],
    *,
    min_support: int = IO80_NAME_MIN_SUPPORT,
) -> NameEvidence:
    """Clean and contradictory singleton-name evidence for one pair.

    min_support exists for analysis only; resolution, validation and metadata always use
    IO80_NAME_MIN_SUPPORT.
    """
    parsed = summary["io80_codes"].map(parse_candidate_codes)
    return _build_evidence(summary, normalized_names, parsed, pair, min_support)


def name_evidence_table(
    summary: pd.DataFrame, codes: tuple[str, ...] | None = None
) -> pd.DataFrame:
    """Singleton name evidence under the current policy: one row per (name, label, entity).

    codes limits the labels; None keeps every singleton label (used for out-of-pair checks).
    """
    _check_preconditions(summary)
    parsed = summary["io80_codes"].map(parse_candidate_codes)
    frame, _ = _evidence_rows(summary, _normalized_names(summary), parsed, codes)
    return frame


def _resolve(summary: pd.DataFrame) -> _Resolution:
    """Compute map fields, evidence and abstention reasons from io80_codes alone."""
    parsed = summary["io80_codes"].map(parse_candidate_codes)
    sizes = parsed.map(len).astype("int64")
    candidate_sets = pd.Series(
        [frozenset(value) for value in parsed], index=summary.index, dtype=object
    )
    names = _normalized_names(summary)
    named = _flags(names.ne(""))

    fields = pd.DataFrame({column: "" for column in IO80_MAP_FIELDS}, index=summary.index)
    singleton = sizes.eq(1)
    multiple = sizes.gt(1)
    fields["io80_map_status"] = "UNMAPPED"
    fields.loc[singleton, "io80_map_code"] = parsed[singleton].map(lambda codes: codes[0])
    fields.loc[singleton, "io80_map_status"] = "SINGLETON_CANDIDATE"
    fields.loc[multiple, "io80_map_status"] = "AMBIGUOUS_SET"

    reasons = pd.Series("", index=summary.index, dtype=object)
    reasons[multiple] = "pair_not_audited"
    evidence: dict[tuple[str, str], NameEvidence] = {}
    for pair in IO80_NAME_PAIRS:
        pair_evidence = _build_evidence(summary, names, parsed, pair, IO80_NAME_MIN_SUPPORT)
        evidence[pair] = pair_evidence
        in_pair = multiple & sizes.eq(2) & candidate_sets.eq(frozenset(pair))
        if not in_pair.any():
            continue
        reasons[in_pair & ~named] = "blank_name"
        target = in_pair & named
        reasons[target] = "no_reference"
        reasons[target & names.isin(pair_evidence.below_min_support)] = "support_below_min"
        reasons[target & names.isin(pair_evidence.contradictory)] = "contradictory_name"

        label_map = {name: value[0] for name, value in pair_evidence.references.items()}
        support_map = {name: str(value[1]) for name, value in pair_evidence.references.items()}
        matched_labels = names[target].map(label_map)
        matched = _flags(matched_labels.notna() & ~names[target].isin(pair_evidence.contradictory))
        resolved_index = matched_labels.index[matched.to_numpy()]
        if len(resolved_index) == 0:
            continue
        fields.loc[resolved_index, "io80_map_code"] = matched_labels.loc[resolved_index].astype(str)
        fields.loc[resolved_index, "io80_map_status"] = "NAME_MATCH_RESOLVED"
        fields.loc[resolved_index, "io80_resolution_method"] = IO80_NAME_METHOD
        fields.loc[resolved_index, "io80_resolution_support"] = names.loc[resolved_index].map(
            support_map
        )
        reasons.loc[resolved_index] = ""
    return _Resolution(fields, parsed, names, evidence, reasons)


def resolve_io80_ambiguity(summary: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of summary whose IO80 map fields are recomputed from io80_codes.

    Previous map and provenance values are never carried over, so reruns are safe.
    """
    _check_preconditions(summary)
    if "io80_codes" not in summary.columns:
        return summary.copy()
    resolution = _resolve(summary)
    out = summary.copy()
    for column in IO80_MAP_FIELDS:
        out[column] = resolution.fields[column]
    # Regression guard: the resolver must never rewrite candidate provenance.
    if not out["io80_codes"].equals(summary["io80_codes"]):
        raise RuntimeError("io80 resolver changed io80_codes")
    return out


def io80_abstention_reasons(summary: pd.DataFrame) -> pd.Series:
    """Why each row that stays AMBIGUOUS_SET under this policy is not resolved ('' otherwise)."""
    _check_preconditions(summary)
    return _resolve(summary).reasons.rename("io80_abstention_reason")


def validate_io80_map_fields(summary: pd.DataFrame, candidate_counts: pd.Series) -> None:
    """Validate IO80 map fields against the candidate sets and the io80-v4 policy.

    Checks, each reported separately on failure:
    - candidate_count_mismatch: io80_codes cardinality equals candidate_counts. This is a
      consistency check; when candidate_counts is itself derived from io80_codes it cannot
      detect a rewritten candidate set.
    - legal statuses and provenance for UNMAPPED, SINGLETON_CANDIDATE, AMBIGUOUS_SET,
      NAME_MATCH_RESOLVED (audited pairs only) and MODEL_RESOLVED (not legal while disabled);
    - name_evidence_mismatch: recorded label and support equal the same-run evidence;
    - differs_from_policy: every map and provenance field equals what this policy produces
      (fixed point). This also detects a row that should have been resolved but was not.
    The evidence is rebuilt with the same functions as the resolver, so these checks detect
    stale or edited fields, not defects in those functions.
    """
    _check_preconditions(summary)
    required = {"io80_codes", "io80_map_code", "io80_map_status", *IO80_RESOLUTION_COLUMNS}
    missing = required - set(summary.columns)
    if missing:
        raise ValueError(f"io80 map validation is missing columns {sorted(missing)}")

    resolution = _resolve(summary)
    parsed = resolution.parsed
    actual_counts = parsed.map(len).astype("int64")
    supplied_counts = candidate_counts.reindex(summary.index).astype("int64")
    stored = {column: _text(summary[column]) for column in IO80_MAP_FIELDS}
    map_codes = stored["io80_map_code"]
    status = stored["io80_map_status"]
    method = stored["io80_resolution_method"]
    support = stored["io80_resolution_support"]
    score = stored["io80_resolution_score"]
    candidate_sets = pd.Series(
        [frozenset(value) for value in parsed], index=summary.index, dtype=object
    )
    chosen_is_candidate = pd.Series(
        [
            bool(chosen) and chosen in candidates
            for chosen, candidates in zip(map_codes, candidate_sets, strict=True)
        ],
        index=summary.index,
    )

    unmapped = actual_counts.eq(0)
    singleton = actual_counts.eq(1)
    multiple = actual_counts.gt(1)
    ambiguous = _flags(status.eq("AMBIGUOUS_SET"))
    name_resolved = _flags(status.eq("NAME_MATCH_RESOLVED"))
    model_resolved = _flags(status.eq("MODEL_RESOLVED"))
    expected_singletons = parsed.map(lambda codes: codes[0] if len(codes) == 1 else "")
    allowed_name_sets = {frozenset(pair) for pair in IO80_NAME_PAIRS}
    name_pair_allowed = candidate_sets.map(lambda value: value in allowed_name_sets).astype(bool)
    support_is_integer = _flags(support.str.fullmatch(r"[1-9][0-9]*"))
    support_at_least_min = _flags(pd.to_numeric(support, errors="coerce").ge(IO80_NAME_MIN_SUPPORT))
    plain = unmapped | singleton | ambiguous

    name_evidence_mismatch = pd.Series(False, index=summary.index)
    for pair, pair_evidence in resolution.evidence.items():
        mask = name_resolved & candidate_sets.eq(frozenset(pair))
        if not mask.any():
            continue
        names = resolution.names[mask]
        expected_label = names.map({n: value[0] for n, value in pair_evidence.references.items()})
        expected_support = names.map(
            {n: str(value[1]) for n, value in pair_evidence.references.items()}
        )
        mismatch = (
            expected_label.isna()
            | _flags(map_codes[mask].ne(expected_label))
            | _flags(support[mask].ne(expected_support))
            | names.isin(pair_evidence.contradictory)
        )
        name_evidence_mismatch.loc[mask] = _flags(mismatch).to_numpy()

    differs_from_policy = pd.Series(False, index=summary.index)
    for column in IO80_MAP_FIELDS:
        differs_from_policy |= _flags(stored[column].ne(_text(resolution.fields[column])))

    rules = {
        "candidate_count_mismatch": actual_counts.ne(supplied_counts),
        "duplicate_candidate_codes": parsed.map(lambda codes: len(codes) != len(set(codes))),
        "unmapped_state": unmapped & _flags(map_codes.ne("") | status.ne("UNMAPPED")),
        "singleton_state": singleton
        & _flags(map_codes.ne(expected_singletons) | status.ne("SINGLETON_CANDIDATE")),
        "multiple_candidate_status": multiple & ~(ambiguous | name_resolved | model_resolved),
        "ambiguous_with_map_code": ambiguous & _flags(map_codes.ne("")),
        "resolved_code_not_candidate": (name_resolved | model_resolved)
        & (~multiple | ~chosen_is_candidate),
        "plain_row_with_provenance": plain & _flags(method.ne("") | support.ne("") | score.ne("")),
        "name_resolution_outside_policy": name_resolved
        & (
            ~name_pair_allowed
            | _flags(method.ne(IO80_NAME_METHOD))
            | ~support_is_integer
            | ~support_at_least_min
            | _flags(score.ne(""))
        ),
        # No frozen model artifact exists under io80-v3, so any MODEL_RESOLVED row is stale.
        "model_resolution_disabled": model_resolved,
        "name_evidence_mismatch": name_evidence_mismatch,
        "differs_from_policy": differs_from_policy,
    }
    failing = {name: _flags(mask) for name, mask in rules.items() if _flags(mask).any()}
    if failing:
        any_failure = pd.concat(failing.values(), axis=1).any(axis=1)
        details = "; ".join(
            f"{name}={int(mask.sum())} (first rows: {mask[mask].index[:3].tolist()})"
            for name, mask in failing.items()
        )
        raise ValueError(
            "io80 map fields disagree with candidate sets or resolver policy on "
            f"{int(any_failure.sum())} row(s): {details}"
        )


def io80_resolver_metadata(summary: pd.DataFrame) -> dict[str, object]:
    """Policy identity and same-run evidence fingerprints for run and GIS provenance."""
    metadata: dict[str, object] = {
        "version": _POLICY["version"],
        "policy_fingerprint": IO80_RESOLVER_FINGERPRINT,
        "implementation_sha256": IO80_IMPLEMENTATION_SHA256,
        "unicode_data_version": _POLICY["unicode_data_version"],
        "reference_scope": "same_run_singleton_rows",
        "reference_stability": _POLICY["reference_stability"],
        "evidence_semantics": _POLICY["evidence_semantics"],
        "upstream_name_dependence": (
            "PSIC classification may itself use establishment names; resolver evidence is "
            "cross-row consistency, not statistically independent truth"
        ),
        "name_method": IO80_NAME_METHOD,
        "name_min_support": IO80_NAME_MIN_SUPPORT,
        "name_pairs": ["; ".join(pair) for pair in IO80_NAME_PAIRS],
        "support_unit": _POLICY["support_unit"],
        "missing_entity_id_policy": _missing_id_policy(),
        "contradiction_scope": _POLICY["contradiction_scope"],
        "model": dict(_POLICY["model"]),
        "references": {},
        "abstention_reason_counts": {},
    }
    if "io80_codes" not in summary.columns:
        return metadata
    _check_preconditions(summary)
    resolution = _resolve(summary)
    metadata["references"] = {
        "; ".join(pair): {
            "unique_names": evidence.unique_names,
            "clean_reference_names": len(evidence.references),
            "contradictory_names": len(evidence.contradictory),
            "below_min_support_names": len(evidence.below_min_support),
            "supporting_entities": evidence.supporting_entities,
            "clean_supporting_entities": evidence.clean_supporting_entities,
            "evidence_rows_without_canonical_id": evidence.rows_without_entity_id,
            "reference_digest": evidence.digest,
        }
        for pair, evidence in resolution.evidence.items()
    }
    metadata["abstention_reason_counts"] = {
        reason: int(resolution.reasons.eq(reason).sum()) for reason in IO80_ABSTENTION_REASONS
    }
    return metadata
