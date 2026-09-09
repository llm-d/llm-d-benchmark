"""Tests for the --no-pvc flag plumbing (CLI parser + ExecutionContext)."""

from __future__ import annotations

import argparse

from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.interface import run as run_interface


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    run_interface.add_subcommands(subparsers, parents=[])
    return parser.parse_args(argv)


def test_no_pvc_flag_defaults_false() -> None:
    args = _parse(["run"])
    assert args.no_pvc is False


def test_no_pvc_flag_parses_true() -> None:
    args = _parse(["run", "--no-pvc"])
    assert args.no_pvc is True


def test_no_pvc_env_var_sets_default(monkeypatch) -> None:
    monkeypatch.setenv("LLMDBENCH_NO_PVC", "1")
    args = _parse(["run"])
    assert args.no_pvc is True


def test_cli_flag_beats_unset_env(monkeypatch) -> None:
    monkeypatch.delenv("LLMDBENCH_NO_PVC", raising=False)
    args = _parse(["run", "--no-pvc"])
    assert args.no_pvc is True


def test_context_no_pvc_defaults_false(tmp_path) -> None:
    context = ExecutionContext(plan_dir=tmp_path, workspace=tmp_path)
    assert context.no_pvc is False


def test_context_accepts_no_pvc(tmp_path) -> None:
    context = ExecutionContext(plan_dir=tmp_path, workspace=tmp_path, no_pvc=True)
    assert context.no_pvc is True
