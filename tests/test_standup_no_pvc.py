"""standup --no-pvc: flag plumbing, overrides, and step gates."""

from __future__ import annotations

import argparse

from llmdbenchmark.interface import standup as standup_interface


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    standup_interface.add_subcommands(subparsers, parents=[])
    return parser.parse_args(argv)


def test_standup_no_pvc_defaults_false() -> None:
    assert _parse(["standup"]).no_pvc is False


def test_standup_no_pvc_parses_true() -> None:
    assert _parse(["standup", "--no-pvc"]).no_pvc is True


def test_standup_no_pvc_env_var(monkeypatch) -> None:
    monkeypatch.setenv("LLMDBENCH_NO_PVC", "1")
    assert _parse(["standup"]).no_pvc is True
