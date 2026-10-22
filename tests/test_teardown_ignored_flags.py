"""Tests for ``teardown --hostpath`` / ``teardown --network``.

Both flags are accepted so a standup command line can be reused for
teardown, but they are ignored: they must not land on the ``hostpath`` /
``network`` attributes that the plan render reads.
"""

from __future__ import annotations

import argparse

from llmdbenchmark.interface import teardown


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    teardown.add_subcommands(subparsers)
    return parser.parse_args(["teardown", *argv])


def test_flags_are_accepted_and_not_applied():
    args = _parse(["--hostpath", "/mnt/models", "--network", "multi-nic"])
    assert getattr(args, "hostpath", None) is None
    assert getattr(args, "network", None) is None


def test_flags_ignore_env(monkeypatch):
    monkeypatch.setenv("LLMDBENCH_HOSTPATH", "/mnt/models")
    monkeypatch.setenv("LLMDBENCH_NETWORK", "multi-nic")
    args = _parse([])
    assert getattr(args, "hostpath", None) is None
    assert getattr(args, "network", None) is None
