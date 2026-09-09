"""Tests for the --no-cleanup flag (keep harness pods after the run)."""

from __future__ import annotations

import argparse

from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.interface import run as run_interface
from llmdbenchmark.run.steps.step_11_cleanup_post import RunCleanupPostStep


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    run_interface.add_subcommands(subparsers, parents=[])
    return parser.parse_args(argv)


def test_no_cleanup_flag_defaults_false() -> None:
    args = _parse(["run"])
    assert args.no_cleanup is False


def test_no_cleanup_flag_parses_true() -> None:
    args = _parse(["run", "--no-cleanup"])
    assert args.no_cleanup is True


def test_no_cleanup_env_var_sets_default(monkeypatch) -> None:
    monkeypatch.setenv("LLMDBENCH_NO_CLEANUP", "1")
    args = _parse(["run"])
    assert args.no_cleanup is True


def test_context_no_cleanup_defaults_false(tmp_path) -> None:
    context = ExecutionContext(plan_dir=tmp_path, workspace=tmp_path)
    assert context.no_cleanup is False


def test_cleanup_post_step_skips_with_no_cleanup(tmp_path) -> None:
    context = ExecutionContext(plan_dir=tmp_path, workspace=tmp_path, no_cleanup=True)
    assert RunCleanupPostStep().should_skip(context) is True


def test_cleanup_post_step_runs_by_default(tmp_path) -> None:
    context = ExecutionContext(plan_dir=tmp_path, workspace=tmp_path)
    assert RunCleanupPostStep().should_skip(context) is False
