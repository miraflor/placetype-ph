from __future__ import annotations

import pandas as pd
import pytest

import placetype_ph.io80_resolver as resolver
from placetype_ph.io80_resolver import (
    IO80_MAP_FIELDS,
    IO80_NAME_METHOD,
    io80_abstention_reasons,
    io80_resolver_metadata,
    normalize_io80_name,
    parse_candidate_codes,
    resolve_io80_ambiguity,
    validate_io80_map_fields,
)


def _frame(rows):
    return pd.DataFrame(rows)


def _counts(frame):
    return frame["io80_codes"].map(lambda value: len(parse_candidate_codes(value))).astype("int64")


def test_empty_arrow_string_input_is_well_formed():
    frame = pd.DataFrame(
        {
            "canonical_id": pd.Series([], dtype="string[pyarrow]"),
            "canonical_name": pd.Series([], dtype="string[pyarrow]"),
            "io80_codes": pd.Series([], dtype="string[pyarrow]"),
        }
    )
    out = resolve_io80_ambiguity(frame)
    assert out.empty
    assert set(IO80_MAP_FIELDS) <= set(out.columns)


def test_55_56_exact_name_resolves_and_preserves_candidates():
    frame = _frame([
        {"canonical_id": "a1", "canonical_name": "Alpha", "io80_codes": "55"},
        {"canonical_id": "a2", "canonical_name": "Alpha", "io80_codes": "55"},
        {"canonical_id": "t", "canonical_name": "Alpha", "io80_codes": "55; 56"},
    ])
    out = resolve_io80_ambiguity(frame)
    assert out.loc[2, "io80_map_code"] == "55"
    assert out.loc[2, "io80_map_status"] == "NAME_MATCH_RESOLVED"
    assert out.loc[2, "io80_resolution_method"] == IO80_NAME_METHOD
    assert out.loc[2, "io80_resolution_support"] == "2"
    assert out["io80_codes"].equals(frame["io80_codes"])


def test_66_67_exact_name_resolves():
    frame = _frame([
        {"canonical_id": "a1", "canonical_name": "Metro Bank", "io80_codes": "66"},
        {"canonical_id": "a2", "canonical_name": "Metro Bank", "io80_codes": "66"},
        {"canonical_id": "t", "canonical_name": "Metro Bank", "io80_codes": "66; 67"},
    ])
    out = resolve_io80_ambiguity(frame)
    assert out.loc[2, "io80_map_code"] == "66"


def test_out_of_pair_singleton_evidence_vetoes_resolution():
    frame = _frame([
        {"canonical_id": "a1", "canonical_name": "JM Trading", "io80_codes": "55"},
        {"canonical_id": "a2", "canonical_name": "JM Trading", "io80_codes": "55"},
        {"canonical_id": "x", "canonical_name": "JM Trading", "io80_codes": "66"},
        {"canonical_id": "t", "canonical_name": "JM Trading", "io80_codes": "55; 56"},
    ])
    out = resolve_io80_ambiguity(frame)
    assert out.loc[3, "io80_map_status"] == "AMBIGUOUS_SET"
    assert io80_abstention_reasons(frame).loc[3] == "contradictory_name"


def test_within_pair_contradiction_vetoes_resolution():
    frame = _frame([
        {"canonical_id": "a1", "canonical_name": "Mixed", "io80_codes": "55"},
        {"canonical_id": "a2", "canonical_name": "Mixed", "io80_codes": "55"},
        {"canonical_id": "b", "canonical_name": "Mixed", "io80_codes": "56"},
        {"canonical_id": "t", "canonical_name": "Mixed", "io80_codes": "55; 56"},
    ])
    assert resolve_io80_ambiguity(frame).loc[3, "io80_map_status"] == "AMBIGUOUS_SET"


def test_support_one_abstains():
    frame = _frame([
        {"canonical_id": "a1", "canonical_name": "Alpha", "io80_codes": "55"},
        {"canonical_id": "t", "canonical_name": "Alpha", "io80_codes": "55; 56"},
    ])
    assert io80_abstention_reasons(frame).loc[1] == "support_below_min"


def test_missing_entity_id_is_excluded_by_default():
    frame = _frame([
        {"canonical_id": "", "canonical_name": "Alpha", "io80_codes": "55"},
        {"canonical_id": None, "canonical_name": "Alpha", "io80_codes": "55"},
        {"canonical_id": "t", "canonical_name": "Alpha", "io80_codes": "55; 56"},
    ])
    out = resolve_io80_ambiguity(frame)
    assert out.loc[2, "io80_map_status"] == "AMBIGUOUS_SET"
    meta = io80_resolver_metadata(out)
    assert meta["references"]["55; 56"]["evidence_rows_without_canonical_id"] == 2


