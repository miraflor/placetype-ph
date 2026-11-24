"""Policy, cache and fail-closed metadata regressions."""

import json

import pandas as pd
import pytest
from typer.testing import CliRunner

from placetype_ph.cli import app
from placetype_ph.express import input_cache_key, pipeline_state
from placetype_ph.io_resolution import POLICY, draft_dependencies


@pytest.mark.parametrize("value", [None, "", "  ", pd.NA, float("nan")])
def test_legacy_provisional_metadata_is_conservative(value):
    assert draft_dependencies(value, provisional=True) == {"io16", "io80", "io240"}
    assert not draft_dependencies(value, provisional=False)


@pytest.mark.parametrize("value", ["io420", "io80; io80", "io80;", "none; io80", "IO80"])
def test_invalid_draft_metadata_fails(value):
    with pytest.raises(ValueError, match="io_mapping_draft_levels"):
        draft_dependencies(value, provisional=True)


@pytest.mark.parametrize("value", ["io80", "none"])
def test_ordinary_rows_cannot_claim_draft_metadata(value):
    with pytest.raises(ValueError, match="io_mapping_draft_levels"):
        draft_dependencies(value, provisional=False)


def test_explicit_independence_and_partial_dependence():
    assert not draft_dependencies("none", provisional=True)
    assert draft_dependencies("io80; io240", provisional=True) == {"io80", "io240"}


def test_policy_changes_express_cache_key(tmp_path):
    path = tmp_path / "input.parquet"
    path.write_bytes(b"test")
    policies = ("strict", "evidence-weighted", "extended")
    states = [pipeline_state(policy) for policy in policies]
    keys = [input_cache_key(path, [state]) for state in states]
    assert len(set(keys)) == len(policies)
    assert pipeline_state() == states[0]
    for policy, state in zip(policies, states, strict=True):
        info = json.loads(state.removeprefix("pipeline:"))["io_resolution"]
        assert info["selected_policy"] == policy
        assert info["implementation_policy"] == POLICY
        assert len(info["references"]) == 5
        assert all(len(value) == 64 for value in info["references"].values())


def test_reference_content_changes_express_key(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path

    from placetype_ph import express

    source = Path(express.__file__).resolve().parent / "data"
    destination = tmp_path / "data"
    destination.mkdir()
    info = json.loads(pipeline_state().removeprefix("pipeline:"))["io_resolution"]
    for name in info["references"]:
        shutil.copyfile(source / name, destination / name)
    monkeypatch.setattr(express, "__file__", str(tmp_path / "express.py"))
    before = pipeline_state()
    path = destination / "psic_rev5_to_psic_2019.csv"
    path.write_bytes(path.read_bytes() + b"\n")
    assert pipeline_state() != before


def test_invalid_cache_policy_fails():
    with pytest.raises(ValueError, match="unknown I-O resolution policy"):
        pipeline_state("invalid")


@pytest.mark.parametrize("command", ["classify", "express"])
def test_policy_option_visible_and_invalid_value_fails(command):
    runner = CliRunner()
    help_result = runner.invoke(app, [command, "--help"], color=False)
    assert help_result.exit_code == 0, help_result.output
    # Rich may insert ANSI sequences or wrap help across terminal columns.
    assert "resolution" in help_result.output
    invalid = runner.invoke(
        app, [command, "missing.parquet", "--io-resolution-policy", "invalid"]
    )
    assert invalid.exit_code != 0
    assert "evidence-weighted" in invalid.output
