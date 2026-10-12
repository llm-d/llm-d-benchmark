"""Tests for the workload shown in the run summary banner."""

from types import SimpleNamespace

import yaml

from llmdbenchmark.cli import _resolve_summary_workload


def _stack(tmp_path, name, harness):
    stack = tmp_path / name
    stack.mkdir()
    (stack / "config.yaml").write_text(yaml.safe_dump({"harness": harness}))
    return stack


def _context(harness_profile=None, rendered_stacks=None, stack_filter=None):
    return SimpleNamespace(
        harness_profile=harness_profile,
        rendered_stacks=rendered_stacks or [],
        stack_filter=stack_filter,
    )


def test_cli_workload_wins(tmp_path):
    stack = _stack(tmp_path, "s1", {"profile": "chatbot_synthetic.yaml"})
    ctx = _context(harness_profile="shared_prefix.yaml.in", rendered_stacks=[stack])
    assert _resolve_summary_workload(ctx) == "shared_prefix.yaml"


def test_default_from_rendered_config(tmp_path):
    stack = _stack(tmp_path, "s1", {"profile": "sanity_random.yaml"})
    assert _resolve_summary_workload(_context(rendered_stacks=[stack])) == (
        "sanity_random.yaml"
    )


def test_experiment_profile_takes_precedence(tmp_path):
    stack = _stack(
        tmp_path,
        "s1",
        {"experimentProfile": "chatbot_synthetic.yaml.in", "profile": "x.yaml"},
    )
    assert _resolve_summary_workload(_context(rendered_stacks=[stack])) == (
        "chatbot_synthetic.yaml"
    )


def test_no_rendered_stacks_uses_default():
    assert _resolve_summary_workload(_context()) == "sanity_random.yaml"


def test_distinct_stack_profiles_listed_and_filter_honored(tmp_path):
    s1 = _stack(tmp_path, "s1", {"profile": "a.yaml"})
    s2 = _stack(tmp_path, "s2", {"profile": "b.yaml"})
    s3 = _stack(tmp_path, "s3", {"profile": "a.yaml"})
    s4 = _stack(tmp_path, "s4", {"profile": "c.yaml"})
    ctx = _context(rendered_stacks=[s1, s2, s3, s4], stack_filter=["s1", "s2", "s3"])
    assert _resolve_summary_workload(ctx) == "a.yaml, b.yaml"


def test_missing_config_falls_back_to_default(tmp_path):
    stack = tmp_path / "s1"
    stack.mkdir()
    assert _resolve_summary_workload(_context(rendered_stacks=[stack])) == (
        "sanity_random.yaml"
    )