def test_duplicate_rows_of_same_entity_count_once():
    frame = _frame([
        {"canonical_id": "same", "canonical_name": "Alpha", "io80_codes": "55"},
        {"canonical_id": "same", "canonical_name": "Alpha", "io80_codes": "55"},
        {"canonical_id": "t", "canonical_name": "Alpha", "io80_codes": "55; 56"},
    ])
    assert resolve_io80_ambiguity(frame).loc[2, "io80_map_status"] == "AMBIGUOUS_SET"


def test_blank_name_abstains():
    frame = _frame([
        {"canonical_id": "a1", "canonical_name": "", "io80_codes": "55"},
        {"canonical_id": "a2", "canonical_name": "", "io80_codes": "55"},
        {"canonical_id": "t", "canonical_name": "", "io80_codes": "55; 56"},
    ])
    assert io80_abstention_reasons(frame).loc[2] == "blank_name"


def test_unrelated_pairs_are_not_resolved():
    frame = _frame([
        {"canonical_id": "a1", "canonical_name": "School", "io80_codes": "74"},
        {"canonical_id": "a2", "canonical_name": "School", "io80_codes": "74"},
        {"canonical_id": "t", "canonical_name": "School", "io80_codes": "74; 75"},
    ])
    out = resolve_io80_ambiguity(frame)
    assert out.loc[2, "io80_map_status"] == "AMBIGUOUS_SET"
    assert io80_abstention_reasons(frame).loc[2] == "pair_not_audited"


def test_rerun_is_idempotent_and_order_invariant():
    frame = _frame([
        {"canonical_id": "a1", "canonical_name": "Alpha", "io80_codes": "55"},
        {"canonical_id": "a2", "canonical_name": "Alpha", "io80_codes": "55"},
        {"canonical_id": "b1", "canonical_name": "Bank", "io80_codes": "66"},
        {"canonical_id": "b2", "canonical_name": "Bank", "io80_codes": "66"},
        {"canonical_id": "t1", "canonical_name": "Alpha", "io80_codes": "55; 56"},
        {"canonical_id": "t2", "canonical_name": "Bank", "io80_codes": "66; 67"},
    ])
    once = resolve_io80_ambiguity(frame)
    twice = resolve_io80_ambiguity(once)
    assert once[list(IO80_MAP_FIELDS)].equals(twice[list(IO80_MAP_FIELDS)])
    shuffled = resolve_io80_ambiguity(frame.sample(frac=1, random_state=4)).loc[once.index]
    assert once[list(IO80_MAP_FIELDS)].equals(shuffled[list(IO80_MAP_FIELDS)])


def test_validator_detects_underresolution_fixed_point():
    frame = _frame([
        {"canonical_id": "a1", "canonical_name": "Alpha", "io80_codes": "55"},
        {"canonical_id": "a2", "canonical_name": "Alpha", "io80_codes": "55"},
        {"canonical_id": "t", "canonical_name": "Alpha", "io80_codes": "55; 56"},
    ])
    resolved = resolve_io80_ambiguity(frame)
    broken = resolved.copy()
    broken.loc[2, list(IO80_MAP_FIELDS)] = ["", "AMBIGUOUS_SET", "", "", ""]
    with pytest.raises(ValueError, match="differs_from_policy"):
        validate_io80_map_fields(broken, _counts(broken))


def test_validator_rejects_illegal_model_resolution_while_disabled():
    frame = _frame([
        {"canonical_id": "t", "canonical_name": "Alpha", "io80_codes": "55; 56"},
    ])
    out = resolve_io80_ambiguity(frame)
    out.loc[0, list(IO80_MAP_FIELDS)] = [
        "55",
        "MODEL_RESOLVED",
        resolver.IO80_MODEL_METHOD,
        "",
        "0.99",
    ]
    with pytest.raises(ValueError):
        validate_io80_map_fields(out, _counts(out))


def test_nonunique_index_rejected():
    frame = _frame([
        {"canonical_id": "a", "canonical_name": "Alpha", "io80_codes": "55"},
        {"canonical_id": "b", "canonical_name": "Alpha", "io80_codes": "55"},
    ])
    frame.index = [0, 0]
    with pytest.raises(ValueError, match="unique index"):
        resolve_io80_ambiguity(frame)


def test_unicode_normalization_preserves_non_ascii_and_nfkc():
    assert normalize_io80_name("Ｃａｆé 新光") == "café 新光"
    assert normalize_io80_name("大华 Store") != normalize_io80_name("新光 Store")


def test_metadata_states_actual_evidence_semantics():
    meta = io80_resolver_metadata(pd.DataFrame(columns=["io80_codes"]))
    assert meta["version"] == "io80-v4"
    assert meta["contradiction_scope"] == "all_singleton_io80_codes"
    assert meta["reference_stability"] == "same_run_composition_dependent"
    assert "not statistically independent" in meta["upstream_name_dependence"]
